# How this was built: AI tools, workflow and where AI changed the outcome

This is an account of the **build process**, separate from the solution's design trade-offs (those are in the README).
Times are AWST on 3 Nov 2026 and come from the run logs in `evidence/`.

## Tools

| Tool | Used for |
|---|---|
| **Claude Code** (Claude Opus, terminal agent), session `0fba0ff7-49e5-4440-a0ab-d5536ac03bca` | The whole build: planning, data generator, pipeline, SQL, notebooks, optimiser, app, Lakebase setup, Genie space, smoke tests, deck |
| Claude Code **Databricks skills** | `databricks-pipelines` (SDP Python API, `row_filter` / `MASK` syntax), `databricks-lakebase` (synced tables, autoscaling app binding, Lakehouse Sync limits), `databricks-genie` (space format), `google-slides` then `databricks-impeccable` (deck) |
| Claude Code **persistent memory** | Lessons from my earlier FEVM builds: service-principal cap on new Apps, federated Postgres role for app SPs, non-blocking Lakebase pool start, `requirements.txt` for Apps, Genie create gotchas |
| **Databricks CLI / SDK / Databricks Connect**, driven by the agent | Deploying the bundle, running the pipeline, executing notebooks against serverless, querying Lakebase, calling the app and Genie APIs |
| **AI inside the solution** | `ai_query` with Claude on Databricks (blend rationale), Genie Agent (natural-language questions) |

## Workflow

1. **Plan with the agent first (~15 min).** I gave Claude Code the cert brief. It checked my existing Fortescue builds against the six required stages and proposed reusing one. I chose a fresh grade-blending build instead, and we agreed the stage plan, KPIs and personas before any code was written.
2. **Build one stage at a time, verify live, then move on.** Each stage ended with a real run against the workspace (pipeline update, SQL checks, model hold-out metrics, `psql` against Lakebase, calls to the deployed app, Genie questions). The agent read the output and decided whether the stage was done. Those outputs are what's committed in `evidence/` and the notebooks.
3. **Keep me on the decisions.** I chose the use case, which existing app slot to overwrite, naming and repo visibility. When the agent's tool permissions blocked a change to a shared Apps space, it stopped and proposed a safer alternative instead of forcing the change.
4. **Review every number.** I checked deck numbers against `evidence/RESULTS.md`, which maps each figure to the file it came from.

Timeline from the logs: pipeline first run 16:36 → model registered 16:59 → recommendations with `ai_query` 20:42 → app live on Lakebase 21:04 → full app smoke test 21:11 → scheduled job green on serverless 21:19 → deck 21:35. Roughly **5.5 hours from brief to a fully evidenced build**, against my own estimate of 8-9 hours.

## Where AI meaningfully changed speed or quality

**1. Catching unrealistic data before it reached the model (quality).** On the generator's first run, the agent ran a sanity query and saw Solomon Kings Fines trains at **99% below typical Fe**. That is too extreme to be credible, because the candidate stockpiles could never reach target. It retuned the biases and specs, re-ran, and confirmed a realistic pattern (one problem hub at ~75%, the others at 13-39%). Doing that by hand would have meant noticing it much later, in the model results.

**2. Finding a silent aggregation bug from the gold output (quality).** After the first pipeline run, vessel penalties came out at **US$66M for 90 days**. The agent traced it to vessels containing a train whose lab assay had been rejected by QA/QC: the tonnage still counted in the denominator, which dragged vessel Fe down. It fixed the weighting in one change (now US$1.3M, a plausible figure). In the same pass it fixed stockpiles showing zero inventory by adding opening balances.

**3. Governance syntax right on the first pipeline run (speed).** Row filters and column masks on pipeline-managed tables use syntax I don't write often. The pipelines skill gave the agent the exact `row_filter=` and `MASK` forms, and it caught a `.collect()` inside a dataset function (not allowed) before deploying. The 23-dataset pipeline, including the row filter and mask, **succeeded on its first run**.

**4. Making the model metrics honest (quality).** The first model run computed the prediction error band from training residuals (±0.117% Fe). The agent flagged that this understated real error and switched to the held-out p90 (±0.238%). That wider band feeds the optimiser's safety margin, so the recommendations are more conservative and more defensible.

**5. Not overstating the value case (quality).** The first backtest priced each train's shortfall as a penalty (≈US$17M/yr). The agent pointed out that penalties apply per vessel, after trains are blended, and rebuilt the backtest at vessel level (US$5.6M/yr upper bound). That is the number in the deck, alongside a 50% planning case.

**6. Unblocking the app in about 30 minutes instead of an afternoon (speed).** Three platform blockers came up back to back:
- New apps failed on the account's service-principal cap. Memory from a past build named this immediately.
- The reused app sat in an Apps space that allows no resource bindings, and the Genie permissions API wouldn't accept that app's identity.
- The app's first start logged `permission denied for database`.

The agent read the deploy and app logs and switched to explicit UC and Postgres grants plus on-behalf-of-user Genie calls (which also enforce row filters per person). It fixed the database error with a single `GRANT CREATE`. It then proved the result end to end with `scripts/app_smoke_test.py`.

**7. Bugs found by testing the live app, not by reading code (quality).** The smoke test against the deployed app showed Genie's answer and its restatement of the question in the wrong fields. It also showed ~1.7 s per request. The agent added server-side timing, which proved Lakebase itself takes 4-15 ms and the rest is network from Perth. That is the latency figure quoted.

**8. Adapting to the shared metastore (speed).** Tagging failed because several tag keys are governed policies in the FEVM metastore. The agent listed the tag policies, switched to allowed values (`domain=supply_chain`, `layer=gold`, `system.certification_status=certified`) and prefixed the custom keys, all in one iteration.

## Where AI needed a human or was slower

- Each notebook execution against serverless through Databricks Connect took ~8 minutes, so iteration on notebook output was slower than on the pipeline.
- One notebook run failed on a transient OAuth refresh (401). The agent verified auth was fine and re-ran it rather than changing code.
- The first deck, built from the corporate Slides template, was functional but text-heavy. I asked for a redesign, and the agent rebuilt it as a 6-slide HTML deck with a real app screenshot.
- Business framing (which personas, which pilot ask, how conservative to be on value) was a conversation, not something I delegated.
