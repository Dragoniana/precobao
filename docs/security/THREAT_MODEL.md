# Preço Bão threat model

## Assets

- Account email, password hash, JWT, recovery tokens, and user favorites.
- Catalog, offers, image blobs, and price history.
- HMAC credentials and ingestion payloads for pharmacy collectors.
- PostgreSQL, VM access, Docker configuration, backups, and Android artifacts.

## Trust boundaries

- Public browser/mobile clients to Caddy, then the web container and FastAPI.
- FastAPI and scraper workers to managed PostgreSQL over verified TLS.
- Authorized local collectors to HTTPS/HMAC ingestion.
- VM operators, deployment artifacts, backup encryption keys, and GitHub Actions.

## Threats and controls

- Account takeover: password hashing and bounded token expiry; bearer JWT remains in browser localStorage and needs a separate HttpOnly-cookie/CSRF design.
- Cross-user access: backend ownership-scoped `/users/me` routes; regression tests cover favorites isolation.
- Forged ingestion: HMAC, timestamp window, body limits, source validation; current production key is shared between sources, so per-source rotation is still needed.
- Malicious image delivery: HTTPS source allowlists, redirects revalidated, byte/MIME/signature limits, image normalization.
- Artifact substitution: HTTPS and a declared SHA-256; Android release signing must use a private dedicated keystore, never the shared debug key.
- Data loss: encrypted PostgreSQL and image-registry backups with hash and restore checks; isolated restore succeeded, while off-VM copy and independent key custody remain unverified.

## Out of scope

No testing was performed against pharmacy infrastructure or other third parties. Production checks were limited to low-volume, non-destructive probes. Azure Portal confirmed restrictive NSG rules and private TLS-enforced PostgreSQL access; no paid Azure resource was created.
