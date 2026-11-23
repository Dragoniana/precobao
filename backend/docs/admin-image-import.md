# Administrative offline image import

This tool is separate from scraper HMAC ingestion. It never downloads URLs.
Run commands from `backend`, with database URLs supplied through protected
environment variables. Binary packages must stay outside the repository.

Export a read-only repeatable-read snapshot:

```powershell
python -m scripts.export_admin_images --output C:\private\image-package
```

The source is `PRECOBAO_DEVELOPMENT_DATABASE_URL`. The export records source
rejections in `package.json`, writes a SHA-256 protected `manifest.jsonl`, and
stores normalized, metadata-free bytes once per hash in `blobs/`. Re-running
preserves and verifies existing blobs. Only a completed manifest is published.
Transfer the complete directory using SSH/SCP with strict host verification.

Import into an isolated restored database first:

```sh
python -m scripts.admin_image_import --run-id staging-validation --manifest /private/image-package/manifest.jsonl
python -m scripts.admin_image_import --run-id staging-validation --rollback
```

The target is `DATABASE_URL`. Every committed image and its before/after journal
are atomic; reusing the run ID skips committed items. The journal is
`admin_image_import_journal` in the target search path. The tool acquires a
session advisory lock. Existing blobs are preserved, including after rollback.
Rollback is all-or-nothing and refuses links modified since import. It touches
only image links and its own journal, preserving prices and histories.

Production execution requires successful real PostgreSQL staging import,
idempotency, failure recovery and rollback tests, plus an accessible verified
encrypted backup. Do not execute against production until those checks pass.
