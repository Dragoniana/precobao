#!/usr/bin/env bash
set -Eeuo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
env_file="${PRECOBAO_AZURE_ENV_FILE:-$script_dir/.env.azure}"
if [[ "${CONFIRM_DATABASE_MIGRATION:-}" != "BACKUP_VERIFIED_AND_MIGRATE" ]]; then
  echo "Set CONFIRM_DATABASE_MIGRATION=BACKUP_VERIFIED_AND_MIGRATE after verifying a recent backup and the target server." >&2
  exit 2
fi
bash "$script_dir/preflight.sh"
api_image="${API_IMAGE:-}"
[[ -n "$api_image" ]] || {
  echo "Set API_IMAGE to the versioned candidate image before running migrations." >&2
  exit 2
}

tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/preco-bao-migrate.XXXXXX")"
chmod 700 "$tmp_dir"
trap 'rm -rf -- "$tmp_dir"' EXIT
compose_config="$tmp_dir/compose.json"
docker compose --project-name preco-bao-azure --project-directory "$script_dir" \
  --env-file "$env_file" -f "$script_dir/docker-compose.yml" config --format json > "$compose_config"
python3 - "$compose_config" "$tmp_dir/api.env" "$tmp_dir/mounts.json" <<'PY'
import json, os, sys
from pathlib import Path

config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
api = config["services"]["api"]
environment = api.get("environment", {})
if not environment.get("DATABASE_URL"):
    raise SystemExit("The candidate migration environment has no DATABASE_URL.")
for target in (
    "/run/secrets/azure-postgres-root-ca.pem",
    "/run/secrets/scraper_ingest_hmac",
):
    mount = next((item for item in api.get("volumes", []) if item.get("target") == target), None)
    if not mount or mount.get("type") != "bind" or not mount.get("read_only"):
        raise SystemExit("Required migration secret mounts must be read-only bind mounts.")
    if not Path(mount.get("source", "")).is_file():
        raise SystemExit("A required migration secret mount source is missing.")
    if target.endswith("azure-postgres-root-ca.pem"):
        ca_path = mount["source"]
    else:
        hmac_path = mount["source"]

env_path = Path(sys.argv[2])
with env_path.open("w", encoding="utf-8", newline="\n") as stream:
    for key, value in environment.items():
        value = "" if value is None else str(value)
        if "\n" in value or "\r" in value:
            raise SystemExit("The migration environment contains a multiline value.")
        stream.write(f"{key}={value}\n")
os.chmod(env_path, 0o600)
Path(sys.argv[3]).write_text(json.dumps({"ca": ca_path, "hmac": hmac_path}), encoding="utf-8")
os.chmod(sys.argv[3], 0o600)
PY
readarray -t secret_mounts < <(python3 - "$tmp_dir/mounts.json" <<'PY'
import json, sys
mounts=json.load(open(sys.argv[1], encoding="utf-8"))
print(mounts["ca"])
print(mounts["hmac"])
PY
)
docker run --rm --network bridge --env-file "$tmp_dir/api.env" --group-add 20000 \
  --mount "type=bind,src=${secret_mounts[0]},dst=/run/secrets/azure-postgres-root-ca.pem,readonly" \
  --mount "type=bind,src=${secret_mounts[1]},dst=/run/secrets/scraper_ingest_hmac,readonly" \
  --entrypoint alembic "$api_image" upgrade head
