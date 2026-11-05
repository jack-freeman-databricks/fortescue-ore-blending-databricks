# Every train to spec: mine-to-port grade control for Fortescue, on Databricks

_Business deck, text export of `deck/fortescue_every_train_to_spec.pdf` (6 slides, source `deck/v2/deck.html`). All data synthetic; penalty schedule illustrative. Every number traces to `evidence/RESULTS.md`._

---

## 1. Every train to spec
Know each train's lab grade when it leaves the load-out, and blend the next one on corrected stockpile grades.

| Outcome | Value |
|---|---|
| Solomon trains below typical Fe | 73% → under 5% (simulated, blending on lab-reconciled grade) |
| Below-typical trains flagged at departure | 11% → 71%, 24-48 h before the lab result |
| Grade penalties avoided | US$2.8M/yr planning case (US$5.6M/yr simulated upper bound) |

> **Talk track.** One decision, made ~15 times a day across Cloudbreak, Christmas Creek, Solomon and Eliwana: which stockpiles go into the next train. Get it right and the vessel meets spec; get it wrong and the cost lands at port two days later.

## 2. Trains are blended on biased grades and checked 36 h later
Pit → ROM stockpile (block model over-states Fe by up to 0.45%) → train load-out (controller picks the blend; cross-belt analyser drifts) → rail (~34.5 kt/train, ~15 trains/day) → Herb Elliott Port (lab assay arrives; re-blend or rehandle) → vessel (off-spec cargo pays the penalty). The lab result comes 24-48 h after departure.

Trains below typical Fe, last 90 days: Solomon Fortescue Blend 80%, Solomon Kings Fines 72%, Cloudbreak 39%, Christmas Creek 14%, Eliwana 13%.

The bias is systematic, so it can be corrected. Hubs with little bias over-deliver instead, giving away iron that could have been sold.

## 3. One control room for every train, on governed data
Screenshot of the live Blend Control Room app.
1. **Predicted lab grade for every train in flight**: ML model registered in Unity Catalog, scored each shift
2. **Recommended blend for every planned train**: optimiser on lab-reconciled grades, with an AI-written reason
3. **Approve, re-plan around an outage, or reject**: decisions written to Lakebase in milliseconds, with name and reason
4. **Ask questions in plain English**: Genie answers from certified metrics, under the user's own access

Built end to end on Databricks: Lakeflow, Unity Catalog, MLflow, AI Functions, Lakebase, Genie, Databricks Apps.

## 4. The grade is known at departure, not after the lab
| Signal at departure | Fe error (MAE, % Fe) | Below-typical trains caught |
|---|---|---|
| Block model (today) | 0.249 | 11% |
| Cross-belt analyser | 0.172 | 66% |
| Databricks model | **0.111** | **71%** |

Held out: last 21 days, 313 trains the model never saw. Precision 87%. No look-ahead: only lab results returned before departure.

## 5. Revenue protected for planning, fewer surprises on shift
**Executive sponsor (GM Integrated Planning & Marketing):** US$2.8M/yr grade penalties avoided (planning case, 50% realisation). Vessels below typical Fe 16 of 76 → 0 in the 30-day backtest; trains below typical 39% → 6%; one certified definition of compliance.

**Domain owner (Superintendent Grade Control):** 36 h → 0 wait to know a train's grade. Re-plan in seconds when a reclaimer goes down; controllers stay in charge with every override logged; each hub sees its own trains, penalty dollars only for commercial roles.

Trade-off: average train Fe rises 57.82% → 58.07%, so more high-grade ore is used at Solomon.

## 6. An 8-week pilot at Solomon, decided on value
Weeks 1-2 **Connect** LIMS, fleet management, analyser and rail feeds through Lakeflow Connect · Weeks 3-4 **Calibrate** on Fortescue history and agree contract penalty terms · Weeks 5-7 **Shadow** beside current practice · Week 8 **Decide** go or no-go for all four hubs.

Success: below-typical trains at Solomon halved; prediction error under 0.15% Fe on Fortescue data; no extra controller effort. Needed from Fortescue: Solomon grade control as pilot owner, read access to LIMS/FMS/analyser data, marketing input on penalty terms.
