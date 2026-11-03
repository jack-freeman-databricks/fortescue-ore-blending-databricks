# %% [markdown]
# # 01 · Train-grade predictor (ML)
# **Problem:** a train's lab assay comes back 24-48 h after it departs the train load-out (TLO).
# By then the ore is at Herb Elliott Port and often already on a vessel. Controllers only have
# two in-the-moment signals, and both are biased:
# * the **block model** grade of the stockpiles they reclaimed (Solomon over-estimates Fe by ~0.45%)
# * the **cross-belt analyser** (PGNAA), which drifts between calibrations
#
# **Model:** gradient-boosted regressor that predicts the *lab* Fe / SiO2 / Al2O3 of a train **at departure**.
# It uses analyser statistics, the reclaim-weighted block-model grade, days since analyser calibration,
# and each hub's recent lab-vs-sensor bias (only from lab results that had returned *before* the train
# departed, so there is no leakage).
#
# Source: `jack_freeman_catalog.fmg_ore_blending.train_features` (Lakeflow gold). Tracked in MLflow,
# registered to Unity Catalog as `train_grade_predictor` with alias `@champion`.

# %%
import os, warnings
os.environ["GRPC_VERBOSITY"] = "ERROR"
os.environ["GLOG_minloglevel"] = "2"
os.environ["MLFLOW_DISABLE_AGENT_HINT"] = "1"
warnings.filterwarnings("ignore")
os.environ.setdefault("DATABRICKS_CONFIG_PROFILE", "fevm-serverless-stable-1s8h43")
try:
    spark  # noqa: F821  (defined on Databricks)
except NameError:
    from databricks.connect import DatabricksSession
    spark = DatabricksSession.builder.serverless().getOrCreate()

import mlflow
import numpy as np
import pandas as pd
from mlflow.models import infer_signature
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error

CATALOG, SCHEMA = "jack_freeman_catalog", "fmg_ore_blending"
MODEL_NAME = f"{CATALOG}.{SCHEMA}.train_grade_predictor"
mlflow.set_tracking_uri("databricks")
mlflow.set_registry_uri("databricks-uc")
mlflow.set_experiment("/Users/jack.freeman@databricks.com/fmg-ore-blending-train-grade")

df = spark.table(f"{CATALOG}.{SCHEMA}.train_features").toPandas()
df["depart_ts"] = pd.to_datetime(df["depart_ts"])
print(df.shape, "| lab status:", df.lab_status.value_counts().to_dict())
df[["train_id", "hub_code", "product_code", "depart_ts", "bm_fe", "xba_fe", "lab_fe",
    "hub_bm_bias_fe", "hub_xba_bias_fe", "days_since_cal"]].tail(5)

# %%
TARGETS = ["fe", "sio2", "al2o3"]
HUBS = ["CB", "CC", "ELI", "SOL"]
NUM = ([f"xba_{e}" for e in ["fe", "sio2", "al2o3", "p", "loi"]] + ["xba_fe_std", "xba_n"]
       + [f"bm_{e}" for e in ["fe", "sio2", "al2o3", "p", "loi"]]
       + [f"hub_xba_bias_{e}" for e in TARGETS] + [f"hub_bm_bias_{e}" for e in TARGETS]
       + ["hub_bias_n", "days_since_cal", "n_stockpiles", "max_stockpile_share", "moisture_pct", "hour_of_day"])


def featurise(d: pd.DataFrame) -> pd.DataFrame:
    X = d[NUM].astype(float).copy()
    for h in HUBS:
        X[f"hub_{h}"] = (d["hub_code"] == h).astype(float)
    X["is_kf"] = (d["product_code"] == "KF").astype(float)
    # physically-motivated features: analyser corrected by its recent bias
    for e in TARGETS:
        X[f"xba_corr_{e}"] = X[f"xba_{e}"] + X[f"hub_xba_bias_{e}"].fillna(0)
        X[f"bm_corr_{e}"] = X[f"bm_{e}"] + X[f"hub_bm_bias_{e}"].fillna(0)
    return X


lab = df[df.has_lab].sort_values("depart_ts")
cut = lab.depart_ts.max() - pd.Timedelta(days=21)          # time-based split: last 3 weeks = test
train, test = lab[lab.depart_ts <= cut], lab[lab.depart_ts > cut]
Xtr, Xte = featurise(train), featurise(test)
print(f"train {len(train)} trains (to {cut:%Y-%m-%d}) | test {len(test)} trains")

# %% [markdown]
# ### Baselines vs model (test set = last 21 days, never seen in training)

# %%
mlflow.sklearn.autolog(disable=True)
models, rows, q90 = {}, [], {}
with mlflow.start_run(run_name="hgb_train_grade_v1") as run:
    for e in TARGETS:
        m = HistGradientBoostingRegressor(max_iter=400, learning_rate=0.04, max_leaf_nodes=15,
                                          min_samples_leaf=20, l2_regularization=1.0, random_state=7)
        m.fit(Xtr, train[f"lab_{e}"])
        models[e] = m
        pred = m.predict(Xte)
        resid = np.abs(test[f"lab_{e}"].values - pred)
        r = dict(element=e,
                 mae_block_model=mean_absolute_error(test[f"lab_{e}"], test[f"bm_{e}"]),
                 mae_analyser_raw=mean_absolute_error(test[f"lab_{e}"], test[f"xba_{e}"]),
                 mae_analyser_bias_corrected=mean_absolute_error(test[f"lab_{e}"], Xte[f"xba_corr_{e}"]),
                 mae_model=float(resid.mean()), p90_abs_err_model=float(np.quantile(resid, 0.9)))
        q90[e] = float(np.quantile(resid, 0.9))  # honest band: p90 abs error on held-out weeks
        rows.append(r)
        for k, v in r.items():
            if k != "element":
                mlflow.log_metric(f"{e}_{k}", v)
    res = pd.DataFrame(rows).set_index("element").round(3)
    mlflow.log_params(dict(model="HistGradientBoostingRegressor", max_iter=400, learning_rate=0.04,
                           split="time-based, last 21 days test", n_train=len(train), n_test=len(test),
                           n_features=Xtr.shape[1]))
    mlflow.log_table(res.reset_index(), "test_metrics.json")
    run_id = run.info.run_id
print("MLflow run:", run_id)
res

# %% [markdown]
# ### Off-spec detection at departure (vs waiting 24-48 h for the lab)
# A train is flagged off-spec if the predicted lab Fe is below the product's typical Fe.

# %%
typ = test.product_code.map({"FB": 58.0, "KF": 56.0})
actual_low = test.lab_fe < typ
for name, p in [("block model (today)", test.bm_fe.values), ("analyser raw", test.xba_fe.values),
                ("model", models["fe"].predict(Xte))]:
    flag = p < typ
    tp = int((flag & actual_low).sum()); fp = int((flag & ~actual_low).sum()); fn = int((~flag & actual_low).sum())
    print(f"{name:22s} recall={tp / max(1, tp + fn):.0%} precision={tp / max(1, tp + fp):.0%}  "
          f"(caught {tp} of {tp + fn} below-typical trains, {fp} false alarms)")
with mlflow.start_run(run_id=run_id):
    flag = models["fe"].predict(Xte) < typ
    mlflow.log_metric("offspec_recall_model", float((flag & actual_low).sum() / max(1, actual_low.sum())))
    mlflow.log_metric("offspec_recall_block_model", float(((test.bm_fe < typ) & actual_low).sum() / max(1, actual_low.sum())))

# %%
by_hub = test.assign(pred_fe=models["fe"].predict(Xte)).groupby("hub_code").apply(
    lambda g: pd.Series(dict(trains=len(g), mae_block_model=(g.bm_fe - g.lab_fe).abs().mean(),
                             mae_model=(g.pred_fe - g.lab_fe).abs().mean(),
                             bias_block_model=(g.bm_fe - g.lab_fe).mean(), bias_model=(g.pred_fe - g.lab_fe).mean())))
by_hub.round(3)

# %% [markdown]
# ### Package all three element models as one pyfunc and register to Unity Catalog

# %%
class TrainGradePredictor(mlflow.pyfunc.PythonModel):
    """Predicts lab Fe/SiO2/Al2O3 of a train at departure, with a 90% error band."""

    def __init__(self, models, q90, hubs):
        self.models, self.q90, self.hubs = models, q90, hubs

    def _featurise(self, d):
        X = d[NUM].astype(float).copy()
        for h in self.hubs:
            X[f"hub_{h}"] = (d["hub_code"] == h).astype(float)
        X["is_kf"] = (d["product_code"] == "KF").astype(float)
        for e in TARGETS:
            X[f"xba_corr_{e}"] = X[f"xba_{e}"] + X[f"hub_xba_bias_{e}"].fillna(0)
            X[f"bm_corr_{e}"] = X[f"bm_{e}"] + X[f"hub_bm_bias_{e}"].fillna(0)
        return X

    def predict(self, context, model_input, params=None):
        X = self._featurise(model_input)
        out = pd.DataFrame(index=model_input.index)
        for e, m in self.models.items():
            out[f"pred_{e}"] = m.predict(X)
            out[f"pred_{e}_band"] = self.q90[e]
        return out


inp = test[["hub_code", "product_code"] + NUM].head(20)
pm = TrainGradePredictor(models, q90, HUBS)
sig = infer_signature(inp, pm.predict(None, inp))
with mlflow.start_run(run_id=run_id):
    info = mlflow.pyfunc.log_model(name="model", python_model=pm, signature=sig, input_example=inp.head(3),
                                   registered_model_name=MODEL_NAME,
                                   pip_requirements=["scikit-learn==1.9.1", "pandas", "numpy"])
from mlflow import MlflowClient
c = MlflowClient()
ver = info.registered_model_version
c.set_registered_model_alias(MODEL_NAME, "champion", ver)
c.update_registered_model(MODEL_NAME, description=(
    "Predicts a train's lab assay (Fe, SiO2, Al2O3) at TLO departure from analyser stats, "
    "reclaim-weighted block model and hub bias known at departure. Trained on Lakeflow gold "
    f"train_features. Test MAE Fe: model {res.loc['fe','mae_model']} vs block model {res.loc['fe','mae_block_model']}."))
print(f"Registered {MODEL_NAME} version {ver} -> @champion")
print("q90 error band:", {k: round(v, 3) for k, v in q90.items()})
