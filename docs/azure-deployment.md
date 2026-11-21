# Preço Bão on Azure — deployment runbook

This runbook describes the current `Dragoniana/precobao` deployment on:

- An Azure Linux VM running the FastAPI API, the static Expo web frontend in the `web` container, and Caddy as reverse proxy.
- Azure Database for PostgreSQL Flexible Server as an external managed database.
- Android clients calling the same public HTTPS API hostname.

The live `precobao.app` domain currently resolves to the VM, and Caddy serves the web container at `/` and routes `/backend/*` to FastAPI. The repository's `deploy/azure/docker-compose.yml` is the intended Azure stack. The generic `deploy/production/docker-compose.yml` includes local PostgreSQL and is not the Azure stack. Confirm the actual checkout, Compose project, and running image on the VM before any release; this document does not assert that the VM is already running the current `main` commit.

## 1. Network and DNS

1. Create the VM and Flexible Server in a private network design. Prefer Flexible Server private VNet integration with private DNS; attach the VM to a VNet that can resolve and reach the server's private FQDN. Do not enable public PostgreSQL access. Permit TCP 5432 only from the VM subnet/security group and keep SSH restricted to the operator's management source.
2. Give the VM a stable public IP and point the web/API domain to it. Permit inbound TCP 80/443 to Caddy; do not publish API port 8000. Caddy obtains and renews the public HTTPS certificate.
3. Keep the web origin and API base URL explicit: the web site uses `https://precobao.app`, while its API base URL is `https://precobao.app/backend`. Set `BACKEND_CORS_ORIGINS` to the exact web origin; do not use wildcard CORS.

Azure PostgreSQL private network DNS and TLS requirements are described in Microsoft's [Flexible Server networking documentation](https://learn.microsoft.com/en-us/azure/postgresql/flexible-server/concepts-networking) and [TLS connection guide](https://learn.microsoft.com/en-us/azure/postgresql/security/security-tls-how-to-connect). The application URL enforces `sslmode=verify-full` and points to the server's canonical `*.postgres.database.azure.com` FQDN. Mount the current trusted Azure PostgreSQL CA bundle read-only at `/run/secrets/azure-postgres-root-ca.pem`; do not disable hostname or certificate verification.

## 2. Configure the VM

Install Docker Engine with the Compose plugin, Python 3, GnuPG, and `sha256sum`. Backup/restore scripts run PostgreSQL client tools from the versioned Docker image configured by `POSTGRES_CLIENT_IMAGE` (default `postgres:15.19-alpine`), so `pg_dump`/`pg_restore` match the PostgreSQL 15 server without host-client drift. Provision DNS/networking first. Clone this repository on the VM and:

```sh
cd deploy/azure
cp env.example .env.azure
chmod 600 .env.azure
```

Edit `.env.azure` locally on the VM. Set the API hostname, exact web origin in `BACKEND_CORS_ORIGINS`, `EXPO_PUBLIC_API_URL=https://precobao.app/backend`, strong unique JWT secret, private PostgreSQL URL, host CA bundle path, and a private Compose subnet that does not overlap the VNet. URL-encode special characters in PostgreSQL username/password. Keep `.env.azure`, private keys, CA bundles, database dumps and backup archives out of Git. The tracked example uses reserved `.invalid` placeholders and must fail preflight until replaced.

Copy the Azure PostgreSQL CA certificate/bundle from Microsoft's supported certificate distribution to a root-controlled path, and set `AZURE_PG_ROOT_CERT_PATH` to that host path. The container mounts it read-only; host backup tools use the host path. Rotate database and JWT credentials through the VM secret file and Azure PostgreSQL controls, not source control.

## 3. Database and API start

The Azure Compose file contains FastAPI (`api`), the web frontend (`web`), Caddy (`caddy`) and private background workers. FastAPI and `web` are reachable only on the internal Compose network. Caddy is the only service publishing ports 80 and 443. PostgreSQL is managed separately in Azure. Named Docker volumes persist the image reference registry and each worker's resumable state independently from PostgreSQL.

Before the first migration, create the managed database and a least-privilege application user. Take and verify an Azure managed backup or another approved recoverable copy first. The migration script requires a deliberate shell confirmation and never migrates as a side effect of starting the stack:

```sh
./preflight.sh
CONFIRM_DATABASE_MIGRATION=BACKUP_VERIFIED_AND_MIGRATE ./migrate.sh
docker compose --project-name preco-bao-azure --env-file .env.azure up -d --build
docker compose --project-name preco-bao-azure --env-file .env.azure ps
```

Run those commands only after reviewing the target server and maintenance window. Readiness is exposed at `/health/ready`; confirm it over HTTPS after startup. Do not expose port 8000 or PostgreSQL publicly. Apply OS security updates, SSH key-only access, firewall rules, Docker log rotation and host monitoring as part of VM operations.

The Compose startup also enables:

- `offer-refresh`: every 30 minutes, resumes bounded price observations for existing Indiana/Araújo offers and image lookup for existing Indiana/Araújo/Pacheco offers. Price changes are written to offer and history tables; unchanged prices do not create duplicate history events. The worker shares the persistent image registry with FastAPI.
- `pacheco-scheduler`: runs the existing full Pacheco catalog scraper immediately on first startup, then every 24 hours (with a 60-minute retry after an incomplete run). This can insert/update Pacheco catalog entries, offers, images and price history in the configured database. The worker keeps its checkpoint in a named volume.

Both workers use the same managed PostgreSQL URL with the mounted read-only CA bundle, wait for API/database readiness, have bounded CPU/memory, and publish no host ports. Their logs and resumable state can be checked with:

```sh
docker compose --project-name preco-bao-azure --env-file .env.azure logs --tail=200 offer-refresh pacheco-scheduler
docker compose --project-name preco-bao-azure --env-file .env.azure exec offer-refresh python -c 'import json,pathlib; p=pathlib.Path("/app/data/offer_refresh"); print("\\n".join(str(f.name)+": "+str(json.loads(f.read_text()).get("checked",0))+" checked" for f in sorted(p.glob("*.json"))))'
docker compose --project-name preco-bao-azure --env-file .env.azure exec pacheco-scheduler python -c 'import json, pathlib; p=pathlib.Path("/app/data/pacheco/checkpoint.json"); print(json.dumps({k: json.loads(p.read_text()).get(k) for k in ("status", "products_enqueued", "pages_succeeded", "finished_at", "stop_reason")}, ensure_ascii=False) if p.exists() else "first run pending")'
```

The price/image refresh worker does not discover new Indiana or Araújo catalog items. The Pacheco job only discovers products offered by Pacheco. Therefore this deployment change restores automatic behavior for existing offers and Pacheco discovery, but does not copy the development PostgreSQL database or claim identical catalog counts across environments. A count difference must be diagnosed against the source database and pharmacy coverage; it must not be fixed by copying local user data or a development dump into production.

## 4. Web frontend release and Android

`frontend/Dockerfile.web` exports the Expo Router site and packages it in Nginx. Caddy serves `web:80` at the site root and sends `/backend/*` to `api:8000`. The legacy GitHub Actions workflow `.github/workflows/azure-static-web-apps.yml` is not the production deployment path and must remain disabled; do not set `PRECOBAO_AZURE_STATIC_WEB_APPS_ENABLED=true` for VM releases. No Static Web App token is used by this deployment.

For a local release artifact, use the production API base URL and verify the export before connecting to the VM:

```sh
cd frontend
EXPO_PUBLIC_API_URL=https://precobao.app/backend npm ci
EXPO_PUBLIC_API_URL=https://precobao.app/backend npm test -- --runInBand
EXPO_PUBLIC_API_URL=https://precobao.app/backend npx tsc --noEmit
EXPO_PUBLIC_API_URL=https://precobao.app/backend npm run lint
EXPO_PUBLIC_API_URL=https://precobao.app/backend npm run export:web
```

Before a VM release, verify the Compose project and service from the labels on the running `web` container. Record its container ID, image reference, image ID/digest, Compose project/config paths, and available disk space. Keep the old image tagged for rollback. Build only `web`, then update only that service with `up -d --no-deps --no-build web`; do not use a stack-wide `up`, `--remove-orphans`, or any migration command for a frontend-only release. Confirm the running image ID after the update and check `/`, `/login`, `/forgot-password`, `/reset-password`, and `/backend/health/ready`. If validation fails, retag the saved image as the Compose image for `web` and run `up -d --no-deps --no-build web` to roll back only the frontend. Do not remove the saved image until the release is accepted.

This repository preparation does not authorize or perform a VM release. A physical Android device must be able to resolve the public API DNS and trust its Caddy certificate. CORS applies to browsers; Android's native HTTP client still uses HTTPS and the public API URL.

The GitHub Actions workflow `.github/workflows/azure-static-web-apps.yml` is `workflow_dispatch` only and targets `main`. It does nothing unless the repository variable `PRECOBAO_AZURE_STATIC_WEB_APPS_ENABLED` is explicitly set to the literal `true`; then the deployment token secret `AZURE_STATIC_WEB_APPS_API_TOKEN` and public HTTPS variable `EXPO_PUBLIC_API_URL` are required. Keep that variable absent/false until an authorized release. This repository preparation does not authorize or perform a deployment.

## 5. Backups, image registry, and restoration

Azure Flexible Server provides managed automated backups and point-in-time restore into a new server; follow the [Azure backup and restore guidance](https://learn.microsoft.com/en-us/azure/postgresql/backup-restore/concepts-backup-restore), choose retention appropriate to the service tier, and test recovery in a separate server. This is the first recovery layer.

The optional VM-side archive scripts add an encrypted logical backup of PostgreSQL and the separate image-reference JSON volume. Keep a GPG public-key recipient on the VM and the decryption private key in a separately controlled location. Configure its full fingerprint and the backup destination in `.env.azure`; restrict the destination and retain an independent copy when available. Set `POSTGRES_CLIENT_IMAGE=postgres:15.19-alpine` for PostgreSQL 15. The scripts verify that the client and server major versions match; `pg_restore` 15 avoids PostgreSQL 17-only `SET transaction_timeout` output when restoring to PostgreSQL 15. Temporary libpq settings and the password file are root-only and removed at exit. Example:

```sh
./backup.sh
./verify-backup.sh /var/backups/preco-bao/preco-bao-YYYYMMDDTHHMMSSZ
```

`backup.sh` uses TLS hostname verification, encrypts the database dump and image registry, writes checksums and a manifest, and runs the archive verifier before reporting success. It does not print credentials. `verify-backup.sh` checks the hashes, GPG decryption, PostgreSQL archive readability using the configured versioned client, image registry schema, and manifest names. A structural verification is not a substitute for a restore drill.

Restore only into a newly created, empty, separate managed database, after reviewing the archive and verifying the backup. Set `RESTORE_DATABASE_URL` to that separate server/database and explicitly set `RESTORE_CONFIRMATION=RESTORE_TO_A_NEW_EMPTY_DATABASE` for `restore-to-new-database.sh`. The script rejects the production database server as a target, checks for existing user relations and routines, verifies TLS hostname and CA, checks the client/server major versions, and restores in a single transaction. With PostgreSQL 15 tools, the restore does not issue the PostgreSQL 17-only `transaction_timeout` setting. Restore the image registry separately only while its API is stopped, with `CONFIRM_IMAGE_REGISTRY_RESTORE=REPLACE_IMAGE_REGISTRY_WHILE_API_STOPPED`. Never use these scripts against the primary database as a routine test. Follow restore with application-level verification before switching traffic.

## 6. Operational boundaries and pending work

- No Azure resource is provisioned by these files.
- The legacy Static Web Apps workflow is manual and must stay disabled; the production frontend is served from the VM's `web` container behind Caddy.
- The repository contains no real Azure credentials, database password, deploy token, CA private key or production `.env`.
- Set up Azure Monitor/VM monitoring, log retention, alerting, GPG key custody, backup offsite retention, restore drills, DNS, domain ownership, NSGs and secrets outside this repository.
- Review current Azure pricing and service quotas before provisioning; this repository cannot guarantee Azure-hosting costs.
- Indiana/Araújo new-item discovery remains manual; their existing-offer prices/images are refreshed automatically.
