# Frontend dependency audit — 2026-11-15

## Results

Before remediation, `npm audit` reported 29 entries: 6 high, 15 moderate, and
8 low. `npm audit --omit=dev` reported 22: 6 high, 9 moderate, and 7 low.
After remediation, the counts are 27 (3 high, 15 moderate, 9 low) and 20
(3 high, 9 moderate, 8 low), respectively. The earlier Docker build printed
31 from its install-time advisory snapshot; npm's advisory data changes over
time, so the comparable before/after figures above come from running the same
audit commands in this review. The final Node 22 Docker build's npm 10 install
report printed 29 (3 high, 17 moderate, 9 low) for the same lockfile; keep the
CLI-specific totals with their npm versions when comparing later runs.

The three high findings left by the audit are `expo`, `@expo/cli`, and Zod
nested under `@expo/cli`. Their vulnerable paths go through Expo CLI/config and
build tooling, and are not copied into the static Nginx runtime image. The Expo
Router bundle had used `query-string` 7.1.3,
which pulled vulnerable `decode-uri-component` 0.2.2. A tested npm override
updates `query-string` to 10.0.0 and removes `decode-uri-component` from the
installed tree. The generated browser bundle no longer contains the vulnerable
decoder package identifier. The published container contains only Nginx and
the generated static files, not Node.js or `node_modules`.

## Remediation and remaining work

The `query-string` update was kept after all 95 frontend tests, TypeScript, and
the static export passed. A normal `npm audit fix` also applied compatible
transitive fixes; it did not use `--force`. No Expo SDK major upgrade was made.
The remaining three high audit entries are not present in the deployed static
container, but remain in the build dependency graph. npm's remaining suggested
fixes require incompatible Expo/Jest major changes, so they belong in a
separately tested SDK migration rather than this PR.

The advisory inventory may change as the registry database is updated. Re-run
both commands after upgrading the Expo SDK and verify the emitted web bundle as
part of that migration.
