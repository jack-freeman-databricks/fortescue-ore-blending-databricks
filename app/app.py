"""Fortescue Blend Control Room: Databricks App (FastAPI + static UI).

Reads operational state from LAKEBASE (Postgres):
  * synced tables (UC Delta -> Lakebase): stockpile grade, train predictions, blend recommendations
  * native app tables (schema `ops`, owned by the app SP): controller decisions + stockpile availability
Re-plans a train on demand with the same LP optimiser the scoring job uses.
Answers natural-language questions through the Genie Conversation API.
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime

import psycopg
from databricks.sdk import WorkspaceClient
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel

from fmg_blend.optimiser import ELEMENTS, Spec, Stockpile, optimise_train

log = logging.getLogger("blend")
logging.basicConfig(level=logging.INFO)

w = WorkspaceClient()
ENDPOINT = os.environ.get("LAKEBASE_ENDPOINT", "projects/fmg-ore-blending/branches/production/endpoints/primary")
SYNC_SCHEMA = os.environ.get("SYNC_SCHEMA", "fmg_ore_blending")
GENIE_SPACE_ID = os.environ.get("GENIE_SPACE_ID", "")
HOST = os.environ.get("PGHOST") or w.postgres.get_endpoint(name=ENDPOINT).status.hosts.host
DATABASE = os.environ.get("PGDATABASE", "databricks_postgres")
USER = os.environ.get("PGUSER") or os.environ.get("DATABRICKS_CLIENT_ID") or w.current_user.me().user_name
SPECS = {"FB": Spec(58.0, 57.5, 6.0, 2.8, 0.075), "KF": Spec(56.0, 55.5, 7.2, 3.6, 0.080)}


class OAuthConnection(psycopg.Connection):
    @classmethod
    def connect(cls, conninfo="", **kwargs):
        kwargs["password"] = w.postgres.generate_database_credential(endpoint=ENDPOINT).token
        return super().connect(conninfo, **kwargs)


pool = ConnectionPool(
    conninfo=f"dbname={DATABASE} user={USER} host={HOST} port=5432 sslmode=require connect_timeout=10",
    connection_class=OAuthConnection, min_size=1, max_size=8, max_lifetime=2700, open=False,
    kwargs={"row_factory": dict_row, "autocommit": True})

DDL = """
CREATE SCHEMA IF NOT EXISTS ops;
CREATE TABLE IF NOT EXISTS ops.blend_decisions (
  decision_id      BIGSERIAL PRIMARY KEY,
  recommendation_id TEXT NOT NULL,
  planned_train_id TEXT NOT NULL,
  hub_code         TEXT NOT NULL,
  action           TEXT NOT NULL CHECK (action IN ('APPROVED','OVERRIDDEN','REJECTED')),
  final_blend      JSONB NOT NULL,
  predicted_fe     DOUBLE PRECISION,
  reason           TEXT,
  decided_by       TEXT NOT NULL,
  decided_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_decisions_rec ON ops.blend_decisions (recommendation_id, decided_at DESC);
CREATE TABLE IF NOT EXISTS ops.stockpile_availability (
  stockpile_id TEXT PRIMARY KEY,
  available    BOOLEAN NOT NULL,
  reason       TEXT,
  updated_by   TEXT NOT NULL,
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


@asynccontextmanager
async def lifespan(_):
    pool.open(wait=False)  # don't block startup while a scaled-to-zero endpoint wakes
    for attempt in range(6):
        try:
            with pool.connection(timeout=45) as c:
                c.execute(DDL)
            log.info("Lakebase ops schema ready")
            break
        except Exception as e:  # noqa: BLE001
            log.warning("DDL attempt %s failed: %s", attempt, e)
            time.sleep(5)
    yield
    pool.close()


app = FastAPI(title="Fortescue Blend Control Room", lifespan=lifespan)


_qt: contextvars.ContextVar[dict] = contextvars.ContextVar("lakebase_ms", default={"ms": 0.0})


def q(sql: str, params=None) -> list[dict]:
    t0 = time.perf_counter()
    with pool.connection(timeout=45) as c:
        rows = c.execute(sql, params or ()).fetchall()
    _qt.get()["ms"] += (time.perf_counter() - t0) * 1000
    return rows


@app.middleware("http")
async def lakebase_timing(request: Request, call_next):
    acc = {"ms": 0.0}
    _qt.set(acc)  # same dict object is visible to the threadpool worker (context is copied)
    resp = await call_next(request)
    resp.headers["X-Lakebase-ms"] = f"{acc['ms']:.1f}"
    return resp


def who(req: Request) -> str:
    return req.headers.get("x-forwarded-email") or req.headers.get("x-forwarded-preferred-username") or "local-dev"


def clean(rows):
    out = []
    for r in rows:
        out.append({k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in r.items()})
    return out


# ------------------------------------------------------------------------------------ reads
@app.get("/api/health")
def health():
    return {"ok": True, "lakebase": q("SELECT now() AS ts")[0]["ts"].isoformat(), "user": USER}


@app.get("/api/summary")
def summary():
    s = SYNC_SCHEMA
    k = q(f"""
      SELECT
        (SELECT count(*) FROM {s}.lb_train_grade_predictions WHERE in_flight) AS inflight_trains,
        (SELECT count(*) FROM {s}.lb_train_grade_predictions WHERE in_flight AND predicted_status <> 'ON_SPEC') AS inflight_at_risk,
        (SELECT count(*) FROM {s}.lb_blend_recommendations) AS planned_trains,
        (SELECT count(*) FROM {s}.lb_blend_recommendations r WHERE EXISTS
            (SELECT 1 FROM ops.blend_decisions d WHERE d.recommendation_id = r.recommendation_id)) AS decided,
        (SELECT round(avg(hub_bm_bias_fe)::numeric, 3) FROM {s}.lb_stockpile_grade_current WHERE hub_code='SOL') AS sol_bias,
        (SELECT max(scored_at) FROM {s}.lb_train_grade_predictions) AS as_of
    """)[0]
    return clean([k])[0]


@app.get("/api/stockpiles")
def stockpiles():
    return clean(q(f"""
      SELECT s.stockpile_id, s.hub_code, s.grade_class, s.product_code, s.tonnes_on_hand,
             s.bm_fe, s.recon_fe, s.recon_sio2, s.recon_al2o3, s.hub_bm_bias_fe,
             COALESCE(a.available, true) AS available, a.reason AS availability_reason, a.updated_by
      FROM {SYNC_SCHEMA}.lb_stockpile_grade_current s
      LEFT JOIN ops.stockpile_availability a USING (stockpile_id)
      ORDER BY s.stockpile_id"""))


@app.get("/api/trains/inflight")
def inflight():
    return clean(q(f"""
      SELECT train_id, hub_code, product_code, depart_ts, wet_tonnes, bm_fe, xba_fe,
             pred_fe, pred_fe_band, pred_sio2, pred_al2o3, fe_typ, predicted_status, hours_until_lab
      FROM {SYNC_SCHEMA}.lb_train_grade_predictions WHERE in_flight ORDER BY depart_ts DESC"""))


@app.get("/api/recommendations")
def recommendations():
    recs = q(f"""
      SELECT r.recommendation_id, r.planned_train_id, r.hub_code, r.product_code, r.planned_load_start,
             r.target_wet_t, r.status, r.pred_fe, r.pred_sio2, r.pred_al2o3, r.bm_view_fe, r.fe_typ,
             r.blend_json, r.rationale, r.optimiser_message,
             d.action AS decision, d.decided_by, d.decided_at, d.final_blend, d.reason AS decision_reason,
             d.predicted_fe AS decided_fe
      FROM {SYNC_SCHEMA}.lb_blend_recommendations r
      LEFT JOIN LATERAL (SELECT * FROM ops.blend_decisions x WHERE x.recommendation_id = r.recommendation_id
                         ORDER BY decided_at DESC LIMIT 1) d ON true
      ORDER BY r.planned_load_start""")
    for r in recs:
        r["blend"] = json.loads(r.pop("blend_json") or "{}")
    return clean(recs)


@app.get("/api/decisions")
def decisions():
    return clean(q("SELECT * FROM ops.blend_decisions ORDER BY decided_at DESC LIMIT 50"))


# ----------------------------------------------------------------------------------- writes
class Availability(BaseModel):
    available: bool
    reason: str | None = None


@app.post("/api/stockpiles/{stockpile_id}/availability")
def set_availability(stockpile_id: str, body: Availability, req: Request):
    q("""INSERT INTO ops.stockpile_availability (stockpile_id, available, reason, updated_by)
         VALUES (%s,%s,%s,%s) ON CONFLICT (stockpile_id) DO UPDATE
         SET available=EXCLUDED.available, reason=EXCLUDED.reason, updated_by=EXCLUDED.updated_by, updated_at=now()
         RETURNING stockpile_id""", (stockpile_id, body.available, body.reason, who(req)))
    return {"ok": True}


def _stockpiles_for(hub: str, product: str) -> list[Stockpile]:
    rows = q(f"""SELECT s.*, COALESCE(a.available, true) AS available
                 FROM {SYNC_SCHEMA}.lb_stockpile_grade_current s
                 LEFT JOIN ops.stockpile_availability a USING (stockpile_id)
                 WHERE s.hub_code=%s AND s.product_code=%s""", (hub, product))
    return [Stockpile(r["stockpile_id"], float(r["tonnes_on_hand"]),
                      {e: float(r[f"recon_{e}"]) for e in ELEMENTS}, {e: float(r[f"bm_{e}"]) for e in ELEMENTS},
                      bool(r["available"])) for r in rows]


@app.post("/api/recommendations/{rec_id}/replan")
def replan(rec_id: str):
    r = q(f"SELECT * FROM {SYNC_SCHEMA}.lb_blend_recommendations WHERE recommendation_id=%s", (rec_id,))
    if not r:
        raise HTTPException(404, "recommendation not found")
    r = r[0]
    res = optimise_train(_stockpiles_for(r["hub_code"], r["product_code"]), SPECS[r["product_code"]],
                         float(r["target_wet_t"]), float(r["fe_margin"] or 0.15))
    return {"status": res.status, "blend": res.tonnes, "predicted": res.predicted,
            "block_model_view": res.block_model_view, "message": res.message}


class Decision(BaseModel):
    action: str  # APPROVED | OVERRIDDEN | REJECTED
    blend: dict | None = None
    predicted_fe: float | None = None
    reason: str | None = None


@app.post("/api/recommendations/{rec_id}/decision")
def decide(rec_id: str, body: Decision, req: Request):
    r = q(f"SELECT * FROM {SYNC_SCHEMA}.lb_blend_recommendations WHERE recommendation_id=%s", (rec_id,))
    if not r:
        raise HTTPException(404, "recommendation not found")
    r = r[0]
    if body.action not in ("APPROVED", "OVERRIDDEN", "REJECTED"):
        raise HTTPException(400, "bad action")
    if body.action != "APPROVED" and not (body.reason or "").strip():
        raise HTTPException(400, "a reason is required for overrides and rejections")
    blend = body.blend or json.loads(r["blend_json"])
    row = q("""INSERT INTO ops.blend_decisions (recommendation_id, planned_train_id, hub_code, action, final_blend,
                                                predicted_fe, reason, decided_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING decision_id, decided_at""",
            (rec_id, r["planned_train_id"], r["hub_code"], body.action, json.dumps(blend),
             body.predicted_fe if body.predicted_fe is not None else r["pred_fe"], body.reason, who(req)))[0]
    return clean([row])[0]


# ------------------------------------------------------------------------------------ Genie
class Ask(BaseModel):
    question: str
    conversation_id: str | None = None


def user_client(req: Request) -> WorkspaceClient:
    """On-behalf-of-user client: Genie runs as the signed-in user, so UC row filters / masks apply to them."""
    tok = req.headers.get("x-forwarded-access-token")
    if not tok:
        return w
    return WorkspaceClient(host=w.config.host, token=tok, auth_type="pat")


@app.post("/api/genie")
def genie(body: Ask, req: Request):
    if not GENIE_SPACE_ID:
        raise HTTPException(503, "GENIE_SPACE_ID not configured")
    g = user_client(req).genie
    if body.conversation_id:
        msg = g.create_message_and_wait(GENIE_SPACE_ID, body.conversation_id, body.question)
    else:
        msg = g.start_conversation_and_wait(GENIE_SPACE_ID, body.question)
    out = {"conversation_id": msg.conversation_id, "text": None, "interpretation": None, "sql": None,
           "columns": [], "rows": []}
    for a in msg.attachments or []:
        if a.text and a.text.content:
            out["text"] = a.text.content            # Genie's answer
        if a.query:
            out["sql"] = a.query.query
            out["interpretation"] = a.query.description  # Genie's restatement of the question
            res = g.get_message_attachment_query_result(GENIE_SPACE_ID, msg.conversation_id, msg.id,
                                                              a.attachment_id)
            sr = res.statement_response
            if sr and sr.manifest and sr.result:
                out["columns"] = [c.name for c in sr.manifest.schema.columns]
                out["rows"] = (sr.result.data_array or [])[:50]
    out["text"] = out["text"] or out["interpretation"]
    return out


# ----------------------------------------------------------------------------------- static
HERE = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "static", "index.html"))
