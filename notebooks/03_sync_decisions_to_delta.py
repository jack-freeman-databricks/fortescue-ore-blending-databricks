# %% [markdown]
# # 03 · Close the loop: Lakebase decisions → Delta (audit + retraining)
# Controllers approve, override or reject blends in the app. Those writes land in Lakebase
# (`ops.blend_decisions`, `ops.stockpile_availability`) at OLTP latency. This step lands them in
# Unity Catalog Delta tables. That gives governed audit history, lets Genie answer questions
# about decisions, and turns override reasons into labels for the next model iteration.
#
# It also re-triggers the Lakebase **synced tables** (Delta → Postgres) after notebook 02
# refreshes predictions and recommendations.

# %%
import os, warnings
os.environ["GRPC_VERBOSITY"] = "ERROR"; os.environ["GLOG_minloglevel"] = "2"
warnings.filterwarnings("ignore")
os.environ.setdefault("DATABRICKS_CONFIG_PROFILE", "fevm-serverless-stable-1s8h43")
try:
    spark  # noqa: F821
except NameError:
    from databricks.connect import DatabricksSession
    spark = DatabricksSession.builder.serverless().getOrCreate()

import pandas as pd
import psycopg
from databricks.sdk import WorkspaceClient

w = WorkspaceClient()
FQ = "jack_freeman_catalog.fmg_ore_blending"
EP = "projects/fmg-ore-blending/branches/production/endpoints/primary"
host = w.postgres.get_endpoint(name=EP).status.hosts.host
me = w.current_user.me().user_name
token = w.postgres.generate_database_credential(endpoint=EP).token
print("Lakebase host:", host, "| as:", me)

# %%
with psycopg.connect(host=host, dbname="databricks_postgres", user=me, password=token, sslmode="require") as c:
    dec = pd.read_sql("SELECT decision_id, recommendation_id, planned_train_id, hub_code, action, "
                      "final_blend::text AS final_blend_json, predicted_fe, reason, decided_by, decided_at "
                      "FROM ops.blend_decisions ORDER BY decision_id", c)
    av = pd.read_sql("SELECT stockpile_id, available, reason, updated_by, updated_at FROM ops.stockpile_availability", c)
print(f"{len(dec)} decisions, {len(av)} stockpile availability rows in Lakebase")
dec

# %%
if len(dec):
    dec["decided_at"] = pd.to_datetime(dec.decided_at, utc=True).dt.tz_localize(None)
    (spark.createDataFrame(dec).createOrReplaceTempView("dec_src"))
    spark.sql(f"""CREATE TABLE IF NOT EXISTS {FQ}.blend_decisions (
        decision_id BIGINT, recommendation_id STRING, planned_train_id STRING, hub_code STRING, action STRING,
        final_blend_json STRING, predicted_fe DOUBLE, reason STRING, decided_by STRING, decided_at TIMESTAMP)
        COMMENT 'Controller blend decisions captured in the app (Lakebase ops.blend_decisions), landed for audit and retraining'""")
    spark.sql(f"""MERGE INTO {FQ}.blend_decisions t USING dec_src s ON t.decision_id = s.decision_id
                  WHEN NOT MATCHED THEN INSERT *""")
if len(av):
    av["updated_at"] = pd.to_datetime(av.updated_at, utc=True).dt.tz_localize(None)
    spark.createDataFrame(av).write.mode("overwrite").option("overwriteSchema", "true") \
        .saveAsTable(f"{FQ}.stockpile_availability")
spark.sql(f"SELECT action, count(*) n, collect_set(decided_by) by_whom FROM {FQ}.blend_decisions GROUP BY action").toPandas() \
    if spark.catalog.tableExists(f"{FQ}.blend_decisions") else "no decisions yet"

# %% [markdown]
# ### Refresh the Lakebase synced tables (Delta → Postgres snapshot)

# %%
for t in ["lb_stockpile_grade_current", "lb_train_grade_predictions", "lb_blend_recommendations",
          "lb_blend_recommendation_lines"]:
    st = w.postgres.get_synced_table(name=f"synced_tables/{FQ}.{t}")
    pid = st.status.pipeline_id
    upd = w.pipelines.start_update(pipeline_id=pid)
    print(f"{t}: state={st.status.detailed_state} -> refresh update {upd.update_id}")
