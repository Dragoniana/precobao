# Preço Bão on Azure — deployment runbook

This runbook prepares the current `Dragoniana/precobao` `main` branch for:

- An Azure Linux VM running only the FastAPI container and Caddy reverse proxy.
- Azure Database for PostgreSQL Flexible Server as an external managed database.
- Azure Static Web Apps hosting the Expo Router static web export.
- Android clients calling the same public HTTPS API hostname.

It does not create Azure resources, run database migrations, start containers, or publish the Static Web App. The existing `deploy/production/docker-compose.yml` is the generic all-in-one Compose setup (including local PostgreSQL and frontend); it is not the Azure stack. Use `deploy/azure/docker-compose.yml` for Azure.

## 1. Network and DNS

1. Create the VM and Flexible Server in a private network design. Prefer Flexible Server private VNet integration with private DNS; attach the VM to a VNet that can resolve and reach the server's private FQDN. Do not enable public PostgreSQL access. Permit TCP 5432 only from the VM subnet/security group and keep SSH restricted to the operator's management source.
2. Give the VM a stable public IP and point `api.<your-domain>` to it. Permit inbound TCP 80/443 to Caddy; do not publish API port 8000. Caddy obtains and renews the public HTTPS certificate.
3. Create Static Web Apps separately and record its exact production origin. Add any required custom web origin to CORS explicitly. Do not use wildcard CORS.

Azure PostgreSQL private network DNS and TLS requirements are described in Microsoft's [Flexible Server networking documentation](https://learn.microsoft.com/en-us/azure/postgresql/flexible-server/concepts-networking) and [TLS connection guide](https://learn.microsoft.com/en-us/azure/postgresql/security/security-tls-how-to-connect). The application URL enforces `sslmode=verify-full` and points to the server's canonical `*.postgres.database.azure.com` FQDN. Mount the current trusted Azure PostgreSQL CA bundle read-only at `/run/secrets/azure-postgres-root-ca.pem`; do not disable hostname or certificate verification.

## 2. Configure the VM

Install Docker Engine with the Compose plugin, PostgreSQL client tools (`pg_dump`, `pg_restore`, `psql`), Python 3, GnuPG, and `sha256sum`. Provision DNS/networking first. Clone this repository on the VM and:

```sh
cd deploy/azure
cp env.example .env.azure
chmod 600 .env.azure
```

Edit `.env.azure` locally on the VM. Set the API hostname, exact Static Web Apps origin, strong unique JWT secret, private PostgreSQL URL, host CA bundle path, and a private Compose subnet that does not overlap the VNet. URL-encode special characters in PostgreSQL username/password. Keep `.env.azure`, private keys, CA bundles, database dumps and backup archives out of Git. The tracked example uses reserved `.invalid` placeholders and must fail preflight until replaced.

Copy the Azure PostgreSQL CA certificate/bundle from Microsoft's supported certificate distribution to a root-controlled path, and set `AZURE_PG_ROOT_CERT_PATH` to that host path. The container mounts it read-only; host backup tools use the host path. Rotate database and JWT credentials through the VM secret file and Azure PostgreSQL controls, not source control.

## 3. Database and API start

The Azure Compose file contains exactly `api` and `caddy`. FastAPI is reachable only on the internal Compose network. Caddy is the only service publishing ports 80 and 443. PostgreSQL, frontend, workers and scrapers are not Compose services here. A named Docker volume persists `IMAGE_REFERENCE_STORE_PATH` independently from PostgreSQL.

Before the first migration, create the managed database and a least-privilege application user. Take and verify an Azure managed backup or another approved recoverable copy first. The migration script requires a deliberate shell confirmation and never migrates as a side effect of starting the stack:

```sh
./preflight.sh
CONFIRM_DATABASE_MIGRATION=BACKUP_VERIFIED_AND_MIGRATE ./migrate.sh
docker compose --project-name preco-bao-azure --env-file .env.azure up -d --build
docker compose --project-name preco-bao-azure --env-file .env.azure ps
```

Run those commands only after reviewing the target server and maintenance window. Readiness is exposed at `/health/ready`; confirm it over HTTPS after startup. Do not expose port 8000 or PostgreSQL publicly. Apply OS security updates, SSH key-only access, firewall rules, Docker log rotation and host monitoring as part of VM operations.

## 4. Static Web Apps and Android

`frontend/app.json` is configured for Expo's static Metro web output. The export script copies `frontend/staticwebapp.config.json` into `frontend/dist`; the config provides SPA route fallback, JSON MIME handling and browser security headers. Azure Static Web Apps route configuration and fallback behavior are covered by [Microsoft's configuration reference](https://learn.microsoft.com/en-us/azure/static-web-apps/configuration).

For a local release artifact:

```sh
cd frontend
EXPO_PUBLIC_API_URL=https://api.<your-domain> npm ci
EXPO_PUBLIC_API_URL=https://api.<your-domain> npm run export:web
```

Set `EXPO_PUBLIC_API_URL` to the same HTTPS API origin for Android builds. A physical Android device must be able to resolve the public API DNS and trust its Caddy certificate. CORS applies to browsers; Android's native HTTP client still uses HTTPS and the public API URL.

The GitHub Actions workflow `.github/workflows/azure-static-web-apps.yml` is `workflow_dispatch` only and targets `main`. It does nothing unless the repository variable `PRECOBAO_AZURE_STATIC_WEB_APPS_ENABLED` is explicitly set to the literal `true`; then the deployment token secret `AZURE_STATIC_WEB_APPS_API_TOKEN` and public HTTPS variable `EXPO_PUBLIC_API_URL` are required. Keep that variable absent/false until an authorized release. This repository preparation does not authorize or perform a deployment.

## 5. Backups, image registry, and restoration

Azure Flexible Server provides managed automated backups and point-in-time restore into a new server; follow the [Azure backup and restore guidance](https://learn.microsoft.com/en-us/azure/postgresql/backup-restore/concepts-backup-restore), choose retention appropriate to the service tier, and test recovery in a separate server. This is the first recovery layer.

The optional VM-side archive scripts add an encrypted logical backup of PostgreSQL and the separate image-reference JSON volume. Keep a GPG public-key recipient on the VM and the decryption private key off the VM in a separately controlled location. Configure its full fingerprint and an off-VM backup destination in `.env.azure`; restrict the destination and retain an independent copy. Example:

```sh
./backup.sh
./verify-backup.sh /var/backups/preco-bao/preco-bao-YYYYMMDDTHHMMSSZ
```

`backup.sh` uses TLS hostname verification, encrypts the database dump and image registry, writes checksums and a manifest, and does not export secrets to command arguments or logs. Install and test these tools and restore procedures before relying on them operationally.

Restore only into a newly created, empty, separate managed database, after reviewing the archive and verifying the backup. Set `RESTORE_DATABASE_URL` to that separate server/database and explicitly set `RESTORE_CONFIRMATION=RESTORE_TO_A_NEW_EMPTY_DATABASE` for `restore-to-new-database.sh`. The script rejects the application database as a target and checks that the target has no user tables before restoring. Restore the image registry separately while the API is stopped, with `CONFIRM_IMAGE_REGISTRY_RESTORE=REPLACE_IMAGE_REGISTRY_WHILE_API_STOPPED`. Never use these scripts against the primary database as a routine test. Follow restore with application-level verification before switching traffic.

## 6. Operational boundaries and pending work

- No Azure resource is provisioned by these files.
- The GitHub workflow is intentionally disabled unless an authorized owner opts in.
- The repository contains no real Azure credentials, database password, deploy token, CA private key or production `.env`.
- Set up Azure Monitor/VM monitoring, log retention, alerting, GPG key custody, backup offsite retention, restore drills, DNS, domain ownership, NSGs and secrets outside this repository.
- Review current Azure pricing and service quotas before provisioning; this repository cannot guarantee Azure-hosting costs.
- Scrapers and schedulers are intentionally absent from this stack.
