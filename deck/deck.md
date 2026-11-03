# Fortescue | Every train to spec: mine-to-port grade compliance on Databricks

_Business deck (text export). Slides: https://docs.google.com/presentation/d/1DybfwqnMNv8oQ8J9W_JH-tqlo32fQHCxPEuxEGR0qtk · PDF: `deck/fortescue_every_train_to_spec.pdf`. All data synthetic; penalty schedule illustrative._


---

## Slide 1: Every train to spec

_Mine-to-port grade compliance for Fortescue, on Databricks_


> **Speaker notes.** Today is about one decision made around 15 times a day at Cloudbreak, Christmas Creek, Solomon and Eliwana: which stockpiles to reclaim into the next train. Get it right and the cargo meets spec. Get it wrong and the cost lands at port, a day or two later.
>
> _Evidence:_ Working prototype on synthetic data modelled on Fortescue's four hubs and two products (Fortescue Blend, Kings Fines).
>
> _Likely question:_ Is this real Fortescue data? No. It is synthetic, built to Fortescue's public operating shape, so we can show the full workflow safely.

---

## Slide 2: The outcome: grade known at departure, not 36 hours later


**Solomon trains below typical Fe**

- 73% today
- → under 5% (simulated)
- by blending on lab-reconciled grade

**Off-spec trains caught at load-out**

- 11% → 71%
- flagged at departure, 24-48 h before the lab result

**Grade penalties avoided**

- US$5.6M/yr at risk (illustrative)
- Planning case: US$2.8M/yr

> **Speaker notes.** Three numbers. First, Solomon: about three in four trains leave below typical Fe because the block model over-states iron by roughly 0.45%. Blending on lab-reconciled grade fixes the bias at the source; in a 30-day backtest Solomon drops from 73% to under 1% below typical. We quote under 5% to stay conservative. Second, the ML model flags 71% of below-typical trains the moment they leave the load-out, against 11% if you trust the block model. That is 24 to 48 hours before the lab. Third, money: on an illustrative penalty schedule the last 30 days cost US$459k, about US$5.6M a year. Our planning case takes half of that.
>
> _Evidence:_ Backtest: notebooks/02_score_and_recommend.ipynb section 4 (value_backtest); Model hold-out: notebooks/01_train_grade_model.ipynb; evidence/RESULTS.md rows 2, 5, 7, 8
>
> _Likely question:_ Why halve the penalty number? The backtest corrects the bias perfectly in hindsight; real operations will capture part of it, so we plan on 50% and treat the rest as upside.

---

## Slide 3: Why trains miss spec today

- Controllers build each ~34.5 kt train from block-model stockpile grades
- The block model is biased: Solomon over-states Fe by ~0.45%, Cloudbreak by ~0.22%
- The cross-belt analyser drifts between calibrations
- The lab assay, the grade that counts, arrives 24-48 h after departure
- By then the ore is at Herb Elliott Port: re-blend, rehandle or pay the penalty
- Result: 39% of trains below typical Fe; hubs that over-deliver give iron away

> **Speaker notes.** No one is doing anything wrong here. Controllers use the best number they have at the time, which is the block model. But the block model has a hub-specific bias, the analyser drifts, and the truth arrives a day and a half later. The feedback loop is too slow to correct the next train.
>
> _Evidence:_ Block-model bias by hub from the certified metric view: evidence/05_semantic_layer.log; 39.2% of trains below typical in last 30 days: notebooks/02 section 4; Lab lag 24-48 h modelled in data_gen/generate.py
>
> _Likely question:_ Is the bias stable enough to correct? It drifts, which is why the correction is recomputed from the most recent 14 days of returned lab results, using only results available at departure.

---

## Slide 4: For the executive sponsor


**GM Integrated Planning & Marketing**

- Fewer off-spec vessels: 16 of 76 below typical → 0 (simulated)
- US$2.8M/yr planning case, US$5.6M/yr upside
- One certified definition of compliance
- Auditable decisions for every train

**What we ask**

- 8-week pilot at Solomon, the problem hub
- Real LIMS, FMS and analyser feeds
- Success = below-typical trains halved
- Go/no-go on value, not technology

> **Speaker notes.** For you this is a revenue-protection play. Vessel compliance goes up and penalties come down. Every number traces back to a certified definition, so marketing, planning and the hubs argue about actions rather than numbers. The ask is small: an 8-week pilot at Solomon, where the problem is biggest, with a clear value test at the end.
>
> _Evidence:_ Vessels below typical 16 → 0 in 30-day backtest: notebooks/02 section 4; Certified metric views blend_compliance_mv / vessel_compliance_mv: sql/03_semantic_layer.sql
>
> _Likely question:_ What does the pilot cost? Serverless compute plus a small Lakebase instance that scales to zero; we will size it with you against the pilot data volumes.

---

## Slide 5: For the domain owner: grade control


**Superintendent Grade Control**

- Predicted lab grade for every in-flight train
- Recommended blend for every planned train
- One click to approve, re-plan or reject
- Outage? Re-plan in seconds without that stockpile

**Why you can trust it**

- Fe error 0.249% → 0.111% vs block model
- Accurate at every hub: 0.11% MAE at Solomon
- Shows the block-model view next to each blend
- Controllers stay in charge; overrides are logged

> **Speaker notes.** Your controllers open the Blend Control Room at the start of shift. Every planned train has a recommended blend and a plain-English reason. Every train already on the rail has a predicted lab grade. If a reclaimer goes down, they mark the stockpile out and re-plan; the optimiser uses only what is available. Nothing is automatic: they approve, override or reject, and the reason is recorded.
>
> _Evidence:_ Hold-out MAE by element and hub: notebooks/01_train_grade_model.ipynb; Live app run: evidence/09_app_api_calls.log (approve, outage re-plan, override)
>
> _Likely question:_ What if the model is wrong? Each prediction carries a 90% error band (±0.24% Fe), and the optimiser keeps a safety margin above typical Fe.

---

## Slide 6: One integrated data journey

- Lakeflow: FMS hauls, rail, analyser, LIMS and port feeds ingested; quality rules drop bad assays
- Unity Catalog: hub-level row filters, masked penalty $, certified metrics, lineage
- ML + GenAI: train-grade model (MLflow, UC champion), blend optimiser, AI rationale
- Lakebase: live stockpile, prediction and blend state; controller decisions written back
- Genie Agent: ask compliance questions in plain English, governed by the same rules
- Databricks App: the Blend Control Room for every shift

> **Speaker notes.** Six stages, one platform, one set of permissions. Data lands through Lakeflow with quality checks. Unity Catalog governs who sees which hub and who sees commercial numbers. The model and optimiser run on that governed data. Lakebase serves the live operational state to the app in milliseconds and captures every decision, which flows back into the lakehouse for audit and retraining. Genie answers the questions people would otherwise email an analyst.
>
> _Evidence:_ Pipeline run and expectations: evidence/03_pipeline_run.log, evidence/04_pipeline_outputs.log; Lakebase synced tables and ops schema: evidence/06_lakebase.log; Lineage bronze → Lakebase: evidence/08_governance_and_lineage.log
>
> _Likely question:_ Does this replace our block model or LIMS? No. It reads from them and corrects for their known biases; the systems of record stay as they are.

---

## Slide 7: The model: knowing the lab grade before the lab

| Signal available at departure | Fe error (MAE) | Below-typical trains caught | False alarms |
|---|---|---|---|
| Block model (today) | 0.249% | 11% | 1 |
| Cross-belt analyser (raw) | 0.172% | 66% | 20 |
| Train-grade model (Databricks) | 0.111% | 71% | 14 |

> **Speaker notes.** Tested on the most recent three weeks, which the model never saw in training. Fe error halves against the block model. More importantly it catches 71% of the trains that will come back below typical, with fewer false alarms than the analyser alone. The model learns each hub's recent bias from lab results that have already returned, so there is no look-ahead.
>
> _Evidence:_ notebooks/01_train_grade_model.ipynb: baselines vs model, recall/precision; 313 trains in the 21-day hold-out; 131 below typical
>
> _Likely question:_ How often is it retrained? The scheduled job scores every shift; retraining is monthly or when the hold-out error drifts, with MLflow champion/challenger in Unity Catalog.

---

## Slide 8: Value model: 30-day backtest, annualised

| KPI | Today | With Databricks (simulated) |
|---|---|---|
| Trains below typical Fe | 39.2% | 5.9% |
| Solomon trains below typical Fe | 72.7% | 0.8% |
| Vessels below typical Fe (30 d) | 16 of 76 | 0 of 76 |
| Vessel grade penalty, annualised | US$5.6M | ~US$0 |
| Planning case (50% realisation) |  | US$2.8M/yr avoided |
| Trade-off: avg train Fe | 57.82% | 58.07% (more high-grade ore used) |

> **Speaker notes.** This is a backtest, so it is an upper bound. Penalties are calculated per vessel on an illustrative schedule; please swap in your contract terms. The trade-off is real: hitting spec at Solomon means using more of your high-grade stockpile, so planning has to manage high-grade inventory. Not counted here: port rehandle, controller time, and the iron given away at hubs that over-deliver.
>
> _Evidence:_ value_backtest table and notebooks/02 section 4; Penalty schedule: data/landing/reference/penalty_schedule.csv (illustrative)
>
> _Likely question:_ Can we run this on our own contract terms? Yes. The penalty schedule is a reference table; change it and every metric, Genie answer and app view updates.

---

## Slide 9: Governed by design

- A Solomon grade controller sees only Solomon trains (UC row filter)
- Penalty dollars are visible to commercial roles only (UC column mask)
- Genie and the app inherit the same rules; no separate permission model
- Certified metric views: one definition of 'below typical'
- Every recommendation, override and reason is logged and lands in Delta
- Full lineage from raw feed to the app's Lakebase tables

> **Speaker notes.** We tested this: the same query run as a Solomon-only user returns Solomon rows only, and penalty dollars come back masked. Genie in the app runs as the signed-in user, so it obeys the same rules. Decisions are written to Lakebase and land in a Delta table with who, when and why.
>
> _Evidence:_ Before/after entitlement test: evidence/08_governance_and_lineage.log; Decisions in Delta: notebooks/03_sync_decisions_to_delta.ipynb
>
> _Likely question:_ Who manages entitlements? Unity Catalog, through a single entitlements table today, or groups mapped from your identity provider.

---

## Slide 10: Next steps: 8-week Solomon pilot

- Weeks 1-2: connect LIMS, FMS, analyser and rail feeds through Lakeflow Connect
- Weeks 3-4: retrain on Fortescue history; agree penalty terms and certified KPIs
- Weeks 5-7: shadow mode in the Blend Control Room alongside current practice
- Week 8: value review against the baseline; go/no-go to all hubs
- Success criteria: below-typical trains halved at Solomon; no extra controller effort

> **Speaker notes.** We start where the problem is biggest. Shadow mode means controllers keep working as they do now while we measure what the recommendations would have done. At week 8 we compare against the baseline and decide together whether to scale to the other three hubs.
>
> _Evidence:_ Prototype already runs the full journey on a serverless workspace; the job schedules it per shift: resources/jobs.yml
>
> _Likely question:_ Who needs to be involved? Grade control at Solomon, the LIMS owner, and someone from marketing for the penalty terms.

---

## Slide 11: Thank you


> **Speaker notes.** Thank you. Happy to walk through the live Blend Control Room or ask Genie a question from the floor.
>
> _Evidence:_ Live app and Genie Agent on the demo workspace
>
> _Likely question:_ Can we see it with our data? That is exactly what the pilot is for.
