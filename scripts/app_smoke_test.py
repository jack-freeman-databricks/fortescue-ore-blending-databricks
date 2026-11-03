"""Exercise the DEPLOYED Blend Control Room app end to end and log every call (execution evidence).

Reads from Lakebase synced tables, writes controller actions into Lakebase (ops schema),
re-plans a train with the optimiser after a stockpile outage, and asks Genie a question.
Usage: python scripts/app_smoke_test.py > evidence/09_app_api_calls.log
"""
import json
import sys
import time

import requests
from databricks.sdk import WorkspaceClient

PROFILE = "fevm-serverless-stable-1s8h43"
APP = "fortescue-maintenance-runner"
w = WorkspaceClient(profile=PROFILE)
url = w.apps.get(APP).url
H = {"Authorization": f"Bearer {w.config.authenticate()['Authorization'].split(' ', 1)[1]}"}


def call(method, path, body=None, show=1200):
    t0 = time.time()
    r = requests.request(method, url + path, headers=H, json=body, timeout=180)
    ms = (time.time() - t0) * 1000
    print(f"\n### {method} {path}  ->  HTTP {r.status_code}  (round trip {ms:.0f} ms, "
          f"Lakebase time in app {r.headers.get('X-Lakebase-ms', '-')} ms)")
    if body is not None:
        print("request:", json.dumps(body))
    try:
        data = r.json()
        txt = json.dumps(data, indent=1, default=str)
    except Exception:  # noqa: BLE001
        data, txt = None, r.text
    print(txt[:show] + (" …" if len(txt) > show else ""))
    if r.status_code >= 400:
        sys.exit(f"FAILED {path}")
    return data


print(f"App: {APP}  URL: {url}")
call("GET", "/api/health")
call("GET", "/api/summary")
infl = call("GET", "/api/trains/inflight", show=900)
print(f"-> {len(infl)} in-flight trains returned from Lakebase")
recs = call("GET", "/api/recommendations", show=1500)
sol = next(r for r in recs if r["hub_code"] == "SOL" and r["product_code"] == "FB" and not r["decision"])
cb = next(r for r in recs if r["hub_code"] == "CB" and not r["decision"])
kf = next(r for r in recs if r["product_code"] == "KF" and not r["decision"])

# 1. approve the Cloudbreak recommendation as-is
call("POST", f"/api/recommendations/{cb['recommendation_id']}/decision", {"action": "APPROVED"})

# 2. reclaimer outage on SOL-ROM-01 -> re-plan the Solomon FB train -> controller overrides with re-plan
call("POST", "/api/stockpiles/SOL-ROM-01/availability", {"available": False, "reason": "Reclaimer RC2 down for belt repair"})
rp = call("POST", f"/api/recommendations/{sol['recommendation_id']}/replan", {})
print(f"-> original blend {sol['blend']} pred Fe {sol['pred_fe']:.2f}; re-plan {rp['blend']} pred Fe {rp['predicted'].get('fe')} ({rp['status']})")
call("POST", f"/api/recommendations/{sol['recommendation_id']}/decision",
     {"action": "OVERRIDDEN", "blend": rp["blend"], "predicted_fe": rp["predicted"].get("fe"),
      "reason": "SOL-ROM-01 unavailable (reclaimer RC2), re-planned without it"})

# 3. rejecting without a reason is refused (validation)
r = requests.post(url + f"/api/recommendations/{kf['recommendation_id']}/decision", headers=H, json={"action": "REJECTED"})
print(f"\n### POST reject without reason -> HTTP {r.status_code} {r.text}  (expected 400)")

call("GET", "/api/decisions")
s = call("GET", "/api/stockpiles", show=600)
print("-> SOL-ROM-01 now:", [x for x in s if x["stockpile_id"] == "SOL-ROM-01"])

# 4. Genie Agent through the app (on-behalf-of the calling user)
g = call("POST", "/api/genie", {"question": "Which hub had the highest share of trains below typical Fe in October?"}, show=2500)
g2 = call("POST", "/api/genie", {"question": "And what was its average block model Fe error?", "conversation_id": g["conversation_id"]}, show=2500)

# put the stockpile back so the demo starts clean
call("POST", "/api/stockpiles/SOL-ROM-01/availability", {"available": True, "reason": "Returned to service"})
print("\nSMOKE TEST PASSED")
