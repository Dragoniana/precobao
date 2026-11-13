# Azure preparation readiness — 2026-11-07

Repository: `Dragoniana/precobao`, branch `main`
Local working copy: `C:\dev\precobao`

## Scope and preservation

This is a local preparation only. No Azure resources were created, no deployment or GitHub workflow was triggered, no Docker daemon or scraper was started, and no production or managed database was contacted. No commit, push, merge, rebase, reset, or history rewrite was performed. Existing local Azure preparation and application edits were preserved.

The official remote is `https://github.com/Dragoniana/precobao.git`; the checked-out branch is `main`. The old repository worktrees were not used or modified.

## Azure preparation

- `deploy/azure/docker-compose.yml` contains FastAPI and Caddy only; PostgreSQL is external. The API port is internal, Caddy publishes HTTPS/HTTP, and the image-reference JSON registry has persistent storage.
- PostgreSQL configuration requires verified TLS (`sslmode=verify-full`) and a CA mount. Private networking, DNS and the CA chain still need validation from an actual Azure VM.
- Explicit Alembic migration, preflight, encrypted backup, verification and restore scripts are present. Only syntax and static safety paths were checked; no database or GPG key was used.
- Expo web static export and `staticwebapp.config.json` are configured. The GitHub Actions workflow is manual and remains gated by an explicit repository variable; no deployment token was used.
- First deployment, secrets, network and backup procedures are described in `docs/azure-deployment.md`.

## Validation summary

- Backend: **89 tests passed** with an in-memory SQLite test database and test-only JWT; Ruff and Python compileall passed.
- Frontend: **17 Jest suites / 95 tests passed**; TypeScript and ESLint passed; `npm ci` and `npm ls --all` passed.
- Expo dependencies: `npx expo install --check` passed. Expo Doctor passed 19/20; the remaining check concerns existing native `android/` and `ios/` directories not receiving all `app.json` configuration under EAS. Native directories were not regenerated.
- Expo Web export passed and emitted 25 static routes with the SWA config copied into the output. Android export produced a 5.9 MB Hermes bundle; this is not an APK or device validation.
- Azure Compose interpolation with the example environment, all Azure shell script syntax, workflow/config parsing and `git diff --check` passed.
- `npm audit` still exits nonzero: **31 package nodes** (6 high, 17 moderate, 8 low); production-only audit: **22** (6 high, 9 moderate, 7 low). Compatible fixes were applied without force. Remaining Expo/deep-link risks are detailed in `docs/azure-security-and-predeploy-validation.md`.

## Security review and remaining work

No real environment files, database dumps, private-key files or common high-confidence token signatures were found in the tracked working tree. A historical `backend/.env` commit was inspected: no JWT secret or credentials in its database URL were identified. The file remains in Git history; no history rewrite was done. Pattern matching is not a full secret scan. The academic DOCX has non-placeholder author metadata and should be sanitized before external publication.

This project is **not yet approved for deployment**. Address the remaining high dependency advisories with an Expo-compatible upgrade plan; perform a dedicated Git-history secret scan; resolve the native-folder Expo Doctor warning; provision and verify Azure VM, private networking, managed PostgreSQL, TLS, domain and secrets; run migrations only after backup and authorization; configure off-VM encrypted backups and conduct a restore drill; test the deployed API, web and Android flows; and explicitly authorize the gated Static Web Apps workflow.

See `docs/azure-security-and-predeploy-validation.md` for audit classification, exact validation results and limitations. Build/export checks are not functional Azure or device tests.
