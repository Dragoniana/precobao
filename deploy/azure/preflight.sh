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
import stat
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

config = json.load(sys.stdin)
services = config.get("services", {})
required_services = {
    "api", "web", "caddy", "scraper-indiana", "offer-refresh",
    "araujo-scheduler", "pacheco-scheduler",
}
if set(services) != required_services:
    raise SystemExit("Azure Compose services do not match the reviewed production set.")
api, web, caddy = services["api"], services["web"], services["caddy"]
environment = api.get("environment", {})
smtp_starttls = str(environment.get("SMTP_STARTTLS", "")).casefold()
try:
    smtp_port = int(environment.get("SMTP_PORT", ""))
except (TypeError, ValueError):
    raise SystemExit("SMTP_PORT must be a valid port number.")
reset_url = urlsplit(str(environment.get("PASSWORD_RESET_URL", "")))
if (
    not environment.get("SMTP_HOST")
    or not environment.get("SMTP_USERNAME")
    or not environment.get("SMTP_PASSWORD")
    or not environment.get("SMTP_FROM_EMAIL")
    or smtp_port != 587
    or smtp_starttls not in {"1", "true", "yes", "on"}
    or reset_url.scheme != "https"
    or reset_url.hostname != "precobao.app"
    or reset_url.path != "/reset-password"
    or reset_url.username
    or reset_url.password
    or reset_url.query
    or reset_url.fragment
):
    raise SystemExit("SMTP password recovery settings must remain configured with HTTPS STARTTLS.")
api_image_mount = next(
    (m for m in api.get("volumes", []) if m.get("target") == "/app/data/image_references"),
    None,
)
if not api_image_mount or api_image_mount.get("type") != "volume":
    raise SystemExit("The image reference JSON must use a persistent named volume.")
if api.get("ports"):
    raise SystemExit("The FastAPI port must not be published to the VM host.")
published = {str(item.get("published")) for item in caddy.get("ports", [])}
if published != {"80", "443"}:
    raise SystemExit("Only Caddy may publish TCP ports 80 and 443.")
if web.get("ports") or web.get("restart") != "unless-stopped":
    raise SystemExit("The web service must have no host ports and restart automatically.")
if not web.get("healthcheck"):
    raise SystemExit("Caddy must wait on a frontend health check.")
public_api_url = urlsplit(web.get("build", {}).get("args", {}).get("EXPO_PUBLIC_API_URL", ""))
api_domain = caddy.get("environment", {}).get("API_DOMAIN", "")
if (
    public_api_url.scheme != "https"
    or public_api_url.hostname != api_domain
    or public_api_url.path.rstrip("/") != "/backend"
    or public_api_url.username
    or public_api_url.password
    or public_api_url.query
    or public_api_url.fragment
):
    raise SystemExit("The web build must use the public HTTPS /backend URL for API_DOMAIN.")
if "api_edge" not in api.get("networks", {}) or "api_edge" not in caddy.get("networks", {}):
    raise SystemExit("FastAPI and Caddy must share the isolated proxy network.")
if "api_edge" not in web.get("networks", {}):
    raise SystemExit("The frontend must share the private Caddy network.")
for worker_name in (
    "scraper-indiana", "offer-refresh", "araujo-scheduler", "pacheco-scheduler"
):
    worker = services[worker_name]
    if worker.get("ports"):
        raise SystemExit(f"The {worker_name} worker must not publish host ports.")
    if worker.get("restart") != "unless-stopped":
        raise SystemExit(f"The {worker_name} worker must restart automatically.")
    if worker.get("environment", {}).get("DATABASE_URL") != environment.get("DATABASE_URL"):
        raise SystemExit(f"The {worker_name} worker must use the configured managed database.")
    worker_image_mount = next(
        (m for m in worker.get("volumes", []) if m.get("target") == "/app/data/image_references"),
        None,
    )
    if not worker_image_mount or worker_image_mount.get("source") != api_image_mount.get("source"):
        raise SystemExit(f"The {worker_name} worker must share the persistent image registry with FastAPI.")
    if not any(m.get("target") == "/run/secrets/azure-postgres-root-ca.pem" and m.get("read_only") for m in worker.get("volumes", [])):
        raise SystemExit(f"The {worker_name} worker must verify PostgreSQL TLS using the read-only CA bundle.")
    if worker.get("depends_on", {}).get("api", {}).get("condition") != "service_healthy":
        raise SystemExit(f"The {worker_name} worker must wait until FastAPI/database readiness.")
for service_name, variable in (
    ("scraper-indiana", "INDIANA_SCRAPER_ENABLED"),
    ("offer-refresh", "OFFER_REFRESH_ENABLED"),
    ("araujo-scheduler", "ARAUJO_SCHEDULER_ENABLED"),
    ("pacheco-scheduler", "PACHECO_SCHEDULER_ENABLED"),
):
    if variable not in services[service_name].get("environment", {}):
        raise SystemExit(f"{variable} must be explicitly configured.")
for service_name, variable in (
    ("scraper-indiana", "INDIANA_SCRAPER_ENABLED"),
    ("offer-refresh", "OFFER_REFRESH_ENABLED"),
):
    worker_enabled = str(
        services[service_name].get("environment", {}).get(variable, "false")
    ).casefold()
    if worker_enabled not in {"1", "true", "yes", "on"}:
        raise SystemExit(f"{variable} must remain enabled for production.")
for pharmacy in ("ARAUJO", "PACHECO"):
    service_name = f"{pharmacy.lower()}-scheduler"
    settings = services[service_name].get("environment", {})
    mode = str(settings.get(f"{pharmacy}_SOURCE_MODE", "DISABLED")).upper()
    if mode not in {"DIRECT", "FEED", "DISABLED"}:
        raise SystemExit(f"{pharmacy}_SOURCE_MODE must be DIRECT, FEED or DISABLED.")
    if mode == "DIRECT":
        for variable in (
            f"{pharmacy}_PUBLIC_CATALOG_AUTHORIZED"
            if pharmacy == "ARAUJO"
            else f"{pharmacy}_PUBLIC_API_AUTHORIZED",
            f"{pharmacy}_ACCESS_CONFIRMED",
        ):
            value = str(settings.get(variable, "false")).casefold()
            if value not in {"1", "true", "yes", "on"}:
                raise SystemExit(
                    f"{variable} must be true before enabling {pharmacy} DIRECT mode."
                )
for service_name, state_path in (
    ("scraper-indiana", "/app/data/scraper_state"),
    ("offer-refresh", "/app/data/offer_refresh"),
    ("araujo-scheduler", "/app/data/araujo"),
    ("pacheco-scheduler", "/app/data/pacheco"),
):
    if not any(
        m.get("target") == state_path
        and m.get("type") in {"volume", "bind"}
        and (m.get("type") == "volume" or Path(m.get("source", "")).is_dir())
        for m in services[service_name].get("volumes", [])
    ):
        raise SystemExit(f"The {service_name} checkpoint must use persistent storage.")

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

hmac_file = environment.get("SCRAPER_INGEST_HMAC_SECRET_FILE", "").strip()
legacy_hmac = environment.get("SCRAPER_INGEST_HMAC_SECRET", "").encode("utf-8")
if hmac_file:
    if legacy_hmac:
        raise SystemExit("When a protected HMAC file is configured, the legacy secret environment value must be empty.")
    secret_mount = next(
        (m for m in api.get("volumes", []) if m.get("target") == hmac_file), None
    )
    if not secret_mount or secret_mount.get("type") != "bind" or not secret_mount.get("read_only"):
        raise SystemExit("The configured HMAC file must be a read-only bind mount.")
    secret_path = Path(secret_mount.get("source", ""))
    if not secret_path.is_file() or secret_path.is_symlink():
        raise SystemExit("The configured HMAC secret file is missing or invalid.")
    secret_stat = secret_path.stat()
    secret_mode = stat.S_IMODE(secret_stat.st_mode)
    if (
        secret_stat.st_uid != 0
        or secret_stat.st_gid != 20000
        or secret_mode != 0o440
        or "20000" not in {str(group) for group in api.get("group_add", [])}
    ):
        raise SystemExit("The HMAC file must be root-owned, mode 0440, and readable only by the API group.")
    if len(secret_path.read_bytes().rstrip(b"\r\n")) < 32:
        raise SystemExit("The configured HMAC secret file is empty or too short.")
elif len(legacy_hmac) < 32:
    raise SystemExit("Configure a 32-byte HMAC secret using a protected file or legacy environment value.")

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
if api_domain not in {urlsplit(origin).hostname for origin in origins}:
    raise SystemExit("CORS must allow the exact HTTPS origin hosting the web app.")

caddyfile = Path(sys.argv[1]).read_text(encoding="utf-8")
for required_route in (
    "handle_path /backend/*",
    "reverse_proxy api:8000",
    "reverse_proxy web:80",
    "redir https://precobao.app{uri} permanent",
):
    if required_route not in caddyfile:
        raise SystemExit(f"Caddyfile is missing the reviewed route: {required_route}")

cert_mounts = [m for m in api.get("volumes", []) if m.get("target") == "/run/secrets/azure-postgres-root-ca.pem"]
if len(cert_mounts) != 1 or cert_mounts[0].get("type") != "bind" or not Path(cert_mounts[0].get("source", "")).is_file():
    raise SystemExit("The configured Azure PostgreSQL CA bundle must exist on the VM.")
if not any(m.get("type") == "volume" and m.get("target") == "/app/data/image_references" for m in api.get("volumes", [])):
    raise SystemExit("The image reference JSON must use a persistent named volume.")
print("Azure preflight passed. No images were built, no containers were started, and no Azure resources were changed.")
' "$script_dir/Caddyfile"
