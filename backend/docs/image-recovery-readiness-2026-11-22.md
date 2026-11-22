# Production image recovery — 2026-11-22

## Findings and controlled change

- The 404 came from the API image, not from Caddy path handling. Caddy's
  `handle_path /backend/*` correctly strips `/backend`; the old image
  `preco-bao-api:partial-batch-b0ee209` did not register
  `/api/v1/internal/medicine-images/migration-check`.
- Production PostgreSQL is 15.19 at Alembic revision `a4f73b9210cd`, equal to
  the candidate head. No migration was needed.
- The new API image was built from commit `bd69919` and tested against a fresh
  isolated restore before deployment. Health returned 200; signed HMAC reached
  request validation, invalid HMAC returned 401, and HTTP without forwarded
  HTTPS was rejected. The staging API and temporary restore database were
  removed after validation.
- Only the production API container was updated to
  `preco-bao-api:image-migration-bd69919`. The previous image remains available
  as `preco-bao-api:rollback-before-image-migration-bd69919`. HTTPS health is
  ready, the route exists, and an unsigned public request returns 401. The API
  is healthy.
- Web, Caddy, database, volumes, Indiana, offer-refresh, and all schedulers were
  left running. Compose reported an existing orphan container; no orphan was
  removed.

## Backup and restore

- Backup: `/var/backups/preco-bao/preco-bao-20261122T183037Z`.
- Encrypted PostgreSQL dump: 8,485,393 bytes; SHA-256
  `40ecf47d8a764e4bac1aca00bdc6116e1e9eeae8b3c5d8725897567a80d2f91e`.
- Encrypted image-reference archive: 326,566 bytes; SHA-256
  `0158325645e5b35b353fc47529ca9d5a087d24896f2e4f17acac8d977b82995b`.
- Both hashes, GPG decryption, PostgreSQL archive structure, image-reference
  JSON, and manifest passed verification.
- Restored in a separate PostgreSQL 15.19 database with no published ports.
  Counts: 12,791 catalog items, 15,147 offers, 18,761 price-history rows,
  166 image blobs, and 929 image links. All 166 restored blobs passed SHA-256,
  byte-size, and image decoding checks; there were no dangling image links.
  The image-reference archive had 4,682 records. The temporary plaintext and
  restore database were removed after validation; the encrypted backup remains.

## Development source and dry-run

- Source PostgreSQL is 15.17 at Alembic revision `9a6d2f81c4b7`, with 118 blobs
  and 611 links. The source predates `is_generic`; the migration tool now reads
  that schema read-only without applying a migration.
- PowerShell 5.1 dry-run compared 226 validated Araújo/Pacheco candidates
  (7,057,464 bytes) with production. Results:

  | Source | Eligible | Already had image | Other production blocks |
  |---|---:|---:|---:|
  | Araújo | 0 | 2 | 0 |
  | Pacheco | 2 | 222 | 0 |

- 382 source catalog associations were excluded before production checking;
  384 links had generic-image classification differences in the older source
  schema. They were not sent or used as identity evidence.

## Controlled transfer and delivery

- Transferred exactly 2 Pacheco images, total 48,846 bytes. The API rechecked
  each candidate immediately before writing, required `only_if_missing`, and
  returned `STORED` for both. 224 candidates were skipped because production
  already had an image (Araújo 2, Pacheco 222).
- Both migrated catalog-image API URLs returned HTTP 200 with `image/webp`;
  downloaded bytes matched the stored SHA-256 and ETag. The responses were
  verified in memory; no local image files were created or deleted.
- Migration code writes image blobs, image links, and the idempotency ledger;
  it does not write catalog items, offers, prices, availability, or history.
  Normal production scrapers remained active during this operation, so the
  history count later observed was 18,780; the image upload path itself has no
  history/price mutation.

## Remaining coverage and integrity

- Final production data snapshot: 12,791 catalog items, 15,147 offers, 18,780
  price-history rows, 172 image blobs, 961 links, and 1,905 upload-ledger rows.
  All 172 blobs passed SHA-256, byte-size, and image-decoding validation. Of
  961 links, 960 passed source/SKU/catalog/offer identity checks; one existing
  non-primary generic Pacheco link has an EAN/catalog mismatch. No blob was
  unlinked. That inconsistent link was not changed or used for migration.
- API image-reference audit: 386 verified stored catalog-primary references,
  568 offer-scoped stored fallback references, 1,660 legacy external URL
  references, 2,583 catalog products with at least one API reference candidate,
  and 10,208 with no current API image-reference candidate. These categories
  overlap and external URLs were not live-probed, so they do not prove that an
  image renders in the browser. By-products-with-offers counts also overlap:
  Indiana 3,942 total / 1,799 candidates / 2,143 with none; Pacheco 11,115 /
  1,343 / 9,772; Araújo 82 / 47 / 35. Do not interpret URL candidates as
  verified reachable images or as a complete coverage measure.
- The prior single EAN/catalog mismatch remains. It was identified as an EAN
  mismatch, not automatically corrected, and was excluded from migration.
- Indiana has no HMAC image-ingest contract in this release. Its existing
  external image references require a separate reachability and identity audit;
  no Indiana permission was added.
- No external image downloads were made. HTTP 403/429 handling was not
  bypassed. Regular scrapers and their checkpoints/outboxes were not altered.

## Published state

- Branch: `codex/scraper-integration-20261120`; PR #5 remains open and draft.
- The source-schema compatibility, per-source dry-run counts, and Windows
  module-launch correction are published in commit `4ed95fa`.
- Local validation: 302 backend tests, Ruff, and an actual PowerShell 5.1
  dry-run passed. GitHub Actions run 50 passed for commit `4ed95fa`. No merge
  was performed.
