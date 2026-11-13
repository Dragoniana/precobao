#!/usr/bin/env bash
set -Eeuo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
env_file="${PRECOBAO_AZURE_ENV_FILE:-$script_dir/.env.azure}"
if [[ "${CONFIRM_DATABASE_MIGRATION:-}" != "BACKUP_VERIFIED_AND_MIGRATE" ]]; then
  echo "Set CONFIRM_DATABASE_MIGRATION=BACKUP_VERIFIED_AND_MIGRATE after verifying a recent backup and the target server." >&2
  exit 2
fi
"$script_dir/preflight.sh"
docker compose --project-name preco-bao-azure --project-directory "$script_dir" \
  --env-file "$env_file" -f "$script_dir/docker-compose.yml" \
  run --rm --no-deps api alembic upgrade head
