#!/usr/bin/env bash
# Lakebase operational layer for the ore-blending app.
#  1. autoscaling project (production branch, primary endpoint, scale-to-zero)
#  2. synced tables: UC Delta gold/ML outputs -> Postgres schema fmg_ore_blending (SNAPSHOT, re-triggered per shift)
#  3. federated OAuth role for the app service principal + read grants on the synced schema
# The app SP creates and owns schema `ops` (decisions, stockpile availability) on first start.
set -euo pipefail
P=${PROFILE:-fevm-serverless-stable-1s8h43}
PROJECT=fmg-ore-blending
BRANCH=projects/$PROJECT/branches/production
EP=$BRANCH/endpoints/primary
FQ=jack_freeman_catalog.fmg_ore_blending
APP_SP=${APP_SP:-70e678f6-7f63-49bf-b1fc-d34037e79eff}   # SP of app fortescue-maintenance-runner

if ! databricks postgres get-project projects/$PROJECT -p "$P" >/dev/null 2>&1; then
  databricks postgres create-project $PROJECT -p "$P" --json \
    '{"spec":{"display_name":"FMG ore blending - load-out operations","default_endpoint_settings":{"autoscaling_limit_min_cu":0.5,"autoscaling_limit_max_cu":2}}}'
fi

sync() {  # sync <source table> <pk column>
  local src=$1 pk=$2 name="$FQ.lb_$1"
  if databricks postgres get-synced-table "synced_tables/$name" -p "$P" >/dev/null 2>&1; then
    echo "exists: $name"; return
  fi
  databricks postgres create-synced-table "$name" --no-wait -p "$P" --json "{\"spec\":{
    \"source_table_full_name\":\"$FQ.$src\",\"primary_key_columns\":[\"$pk\"],\"scheduling_policy\":\"SNAPSHOT\",
    \"branch\":\"$BRANCH\",\"postgres_database\":\"databricks_postgres\",\"create_database_objects_if_missing\":true,
    \"new_pipeline_spec\":{\"storage_catalog\":\"jack_freeman_catalog\",\"storage_schema\":\"fmg_ore_blending\"}}}" \
    | jq -r '.response.name'
}
sync stockpile_grade_current stockpile_id
sync train_grade_predictions train_id
sync blend_recommendations recommendation_id
sync blend_recommendation_lines line_id

# federated login role for the app SP (a plain CREATE ROLE would be NO_LOGIN)
databricks postgres create-role $BRANCH --role-id app-blend-control-room -p "$P" --json \
  "{\"spec\":{\"identity_type\":\"SERVICE_PRINCIPAL\",\"postgres_role\":\"$APP_SP\",\"auth_method\":\"LAKEBASE_OAUTH_V1\"}}" 2>/dev/null || echo "role exists"

HOST=$(databricks postgres get-endpoint $EP -p "$P" -o json | jq -r .status.hosts.host)
TOKEN=$(databricks postgres generate-database-credential $EP -p "$P" -o json | jq -r .token)
ME=$(databricks current-user me -p "$P" -o json | jq -r .userName)
PGPASSWORD=$TOKEN psql "host=$HOST user=$ME dbname=databricks_postgres sslmode=require" -v ON_ERROR_STOP=1 <<SQL
GRANT CONNECT, CREATE ON DATABASE databricks_postgres TO "$APP_SP";
GRANT USAGE ON SCHEMA fmg_ore_blending TO "$APP_SP";
GRANT SELECT ON ALL TABLES IN SCHEMA fmg_ore_blending TO "$APP_SP";
ALTER DEFAULT PRIVILEGES IN SCHEMA fmg_ore_blending GRANT SELECT ON TABLES TO "$APP_SP";
SQL
echo "Lakebase ready: $HOST"
