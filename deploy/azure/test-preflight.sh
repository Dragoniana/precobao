#!/usr/bin/env bash
set -Eeuo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
test_dir="$(mktemp -d)"
trap 'rm -rf -- "$test_dir"' EXIT
mkdir -p "$test_dir/scraper-state"
env_file="$test_dir/.env.test"

cat >"$env_file" <<EOF
API_DOMAIN=precobao.app
EXPO_PUBLIC_API_URL=https://precobao.app/backend
BACKEND_CORS_ORIGINS='["https://precobao.app"]'
CADDY_API_SUBNET=172.29.240.0/24
CADDY_API_IP=172.29.240.2
API_CONTAINER_IP=172.29.240.3
WEB_CONTAINER_IP=172.29.240.4
DATABASE_URL=postgresql+psycopg2://preflight:non-secret-test-password@preflight.postgres.database.azure.com:5432/preflight?sslmode=verify-full&sslrootcert=/run/secrets/azure-postgres-root-ca.pem
AZURE_PG_ROOT_CERT_PATH=/etc/ssl/certs/ca-certificates.crt
JWT_SECRET=preflight-only-not-a-production-secret-9c6f34d220a2
SCRAPER_INGEST_HMAC_SECRET=preflight-only-hmac-secret-32-bytes-minimum
SCRAPER_INGEST_ALLOWED_SOURCES=araujo,pacheco
SCRAPER_STATE_HOST_DIR=$test_dir/scraper-state
INDIANA_SCRAPER_ENABLED=true
OFFER_REFRESH_ENABLED=true
OFFER_REFRESH_SOURCES=indiana
OFFER_IMAGE_REFRESH_SOURCES=indiana
ARAUJO_SOURCE_MODE=DISABLED
ARAUJO_SCHEDULER_ENABLED=false
ARAUJO_PUBLIC_CATALOG_AUTHORIZED=false
ARAUJO_ACCESS_CONFIRMED=false
PACHECO_SOURCE_MODE=DISABLED
PACHECO_SCHEDULER_ENABLED=false
PACHECO_PUBLIC_API_AUTHORIZED=false
PACHECO_ACCESS_CONFIRMED=false
EOF

PRECOBAO_AZURE_ENV_FILE="$env_file" bash "$script_dir/preflight.sh"

sed 's/^INDIANA_SCRAPER_ENABLED=true$/INDIANA_SCRAPER_ENABLED=false/' \
  "$env_file" >"$test_dir/.env.indiana-disabled"
if PRECOBAO_AZURE_ENV_FILE="$test_dir/.env.indiana-disabled" \
  bash "$script_dir/preflight.sh" >"$test_dir/disabled.stdout" 2>"$test_dir/disabled.stderr"; then
  echo "Preflight unexpectedly allowed the required Indiana worker to be disabled." >&2
  exit 1
fi
if ! grep -q 'INDIANA_SCRAPER_ENABLED must remain enabled' "$test_dir/disabled.stderr"; then
  cat "$test_dir/disabled.stderr" >&2
  echo "Preflight failed for an unexpected reason." >&2
  exit 1
fi
echo "Preflight correctly rejected a disabled Indiana worker."
