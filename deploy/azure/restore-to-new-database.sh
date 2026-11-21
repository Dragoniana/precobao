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
for command_name in docker gpg python3 sha256sum; do
  command -v "$command_name" >/dev/null 2>&1 || { echo "Required tool is missing: $command_name" >&2; exit 2; }
done
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
env_file="${PRECOBAO_AZURE_ENV_FILE:-$script_dir/.env.azure}"
[[ -f "$env_file" ]] || { echo "Set PRECOBAO_AZURE_ENV_FILE to the protected .env.azure file." >&2; exit 2; }
postgres_client_image="${POSTGRES_CLIENT_IMAGE:-$(python3 - "$env_file" "$script_dir/postgres-client-env.py" <<'PY'
import runpy, sys
from pathlib import Path
module=runpy.run_path(sys.argv[2]); values=module["read_env"](Path(sys.argv[1]))
print(values.get("POSTGRES_CLIENT_IMAGE", "postgres:15.19-alpine"))
PY
)}"
docker image inspect "$postgres_client_image" >/dev/null 2>&1 || {
  echo "Required PostgreSQL client image is not present locally: $postgres_client_image" >&2
  exit 2
}
tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/preco-bao-restore.XXXXXX")"
chmod 700 "$tmp_dir"
trap 'rm -rf -- "$tmp_dir"' EXIT
pg_env_file="$tmp_dir/postgres.env"
pgpass_file="$tmp_dir/pgpass"
python3 "$script_dir/postgres-client-env.py" --env-file "$env_file" \
  --url-key RESTORE_DATABASE_URL --compare-url-key DATABASE_URL \
  --output-env "$pg_env_file" --output-passfile "$pgpass_file"
ca_path="$(python3 - "$env_file" "$script_dir/postgres-client-env.py" <<'PY'
import runpy, sys
from pathlib import Path
module=runpy.run_path(sys.argv[2]); values=module["read_env"](Path(sys.argv[1]))
print(values["AZURE_PG_ROOT_CERT_PATH"])
PY
)"
POSTGRES_CLIENT_IMAGE="$postgres_client_image" "$script_dir/verify-backup.sh" "$base"
pg_run=(docker run --rm --network host --env-file "$pg_env_file"
  -v "$pgpass_file:/run/secrets/pgpass:ro"
  -v "$ca_path:/run/secrets/azure-postgres-root-ca.pem:ro"
  --entrypoint)
target_objects="$("${pg_run[@]}" psql "$postgres_client_image" --no-psqlrc --tuples-only --no-align \
  --command "SELECT (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%' AND n.nspname NOT LIKE 'pg_temp%') + (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema'))")"
[[ "$target_objects" == "0" ]] || {
  echo "Restore target contains database objects; nothing was restored." >&2
  exit 2
}
server_version="$("${pg_run[@]}" psql "$postgres_client_image" --no-psqlrc --tuples-only --no-align --command 'SHOW server_version_num')"
client_major="$("${pg_run[@]}" pg_restore "$postgres_client_image" --version | sed -E 's/.* ([0-9]+)\..*/\1/')"
server_major="$((server_version / 10000))"
[[ "$client_major" == "$server_major" ]] || {
  echo "PostgreSQL restore client major $client_major does not match target server major $server_major." >&2
  exit 2
}
# The client image is pinned to the target server's major version. In
# particular, pg_restore 15 does not emit the PG17-only transaction_timeout SET.
gpg --batch --decrypt "$base.dump.gpg" | "${pg_run[@]}" pg_restore "$postgres_client_image" \
  --no-owner --no-acl --exit-on-error --single-transaction -
echo "Database restored to the explicitly configured separate target. Restore image registry separately after stopping the API."
