-- Governed semantic layer + discovery metadata in Unity Catalog.
USE CATALOG jack_freeman_catalog;
USE SCHEMA fmg_ore_blending;

-- Certified KPI definitions for train grade compliance (one definition for Genie, app and BI)
CREATE OR REPLACE VIEW blend_compliance_mv
WITH METRICS LANGUAGE YAML
COMMENT 'Certified train grade-compliance KPIs (Fortescue mine-to-port). Source: Lakeflow gold train_performance (row-filtered by hub).'
AS $$
version: 1.1
source: jack_freeman_catalog.fmg_ore_blending.train_performance
filter: lab_status = 'RETURNED'
dimensions:
  - name: Hub
    expr: CASE hub_code WHEN 'CB' THEN 'Cloudbreak' WHEN 'CC' THEN 'Christmas Creek' WHEN 'SOL' THEN 'Solomon' WHEN 'ELI' THEN 'Eliwana' END
    comment: Mine hub / train load-out
  - name: Hub Code
    expr: hub_code
  - name: Product
    expr: CASE product_code WHEN 'FB' THEN 'Fortescue Blend' WHEN 'KF' THEN 'Kings Fines' END
  - name: Departure Date
    expr: depart_date
  - name: Departure Week
    expr: DATE_TRUNC('WEEK', depart_date)
  - name: Departure Month
    expr: DATE_TRUNC('MONTH', depart_date)
measures:
  - name: Trains
    expr: COUNT(1)
  - name: Dry Tonnes
    expr: SUM(dry_tonnes)
  - name: Avg Lab Fe Pct
    expr: SUM(lab_fe * dry_tonnes) / SUM(dry_tonnes)
    comment: Tonnage-weighted lab Fe %
  - name: Avg Fe vs Typical
    expr: SUM(fe_vs_typical * dry_tonnes) / SUM(dry_tonnes)
    comment: Tonnage-weighted lab Fe minus product typical Fe (negative = iron shortfall)
  - name: Pct Trains Below Typical Fe
    expr: AVG(CASE WHEN below_typical_fe THEN 1.0 ELSE 0.0 END) * 100
  - name: Trains Below Typical Fe
    expr: SUM(CASE WHEN below_typical_fe THEN 1 ELSE 0 END)
  - name: Trains Breaching Limits
    expr: SUM(CASE WHEN limit_breach THEN 1 ELSE 0 END)
    comment: Trains below min Fe or above max SiO2/Al2O3/P
  - name: Block Model Fe Bias
    expr: AVG(bm_fe_error)
    comment: Block-model Fe minus lab Fe (positive = block model over-states iron)
  - name: Block Model Fe MAE
    expr: AVG(ABS(bm_fe_error))
  - name: Analyser Fe MAE
    expr: AVG(ABS(xba_fe_error))
$$;

CREATE OR REPLACE VIEW vessel_compliance_mv
WITH METRICS LANGUAGE YAML
COMMENT 'Certified vessel cargo compliance KPIs incl. illustrative grade penalty (USD masked for non-commercial roles).'
AS $$
version: 1.1
source: jack_freeman_catalog.fmg_ore_blending.vessel_compliance
dimensions:
  - name: Product
    expr: CASE product_code WHEN 'FB' THEN 'Fortescue Blend' WHEN 'KF' THEN 'Kings Fines' END
  - name: Destination Port
    expr: destination
  - name: Load Month
    expr: load_month
  - name: Load Date
    expr: DATE(load_complete_ts)
measures:
  - name: Vessels
    expr: COUNT(1)
  - name: Shipped Dry Tonnes
    expr: SUM(dry_tonnes)
  - name: Avg Cargo Fe Pct
    expr: SUM(fe * dry_tonnes) / SUM(dry_tonnes)
  - name: Vessels Below Typical Fe
    expr: SUM(CASE WHEN below_typical THEN 1 ELSE 0 END)
  - name: Pct Vessels Below Typical Fe
    expr: AVG(CASE WHEN below_typical THEN 1.0 ELSE 0.0 END) * 100
  - name: Grade Penalty USD
    expr: SUM(penalty_usd)
    comment: Illustrative penalty schedule; NULL for principals without commercial entitlement
  - name: Penalty USD per Dry Tonne
    expr: SUM(penalty_usd) / SUM(dry_tonnes)
$$;

-- Discovery tags. Keys like domain/layer/sensitivity are governed tag policies in this metastore,
-- so allowed values are used; demo-specific keys are prefixed fmg_.
ALTER SCHEMA fmg_ore_blending SET TAGS ('domain' = 'supply_chain', 'fmg_data_product' = 'ore_blending', 'fmg_owner_team' = 'integrated_planning');
ALTER TABLE train_performance SET TAGS ('layer' = 'gold', 'system.certification_status' = 'certified', 'sensitivity' = 'internal', 'fmg_access_control' = 'row_filter_by_hub');
ALTER TABLE vessel_compliance SET TAGS ('layer' = 'gold', 'system.certification_status' = 'certified', 'sensitivity' = 'internal', 'fmg_access_control' = 'column_mask_penalty_usd');
ALTER TABLE stockpile_grade_current SET TAGS ('layer' = 'gold', 'fmg_serving' = 'lakebase_synced');
ALTER TABLE train_features SET TAGS ('layer' = 'gold', 'fmg_purpose' = 'ml_features');
ALTER TABLE silver_lab_assays SET TAGS ('layer' = 'silver');
ALTER TABLE bronze_lims_assays SET TAGS ('layer' = 'bronze');
ALTER TABLE access_policy SET TAGS ('fmg_purpose' = 'entitlements', 'sensitivity' = 'internal');
ALTER VIEW blend_compliance_mv SET TAGS ('system.certification_status' = 'certified', 'fmg_semantic_layer' = 'metric_view');
ALTER VIEW vessel_compliance_mv SET TAGS ('system.certification_status' = 'certified', 'fmg_semantic_layer' = 'metric_view');

-- Verify: query the certified metrics
SELECT `Hub`, `Product`, MEASURE(`Trains`) trains, ROUND(MEASURE(`Avg Lab Fe Pct`), 2) avg_fe, ROUND(MEASURE(`Pct Trains Below Typical Fe`), 1) pct_below_typ, ROUND(MEASURE(`Block Model Fe Bias`), 3) bm_bias FROM blend_compliance_mv GROUP BY ALL ORDER BY 1, 2;
SELECT `Product`, `Load Month`, MEASURE(`Vessels`) vessels, ROUND(MEASURE(`Avg Cargo Fe Pct`), 2) fe, MEASURE(`Vessels Below Typical Fe`) below_typ, ROUND(MEASURE(`Grade Penalty USD`), 0) penalty_usd FROM vessel_compliance_mv GROUP BY ALL ORDER BY 1, 2;
