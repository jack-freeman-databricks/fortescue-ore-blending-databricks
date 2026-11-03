"""Run a .sql file (or inline SQL) on a SQL warehouse; print each statement and its result.

Used for setup steps AND as execution evidence: output is tee'd into evidence/*.log.
Usage: python scripts/run_sql.py sql/file.sql   |   python scripts/run_sql.py -e "SELECT 1"
"""
import argparse, os, re, sys
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState

ap = argparse.ArgumentParser()
ap.add_argument("file", nargs="?")
ap.add_argument("-e", "--execute")
ap.add_argument("--profile", default=os.getenv("DATABRICKS_CONFIG_PROFILE", "fevm-serverless-stable-1s8h43"))
ap.add_argument("--warehouse", default=os.getenv("WAREHOUSE_ID", "495cddfca8ecaa75"))
ap.add_argument("--max-rows", type=int, default=40)
a = ap.parse_args()
sql = a.execute or open(a.file).read()
# strip line comments, split on ; at end of line
body = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))
stmts = [s.strip() for s in re.split(r";\s*\n", body + "\n") if s.strip()]
w = WorkspaceClient(profile=a.profile)
ctx = {"catalog": None, "schema": None}
for s in stmts:
    m = re.match(r"USE\s+CATALOG\s+(\S+)", s, re.I)
    if m: ctx["catalog"] = m.group(1); print(f"-- {s}"); continue
    m = re.match(r"USE\s+SCHEMA\s+(\S+)", s, re.I)
    if m: ctx["schema"] = m.group(1); print(f"-- {s}"); continue
    print(f"\n>>> {s}")
    r = w.statement_execution.execute_statement(statement=s, warehouse_id=a.warehouse, wait_timeout="50s",
                                                catalog=ctx["catalog"], schema=ctx["schema"])
    while r.status.state in (StatementState.PENDING, StatementState.RUNNING):
        r = w.statement_execution.get_statement(r.statement_id)
    if r.status.state != StatementState.SUCCEEDED:
        print(f"!!! {r.status.state}: {r.status.error.message if r.status.error else ''}")
        sys.exit(1)
    if r.manifest and r.manifest.schema and r.manifest.schema.columns:
        cols = [c.name for c in r.manifest.schema.columns]
        rows = (r.result.data_array or []) if r.result else []
        print(" | ".join(cols))
        for row in rows[: a.max_rows]:
            print(" | ".join("NULL" if v is None else str(v) for v in row))
        total = r.manifest.total_row_count
        if total and total > a.max_rows:
            print(f"... ({total} rows total)")
    print("OK")
