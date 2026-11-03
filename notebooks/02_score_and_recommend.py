# %% [markdown]
# # 02 · Score in-flight trains, recommend today's blends, explain them (ML + GenAI)
# 1. Load `train_grade_predictor@champion` from Unity Catalog and score every train, especially
#    the ones **still awaiting lab** (departed in the last ~36 h). Output: `train_grade_predictions`.
# 2. Run the LP blend optimiser for every **planned train today** using the *lab-reconciled*
#    stockpile grades from Lakeflow gold. Output: `blend_recommendations` (+ per-stockpile lines).
# 3. `ai_query` writes a short plain-English rationale for the load-out controller.
# 4. Backtest the value: what would compliance have looked like if trains had been targeted on
#    reconciled grade instead of raw block model? Output: `value_backtest`.
#
# These tables feed the Lakebase synced tables that the app reads.

# %%
import os, sys, warnings
os.environ["GRPC_VERBOSITY"] = "ERROR"; os.environ["GLOG_minloglevel"] = "2"; os.environ["MLFLOW_DISABLE_AGENT_HINT"] = "1"
warnings.filterwarnings("ignore")
os.environ.setdefault("DATABRICKS_CONFIG_PROFILE", "fevm-serverless-stable-1s8h43")
try:
    spark  # noqa: F821
except NameError:
    from databricks.connect import DatabricksSession
    spark = DatabricksSession.builder.serverless().getOrCreate()
_here = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
for _p in [os.path.join(_here, "..", "src"), os.path.join(os.getcwd(), "..", "src"), os.path.join(os.getcwd(), "src")]:
    if os.path.isdir(os.path.join(_p, "fmg_blend")):
        sys.path.insert(0, os.path.abspath(_p)); break

import mlflow
import numpy as np
import pandas as pd
from pyspark.sql import functions as F
from fmg_blend.optimiser import ELEMENTS, Spec, Stockpile, plan_shift

C, S = "jack_freeman_catalog", "fmg_ore_blending"
FQ = f"{C}.{S}"
AS_OF = pd.Timestamp("2026-11-03 06:00:00")
mlflow.set_registry_uri("databricks-uc")
model = mlflow.pyfunc.load_model(f"models:/{FQ}.train_grade_predictor@champion")
mv = mlflow.MlflowClient().get_model_version_by_alias(f"{FQ}.train_grade_predictor", "champion")
print("Loaded champion version", mv.version)

# %% [markdown]
# ### 1 · Predict lab grade at departure for every train

# %%
tf = spark.table(f"{FQ}.train_features").toPandas()
pred = model.predict(tf)
specs_pd = spark.table(f"{FQ}.ref_product_specs").toPandas().set_index("product_code")
out = pd.concat([tf[["train_id", "hub_code", "product_code", "depart_ts", "wet_tonnes", "bm_fe", "xba_fe",
                     "lab_fe", "has_lab", "lab_status"]].reset_index(drop=True), pred.reset_index(drop=True)], axis=1)
out["fe_typ"] = out.product_code.map(specs_pd.fe_typ)
out["predicted_status"] = np.select(
    [out.pred_fe < out.fe_typ - out.pred_fe_band, out.pred_fe < out.fe_typ],
    ["OFF_SPEC_LIKELY", "AT_RISK"], "ON_SPEC")
out["in_flight"] = out.lab_status == "AWAITING_LAB"
out["hours_until_lab"] = np.where(~out.in_flight, 0.0,
                                  np.clip(36 - (AS_OF - pd.to_datetime(out.depart_ts)).dt.total_seconds() / 3600, 0, None))
out["model_version"] = str(mv.version)
out["scored_at"] = AS_OF
spark.createDataFrame(out).write.mode("overwrite").option("overwriteSchema", "true") \
    .saveAsTable(f"{FQ}.train_grade_predictions")
spark.sql(f"ALTER TABLE {FQ}.train_grade_predictions SET TBLPROPERTIES (delta.enableChangeDataFeed = true)")
pending = out[out.in_flight].sort_values("depart_ts")
print(f"{len(out)} trains scored; {len(pending)} in flight awaiting lab; "
      f"{int((out.lab_status == 'QAQC_REJECTED').sum())} with lab rejected by QA/QC")
pending[["train_id", "hub_code", "product_code", "depart_ts", "bm_fe", "xba_fe", "pred_fe", "pred_fe_band",
         "fe_typ", "predicted_status"]].round(3)

# %% [markdown]
# ### 2 · Recommend reclaim blends for today's planned trains (LP on lab-reconciled grades)

# %%
sp_pd = spark.table(f"{FQ}.stockpile_grade_current").toPandas()
plan = spark.table(f"{FQ}.silver_train_plan").toPandas()
specs = {k: Spec(r.fe_typ, r.fe_min, r.sio2_max, r.al2o3_max, r.p_max) for k, r in specs_pd.iterrows()}
sps = {r.stockpile_id: Stockpile(r.stockpile_id, float(r.tonnes_on_hand),
                                 {e: float(getattr(r, f"recon_{e}")) for e in ELEMENTS},
                                 {e: float(getattr(r, f"bm_{e}")) for e in ELEMENTS}) for r in sp_pd.itertuples()}
cands = sp_pd.groupby(["hub_code", "product_code"]).stockpile_id.apply(list).to_dict()
fe_margin = float(model.predict(tf.head(1))["pred_fe_band"].iloc[0]) * 0.75
results = plan_shift(plan.to_dict("records"), sps, specs, cands, fe_margin=round(fe_margin, 3))

recs, lines = [], []
for t, r in results:
    rid = f"REC-{t['planned_train_id']}"
    recs.append(dict(recommendation_id=rid, planned_train_id=t["planned_train_id"], hub_code=t["hub_code"],
                     product_code=t["product_code"], planned_load_start=pd.Timestamp(t["planned_load_start"]),
                     target_wet_t=float(t["target_wet_t"]), status=r.status, fe_margin=r.fe_margin,
                     **{f"pred_{e}": r.predicted.get(e) for e in ELEMENTS},
                     bm_view_fe=r.block_model_view.get("fe"), fe_typ=specs[t["product_code"]].fe_typ,
                     blend_json=pd.Series(r.tonnes).to_json(), optimiser_message=r.message,
                     model_version=str(mv.version), created_at=AS_OF))
    for sid, tn in r.tonnes.items():
        lines.append(dict(line_id=f"{rid}-{sid}", recommendation_id=rid, planned_train_id=t["planned_train_id"],
                          stockpile_id=sid, wet_tonnes=tn, share=tn / float(t["target_wet_t"])))
recs_df, lines_df = pd.DataFrame(recs), pd.DataFrame(lines)
recs_df[["planned_train_id", "hub_code", "product_code", "planned_load_start", "status", "pred_fe",
         "bm_view_fe", "fe_typ", "blend_json"]]

# %% [markdown]
# ### 3 · GenAI rationale for the controller (`ai_query`, Claude Sonnet 4.5 on Databricks)

# %%
spark.createDataFrame(recs_df).createOrReplaceTempView("recs_tmp")
explained = spark.sql(f"""
SELECT r.*, ai_query('databricks-claude-sonnet-4-5',
  concat('You are a mine-to-port grade control assistant at Fortescue. In 2 short sentences (max 45 words), ',
         'tell the train load-out controller what to reclaim and why. Plain language, no preamble, no markdown. ',
         'Hub: ', hub_code, '. Product: ', product_code, ' (typical Fe ', fe_typ, '%). ',
         'Recommended reclaim tonnes by stockpile: ', blend_json, '. ',
         'Predicted lab Fe using lab-reconciled grades: ', round(pred_fe, 2), '%. ',
         'What the raw block model would show for the same blend: ', round(bm_view_fe, 2), '%. ',
         'Optimiser status: ', status, ' - ', optimiser_message, '.'),
  modelParameters => named_struct('max_tokens', 120, 'temperature', 0.2)) AS rationale
FROM recs_tmp r
""")
explained.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{FQ}.blend_recommendations")
spark.createDataFrame(lines_df).write.mode("overwrite").option("overwriteSchema", "true") \
    .saveAsTable(f"{FQ}.blend_recommendation_lines")
for t in ["blend_recommendations", "blend_recommendation_lines"]:
    spark.sql(f"ALTER TABLE {FQ}.{t} SET TBLPROPERTIES (delta.enableChangeDataFeed = true)")
for r in spark.table(f"{FQ}.blend_recommendations").orderBy("planned_load_start").limit(4).collect():
    print(f"{r.planned_train_id} {r.hub_code}/{r.product_code} pred Fe {r.pred_fe:.2f} (block model says {r.bm_view_fe:.2f})\n  -> {r.rationale}\n")

# %% [markdown]
# ### 4 · Value backtest (last 30 days, simulated)
# Today the controller targets typical Fe + 0.30 using **raw block model** grades, so each train lands
# short by its hub's block-model bias. Targeting on **lab-reconciled** grade shifts each train's Fe by
# the bias that was *known when it departed* (`hub_bm_bias_fe`, no future information).
# Compliance is re-scored per train, then trains are re-blended into the **same vessels** and the
# illustrative penalty schedule is re-applied at vessel level (penalties are charged per cargo).
# The cost side is also shown: correcting the shortfall uses more high-grade ore (Fe "giveaway").

# %%
tp = spark.table(f"{FQ}.train_performance").toPandas()
vt = spark.table(f"{FQ}.silver_vessel_trains").select("vessel_id", "product_code", "load_complete_ts", "train_id").toPandas()
bt = tp[tp.lab_status == "RETURNED"].merge(tf[["train_id", "hub_bm_bias_fe"]], on="train_id")
bt["fe_today"] = bt.lab_fe
bt["fe_reconciled"] = bt.lab_fe - bt.hub_bm_bias_fe.fillna(0).clip(upper=0)  # correct block-model over-statement only
win = vt[pd.to_datetime(vt.load_complete_ts) >= AS_OF - pd.Timedelta(days=30)]
v = win.merge(bt[["train_id", "dry_tonnes", "fe_today", "fe_reconciled", "fe_typ", "fe_min"]], on="train_id")
rows = []
for label, col in [("today: block-model targeting", "fe_today"), ("with reconciled-grade targeting", "fe_reconciled")]:
    tr = bt[bt.train_id.isin(win.train_id)]
    g = v.assign(fe_t=v[col] * v.dry_tonnes).groupby("vessel_id").agg(
        dry=("dry_tonnes", "sum"), fe_t=("fe_t", "sum"), typ=("fe_typ", "first"), fmin=("fe_min", "first"))
    g["fe"] = g.fe_t / g.dry
    pen_dmt = np.clip(g.typ - g.fe, 0, None) * 1.80 + np.clip(g.fmin - g.fe, 0, None) * 4.50
    rows.append(dict(scenario=label, trains=len(tr), vessels=len(g),
                     pct_trains_below_typical=round(100 * (tr[col] < tr.fe_typ).mean(), 1),
                     pct_solomon_trains_below_typical=round(100 * (tr[col] < tr.fe_typ)[tr.hub_code == "SOL"].mean(), 1),
                     vessels_below_typical=int((g.fe < g.typ).sum()),
                     vessel_penalty_usd_30d=round(float((pen_dmt * g.dry).sum()), 0),
                     avg_train_fe=round(float((tr[col] * tr.dry_tonnes).sum() / tr.dry_tonnes.sum()), 3)))
vb = pd.DataFrame(rows)
vb["annualised_penalty_usd"] = (vb.vessel_penalty_usd_30d * 365 / 30).round(0)
spark.createDataFrame(vb).write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{FQ}.value_backtest")
vb.T
