-- Unity Catalog governance objects that the Lakeflow pipeline references.
-- Run BEFORE the first pipeline update (row filters / masks must already exist).
USE CATALOG jack_freeman_catalog;
USE SCHEMA fmg_ore_blending;

-- Entitlements: which principal may see which mine hub's train data, and who may
-- see commercial (penalty $) values. '*' = all hubs. UC is the single source of truth;
-- the same table is synced to Lakebase for the app.
CREATE TABLE IF NOT EXISTS access_policy (
  principal STRING NOT NULL COMMENT 'User email or service principal application id',
  hub_code STRING NOT NULL COMMENT 'Mine hub code (CB, CC, SOL, ELI) or * for all hubs',
  can_see_commercials BOOLEAN NOT NULL COMMENT 'May see vessel penalty USD values',
  role STRING COMMENT 'Business role, documentation only'
) COMMENT 'Row/column entitlements for the ore-blending data product (drives row filter + column mask)';

MERGE INTO access_policy t
USING (SELECT * FROM VALUES
  ('jack.freeman@databricks.com', '*', true, 'Integrated planning (demo owner)'),
  ('solomon.grade.controller@example.com', 'SOL', false, 'Solomon hub grade controller'),
  ('cloudbreak.grade.controller@example.com', 'CB', false, 'Cloudbreak hub grade controller'),
  ('marketing.analyst@example.com', '*', true, 'Iron ore marketing')
  AS v(principal, hub_code, can_see_commercials, role)) s
ON t.principal = s.principal AND t.hub_code = s.hub_code
WHEN NOT MATCHED THEN INSERT *;

-- Row filter: grade controllers see only their own hub.
CREATE OR REPLACE FUNCTION hub_row_filter(hub STRING)
RETURNS BOOLEAN
COMMENT 'Row filter: caller must be entitled to the hub in access_policy (or *)'
RETURN EXISTS (
  SELECT 1 FROM jack_freeman_catalog.fmg_ore_blending.access_policy p
  WHERE p.principal = current_user() AND (p.hub_code = '*' OR p.hub_code = hub)
);

-- Column mask: commercial penalty values only for entitled principals.
CREATE OR REPLACE FUNCTION commercial_mask(v DOUBLE)
RETURNS DOUBLE
COMMENT 'Column mask: hides vessel penalty USD unless access_policy.can_see_commercials'
RETURN CASE WHEN EXISTS (
  SELECT 1 FROM jack_freeman_catalog.fmg_ore_blending.access_policy p
  WHERE p.principal = current_user() AND p.can_see_commercials
) THEN v ELSE NULL END;
