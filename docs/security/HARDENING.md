# Security hardening record

## Applied in this change

- Password-reset email links place the token in the URL fragment, which browsers do not send to the server or in Referer headers. The web screen accepts existing query links temporarily and removes the token from browser history immediately.
- Caddy adds a conservative Content-Security-Policy (`object-src`, `base-uri`, `form-action`, `frame-ancestors`) without changing inline app scripts or styles, plus a Permissions-Policy disabling unused camera, microphone, and geolocation capabilities.
- Android release and standalone builds fail closed unless a readable absolute external release keystore and all signing values are supplied. The shared debug keystore is removed from the repository; debug variants use the Android Gradle plugin's local debug signing.
- The Azure web container receives `/downloads` from a dedicated host directory mounted read-only. The APK route remains HTTPS-only and serves an attachment with the Android package MIME type.
- Caddy's CSP and Permissions-Policy are deployed and verified on the public HTTPS site. The policy intentionally leaves existing script/style behavior unchanged while disabling object embeds and framing.

## Residual risks requiring planned work

- Browser bearer JWTs are persisted in localStorage. An XSS could read them; migrate web authentication to secure HttpOnly cookies only with CSRF protection and compatibility tests, while retaining native SecureStore.
- Ingestion currently has one shared HMAC credential. Provision and rotate independent per-source credentials as a coordinated server/collector rollout.
- The currently built internal APK uses the public Android debug signing certificate and has a `.standalone` package id. Do not use it as a Play Store/release signing identity. Public download is an internal sideload artifact; a dedicated signing-key custody and update-compatibility plan is still required.
- Azure Portal confirmed the VM NSG allows SSH only from the configured administrator address, permits public HTTP/HTTPS, and denies other inbound traffic by default. PostgreSQL Flexible Server requires TLS and is attached to a private VNet subnet. The portal reported subscription `Azure for Students`; no paid resource was created.
- Web and Caddy containers run with default container users and writable root filesystems; harden these after compatibility testing.
- Login limits are process-local and IP-based. They are adequate only for the current single API process; distributed/account-level controls need a shared limiter before scaling.
- Backup integrity and a full isolated PostgreSQL restore were verified on the VM, but no independent off-VM copy or independent key escrow was proven. VM loss could therefore affect both production and the verified backup.

## Validation scope

See `SECURITY_AUDIT.md` for dated evidence, executed checks, and limitations. No claim of complete security or legal/LGPD compliance is made.
