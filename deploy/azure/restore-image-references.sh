#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 /absolute/path/to/preco-bao-TIMESTAMP (without suffix)" >&2
  exit 2
fi
if [[ "${CONFIRM_IMAGE_REGISTRY_RESTORE:-}" != "REPLACE_IMAGE_REGISTRY_WHILE_API_STOPPED" ]]; then
  echo "Stop the API and set CONFIRM_IMAGE_REGISTRY_RESTORE=REPLACE_IMAGE_REGISTRY_WHILE_API_STOPPED." >&2
  exit 2
fi
base="$1"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
env_file="${PRECOBAO_AZURE_ENV_FILE:-$script_dir/.env.azure}"
[[ -f "$env_file" ]] || { echo "Missing protected Azure environment file." >&2; exit 2; }
command -v gpg >/dev/null 2>&1 && command -v docker >/dev/null 2>&1 || { echo "gpg and Docker are required." >&2; exit 2; }
"$script_dir/verify-backup.sh" "$base"
gpg --batch --decrypt "$base.images.json.gpg" | docker compose --project-name preco-bao-azure \
  --project-directory "$script_dir" --env-file "$env_file" \
  -f "$script_dir/docker-compose.yml" run --rm --no-deps -T api python -c '
import json,os,tempfile,sys
from pathlib import Path
data=json.load(sys.stdin)
if data.get("schema")!="preco-bao.image-registry" or data.get("version")!=1 or not isinstance(data.get("records"),list):
    raise SystemExit("Invalid image registry archive")
target=Path(os.environ["IMAGE_REFERENCE_STORE_PATH"])
target.parent.mkdir(parents=True,exist_ok=True)
fd,name=tempfile.mkstemp(prefix=".images-restore-",suffix=".tmp",dir=target.parent)
try:
    with os.fdopen(fd,"w",encoding="utf-8") as stream:
        json.dump(data,stream,ensure_ascii=False,indent=2); stream.flush(); os.fsync(stream.fileno())
    os.chmod(name,0o600); os.replace(name,target)
finally:
    if os.path.exists(name): os.unlink(name)
print("Image reference registry restored atomically.")
'
echo "Image registry restore completed. Keep the API stopped until operator verification is complete."
