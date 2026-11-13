# Security and pre-deploy validation — 2026-11-07

Scope: official repository `Dragoniana/precobao`, branch `main`, local copy `C:\dev\precobao`. This review did not deploy, create Azure resources, start Docker, connect to PostgreSQL, run scrapers, or rewrite Git history.

## Dependency audit

`npm audit --json` initially reported **33 package-level findings: 8 high, 17 moderate, 8 low, 0 critical**. `npm audit --omit=dev --json` initially reported **24: 8 high, 9 moderate, 7 low**. These are npm's vulnerable package nodes in the dependency graph, not 33/24 distinct advisories; multiple parent packages aggregate the same vulnerable leaf.

I ran `npm audit fix` without `--force`, after reviewing its dry run. It changed the lockfile only for compatible transitive updates, including:

- `node-forge` 1.4.0 → 1.4.3, clearing its reported advisories.
- `follow-redirects` 1.16.1 → 1.16.2, clearing GHSA-mrwp-f3q8-3g9p.
- `glob` 13.0.6 → 13.0.7 and `minimatch` 10.2.5 → 10.2.6. These remain inside the advisory ranges, so they are not counted as resolved.
- `eslint-plugin-expo` 1.0.3 → 1.2.1 and the compatible `@typescript-eslint` 8.61.0 → 8.72.1 family.

For reproducible Jest installs with React 19.2, I pinned the optional test renderer peer as a development dependency: `test-renderer` 1.2.0 uses `react-reconciler` 0.33, whose React peer range includes the Expo SDK's React 19.2. This resolved the prior invalid peer in `npm ls --all`; Expo, Expo Router, React, React Native, and native module versions were not upgraded.

Final audit after clean `npm ci`:

| Audit | High | Moderate | Low | Critical | Total |
|---|---:|---:|---:|---:|---:|
| `npm audit` | 6 | 17 | 8 | 0 | 31 |
| `npm audit --omit=dev` | 6 | 9 | 7 | 0 | 22 |

Both audit commands exit nonzero because advisories remain. The findings by package node, installed version, source, and project impact are below. Parent nodes inherit severity from their affected dependency; leaf advisories are linked.

### High findings (6 package nodes; 2 underlying groups)

| Package node | Installed version | Introduced by / use | Assessment and fix status |
|---|---|---|---|
| `expo` | 55.0.32 | Direct platform package; contains build/config/runtime modules | Aggregates vulnerable Expo CLI/config dependencies. npm's suggested fix is a major platform change/downgrade to Expo 52.0.49; the current SDK 55 line has no safe fix exposed by audit. |
| `@expo/cli` | 55.0.37 | Expo CLI, run during local and CI builds | Build-time, not emitted as browser/Android app code, but a build-chain risk. It includes vulnerable `zod` and Expo configuration modules. |
| nested `zod` | 3.25.76 | `@expo/cli` input/config validation | GHSA-mrhx-c7xc-m3w5 (memory exhaustion on oversized constrained collections). The repository's direct Zod 4.6.7 is not this node; overriding the CLI's Zod 3 dependency to Zod 4 would cross a major API boundary. |
| `expo-router` | 55.0.18 | Direct navigation/runtime dependency | Audit aggregates the vulnerable query parser and an optional testing peer. Updating to the suggested Expo Router 58 is a multi-major platform change, not a safe patch. |
| `query-string` | 7.1.3 | Runtime dependency of Expo Router | Depends on the vulnerable URI decoder. Used for URL/query parsing in routing; relevant to malformed Web URLs and Android deep links. |
| `decode-uri-component` | 0.2.2 | Transitive runtime dependency of `query-string` | GHSA-vcc3-ghjq-m6fr and GHSA-qr28-fpjm-wq7f describe denial of service on malformed percent-encoded input. Registry metadata has a fixed 0.5.1, but it changes the package from CommonJS to ESM while `query-string` 7.1.3 calls it with CommonJS `require`; a forced override could break routing/module interop. No override was applied. |

### Moderate findings (17 package nodes)

| Package nodes and installed versions | Source / scope | Status |
|---|---|---|
| `@expo/config` 55.0.21; `@expo/config-plugins` 55.0.12; `@expo/fingerprint` 0.16.8; `@expo/local-build-cache-provider` 55.0.16; `@expo/metro-config` 55.0.27; `@expo/prebuild-config` 55.0.22; `expo-splash-screen` 55.0.25 | Expo build/prebuild/configuration tree | These inherit vulnerable `glob`, `minimatch`, and `deepmerge` findings. The fixes npm suggests require moving away from SDK 55, including major changes. |
| `glob` 13.0.7 | Expo CLI/config/build tool | GHSA-3g3m-2fjr-6pw4; affected through 14.0.1, fixed range starts at 14.0.2. The installed 13→14 jump is major. |
| `minimatch` 10.2.6 | Expo fingerprint and TypeScript ESLint tooling | GHSA-g3hv-75wg-m249; fixed at 11.0.1, a major jump from the current 10 line. |
| `@typescript-eslint/eslint-plugin`, `@typescript-eslint/parser`, `@typescript-eslint/type-utils`, `@typescript-eslint/typescript-estree`, `@typescript-eslint/utils` — all 8.72.1 | Lint/build-time only | Their audit nodes inherit the `minimatch` issue. The semver-compatible update was applied; no supported patched 10.x minimatch was reported. |
| `eslint-config-expo` 55.0.1; `eslint-plugin-expo` 1.2.1 | Development/CI lint tooling | Inherit the TypeScript ESLint chain. npm suggests `eslint-config-expo` 6.0.0, which is incompatible with the Expo 55 config family. |
| `jest-expo` 55.0.22 | Jest/test configuration | Inherits Expo config findings; the suggested fixed version is from a later Expo major. |

### Low findings (8 package nodes)

| Package node | Installed version | Scope / status |
|---|---|---|
| `@jest/core`, `create-jest`, `jest`, `jest-cli`, `jest-config` | 29.7.0 | Jest development/test graph. Suggested fixes downgrade Jest to 25.1.0, a major compatibility break; not applied. |
| `jest-watch-typeahead` | 2.2.1 | Jest development/test graph; inherited Jest finding. |
| `@testing-library/react-native` | 14.1.0 | Test-only. Expo Router declares it as an optional peer, which is why npm's `--omit=dev` report still includes this graph. It is not present in exported Web/Android bundles. |
| `deepmerge` | 4.3.1 | Expo build/test configuration dependency. GHSA-gcqx-w48m-w2h4 covers versions through 4.3.1. The npm registry's latest 4.x is 4.3.1, so no patched compatible release was available during this review. |

The package-node counts include inherited entries; the direct advisories above explain the affected leaves. The complete audit JSON remains reproducible with the commands above. No advisory was silenced, and no broad override was added.

## Frontend dependency and compatibility checks

- `npm ci`: passed from the updated lockfile; npm reports the 31 residual findings above.
- `npm ls --all`: passed after pinning the test renderer. Remaining `UNMET OPTIONAL DEPENDENCY` rows are optional native/runtime accelerators, not invalid package versions.
- `npm outdated --json`: read-only check completed. It showed newer SDK 58 releases and unrelated compatible updates; no unrelated upgrades were applied. `npx expo install --check` reports SDK dependencies up to date.
- `npx expo-doctor`: 19/20 checks pass. The remaining warning is that tracked native `android/` and `ios/` folders coexist with native settings in `app.json`; EAS Build will not sync those properties automatically. I did not run Prebuild or alter native folders because that could overwrite native project state.
- The Expo Router + decoder high finding remains a pre-deploy risk. Plan a dedicated Expo platform upgrade and validate deep-link behavior before public release; do not use the current result as a security clearance.

## Backend environment and tests

- Poetry 2.3.4 and Python 3.12.10 are in use; `backend/.venv` is isolated and based on Python 3.12, matching `pyproject.toml`'s supported range. `poetry.lock` was unchanged.
- DNS, TCP 443, and a normal HTTPS request to the official PyPI endpoint succeeded. Poetry failed because it could not read/write a cache file under the pre-existing user Poetry cache (`Permission denied`), not because application code or PyPI TLS failed.
- Installing from the existing lock succeeded by setting `POETRY_CACHE_DIR` only for that process to a fresh temp cache. No global Python packages, TLS settings, index, proxy, or Poetry configuration were changed.
- Backend test command used `DATABASE_URL=sqlite:///:memory:` and a test-only JWT value. **89/89 tests passed**, matching the reported baseline; SQLite fixtures and mocked scraper tests were used. No main or Azure database was contacted, and no live scraper ran.
- `poetry run ruff check app tests`: passed. `poetry run python -m compileall -q app tests`: passed.

## Azure and repository checks

- Azure Compose is valid statically and defines only FastAPI + Caddy; only Caddy publishes 80/443. It has no local PostgreSQL, web container, scraper, scheduler, or startup migration. API port 8000 is internal only.
- PostgreSQL uses the managed server's DNS name with `sslmode=verify-full` and a mounted CA. VNet/private DNS and real CA validation are documented but not tested against Azure.
- Web export emitted 25 routes and included `staticwebapp.config.json`; Android export emitted a 5.9 MB Hermes bundle. Neither export is a deployed service or APK.
- GitHub workflow is manual (`workflow_dispatch`) and gated by an explicit repository variable; no token or deploy was used.
- All Azure shell scripts passed Bash syntax validation. Backup and restore were **not operationally homologated**: no database, GPG key, Docker service, or archive was used. Only syntax and static guard paths were reviewed.
- Current tree contains no real `.env`, dump, private-key file, or matches for common high-confidence token/private-key signatures. Early Git history did include `backend/.env`; its structured review found no JWT secret and no credentials in its database URL. The file was removed from the index by a later commit and remains in history; no history rewrite was attempted. This pattern-based check does not replace a managed secret scanner.
- The academic DOCX still has non-placeholder author metadata. It was not modified; sanitize its document properties before publishing it externally.

## Not approved for deployment yet

The remaining 6 high findings need an Expo-compatible remediation plan. Also pending: run a full security review of Git history with a dedicated secret-scanning tool; resolve the EAS native-folder config warning; establish VM, VNet/private DNS, database, domains and secrets; test PostgreSQL TLS from the actual VM; run Alembic only after backup and approval; configure off-VM backup/GPG key custody and perform a restore drill; verify health from Azure; authorize Static Web Apps workflow; and test the Android app against the hosted API. This sprint performed none of those external actions.
