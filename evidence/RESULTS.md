# Results quoted in the deck, traced to execution evidence

All figures come from the synthetic dataset (90 days, 2026-08-05 → 2026-11-03, 1,354 trains, 220 vessels, ~42 Mt dry shipped).
The penalty schedule is illustrative. "Simulated" = backtest on historical trains, not a live trial.

| # | Metric | Value | Source |
|---|---|---|---|
| 1 | Trains below typical Fe (all hubs, lab returned) | 39.2% (last 30 d); 13-39% at CC/ELI/CB | `notebooks/02_score_and_recommend.ipynb` §4; `evidence/05_semantic_layer.log` |
| 2 | Solomon trains below typical Fe | 80.2% Fortescue Blend, 72.3% Kings Fines (90 d); 73.4% in Oct | `evidence/05_semantic_layer.log`; `evidence/07_genie_conversations.md` Q1 |
| 3 | Solomon block-model Fe bias vs lab | +0.45% Fe (over-states iron) | `evidence/05_semantic_layer.log` (`Block Model Fe Bias`) |
| 4 | Fe prediction error at departure (MAE, 21-day hold-out) | block model 0.249 → analyser 0.172 → **model 0.111** (−55% vs block model) | `notebooks/01_train_grade_model.ipynb` |
| 5 | Below-typical trains caught at departure (recall / precision) | block model 11% / 93% → **model 71% / 87%** (93 of 131) | `notebooks/01_train_grade_model.ipynb` |
| 6 | Time to know a train's grade | 24-48 h (lab) → at departure (model) | `data_gen/generate.py` lab lag; `notebooks/02_score_and_recommend.ipynb` §1 |
| 7 | Illustrative vessel grade penalty, last 30 d | US$459k (16 of 76 vessels below typical) → annualised **US$5.6M** | `notebooks/02_score_and_recommend.ipynb` §4 (`value_backtest`) |
| 8 | Simulated reconciled-grade targeting | trains below typical 39.2% → 5.9%; Solomon 72.7% → 0.8%; vessels below typical 16 → 0; penalty → ~US$0 | same |
| 9 | Ore cost of the fix | avg train Fe +0.25% (57.82 → 58.07), i.e. more high-grade ore per train | same |
| 10 | Lakebase serving latency inside the app | 4-15 ms per request (network round trip from Perth ~1.7 s) | `evidence/09_app_api_calls.log` (`X-Lakebase-ms`) |
| 11 | Data quality caught by Lakeflow expectations | 25 bad payloads, 40 duplicate hauls, 6 lab closure failures, 851 analyser faults | `evidence/04_pipeline_outputs.log` |
| 12 | Governance | Row filter: Solomon-only caller sees 361 SOL trains only; column mask: penalty USD NULL without entitlement | `evidence/08_governance_and_lineage.log` |

Planning case in the deck: 50% realisation of the simulated penalty avoidance = **~US$2.8M/yr**, before port
rehandle savings and controller time (not quantified).
