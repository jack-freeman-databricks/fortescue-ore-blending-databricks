"""Synthetic mine-to-port data for the Fortescue ore-blending demo.

Simulates 90 days of Pilbara operations (AWST, naive timestamps):
  * 4 mine hubs (Cloudbreak, Christmas Creek, Solomon, Eliwana), 5 ROM stockpiles each
  * haul-truck deliveries from the fleet management system (block-model grade estimates)
  * train load-outs (reclaim mix per stockpile) at each hub's train load-out (TLO)
  * cross-belt analyser (PGNAA) readings every minute while a train loads
  * lab assays (LIMS) per train composite, returned 24-48 h after departure
  * vessel cargoes at Herb Elliott Port (trains -> vessel)
  * planned trains for "today" (2026-11-03) with no reclaim plan yet

Ground truth is hidden from the outputs. The block model carries a hub-specific
bias, the analyser drifts between calibrations, and controllers build trains
from block-model grades. Those three effects cause the off-spec trains the demo
is built to fix.

All data is synthetic. Product names are Fortescue's public product names; the
specs and penalty schedule are illustrative, not contractual.

Usage: python data_gen/generate.py --out data/landing
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42
START = datetime(2026, 8, 5)
NOW = datetime(2026, 11, 3, 6, 0)  # "today" 06:00 AWST, start of day shift
ELEMENTS = ["fe", "sio2", "al2o3", "p", "loi"]

HUBS = {
    # hub: name, base grade, block-model bias (estimate - truth), trains/day
    "CB": dict(name="Cloudbreak", base=[57.6, 5.6, 2.55, 0.064, 8.4],
               bm_bias=[0.22, -0.15, -0.05, 0.000, 0.0], trains=4, lat=-22.30, lon=119.43),
    "CC": dict(name="Christmas Creek", base=[58.1, 5.1, 2.40, 0.061, 8.1],
               bm_bias=[0.10, -0.05, 0.00, 0.000, 0.0], trains=4, lat=-22.38, lon=119.65),
    "SOL": dict(name="Solomon", base=[57.4, 5.7, 2.75, 0.070, 9.1],
                bm_bias=[0.45, -0.30, -0.10, -0.003, -0.15], trains=4, lat=-22.13, lon=117.95),
    "ELI": dict(name="Eliwana", base=[59.4, 4.3, 2.00, 0.058, 6.4],
                bm_bias=[0.20, -0.10, -0.05, 0.000, 0.0], trains=3, lat=-22.45, lon=116.87),
}
# stockpile grade classes relative to hub base: (fe, sio2, al2o3, p, loi)
CLASSES = {
    "HG": [1.6, -1.1, -0.45, -0.006, -0.6],
    "MG1": [0.7, -0.5, -0.20, -0.003, -0.3],
    "MG2": [0.0, 0.0, 0.00, 0.000, 0.0],
    "LG1": [-0.8, 0.6, 0.25, 0.004, 0.35],
    "LG2": [-1.7, 1.3, 0.55, 0.008, 0.7],
}
PRODUCTS = {
    # product: typical, min/max limits (illustrative)
    "FB": dict(name="Fortescue Blend", fe_typ=58.0, fe_min=57.5, sio2_max=6.0,
               al2o3_max=2.8, p_max=0.075, loi_max=9.0),
    "KF": dict(name="Kings Fines", fe_typ=56.0, fe_min=55.5, sio2_max=7.2,
               al2o3_max=3.6, p_max=0.080, loi_max=10.0),
}
TRAIN_T = 34_500   # wet tonnes per train
VESSEL_T = 190_000  # Capesize cargo, wet tonnes

rng = np.random.default_rng(SEED)


def stockpiles() -> pd.DataFrame:
    rows = []
    for hub, h in HUBS.items():
        for i, (cls, off) in enumerate(CLASSES.items()):
            # Solomon Kings ore: LG stockpiles feed Kings Fines, rest Fortescue Blend
            product = "KF" if (hub == "SOL" and cls in ("LG1", "LG2")) else "FB"
            g = np.array(h["base"]) + np.array(off)
            rows.append(dict(stockpile_id=f"{hub}-ROM-{i + 1:02d}", hub_code=hub,
                             grade_class=cls, product_code=product,
                             capacity_t=2_000_000, design_fe=round(g[0], 2),
                             design_sio2=round(g[1], 2), design_al2o3=round(g[2], 3),
                             design_p=round(g[3], 4), design_loi=round(g[4], 2)))
    return pd.DataFrame(rows)


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for d in ["reference", "fms", "rail", "analyser", "lims", "port"]:
        (out / d).mkdir(exist_ok=True)

    sp = stockpiles()
    sp.to_csv(out / "reference/stockpiles.csv", index=False)
    pd.DataFrame([dict(hub_code=k, hub_name=v["name"], latitude=v["lat"], longitude=v["lon"],
                       tlo_id=f"{k}-TLO1", trains_per_day=v["trains"]) for k, v in HUBS.items()]
                 ).to_csv(out / "reference/mine_hubs.csv", index=False)
    pd.DataFrame([dict(product_code=k, product_name=v["name"], **{x: v[x] for x in v if x != "name"})
                  for k, v in PRODUCTS.items()]).to_csv(out / "reference/product_specs.csv", index=False)
    pd.DataFrame([
        dict(element="fe", basis="below_typical", usd_per_dmt_per_unit=1.80, unit_pct=1.0),
        dict(element="fe", basis="below_min", usd_per_dmt_per_unit=4.50, unit_pct=1.0),
        dict(element="sio2", basis="above_max", usd_per_dmt_per_unit=1.50, unit_pct=1.0),
        dict(element="al2o3", basis="above_max", usd_per_dmt_per_unit=2.50, unit_pct=1.0),
        dict(element="p", basis="above_max", usd_per_dmt_per_unit=0.40, unit_pct=0.01),
    ]).to_csv(out / "reference/penalty_schedule.csv", index=False)

    sp_idx = {r.stockpile_id: r for r in sp.itertuples()}
    # true grade of each stockpile drifts daily (AR(1)) around its design grade
    sd_drift = np.array([0.35, 0.25, 0.10, 0.003, 0.20])
    drift = {s: np.zeros(5) for s in sp.stockpile_id}
    # analyser bias per hub (fe, sio2, al2o3, p, loi): random walk, reset on calibration
    an_bias = {h: rng.normal(0, [0.3, 0.2, 0.08, 0.002, 0.15]) for h in HUBS}
    last_cal = {h: START for h in HUBS}
    cal_log = []

    hauls, trains, an_rows, assays, plans = [], [], [], [], []
    truth_window = {s: [] for s in sp.stockpile_id}  # recent (true, bm) delivery grades
    days = (NOW.date() - START.date()).days + 1
    haul_seq = train_seq = sample_seq = 0

    for d in range(days):
        day = START + timedelta(days=d)
        for s in sp.stockpile_id:
            drift[s] = 0.85 * drift[s] + rng.normal(0, sd_drift * 0.5)
        for h in HUBS:
            an_bias[h] = an_bias[h] + rng.normal(0, [0.06, 0.04, 0.015, 0.0004, 0.03])
            if (day - last_cal[h]).days >= rng.integers(18, 25):
                an_bias[h] = rng.normal(0, [0.12, 0.08, 0.03, 0.001, 0.06])
                last_cal[h] = day
                cal_log.append(dict(tlo_id=f"{h}-TLO1", calibrated_at=day + timedelta(hours=10)))

        # ---- haul deliveries (FMS) ----
        for s, r in sp_idx.items():
            h = HUBS[r.hub_code]
            n = rng.poisson(h["trains"] * TRAIN_T / 5 / 235)
            design = np.array([r.design_fe, r.design_sio2, r.design_al2o3, r.design_p, r.design_loi])
            for _ in range(n):
                if day + timedelta(hours=24) > NOW and day.date() == NOW.date():
                    break
                ts = day + timedelta(seconds=int(rng.integers(0, 86400)))
                if ts >= NOW:
                    continue
                true = design + drift[s] + rng.normal(0, [0.9, 0.7, 0.25, 0.006, 0.5])
                bm = true + np.array(h["bm_bias"]) + rng.normal(0, [0.45, 0.35, 0.12, 0.003, 0.25])
                haul_seq += 1
                truth_window[s].append((ts, true, bm))
                hauls.append(dict(cycle_id=f"HC{haul_seq:08d}", hub_code=r.hub_code,
                                  truck_id=f"{r.hub_code}-T{rng.integers(1, 46):03d}",
                                  dump_ts=ts.strftime("%Y-%m-%d %H:%M:%S"),
                                  source_block=f"{r.hub_code}-B{rng.integers(1000, 1400)}",
                                  destination_stockpile=s,
                                  payload_t=round(float(rng.normal(236, 9)), 1),
                                  bm_fe=round(bm[0], 2), bm_sio2=round(bm[1], 2),
                                  bm_al2o3=round(bm[2], 3), bm_p=round(bm[3], 4), bm_loi=round(bm[4], 2)))

        # ---- trains ----
        for hub, h in HUBS.items():
            hub_sp = sp[sp.hub_code == hub]
            for k in range(h["trains"]):
                start = day + timedelta(hours=1 + k * 24 / h["trains"] + float(rng.normal(0, 0.4)))
                load_h = float(rng.normal(2.6, 0.2))
                depart = start + timedelta(hours=load_h)
                # product: Solomon alternates Kings Fines / Fortescue Blend
                product = "KF" if (hub == "SOL" and k % 2 == 1) else "FB"
                cands = hub_sp[hub_sp.product_code == product]
                if depart > NOW:
                    if start.date() == NOW.date() or start - NOW < timedelta(hours=18):
                        train_seq += 1
                        plans.append(dict(planned_train_id=f"T{train_seq:06d}", hub_code=hub,
                                          tlo_id=f"{hub}-TLO1", product_code=product,
                                          planned_load_start=start.strftime("%Y-%m-%d %H:%M:%S"),
                                          target_wet_t=TRAIN_T))
                    continue

                # controller view: block-model mean of last ~3 days of deliveries
                def window(s):
                    w = [x for x in truth_window[s] if start - timedelta(days=3) <= x[0] < start]
                    if not w:
                        w = truth_window[s][-20:]
                    return np.mean([x[1] for x in w], 0), np.mean([x[2] for x in w], 0)

                views = {s: window(s) for s in cands.stockpile_id}
                target = PRODUCTS[product]["fe_typ"] + 0.30
                ids = list(views)
                hi = [s for s in ids if views[s][1][0] >= target]
                lo = [s for s in ids if views[s][1][0] < target]
                if not hi or not lo:
                    pick = list(rng.choice(ids, size=min(2, len(ids)), replace=False))
                    w = np.full(len(pick), 1 / len(pick))
                else:
                    a, b = rng.choice(hi), rng.choice(lo)
                    fa, fb = views[a][1][0], views[b][1][0]
                    wa = float(np.clip((target - fb) / (fa - fb + 1e-9), 0.15, 0.85))
                    pick, w = [a, b], np.array([wa, 1 - wa])
                    rest = [s for s in ids if s not in pick]
                    if rest and rng.random() < 0.3:
                        c = rng.choice(rest)
                        pick.append(c)
                        w = np.append(w * 0.8, 0.2)
                w = w + rng.normal(0, 0.03, len(w))
                w = np.clip(w, 0.05, None)
                w = w / w.sum()
                tonnes = TRAIN_T * float(rng.normal(1, 0.025))
                true_g = sum(wi * views[s][0] for wi, s in zip(w, pick))
                train_seq += 1
                tid = f"T{train_seq:06d}"
                moisture = float(np.clip(rng.normal(7.8, 0.6), 6, 10))
                trains.append(dict(
                    train_id=tid, hub_code=hub, tlo_id=f"{hub}-TLO1", product_code=product,
                    load_start=start.strftime("%Y-%m-%d %H:%M:%S"),
                    depart_ts=depart.strftime("%Y-%m-%d %H:%M:%S"),
                    wagons=244, wet_tonnes=round(tonnes, 0), moisture_pct=round(moisture, 2),
                    reclaim=[dict(stockpile_id=s, wet_tonnes=round(tonnes * wi, 0)) for s, wi in zip(pick, w)],
                ))
                # analyser: one reading per minute during loading
                n_min = int(load_h * 60)
                prof = true_g + an_bias[hub]
                for m in range(n_min):
                    ts = start + timedelta(minutes=m)
                    rd = prof + rng.normal(0, [0.9, 0.6, 0.2, 0.006, 0.4])
                    status = "OK" if rng.random() > 0.004 else "FAULT"
                    an_rows.append(dict(tlo_id=f"{hub}-TLO1", train_id=tid,
                                        reading_ts=ts.strftime("%Y-%m-%d %H:%M:%S"),
                                        fe=round(rd[0], 2) if status == "OK" else -99.0,
                                        sio2=round(rd[1], 2), al2o3=round(rd[2], 3),
                                        p=round(rd[3], 4), loi=round(rd[4], 2),
                                        tph=round(float(rng.normal(13_200, 600)), 0), status=status))
                # lab assay: composite of the train, returned 24-48 h later
                result_ts = depart + timedelta(hours=float(rng.uniform(24, 48)))
                if result_ts <= NOW:
                    lab = true_g + rng.normal(0, [0.12, 0.10, 0.04, 0.002, 0.08])
                    sample_seq += 1
                    a = dict(sample_id=f"LIMS-{sample_seq:07d}", train_id=tid, sample_type="TRAIN_COMPOSITE",
                             lab="Port Hedland Lab", received_ts=(depart + timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%S"),
                             result_ts=result_ts.strftime("%Y-%m-%dT%H:%M:%S"),
                             analytes=[dict(code=e.upper(), value=round(float(v), 4 if e == "p" else 3), unit="%")
                                       for e, v in zip(ELEMENTS, lab)])
                    if rng.random() < 0.006:  # planted QA/QC failure: closure way off
                        a["analytes"][0]["value"] = round(float(lab[0]) + 9.0, 3)
                    assays.append(a)

    # ---- vessels: consecutive trains per product fill a cargo ----
    tdf = pd.DataFrame(trains).sort_values("depart_ts")
    vessels, vseq = [], 0
    names = ["Cape Pilbara", "Iron Brolga", "Eliwana Star", "FMG Nyabalee", "Ocean Kestrel", "Hedland Spirit",
             "Pacific Dawn", "Coral Harrier", "Red Earth", "Great Banks", "Tasman Ore", "Southern Cross"]
    for prod, grp in tdf.groupby("product_code"):
        buf, t = [], 0.0
        for r in grp.itertuples():
            buf.append(r.train_id)
            t += r.wet_tonnes
            if t >= VESSEL_T:
                last_dep = datetime.strptime(r.depart_ts, "%Y-%m-%d %H:%M:%S")
                done = last_dep + timedelta(hours=float(rng.uniform(30, 60)))
                if done <= NOW:
                    vseq += 1
                    vessels.append(dict(vessel_id=f"V{vseq:04d}", vessel_name=f"{rng.choice(names)} {vseq}",
                                        product_code=prod, berth=f"HEP-B{rng.integers(1, 6)}",
                                        load_complete_ts=done.strftime("%Y-%m-%dT%H:%M:%S"),
                                        destination=str(rng.choice(["Qingdao", "Caofeidian", "Rizhao", "Pohang", "Kashima"])),
                                        cargo_trains=buf, wet_tonnes=round(t, 0)))
                buf, t = [], 0.0

    # ---- write landing files (daily partitions like the source systems export) ----
    def daily(df, ts_col, sub, prefix, fmt):
        df = df.copy()
        df["_d"] = df[ts_col].str[:10].str.replace("-", "")
        for dkey, g in df.groupby("_d"):
            g = g.drop(columns="_d")
            path = out / sub / f"{prefix}_{dkey}.{fmt}"
            if fmt == "csv":
                g.to_csv(path, index=False)
            else:
                with open(path, "w") as f:
                    for rec in g.to_dict("records"):
                        f.write(json.dumps(rec) + "\n")

    hdf = pd.DataFrame(hauls)
    hdf = pd.concat([hdf, hdf.sample(40, random_state=1)])        # planted duplicates
    hdf.loc[hdf.sample(25, random_state=2).index, "payload_t"] = -1.0  # planted bad payloads
    daily(hdf, "dump_ts", "fms", "haul_cycles", "csv")
    daily(tdf, "depart_ts", "rail", "train_consists", "json")
    daily(pd.DataFrame(an_rows), "reading_ts", "analyser", "xba_readings", "csv")
    daily(pd.DataFrame(assays), "result_ts", "lims", "assays", "json")
    daily(pd.DataFrame(vessels), "load_complete_ts", "port", "vessel_cargoes", "json")
    pd.DataFrame(plans).to_csv(out / "rail/train_plan_20261103.csv", index=False)
    pd.DataFrame(cal_log).assign(calibrated_at=lambda x: x.calibrated_at.astype(str)) \
        .to_csv(out / "reference/analyser_calibrations.csv", index=False)

    # opening ROM inventory (2026-08-05) sized so every stockpile stays positive
    net = (hdf.drop_duplicates("cycle_id").query("payload_t > 0").groupby("destination_stockpile").payload_t.sum()
           .sub(pd.DataFrame([r for t in trains for r in t["reclaim"]]).groupby("stockpile_id").wet_tonnes.sum(),
                fill_value=0))
    lowest = {s: 0.0 for s in sp.stockpile_id}
    sp["opening_balance_t"] = [round(max(150_000.0, -net.get(s, 0) + float(rng.uniform(120_000, 380_000))), -3)
                               for s in sp.stockpile_id]
    sp.to_csv(out / "reference/stockpiles.csv", index=False)

    print(f"stockpiles={len(sp)} haul_cycles={len(hdf)} trains={len(tdf)} analyser_readings={len(an_rows)} "
          f"assays={len(assays)} vessels={len(vessels)} planned_trains_today={len(plans)}")
    print(f"window {START:%Y-%m-%d} -> {NOW:%Y-%m-%d %H:%M} AWST; trains awaiting lab = {len(tdf) - len(assays)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/landing")
    main(Path(ap.parse_args().out))
