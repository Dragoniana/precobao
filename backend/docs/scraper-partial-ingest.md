# Scraper partial ingestion and Windows task limits

The authenticated scraper batch endpoint keeps the existing HMAC request and
returns either `COMPLETE` or `PARTIAL`. `product_count` is the submitted count;
`accepted_count` and `rejected_count` sum to it. Rejections contain only stable
source identifiers (`product_id`, SKU, EAN, URL) and a reason. A batch in
`PARTIAL` state is idempotent: the same `batch_id` and payload receive the same
stored result. Rejected product details are persisted in the batch audit row.

Conflicting items are excluded before catalog, offer, price-history, or image
reference writes. Accepted siblings continue through the existing persistence
path. A persistence error remains a failed batch and returns an error; it is
never represented as an identity rejection. The Windows collector atomically
stores the original rejected product under
`%LOCALAPPDATA%\PrecoBaoCollector\state\rejected\<source>.json` before it
removes the batch outbox. This is the durable local review queue; it does not
rewrite EANs or merge products by name.

The `a4f73b9210cd` migration adds `rejected_items` to
`scraper_import_batches`. It is additive. Rollback is application-only: retain
the migration and column so rejection evidence is not lost. Do not downgrade
this migration.

Windows tasks are separate per pharmacy. The task registration script only
creates tasks for sources already set to `DIRECT` with access confirmed. It
requires a per-run limit from 1 to 50 and unique source times. The supplied
defaults are Araújo at 08:00 and 20:00, Pacheco at 10:00 and 22:00, in
`America/Sao_Paulo`; the two-hour gap prevents overlap under the task's
two-hour execution cap. The installer does not register tasks. The PowerShell
entrypoint and Python collector both fail closed if a scheduled run lacks a
valid product cap.

Image upload manifests retain the normalized bytes, upload ID, attempt count,
last error, status, and next retry time. Uploads use bounded exponential delay
and move permanent HTTP 4xx failures or exhausted retries to `DEAD_LETTER`
without deleting the files. Source image URLs returning 403 or 404 are recorded
by URL hash and not fetched again automatically. This image path remains
independent of price and offer persistence.
