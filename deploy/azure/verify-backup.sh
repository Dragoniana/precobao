#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 /absolute/path/to/preco-bao-TIMESTAMP (without suffix)" >&2
  exit 2
fi
base="$1"
for file in "$base.dump.gpg" "$base.images.json.gpg" "$base.sha256" "$base.manifest.json"; do
  [[ -s "$file" ]] || { echo "Missing backup artifact: $file" >&2; exit 2; }
done
for command_name in gpg pg_restore python3 sha256sum; do
  command -v "$command_name" >/dev/null 2>&1 || { echo "Required tool is missing: $command_name" >&2; exit 2; }
done
(cd "$(dirname "$base")" && sha256sum --check "$(basename "$base.sha256")")
umask 077
check_dir="$(mktemp -d "${TMPDIR:-/tmp}/preco-bao-backup-check.XXXXXX")"
trap 'rm -rf "$check_dir"' EXIT
dump_check="$check_dir/database.dump"
images_check="$check_dir/images.json"
gpg --batch --decrypt --output "$dump_check" "$base.dump.gpg"
pg_restore --list "$dump_check" >/dev/null
gpg --batch --decrypt --output "$images_check" "$base.images.json.gpg"
python3 - "$images_check" <<'PY'
import json,sys
with open(sys.argv[1], encoding="utf-8") as handle:
    data=json.load(handle)
assert data.get("schema")=="preco-bao.image-registry" and data.get("version")==1 and isinstance(data.get("records"),list)
print("Image registry archive valid ({} records).".format(len(data["records"])))
PY
python3 - "$base.manifest.json" <<'PY'
import json, sys
from pathlib import Path
manifest=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert manifest.get("format")=="preco-bao.azure-backup.v1"
assert manifest.get("database_archive") and manifest.get("image_registry_archive")
print("Backup manifest valid.")
PY
