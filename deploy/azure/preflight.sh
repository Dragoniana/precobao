#!/usr/bin/env bash
set -Eeuo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
compose_file="$script_dir/docker-compose.yml"
env_file="${PRECOBAO_AZURE_ENV_FILE:-$script_dir/.env.azure}"
if [[ ! -f "$env_file" ]]; then
  echo "Missing Azure environment file: $env_file" >&2
  exit 2
fi
for command_name in docker python3; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Required preflight tool is missing: $command_name" >&2
    exit 2
  fi
done

docker compose --project-name preco-bao-azure --project-directory "$script_dir" \
  --env-file "$env_file" -f "$compose_file" config --format json |
  python3 -c '
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

config = json.load(sys.stdin)
services = config.get("services", {})
if set(services) != {"api", "caddy"}:
    raise SystemExit("Azure Compose must contain only FastAPI and Caddy.")
api, caddy = services["api"], services["caddy"]
if api.get("ports"):
    raise SystemExit("The FastAPI port must not be published to the VM host.")
published = {str(item.get("published")) for item in caddy.get("ports", [])}
if published != {"80", "443"}:
    raise SystemExit("Only Caddy may publish TCP ports 80 and 443.")
if "api_edge" not in api.get("networks", {}) or "api_edge" not in caddy.get("networks", {}):
    raise SystemExit("FastAPI and Caddy must share the isolated proxy network.")

environment = api.get("environment", {})
if environment.get("APP_ENV") != "production" or str(environment.get("DEBUG", "")).casefold() != "false":
    raise SystemExit("FastAPI must run with APP_ENV=production and DEBUG=false.")
jwt = environment.get("JWT_SECRET", "")
if len(jwt) < 32 or len(set(jwt)) < 12 or jwt.casefold().startswith(("replace-", "change-me", "test-", "dev-", "local-", "example-")):
    raise SystemExit("JWT_SECRET is weak or still contains an example value.")

database_url = urlsplit(environment.get("DATABASE_URL", ""))
if database_url.scheme != "postgresql+psycopg2" or not database_url.hostname or not database_url.hostname.endswith(".postgres.database.azure.com"):
    raise SystemExit("DATABASE_URL must use the managed Azure PostgreSQL server FQDN.")
query = parse_qs(database_url.query)
if query.get("sslmode") != ["verify-full"] or query.get("sslrootcert") != ["/run/secrets/azure-postgres-root-ca.pem"]:
    raise SystemExit("DATABASE_URL must verify the Azure PostgreSQL TLS certificate and hostname.")
if not database_url.username or not database_url.password or not database_url.path.strip("/"):
    raise SystemExit("DATABASE_URL must include the managed database name and credentials.")

try:
    origins = json.loads(environment.get("BACKEND_CORS_ORIGINS", ""))
except json.JSONDecodeError as error:
    raise SystemExit("BACKEND_CORS_ORIGINS must be a JSON array of exact HTTPS origins.") from error
if not origins or any(
    not isinstance(origin, str)
    or urlsplit(origin).scheme != "https"
    or not urlsplit(origin).netloc
    or urlsplit(origin).username
    or urlsplit(origin).password
    or urlsplit(origin).path not in ("", "/")
    or urlsplit(origin).query
    or urlsplit(origin).fragment
    for origin in origins
):
    raise SystemExit("BACKEND_CORS_ORIGINS must contain exact HTTPS origins only.")
api_domain = caddy.get("environment", {}).get("API_DOMAIN", "")
api_url = urlsplit(f"https://{api_domain}")
if (
    not api_domain
    or ".invalid" in api_domain.casefold()
    or api_url.hostname != api_domain
    or api_domain.casefold() == "localhost"
    or "*" in api_domain
):
    raise SystemExit("Replace API_DOMAIN with a hostname under your control.")
try:
    import ipaddress

    ipaddress.ip_address(api_domain)
except ValueError:
    pass
else:
    raise SystemExit("API_DOMAIN must be a DNS hostname, not an IP address.")
if not any(urlsplit(origin).hostname != api_domain for origin in origins):
    raise SystemExit("CORS must contain the distinct Azure Static Web Apps origin.")

cert_mounts = [m for m in api.get("volumes", []) if m.get("target") == "/run/secrets/azure-postgres-root-ca.pem"]
if len(cert_mounts) != 1 or cert_mounts[0].get("type") != "bind" or not Path(cert_mounts[0].get("source", "")).is_file():
    raise SystemExit("The configured Azure PostgreSQL CA bundle must exist on the VM.")
if not any(m.get("type") == "volume" and m.get("target") == "/app/data/image_references" for m in api.get("volumes", [])):
    raise SystemExit("The image reference JSON must use a persistent named volume.")
print("Azure preflight passed. No images were built, no containers were started, and no Azure resources were changed.")
'
