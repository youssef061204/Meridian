#!/usr/bin/env bash
# Deploy the low-cost Azure footprint and upload the Gold Parquet marts. Status: PREPARED, NOT RUN.
# Requires: az CLI logged in (az login), an empty resource group, and data/marts/*.parquet
# (produced by `python -m meridian.cli insights`). Uses Entra ID auth only; no storage keys.
set -euo pipefail
RG=${RG:?set RG to the resource group name}
EMAIL=${BUDGET_EMAIL:?set BUDGET_EMAIL for budget alerts}
: "${SQL_ADMIN_PASSWORD:?set SQL_ADMIN_PASSWORD (not stored anywhere by this script)}"
ME=$(az ad signed-in-user show --query id -o tsv)
IP=$(curl -fsS https://api.ipify.org || true)

az bicep build -f infra/azure/main.bicep --stdout > /dev/null   # compile check first
OUT=$(az deployment group create -g "$RG" -f infra/azure/main.bicep \
  -p sqlAdminPassword="$SQL_ADMIN_PASSWORD" budgetEmail="$EMAIL" clientIp="$IP" analystObjectId="$ME" \
  --query properties.outputs -o json)
ACCOUNT=$(echo "$OUT" | python -c "import json,sys;print(json.load(sys.stdin)['storageAccount']['value'])")
echo "Storage account: $ACCOUNT"

# Role assignments can take a minute to propagate before the upload is authorised.
for attempt in 1 2 3 4 5; do
  if az storage blob upload-batch --auth-mode login --account-name "$ACCOUNT" -d gold -s data/marts --pattern '*.parquet' --overwrite; then break; fi
  echo "upload not yet authorised (attempt $attempt); waiting for role propagation"; sleep 30
done
echo "Now run synapse/serverless_views.sql against: $(echo "$OUT" | python -c "import json,sys;print(json.load(sys.stdin)['serverlessSqlEndpoint']['value'])")"
