"""Train reclaim-mix optimiser (linear program).

For one planned train: choose wet tonnes x_s from each candidate ROM stockpile (same hub and
product) so that the predicted train grade meets product spec with a safety margin, while
giving away as little iron as possible (use the lowest-grade blend that still meets typical
Fe). Grades come from the LAB-RECONCILED stockpile grade, not the raw block model. That
correction is what fixes Solomon's systematic shortfall.

Pure numpy/scipy so the same code runs in the scoring job and inside the Databricks App.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import linprog

ELEMENTS = ["fe", "sio2", "al2o3", "p", "loi"]


@dataclass
class Stockpile:
    stockpile_id: str
    tonnes_on_hand: float
    grade: dict  # element -> % (lab-reconciled)
    bm_grade: dict | None = None  # element -> % (raw block model), for comparison
    available: bool = True


@dataclass
class Spec:
    fe_typ: float
    fe_min: float
    sio2_max: float
    al2o3_max: float
    p_max: float


@dataclass
class BlendResult:
    status: str
    tonnes: dict = field(default_factory=dict)
    predicted: dict = field(default_factory=dict)
    block_model_view: dict = field(default_factory=dict)
    fe_margin: float = 0.0
    message: str = ""


def optimise_train(stockpiles: list[Stockpile], spec: Spec, target_t: float = 34_500,
                   fe_margin: float = 0.15, max_share: float = 0.7, reserve_t: float = 20_000) -> BlendResult:
    """Solve one train. fe_margin = prediction uncertainty buffer above typical Fe."""
    sp = [s for s in stockpiles if s.available and s.tonnes_on_hand - reserve_t > 1_000]
    if not sp:
        return BlendResult("INFEASIBLE", message="No available stockpile with enough inventory")
    g = {e: np.array([s.grade[e] for s in sp]) for e in ELEMENTS}
    ub = [min(max_share * target_t, s.tonnes_on_hand - reserve_t) for s in sp]
    if sum(ub) < target_t:
        return BlendResult("INFEASIBLE", message="Not enough reclaimable tonnes for a full train")

    # constraints are on tonnage-weighted averages: sum(x*g)/T  >=/<=  limit  ->  linear in x
    A_ub, b_ub = [], []
    fe_floor = spec.fe_typ + fe_margin
    A_ub.append(-g["fe"]); b_ub.append(-fe_floor * target_t)
    A_ub.append(g["sio2"]); b_ub.append((spec.sio2_max - 0.2) * target_t)
    A_ub.append(g["al2o3"]); b_ub.append((spec.al2o3_max - 0.1) * target_t)
    A_ub.append(g["p"]); b_ub.append((spec.p_max - 0.003) * target_t)
    A_eq, b_eq = [np.ones(len(sp))], [target_t]
    bounds = list(zip([0.0] * len(sp), ub))

    # objective: minimise Fe giveaway (iron is the product; don't over-deliver it)
    res = linprog(c=g["fe"], A_ub=np.array(A_ub), b_ub=b_ub, A_eq=np.array(A_eq), b_eq=b_eq,
                  bounds=bounds, method="highs")
    status, msg = "OPTIMAL", "Meets typical Fe with margin at minimum Fe giveaway"
    if not res.success:
        # spec unreachable: maximise Fe instead and flag it
        res = linprog(c=-g["fe"], A_eq=np.array(A_eq), b_eq=b_eq, bounds=bounds, method="highs")
        status, msg = "BEST_EFFORT", "Typical Fe not reachable from available stockpiles; maximised Fe"
        if not res.success:
            return BlendResult("INFEASIBLE", message=res.message)
    x = res.x
    tonnes = {s.stockpile_id: round(float(t), -2) for s, t in zip(sp, x) if t > 500}
    tot = sum(tonnes.values())
    pred = {e: round(float(sum(tonnes[s.stockpile_id] * s.grade[e] for s in sp if s.stockpile_id in tonnes) / tot), 4)
            for e in ELEMENTS}
    bmv = {}
    if all(s.bm_grade for s in sp):
        bmv = {e: round(float(sum(tonnes[s.stockpile_id] * s.bm_grade[e] for s in sp if s.stockpile_id in tonnes) / tot), 4)
               for e in ELEMENTS}
    return BlendResult(status, tonnes, pred, bmv, fe_margin, msg)


def plan_shift(trains: list[dict], stockpiles_by_id: dict[str, Stockpile], specs: dict[str, Spec],
               candidates: dict[tuple, list[str]], fe_margin: float = 0.15) -> list[tuple[dict, BlendResult]]:
    """Plan trains in load-start order, depleting inventory as each train is allocated."""
    inv = {k: v.tonnes_on_hand for k, v in stockpiles_by_id.items()}
    out = []
    for t in sorted(trains, key=lambda r: str(r["planned_load_start"])):
        ids = candidates.get((t["hub_code"], t["product_code"]), [])
        sps = [Stockpile(i, inv[i], stockpiles_by_id[i].grade, stockpiles_by_id[i].bm_grade,
                         stockpiles_by_id[i].available) for i in ids]
        r = optimise_train(sps, specs[t["product_code"]], float(t.get("target_wet_t", 34_500)), fe_margin)
        for sid, tn in r.tonnes.items():
            inv[sid] -= tn
        out.append((t, r))
    return out
