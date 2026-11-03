-- Prove the Unity Catalog row filter + column mask work: same queries, before/after narrowing the
-- caller's entitlement in access_policy (UC is the single source of truth for who sees what).
USE CATALOG jack_freeman_catalog;
USE SCHEMA fmg_ore_blending;
SELECT current_user() AS caller, hub_code, can_see_commercials FROM access_policy WHERE principal = current_user();
-- (A) full entitlement: all hubs + commercial values
SELECT hub_code, count(*) AS trains FROM train_performance GROUP BY hub_code ORDER BY hub_code;
SELECT count(*) AS vessels, round(sum(penalty_usd), 0) AS penalty_usd_visible FROM vessel_compliance;
SELECT `Hub`, MEASURE(`Trains`) AS trains FROM blend_compliance_mv GROUP BY ALL ORDER BY 1;
-- narrow the caller to a Solomon grade controller without commercial access
UPDATE access_policy SET hub_code = 'SOL', can_see_commercials = false WHERE principal = current_user();
-- (B) same queries: only Solomon rows; penalty USD masked to NULL; metric view inherits the filter
SELECT hub_code, count(*) AS trains FROM train_performance GROUP BY hub_code ORDER BY hub_code;
SELECT count(*) AS vessels, round(sum(penalty_usd), 0) AS penalty_usd_visible FROM vessel_compliance;
SELECT `Hub`, MEASURE(`Trains`) AS trains FROM blend_compliance_mv GROUP BY ALL ORDER BY 1;
-- restore
UPDATE access_policy SET hub_code = '*', can_see_commercials = true WHERE principal = current_user();
SELECT hub_code, count(*) AS trains FROM train_performance GROUP BY hub_code ORDER BY hub_code;
-- Policies attached (information_schema)
SELECT table_name, column_name, mask_name FROM information_schema.column_masks WHERE table_schema = 'fmg_ore_blending';
SELECT table_name, filter_name, target_columns FROM information_schema.row_filters WHERE table_schema = 'fmg_ore_blending';
SELECT table_name, tag_name, tag_value FROM information_schema.table_tags WHERE schema_name = 'fmg_ore_blending' ORDER BY table_name, tag_name;
-- Lineage captured automatically by Unity Catalog (source -> target)
SELECT DISTINCT source_table_name, target_table_name FROM system.access.table_lineage WHERE target_table_schema = 'fmg_ore_blending' AND target_table_catalog = 'jack_freeman_catalog' AND source_table_name IS NOT NULL AND event_date >= '2026-11-03' ORDER BY target_table_name, source_table_name;
