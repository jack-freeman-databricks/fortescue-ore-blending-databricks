-- Grants for the Databricks App's service principal (app: fortescue-maintenance-runner).
-- App resources can't be declared (app lives in a shared Apps space), so access is granted explicitly.
USE CATALOG jack_freeman_catalog;
GRANT USE CATALOG ON CATALOG jack_freeman_catalog TO `70e678f6-7f63-49bf-b1fc-d34037e79eff`;
GRANT USE SCHEMA, SELECT ON SCHEMA jack_freeman_catalog.fmg_ore_blending TO `70e678f6-7f63-49bf-b1fc-d34037e79eff`;
-- Entitlement: app/Genie service identity sees all hubs but NOT commercial penalty values (column mask applies)
MERGE INTO jack_freeman_catalog.fmg_ore_blending.access_policy t USING (SELECT '70e678f6-7f63-49bf-b1fc-d34037e79eff' principal, '*' hub_code, false can_see_commercials, 'Blend Control Room app SP' role) s ON t.principal = s.principal AND t.hub_code = s.hub_code WHEN NOT MATCHED THEN INSERT *;
SELECT * FROM jack_freeman_catalog.fmg_ore_blending.access_policy ORDER BY principal;
SHOW GRANTS `70e678f6-7f63-49bf-b1fc-d34037e79eff` ON SCHEMA jack_freeman_catalog.fmg_ore_blending;
