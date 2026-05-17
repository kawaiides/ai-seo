#!/usr/bin/env bash
# Restore Postgres from the latest S3 backup.
# Pulls aegis-YYYY-MM-DD.sql.gz from the backups bucket and restores via psql.
#
# Usage:
#   ./scripts/restore_db.sh                # latest backup, prompts before applying
#   ./scripts/restore_db.sh 2026-05-12     # specific date
set -euo pipefail

REGION="${AWS_REGION:-$(aws configure get region || echo us-east-1)}"
TF_DIR="$(dirname "$0")/../infra/terraform"
BUCKET=$(cd "$TF_DIR" && terraform output -raw backups_bucket)
DB_HOST=$(cd "$TF_DIR" && terraform output -raw rds_endpoint)

DATE="${1:-}"
if [[ -z "$DATE" ]]; then
    DATE=$(aws s3 ls "s3://$BUCKET/db/" --region "$REGION" | \
        awk '{print $4}' | grep -oE 'aegis-[0-9]{4}-[0-9]{2}-[0-9]{2}' | \
        sort -u | tail -1 | cut -d- -f2-)
fi

KEY="db/aegis-${DATE}.sql.gz"
echo "--> Restore source: s3://$BUCKET/$KEY"
echo "--> Target DB:      $DB_HOST"
read -rp "Proceed? Existing data WILL be overwritten on conflict. [y/N] " ok
[[ "$ok" == "y" ]] || { echo "Aborted."; exit 1; }

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

aws s3 cp --region "$REGION" "s3://$BUCKET/$KEY" "$TMP/dump.sql.gz"
gunzip "$TMP/dump.sql.gz"

# Strip asyncpg from DATABASE_URL since psql wants libpq scheme.
DB_URL=$(aws secretsmanager get-secret-value \
    --region "$REGION" \
    --secret-id "$(cd "$TF_DIR" && terraform output -raw secret_arn)" \
    --query SecretString --output text | \
    jq -r '.DATABASE_URL' | sed 's|postgresql+asyncpg://|postgresql://|')

psql "$DB_URL" -f "$TMP/dump.sql"
echo "--> Restore complete."
