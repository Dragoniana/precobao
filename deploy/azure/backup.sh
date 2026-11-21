#!/usr/bin/env bash
set -Eeuo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
env_file="${PRECOBAO_AZURE_ENV_FILE:-$script_dir/.env.azure}"
if [[ ! -f "$env_file" ]]; then
  echo "Missing Azure environment file: $env_file" >&2
  exit 2
fi
for command_name in docker python3 gpg sha256sum; do
  command -v "$command_name" >/dev/null 2>&1 || { echo "Required tool is missing: $command_name" >&2; exit 2; }
done

# Compose loads secrets from the protected env file; never print or pass the URL
# as a pg_dump argument. The helper exports libpq variables and execs pg_dump.
# Parse only the required values; sourcing the full production env file can
# execute shell syntax and breaks on ordinary values containing spaces.
read_env_value() {
  python3 - "$env_file" "$1" "$2" "$script_dir/postgres-client-env.py" <<'PY'
import runpy, sys
from pathlib import Path
module = runpy.run_path(sys.argv[4])
values = module["read_env"](Path(sys.argv[1]))
print(values.get(sys.argv[2], sys.argv[3]))
PY
}
backup_gpg_recipient="$(read_env_value BACKUP_GPG_RECIPIENT '')"
backup_gpg_fingerprint="$(read_env_value BACKUP_GPG_FINGERPRINT '')"
backup_dir="$(read_env_value BACKUP_DIR /var/backups/preco-bao)"
postgres_client_image="$(read_env_value POSTGRES_CLIENT_IMAGE postgres:15.19-alpine)"
[[ -n "$backup_gpg_recipient" ]] || { echo "Set BACKUP_GPG_RECIPIENT in the protected environment file." >&2; exit 2; }
[[ -n "$backup_gpg_fingerprint" ]] || { echo "Set BACKUP_GPG_FINGERPRINT in the protected environment file." >&2; exit 2; }
[[ -n "$backup_dir" ]] || { echo "BACKUP_DIR must not be empty." >&2; exit 2; }
[[ -n "$postgres_client_image" ]] || { echo "POSTGRES_CLIENT_IMAGE must not be empty." >&2; exit 2; }
umask 077
mkdir -p "$backup_dir"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
base="$backup_dir/preco-bao-$timestamp"
[[ ! -e "$base.dump.gpg" && ! -e "$base.images.json.gpg" ]] || { echo "A backup with this UTC timestamp already exists." >&2; exit 2; }
tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/preco-bao-backup.XXXXXX")"
trap 'rm -rf -- "$tmp_dir"; rm -f "$base.manifest.tmp" "$base.dump.gpg.tmp" "$base.images.json.tmp" "$base.images.json.gpg.tmp"' EXIT
pg_env_file="$tmp_dir/postgres.env"
pgpass_file="$tmp_dir/pgpass"
ca_path="$(read_env_value AZURE_PG_ROOT_CERT_PATH '')"
python3 "$script_dir/postgres-client-env.py" --env-file "$env_file" --url-key DATABASE_URL \
  --output-env "$pg_env_file" --output-passfile "$pgpass_file"
actual_fingerprint="$(gpg --batch --with-colons --list-keys "$backup_gpg_recipient" | awk -F: '$1 == "fpr" { print $10; exit }')"
expected_fingerprint="${backup_gpg_fingerprint// /}"
[[ -n "$actual_fingerprint" && "${actual_fingerprint^^}" == "${expected_fingerprint^^}" ]] || {
  echo "Configured backup recipient does not match BACKUP_GPG_FINGERPRINT." >&2
  exit 2
}
docker image inspect "$postgres_client_image" >/dev/null 2>&1 || {
  echo "Required PostgreSQL client image is not present locally: $postgres_client_image" >&2
  exit 2
}
pg_run=(docker run --rm --network host --env-file "$pg_env_file"
  -v "$pgpass_file:/run/secrets/pgpass:ro"
  -v "$ca_path:/run/secrets/azure-postgres-root-ca.pem:ro"
  --entrypoint)
server_version="$("${pg_run[@]}" psql "$postgres_client_image" --no-psqlrc --tuples-only --no-align --command 'SHOW server_version_num')"
client_major="$("${pg_run[@]}" pg_dump "$postgres_client_image" --version | sed -E 's/.* ([0-9]+)\..*/\1/')"
server_major="$((server_version / 10000))"
[[ "$client_major" == "$server_major" ]] || {
  echo "PostgreSQL backup client major $client_major does not match server major $server_major." >&2
  exit 2
}

"${pg_run[@]}" pg_dump "$postgres_client_image" --format=custom --no-owner --no-acl \
  | gpg --batch --yes --trust-model always --recipient "$backup_gpg_recipient" --output "$base.dump.gpg.tmp" --encrypt

docker compose --project-name preco-bao-azure --project-directory "$script_dir" \
  --env-file "$env_file" -f "$script_dir/docker-compose.yml" \
  exec -T api python -c 'import json,os,shutil; p=os.environ["IMAGE_REFERENCE_STORE_PATH"]; shutil.copyfile(p, "/tmp/image-references-backup.json") if os.path.exists(p) else json.dump({"schema":"preco-bao.image-registry","version":1,"records":[]}, open("/tmp/image-references-backup.json", "w"))' \
  >/dev/null
docker compose --project-name preco-bao-azure --project-directory "$script_dir" \
  --env-file "$env_file" -f "$script_dir/docker-compose.yml" \
  cp api:/tmp/image-references-backup.json "$base.images.json.tmp"
chmod 600 "$base.images.json.tmp"
docker compose --project-name preco-bao-azure --project-directory "$script_dir" \
  --env-file "$env_file" -f "$script_dir/docker-compose.yml" \
  exec -T api python -c 'import os; os.unlink("/tmp/image-references-backup.json")'

python3 - "$base.images.json.tmp" <<'PY'
import json, sys
from pathlib import Path
data=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if data.get("schema")!="preco-bao.image-registry" or data.get("version")!=1 or not isinstance(data.get("records"),list):
    raise SystemExit("Image registry does not match the supported schema")
PY
gpg --batch --yes --trust-model always --recipient "$backup_gpg_recipient" \
  --output "$base.images.json.gpg.tmp" --encrypt "$base.images.json.tmp"
mv "$base.dump.gpg.tmp" "$base.dump.gpg"
mv "$base.images.json.gpg.tmp" "$base.images.json.gpg"
(
  cd "$backup_dir"
  sha256sum "$(basename "$base.dump.gpg")" "$(basename "$base.images.json.gpg")" > "$(basename "$base.sha256")"
)
python3 - "$base.manifest.tmp" "$timestamp" "$backup_gpg_fingerprint" <<'PY'
import json, sys
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({
    "format": "preco-bao.azure-backup.v1",
    "created_at_utc": sys.argv[2],
    "gpg_recipient_fingerprint": sys.argv[3],
    "database_archive": Path(sys.argv[1]).with_name(Path(sys.argv[1]).name.replace(".manifest.tmp", ".dump.gpg")).name,
    "image_registry_archive": Path(sys.argv[1]).with_name(Path(sys.argv[1]).name.replace(".manifest.tmp", ".images.json.gpg")).name,
}, indent=2) + "\n", encoding="utf-8")
PY
mv "$base.manifest.tmp" "$base.manifest.json"
chmod 600 "$base".*
POSTGRES_CLIENT_IMAGE="$postgres_client_image" bash "$script_dir/verify-backup.sh" "$base"
echo "Encrypted local backup created and structurally verified: $base. A restore test is still required before migration."
