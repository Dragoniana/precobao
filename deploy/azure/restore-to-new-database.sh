#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 /absolute/path/to/preco-bao-TIMESTAMP (without suffix)" >&2
  exit 2
fi
if [[ "${RESTORE_CONFIRMATION:-}" != "RESTORE_TO_A_NEW_EMPTY_DATABASE" ]]; then
  echo "Set RESTORE_CONFIRMATION=RESTORE_TO_A_NEW_EMPTY_DATABASE after creating a separate empty target." >&2
  exit 2
fi
base="$1"
for command_name in gpg pg_restore psql python3 sha256sum; do
  command -v "$command_name" >/dev/null 2>&1 || { echo "Required tool is missing: $command_name" >&2; exit 2; }
done
env_file="${PRECOBAO_AZURE_ENV_FILE:-$(dirname "$0")/.env.azure}"
[[ -f "$env_file" ]] || { echo "Set PRECOBAO_AZURE_ENV_FILE to the protected .env.azure file." >&2; exit 2; }
set -a
# shellcheck disable=SC1090
source "$env_file"
set +a
: "${RESTORE_DATABASE_URL:?Set a separate managed PostgreSQL restore target}"
: "${AZURE_PG_ROOT_CERT_PATH:?Set the VM's trusted PostgreSQL CA bundle}"
python3 - <<'PY'
import os
from pathlib import Path
from urllib.parse import unquote, urlsplit

source=urlsplit(os.environ["DATABASE_URL"])
target=urlsplit(os.environ["RESTORE_DATABASE_URL"])
if source.hostname == target.hostname:
    raise SystemExit("Restore target must be a different managed PostgreSQL server")
if target.scheme != "postgresql+psycopg2" or not target.hostname or not target.username or not target.password:
    raise SystemExit("RESTORE_DATABASE_URL must be a complete PostgreSQL URL")
query=dict(part.split("=",1) for part in target.query.split("&") if "=" in part)
if query.get("sslmode") != "verify-full" or not Path(os.environ["AZURE_PG_ROOT_CERT_PATH"]).is_file():
    raise SystemExit("Restore requires TLS hostname verification and an existing CA bundle")
os.environ.update({"PGHOST":target.hostname,"PGPORT":str(target.port or 5432),"PGUSER":unquote(target.username),"PGPASSWORD":unquote(target.password),"PGDATABASE":unquote(target.path.lstrip("/")),"PGSSLMODE":"verify-full","PGSSLROOTCERT":os.environ["AZURE_PG_ROOT_CERT_PATH"]})
PY
"$(dirname "$0")/verify-backup.sh" "$base"
python3 - "$base.dump.gpg" <<'PY'
import os, subprocess, sys
check=subprocess.run(["psql","--no-psqlrc","--tuples-only","--no-align","--command","SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema');"], check=True, text=True, capture_output=True)
if check.stdout.strip() != "0":
    raise SystemExit("Restore target is not empty; no database objects were changed")
decrypt=subprocess.Popen(["gpg","--batch","--decrypt",sys.argv[1]],stdout=subprocess.PIPE)
try:
    restore=subprocess.run(["pg_restore","--no-owner","--no-acl","--exit-on-error","--single-transaction","-"],stdin=decrypt.stdout,check=False)
finally:
    decrypt.stdout.close()
status=decrypt.wait()
if status or restore.returncode:
    raise SystemExit(restore.returncode or status)
PY
echo "Database restored to the explicitly configured separate target. Restore image registry separately after stopping the API."
