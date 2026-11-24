# Security audit — 2026-11-24

## Scope and limits

Reviewed the checked-out application, deployment configuration, the production VM and Azure Portal configuration available to the operator. Production checks were limited to low-volume HTTPS requests, read-only database/role inspection, and the deliberately invalid HMAC request (rejected with 401). No pharmacy sites were contacted. Active exploit testing, fuzzing, brute force, and destructive actions were not performed in production. This is a technical review, not a guarantee of security or a legal/LGPD assessment.

## Findings summary

The counts below group confirmed application/control findings; dependency scanner package alerts are listed separately because scanner output does not prove exploitability or reachability in this application.

| Severity | Found | Corrected in production | Pending in production |
|---|---:|---:|---:|
| Critical | 0 | 0 | 0 |
| High | 0 | 0 | 0 |
| Medium | 3 | 1 | 2 |
| Low | 1 | 1 | 0 |
| Informative / functional | 1 | 1 | 0 |

### Confirmed findings and evidence

| ID | Component | Severity | Evidence and correction | Regression check / result |
|---|---|---|---|---|
| PB-SEC-01 | Password recovery | Medium | Reset links placed bearer tokens in the query string, where browser history and referrers may expose them. The branch now places tokens in the fragment and scrubs them from browser history. | Updated frontend and mailer tests pass. **API/web code fix is published in the branch but not deployed** because the production API image was intentionally left unchanged; production reset emails may still use the old URL format. |
| PB-SEC-02 | Android release build | Medium | A tracked debug keystore and public debug signing values were configured for release/standalone variants. The keystore was removed; release variants now fail closed unless an external release keystore is supplied. | Android signing guard passes; CI includes the guard. The publicly linked internal APK remains debug-signed and is not a Play Store release; dedicated release signing remains pending. |
| PB-SEC-03 | HTTP response headers | Low | CSP and Permissions-Policy were absent at the public edge. A conservative CSP (`object-src`, `base-uri`, `form-action`, `frame-ancestors`) and a restrictive Permissions-Policy are now active on `precobao.app`; existing HSTS, nosniff, referrer, and frame headers remain. | Caddy config validated; public HTTPS responses show all configured headers. |
| PB-SEC-04 | Web server image | Medium | Production used Nginx 1.27.5, which ZAP flagged as a version with known vulnerabilities; official NGINX advisories cover this version range. Affected NGINX TLS and HTTP/2 features are not enabled in the container, so exploitability in this setup was not demonstrated. The frontend image now uses NGINX 1.30.8, and `server_tokens off` removes the patch version from the public response. | Candidate image was built and smoke-tested locally; production reports NGINX 1.30.8 and no version suffix in `Server`. Official version source: https://nginx.org/en/download.html. |
| PB-OPS-01 | Android download | Informative / availability | The stable download route was configured, but its APK file was absent from the production web container, yielding 404. The verified APK is now supplied from a dedicated read-only host mount. | Public HTTPS returned 200, attachment disposition and Android MIME type; a complete download matched the expected byte count and SHA-256. |

## Architecture and attack surface observed

- Public: `precobao.app` HTTPS web frontend and `/backend/*` FastAPI proxy; HTTP redirects to HTTPS. Public ports on the VM are 80/443. SSH 22 is restricted by the NSG to one administrator source address. No public PostgreSQL, API, or Caddy admin port was observed.
- Private: Docker service network; PostgreSQL Flexible Server 15.19 on a private VNet connection with TLS required; application DB role is not superuser and cannot create roles/databases, replicate, or bypass RLS.
- Authentication: FastAPI Users bearer JWT, one-hour configured token lifetime. Browser token persistence uses localStorage; native storage is handled separately. Password reset tokens are random, hash-only, time-limited and single-use. Email sends use verified TLS.
- Authorization: `/users/me` and favorites are owner-scoped in the backend; existing two-user isolation tests pass. No cross-user access issue was reproduced in the reviewed routes.
- Scraper ingestion: HTTPS/HMAC, timestamp window, body bounds, source allowlisting and idempotency. One shared HMAC credential remains across sources; no credential was printed or rotated.
- Images: URL allowlists and redirect revalidation, body/MIME/signature checks, normalization and BYTEA storage were found. The offline administrative importer consumes JSONL/base64 content and resolves catalog identity by source, SKU, URL, and EAN; it is not a public API endpoint. No path-based arbitrary file import was identified in this importer path.
- CORS is configured with exact production origins. FastAPI docs/OpenAPI endpoints are disabled in production configuration. SQLAlchemy parameterized query patterns are used in reviewed routes.
- Docker: production API runs as a non-root application user. Web and Caddy use their image defaults and writable root filesystems; no privileged container was observed. The APK mount is read-only.
- Azure: Portal showed Ubuntu 24.04, 2 vCPU / 4 GiB VM, NSG allow rules for 80/443 and administrator-restricted SSH, with default inbound deny. PostgreSQL is private and enforces TLS. No Azure resources were provisioned.

## Dependency and supply-chain checks

- `pip-audit` against the actual Poetry virtual environment: no application dependency findings remained; the only output was two advisory records for local tooling `pip 26.1.2`, fixed in `pip 26.2`. The Poetry lockfile was updated for compatible security releases. This tooling version is not an application runtime dependency.
- `npm audit --omit=dev`: 15 affected package nodes were reported: 6 high and 9 moderate, 0 critical. Affected paths include Expo/Expo Router and transitive query/glob/matching/schema packages. npm's suggested fixes require major Expo/Router changes or recommend an older Expo release, so no blind major upgrade was applied. Reachability and application impact have not been proven; treat these as unresolved dependency alerts pending a compatibility-tested Expo upgrade. Do not describe the application as dependency-clean.
- Workflows use `contents: read`, checkout does not persist credentials, and production deployment is separately gated. Third-party GitHub Actions are referenced by version tags rather than immutable commit SHAs; pinning is a follow-up supply-chain improvement.
- No complete historical-secret scan of all Git objects and no authenticated OWASP ZAP scan was available in this run. Public bundles were checked for production API URL and obvious private credential patterns; no credential was found. This does not prove every historical commit or bundle is clean.

## Security areas not reproduced as vulnerabilities

No SQL injection, stored/reflected XSS, CSRF, SSRF, CORS bypass, BOLA/IDOR, privilege escalation, HMAC bypass, or secrets disclosure was reproduced in the reviewed scope. One invalid-HMAC request was rejected with 401. Rate limiting is process-local and IP-based; it is not a distributed/account-level defense. Web JWTs in localStorage remain exposed to any future successful same-origin XSS. Per-source HMAC keys are recommended but require a coordinated collector rollout.

| Area | Result |
|---|---|
| Authentication | Password hashing and recovery flow reviewed; reset-link token leakage corrected. Rate limits remain process-local. |
| JWT / sessions | Signature and expiry validation configured; browser token remains in localStorage; logout does not revoke already-issued bearer tokens. |
| Access control | Ownership-scoped user/favorites APIs and two-account isolation tests reviewed; no BOLA reproduced. |
| FastAPI | Typed validation, request limits, production docs disabled, and trusted proxy configuration reviewed. |
| SQL injection | No unsafe SQL concatenation found in reviewed query paths; no injection reproduced. |
| XSS / CSRF | No exploitable XSS reproduced; reset tokens moved to fragments. Cookie-based sessions are not used, so classic cookie CSRF is not applicable to bearer auth. |
| SSRF / uploads | Image source allowlist and redirect checks plus image validation reviewed; no SSRF or malformed-image bypass reproduced. |
| CORS / headers | Exact allowed origins; public CSP and Permissions-Policy now verified alongside HSTS, nosniff, Referrer-Policy, and frame protection. |
| Rate limiting | Present but in-memory and IP-based; insufficient for future multi-replica scaling or account-aware abuse. |
| Scrapers / HMAC | Invalid signature rejected with HTTP 401; timestamp and source checks present. Shared key across sources remains a risk. |
| Image handling | Normalized image validation and BYTEA delivery reviewed; importer is an offline command, not a public endpoint. |
| APK delivery | Stable HTTPS route now returns an attachment and verified bytes. Published standalone build is internal and debug-signed; not for Play Store. |
| PostgreSQL | Version 15.19; private VNet, TLS required; application role lacks superuser and role/database creation privileges. |
| Docker / Azure | VM is Ubuntu 24.04, 2 vCPU / 4 GiB; NSG exposes 80/443 and restricts SSH to administrator IP; no other inbound allow observed. |
| SSH / firewall | Key-only SSH; password auth disabled; NSG is the effective network filter (host `ufw` is inactive); root key login still appears permitted. |
| Backups | Current encrypted backup passed hash/decryption and isolated restore; off-VM replication and separate key custody remain unverified. |
| Privacy / logs | User email, favorites, IP, and medication interests are sensitive. No legal/LGPD compliance determination; no user data was used in tests. Token logging was not observed in reviewed reset flow. |
| Security tests / ZAP | Unit/regression tests and a passive ZAP baseline are recorded below. No active production testing or authenticated ZAP test was performed. |
| Deployment | Only web and Caddy were recreated for APK volume and headers. API, DB, and scraper workers were not changed. |

## Backup and recovery

Backup set `/var/backups/preco-bao/preco-bao-20261124T012615Z` was hash-checked and decrypted successfully. Its PostgreSQL 15.19 custom dump restored in an isolated container with no published ports or network. Counts checked: 12,798 catalog entries, 15,165 offers, 19,921 history rows, 1,853 image blobs, 13,576 image links, and 13 users. The encrypted image reference registry parsed with 4,961 records. The verified copy is on the production VM; no off-VM copy or independent encryption-key escrow was verified. See `BACKUP_RECOVERY.md`.

## Production changes made

- Published the previously missing APK at `/opt/preco-bao/downloads/preco-bao-1.0.1-internal.apk`; SHA-256 `bd9ba1288e00b1ce2b141e41aa30ba5538d1dd4f88509f120750d6d98622bacb`, 40,846,969 bytes. It is exposed by the web container through a read-only bind mount and the public route forces attachment download. Production web now uses `preco-bao-web:security-20261124` with NGINX 1.30.8; previous image remains present.
- Recreated only the `web` container to add that read-only mount and only the `caddy` container to load the validated security headers. API, PostgreSQL, schedulers, Indiana, Pacheco, Araújo, and offer-refresh were not restarted or modified. No migration or database write was performed.
- Previous web image `preco-bao-web:favicon-title-4d0d5ac` remains the current version and is available for rollback. Caddy configuration backup is `Caddyfile.before-audit-20261124` beside the active deployment file. To remove the APK mount, recreate only `web` with its prior Compose file set (without `/tmp/preco-bao-web-downloads-override.yml`); keep the APK file until rollback is verified.

## Tests and production verification

- Backend: 332 tests passed after dependency updates.
- Frontend: 142 tests passed; lint and TypeScript passed; static web export passed.
- Additional checks: Ruff; Android signing guard; APK delivery config guard; web build configuration guard; Compose config validation; isolated Caddy validation; isolated PostgreSQL restore; HMAC rejection; full APK GET and SHA-256 comparison.
- Production: `https://precobao.app/downloads/preco-bao.apk` returned HTTP 200 with `Content-Type: application/vnd.android.package-archive`, `Content-Disposition: attachment`, and 40,846,969 bytes. Downloaded SHA-256 matched the candidate. `/backend/health/ready` returned `{"status":"ready"}`. Web, API, schedulers, and Indiana were healthy/running after the changes; Caddy served the updated headers.
- Passive OWASP ZAP baseline against production crawled 12 URLs: first run 58 checks passed, 0 FAIL alerts, and 10 warning classes; after the web update 60 checks passed, 0 FAIL alerts, and 8 warning classes. NGINX version and version-disclosure alerts now pass. Remaining warnings concern cache policy on public resources, the intentionally partial CSP (ZAP flags missing `default-src`), missing COEP, JS heuristics, timestamp strings, and Android emulator/loopback fallback literals in the shared bundle. Bundle inspection found no credentials or configured production private endpoint; these are not counted as confirmed vulnerabilities. No active ZAP rules were run.
- Mobile browser evidence: an Android Chrome request was visible in Caddy logs before the file was installed. A full download from the public URL was then verified by byte count and SHA-256 from the workstation; a second interactive Android confirmation was not performed.

## Remaining risks and follow-up

1. Plan a compatibility-tested Expo/Expo Router dependency update to resolve the 15 npm audit package alerts.
2. Move browser sessions from localStorage to Secure, HttpOnly, SameSite cookies only as a dedicated CSRF-protected migration; preserve native SecureStore behavior.
3. Provision per-source HMAC secrets and rotate server/collectors together.
4. Create an encrypted off-VM backup copy with independent key escrow; formally choose RPO/RTO.
5. Run web and Caddy as non-root with read-only root filesystems after compatibility validation.
6. Consider shared account-aware rate limiting before adding API replicas.
7. Pin GitHub Actions to immutable SHAs; run a private authenticated ZAP baseline and a historical secret scan.
8. Produce a Play Store-compatible signed APK only after secure release-key custody and signing-identity continuity are established. The currently published `.standalone` APK is an internal sideload artifact, not a Play Store build.
