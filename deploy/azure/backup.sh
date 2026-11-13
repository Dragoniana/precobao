#!/usr/bin/env bash
set -Eeuo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
env_file="${PRECOBAO_AZURE_ENV_FILE:-$script_dir/.env.azure}"
if [[ ! -f "$env_file" ]]; then
  echo "Missing Azure environment file: $env_file" >&2
  exit 2
fi
for command_name in docker python3 pg_dump gpg sha256sum; do
  command -v "$command_name" >/dev/null 2>&1 || { echo "Required tool is missing: $command_name" >&2; exit 2; }
done

# Compose loads secrets from the protected env file; never print or pass the URL
# as a pg_dump argument. The helper exports libpq variables and execs pg_dump.
set -a
# shellcheck disable=SC1090
source "$env_file"
set +a
: "${BACKUP_GPG_RECIPIENT:?Set a GPG public-key recipient}"
: "${BACKUP_GPG_FINGERPRINT:?Set the expected full GPG fingerprint}"
backup_dir="${BACKUP_DIR:-/var/backups/preco-bao}"
umask 077
mkdir -p "$backup_dir"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
base="$backup_dir/preco-bao-$timestamp"
trap 'rm -f "$base.dump.tmp" "$base.images.json.tmp" "$base.manifest.tmp" "$base.dump.gpg.tmp" "$base.images.json.gpg.tmp"' EXIT
actual_fingerprint="$(gpg --batch --with-colons --list-keys "$BACKUP_GPG_RECIPIENT" | awk -F: '$1 == "fpr" { print $10; exit }')"
expected_fingerprint="${BACKUP_GPG_FINGERPRINT// /}"
[[ -n "$actual_fingerprint" && "${actual_fingerprint^^}" == "${expected_fingerprint^^}" ]] || {
  echo "Configured backup recipient does not match BACKUP_GPG_FINGERPRINT." >&2
  exit 2
}

python3 - <<'PY' | gpg --batch --yes --trust-model always --recipient "$BACKUP_GPG_RECIPIENT" --output "$base.dump.gpg.tmp" --encrypt
import os, sys
from urllib.parse import unquote, urlsplit

url = urlsplit(os.environ["DATABASE_URL"])
if url.scheme != "postgresql+psycopg2" or not url.hostname or not url.username or not url.password:
    raise SystemExit("DATABASE_URL is not a complete SQLAlchemy PostgreSQL URL")
query = dict(part.split("=", 1) for part in url.query.split("&") if "=" in part)
if query.get("sslmode") != "verify-full" or not query.get("sslrootcert"):
    raise SystemExit("Database backups require sslmode=verify-full and sslrootcert")
host_ca = os.environ.get("AZURE_PG_ROOT_CERT_PATH", query["sslrootcert"])
if not os.path.isfile(host_ca):
    raise SystemExit("The host PostgreSQL CA bundle is missing")
os.environ.update({
    "PGHOST": url.hostname,
    "PGPORT": str(url.port or 5432),
    "PGUSER": unquote(url.username),
    "PGPASSWORD": unquote(url.password),
    "PGDATABASE": url.path.lstrip("/"),
    "PGSSLMODE": query["sslmode"],
    "PGSSLROOTCERT": host_ca,
})
os.execvp("pg_dump", ["pg_dump", "--format=custom", "--no-owner", "--no-acl"])
PY

docker compose --project-name preco-bao-azure --project-directory "$script_dir" \
  --env-file "$env_file" -f "$script_dir/docker-compose.yml" \
  exec -T api python -c 'import os,shutil; p=os.environ["IMAGE_REFERENCE_STORE_PATH"]; shutil.copyfile(p, "/tmp/image-references-backup.json") if os.path.exists(p) else open("/tmp/image-references-backup.json","w").write("{\"schema\":\"preco-bao.image-registry\",\"version\":1,\"records\":[]}\")' \
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
gpg --batch --yes --trust-model always --recipient "$BACKUP_GPG_RECIPIENT" \
  --output "$base.images.json.gpg.tmp" --encrypt "$base.images.json.tmp"
mv "$base.dump.gpg.tmp" "$base.dump.gpg"
mv "$base.images.json.gpg.tmp" "$base.images.json.gpg"
(
  cd "$backup_dir"
  sha256sum "$(basename "$base.dump.gpg")" "$(basename "$base.images.json.gpg")" > "$(basename "$base.sha256")"
)
python3 - "$base.manifest.tmp" "$timestamp" "$BACKUP_GPG_FINGERPRINT" <<'PY'
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
echo "Encrypted backup created: $base (store an off-VM copy)."
