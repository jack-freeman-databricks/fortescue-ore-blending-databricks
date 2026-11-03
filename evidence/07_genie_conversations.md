# Genie Agent conversations (space `01f1d784cd381d9a847bfae03a44cfbf`)

Asked as `jack.freeman@databricks.com` via the Genie Conversation API on 2026-11-03 21:15 AWST. Each answer is generated live: Genie writes SQL against the Unity Catalog metric views / gold tables; UC row filters and masks apply to the asker.

## Q1. Which hub had the highest share of trains below typical Fe in October?

_status COMPLETED in 22s_

_Interpretation:_ You want to know which mine hub had the highest percentage of trains with iron content below the typical level during October 2026.

```sql
WITH `hub_kpis` AS (
  SELECT
    `Hub`,
    MEASURE(`Trains`) AS `trains`,
    MEASURE(`Pct Trains Below Typical Fe`) AS `pct_below_typical`
  FROM `jack_freeman_catalog`.`fmg_ore_blending`.`blend_compliance_mv`
  WHERE `Departure Month` = DATE'2026-10-01'
    AND `Hub` IS NOT NULL
  GROUP BY ALL
), `ranked` AS (
  SELECT
    `Hub`,
    `trains`,
    `pct_below_typical`,
    RANK() OVER (ORDER BY `pct_below_typical` DESC) AS `rnk`
  FROM `hub_kpis`
)
SELECT `Hub`, `trains`, `pct_below_typical`
FROM `ranked`
WHERE `rnk` = 1
```

| Hub | trains | pct_below_typical |
|---|---|---|
| Solomon | 124 | 73.38700 |

**Answer:** In October 2026, **Solomon** had the highest share of trains below typical Fe, with **73.39%** of **124 trains** below typical Fe. This means Solomon was the weakest-performing hub on this measure for October.

## Q2. What is the block model Fe bias and analyser Fe error by hub?

_status COMPLETED in 15s_

_Interpretation:_ You want to see the block model iron (Fe) bias and the analyser iron error for each mine hub.

```sql
SELECT `Hub`, ROUND(MEASURE(`Block Model Fe Bias`), 3) AS `block_model_fe_bias`, ROUND(MEASURE(`Analyser Fe MAE`), 3) AS `analyser_fe_error` FROM `jack_freeman_catalog`.`fmg_ore_blending`.`blend_compliance_mv` WHERE `Hub` IS NOT NULL GROUP BY ALL ORDER BY `block_model_fe_bias` DESC
```

| Hub | block_model_fe_bias | analyser_fe_error |
|---|---|---|
| Solomon | 0.447 | 0.211 |
| Cloudbreak | 0.223 | 0.283 |
| Eliwana | 0.194 | 0.215 |
| Christmas Creek | 0.095 | 0.224 |

**Answer:** By hub, **Solomon** has the highest block model Fe bias at **0.45**, followed by **Cloudbreak** at **0.22**, **Eliwana** at **0.19**, and **Christmas Creek** at **0.10**. Analyser Fe error is highest at **Cloudbreak** (**0.28**) and lowest at **Solomon** (**0.21**), with **Christmas Creek** at **0.22** and **Eliwana** at **0.22**; across these 4 hubs, block model bias varies more than analyser error.

## Q3. How many trains are in flight right now and how many are predicted below typical Fe, by hub?

_status COMPLETED in 22s_

_Interpretation:_ You want to see the number of trains currently in flight and, for each hub, how many of those trains are predicted to have iron content below the typical level.

```sql
SELECT CASE `hub_code` WHEN 'CB' THEN 'Cloudbreak' WHEN 'CC' THEN 'Christmas Creek' WHEN 'SOL' THEN 'Solomon' WHEN 'ELI' THEN 'Eliwana' END AS `hub`, COUNT(*) AS `in_flight_trains`, SUM(CASE WHEN `pred_fe` < `fe_typ` THEN 1 ELSE 0 END) AS `predicted_below_typical_trains` FROM `jack_freeman_catalog`.`fmg_ore_blending`.`train_grade_predictions` WHERE `in_flight` = true AND `hub_code` IS NOT NULL GROUP BY ALL ORDER BY `hub` ASC
```

| hub | in_flight_trains | predicted_below_typical_trains |
|---|---|---|
| Christmas Creek | 6 | 1 |
| Cloudbreak | 6 | 3 |
| Eliwana | 4 | 2 |
| Solomon | 7 | 6 |

**Answer:** There are **23** in-flight trains right now across the 4 hubs shown, and **12** of them are predicted to be below typical Fe. By hub:
- **Christmas Creek:** **6** in flight, **1** predicted below typical Fe
- **Cloudbreak:** **6** in flight, **3** predicted below typical Fe
- **Eliwana:** **4** in flight, **2** predicted below typical Fe
- **Solomon:** **7** in flight, **6** predicted below typical Fe

**Solomon** has both the highest number of in-flight trains and the highest count predicted below typical Fe.

**Answer:** Would you prefer to see the count of in-flight trains predicted off-spec instead of just below typical Fe?

## Q4. Show monthly vessel grade penalty in USD by product

_status COMPLETED in 28s_

_Interpretation:_ You want to see the monthly grade penalty in USD for each product based on vessel cargo compliance.

```sql
SELECT `Product`, `Load Month`, MEASURE(`Grade Penalty USD`) AS `grade_penalty_usd`
FROM `jack_freeman_catalog`.`fmg_ore_blending`.`vessel_compliance_mv`
WHERE `Product` IS NOT NULL AND `Load Month` IS NOT NULL
GROUP BY ALL
ORDER BY `Product`, `Load Month` ASC
```

| Product | Load Month | grade_penalty_usd |
|---|---|---|
| Fortescue Blend | 2026-08-01 | 114968.17999999998 |
| Fortescue Blend | 2026-09-01 | 125398.24000000002 |
| Fortescue Blend | 2026-10-01 | 146231.28 |
| Fortescue Blend | 2026-11-01 | 0.0 |
| Kings Fines | 2026-08-01 | 237365.57 |
| Kings Fines | 2026-09-01 | 362316.6599999999 |
| Kings Fines | 2026-10-01 | 289951.23000000004 |
| Kings Fines | 2026-11-01 | 41921.85 |

**Answer:** Monthly vessel grade penalties are shown for **Fortescue Blend** and **Kings Fines** from **August to November 2026**, and **Kings Fines** is higher in every month. Examples include:
- **Fortescue Blend**: **$114,968.18** in **Aug 2026**
- **Fortescue Blend**: **$146,231.28** in **Oct 2026**
- **Fortescue Blend**: **$0.00** in **Nov 2026**
- **Kings Fines**: **$362,316.66** in **Sep 2026**
- **Kings Fines**: **$41,921.85** in **Nov 2026**

Across all 8 month-product results, the highest penalty is **Kings Fines in Sep 2026 ($362,316.66)** and the lowest is **Fortescue Blend in Nov 2026 ($0.00)**.

## Q5. Which stockpiles have the biggest gap between block model Fe and lab-reconciled Fe?

_status COMPLETED in 21s_

_Interpretation:_ You want to find the stockpile with the largest difference between the block model iron grade and the lab-reconciled iron grade. The results show the stockpile ID, hub name, product code, grade class, block model Fe, lab-reconciled Fe, and the absolute gap between these two grades. The data excludes any stockpiles where either grade is missing and ranks them to identify the one with the biggest gap.

```sql
WITH ranked AS (
  SELECT
    `stockpile_id`,
    CASE `hub_code`
      WHEN 'CB' THEN 'Cloudbreak'
      WHEN 'CC' THEN 'Christmas Creek'
      WHEN 'SOL' THEN 'Solomon'
      WHEN 'ELI' THEN 'Eliwana'
    END AS `Hub`,
    `product_code`,
    `grade_class`,
    `bm_fe`,
    `recon_fe`,
    ABS(`bm_fe` - `recon_fe`) AS `fe_gap`,
    RANK() OVER (ORDER BY ABS(`bm_fe` - `recon_fe`) DESC) AS `rnk`
  FROM `jack_freeman_catalog`.`fmg_ore_blending`.`stockpile_grade_current`
  WHERE `bm_fe` IS NOT NULL
    AND `recon_fe` IS NOT NULL
    AND `hub_code` IS NOT NULL
)
SELECT
  `stockpile_id`,
  `Hub`,
  `product_code`,
  `grade_class`,
  `bm_fe`,
  `recon_fe`,
  `fe_gap`
FROM ranked
WHERE `rnk` = 1
ORDER BY `fe_gap` DESC, `stockpile_id` ASC
```

| stockpile_id | Hub | product_code | grade_class | bm_fe | recon_fe | fe_gap |
|---|---|---|---|---|---|---|
| SOL-ROM-05 | Solomon | KF | LG2 | 55.9898 | 55.5616 | 0.4282000000000039 |

**Answer:** The biggest gap is for stockpile **SOL-ROM-05** at **Solomon**: block model Fe is **55.99%**, lab-reconciled Fe is **55.56%**, for an absolute gap of **0.43 percentage points**. Stockpile **SOL-ROM-05** is a **KF** stockpile in grade class **LG2**, and it is the top-ranked gap in the current stockpile data.

## Q6. What blend is recommended for the next Solomon Fortescue Blend train and why?

_status COMPLETED in 29s_

_Interpretation:_ You want to see the recommended blend details for the next planned Solomon Fortescue Blend train, including the reason for the recommendation.

```sql
SELECT CASE blend_recommendations.hub_code WHEN 'CB' THEN 'Cloudbreak' WHEN 'CC' THEN 'Christmas Creek' WHEN 'SOL' THEN 'Solomon' WHEN 'ELI' THEN 'Eliwana' END AS hub,
       blend_recommendations.product_code,
       blend_recommendations.recommendation_id,
       blend_recommendations.planned_train_id,
       blend_recommendations.planned_load_start,
       blend_recommendations.target_wet_t,
       blend_recommendations.status,
       blend_recommendations.pred_fe,
       blend_recommendations.fe_typ,
       blend_recommendations.fe_margin,
       blend_recommendations.pred_sio2,
       blend_recommendations.pred_al2o3,
       blend_recommendations.pred_p,
       blend_recommendations.pred_loi,
       blend_recommendations.bm_view_fe,
       blend_recommendations.blend_json,
       blend_recommendations.optimiser_message,
       blend_recommendations.rationale,
       blend_recommendations.model_version,
       blend_recommendations.created_at
FROM jack_freeman_catalog.fmg_ore_blending.blend_recommendations
WHERE blend_recommendations.hub_code = 'SOL'
  AND blend_recommendations.product_code = 'FB'
  AND blend_recommendations.planned_load_start IS NOT NULL
ORDER BY blend_recommendations.planned_load_start ASC
LIMIT 1
```

| hub | product_code | recommendation_id | planned_train_id | planned_load_start | target_wet_t | status | pred_fe | fe_typ | fe_margin | pred_sio2 | pred_al2o3 | pred_p | pred_loi | bm_view_fe | blend_json | optimiser_message | rationale | model_version | created_at |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Solomon | FB | REC-T001361 | T001361 | 2026-11-03T12:48:48.000Z | 34500.0 | OPTIMAL | 58.1773 | 58.0 | 0.179 | 5.1046 | 2.449 | 0.0695 | 8.6603 | 58.6055 | {"SOL-ROM-01":20100.0,"SOL-ROM-03":14400.0} | Meets typical Fe with margin at minimum Fe giveaway | Reclaim 20,100 tonnes from SOL-ROM-01 and 14,400 tonnes from SOL-ROM-03 to make typical FB grade at 58.18% Fe. This blen | 4 | 2026-11-03T06:00:00.000Z |

**Answer:** The next **Solomon Fortescue Blend** train is **planned train T001361**, scheduled to load on **2026-11-03 12:48:48**, with an **optimal** recommendation to reclaim **20,100 tonnes from SOL-ROM-01** and **14,400 tonnes from SOL-ROM-03** for a **34,500-tonne** train. The reason given in **recommendation REC-T001361** is that this blend is expected to deliver **58.18% Fe** against a typical target of **58.00% Fe** with a **0.18% Fe margin**, while meeting spec with minimal Fe giveaway and using reconciled grades that are more accurate than the block model; the block-model view for the same blend is **58.61% Fe**.

