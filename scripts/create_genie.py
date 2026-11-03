"""Create (or update) the Genie Agent space for the ore-blending data product.

Usage: python scripts/create_genie.py [--space-id <id to update>]
Prints the space id. Writes genie/space.json (the serialized definition) for the repo.
"""
import argparse
import json
from pathlib import Path

from databricks.sdk import WorkspaceClient

FQ = "jack_freeman_catalog.fmg_ore_blending"
TABLES = sorted([f"{FQ}.blend_compliance_mv", f"{FQ}.vessel_compliance_mv", f"{FQ}.train_performance",
                 f"{FQ}.vessel_compliance", f"{FQ}.stockpile_grade_current", f"{FQ}.train_grade_predictions",
                 f"{FQ}.blend_recommendations", f"{FQ}.value_backtest"])

INSTRUCTIONS = f"""You are the grade-control analyst for Fortescue's mine-to-port supply chain (Pilbara, Western Australia).
Ore is trucked to ROM stockpiles at 4 mine hubs: CB=Cloudbreak, CC=Christmas Creek, SOL=Solomon, ELI=Eliwana. Trains (~34.5 kt wet) are loaded at each hub's train load-out (TLO) and railed to Herb Elliott Port, where trains are blended into vessel cargoes.
Products: FB = Fortescue Blend (typical Fe 58.0%, min 57.5%), KF = Kings Fines (typical Fe 56.0%, min 55.5%). Specs and penalties are illustrative.

PREFER THE METRIC VIEWS for KPIs. Query them with MEASURE(`Measure Name`) and GROUP BY the named dimensions, e.g.
SELECT `Hub`, MEASURE(`Pct Trains Below Typical Fe`) FROM {FQ}.blend_compliance_mv GROUP BY ALL.
- blend_compliance_mv: train-level compliance (only trains whose lab assay has returned).
- vessel_compliance_mv: vessel cargo compliance and illustrative grade penalty USD.

Detail tables:
- train_performance: one row per train. lab_fe = lab assay Fe % (truth, arrives 24-48 h after departure), bm_fe = block-model Fe of the reclaimed stockpiles, xba_fe = cross-belt analyser Fe. bm_fe_error = bm_fe - lab_fe (positive = block model over-states iron). below_typical_fe / limit_breach flags. lab_status AWAITING_LAB means lab not back yet.
- train_grade_predictions: ML model (train_grade_predictor@champion) prediction of lab grade at departure. pred_fe, pred_fe_band (90% error band), predicted_status ON_SPEC / AT_RISK / OFF_SPEC_LIKELY. in_flight = true for trains that have left the load-out and are awaiting lab: use it for "right now" questions. lab_status QAQC_REJECTED = lab result failed QA/QC.
- blend_recommendations: today's planned trains with the optimiser's recommended reclaim blend (blend_json = tonnes by stockpile), pred_fe (lab-reconciled), bm_view_fe (what the raw block model would show), rationale (AI-written).
- stockpile_grade_current: each ROM stockpile now: tonnes_on_hand, bm_fe (block model), recon_fe (lab-reconciled), hub_bm_bias_fe.
- vessel_compliance: one row per vessel; penalty_usd may be NULL for users without commercial entitlement (column mask), so say so if it is NULL.
- value_backtest: simulated 30-day comparison of block-model targeting vs reconciled-grade targeting.

Always show hub names, not codes. Round grades to 2 decimals and tonnes to whole numbers or kt. "Last month" = the latest full calendar month in the data (data runs 2026-08-05 to 2026-11-03). Dates are AWST."""

SAMPLE_Q = [
    "Which hub had the highest share of trains below typical Fe in October?",
    "What is the average block model Fe error by hub over the last 30 days?",
    "Show vessels loaded in October with Fe below typical and their penalty",
    "How many trains are predicted off-spec right now?",
    "Which stockpiles have the biggest gap between block model and reconciled Fe?",
    "What blend is recommended for the next Solomon train and why?",
]

EXAMPLES = [
    ("Which hub had the highest share of trains below typical Fe last month?",
     f"SELECT `Hub`, MEASURE(`Trains`) AS trains, ROUND(MEASURE(`Pct Trains Below Typical Fe`), 1) AS pct_below_typical\n"
     f"FROM {FQ}.blend_compliance_mv\nWHERE `Departure Month` = DATE'2026-10-01'\nGROUP BY ALL\nORDER BY pct_below_typical DESC"),
    ("What is the block model Fe bias by hub?",
     f"SELECT `Hub`, ROUND(MEASURE(`Block Model Fe Bias`), 3) AS bm_fe_bias, ROUND(MEASURE(`Block Model Fe MAE`), 3) AS bm_fe_mae,\n"
     f"       ROUND(MEASURE(`Analyser Fe MAE`), 3) AS analyser_fe_mae\nFROM {FQ}.blend_compliance_mv\nGROUP BY ALL\nORDER BY bm_fe_bias DESC"),
    ("How many in-flight trains are predicted off-spec?",
     f"SELECT CASE hub_code WHEN 'CB' THEN 'Cloudbreak' WHEN 'CC' THEN 'Christmas Creek' WHEN 'SOL' THEN 'Solomon' WHEN 'ELI' THEN 'Eliwana' END AS hub,\n"
     f"       predicted_status, COUNT(*) AS trains, ROUND(AVG(pred_fe), 2) AS avg_predicted_fe\n"
     f"FROM {FQ}.train_grade_predictions\nWHERE in_flight\nGROUP BY ALL\nORDER BY hub, predicted_status"),
    ("What is the monthly grade penalty by product?",
     f"SELECT `Product`, `Load Month`, MEASURE(`Vessels`) AS vessels, MEASURE(`Vessels Below Typical Fe`) AS vessels_below_typical,\n"
     f"       ROUND(MEASURE(`Grade Penalty USD`), 0) AS penalty_usd\nFROM {FQ}.vessel_compliance_mv\nGROUP BY ALL\nORDER BY `Product`, `Load Month`"),
]


def build():
    return {
        "version": 2,
        "config": {"sample_questions": sorted([{"id": f"1{i:031d}", "question": [q]} for i, q in enumerate(SAMPLE_Q, 1)],
                                              key=lambda x: x["id"])},
        "data_sources": {"tables": [{"identifier": t} for t in TABLES]},
        "instructions": {
            "text_instructions": [{"id": "3" + "0" * 30 + "1", "content": [l + "\n" for l in INSTRUCTIONS.splitlines()]}],
            "example_question_sqls": sorted([{"id": f"2{i:031d}", "question": [q], "sql": [l + "\n" for l in s.splitlines()]}
                                             for i, (q, s) in enumerate(EXAMPLES, 1)], key=lambda x: x["id"]),
        },
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--space-id")
    ap.add_argument("--profile", default="fevm-serverless-stable-1s8h43")
    a = ap.parse_args()
    w = WorkspaceClient(profile=a.profile)
    space = build()
    Path("genie").mkdir(exist_ok=True)
    Path("genie/space.json").write_text(json.dumps(space, indent=2))
    body = {"title": "Fortescue Blend Compliance (Genie Agent)",
            "description": "Ask about train and vessel grade compliance, block-model bias, in-flight train predictions and today's recommended blends across Cloudbreak, Christmas Creek, Solomon and Eliwana.",
            "warehouse_id": "495cddfca8ecaa75", "serialized_space": json.dumps(space)}
    if a.space_id:
        r = w.api_client.do("PATCH", f"/api/2.0/genie/spaces/{a.space_id}", body=body)
    else:
        body["parent_path"] = "/Workspace/Users/jack.freeman@databricks.com"
        r = w.api_client.do("POST", "/api/2.0/genie/spaces", body=body)
    print(r.get("space_id"))
