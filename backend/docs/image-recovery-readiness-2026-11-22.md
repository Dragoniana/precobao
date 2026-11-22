# Image recovery readiness — 2026-11-22

## Verified in this run

- Production PostgreSQL reports version 15.19. The production API container is
  healthy; the VM's existing containers were not restarted or changed.
- The production catalog contains 12,791 rows. A follow-up consistency audit
  found 159 blobs and 914 links. All 159 blobs matched their stored size,
  SHA-256 and decodable image format; 913 links matched their source, SKU and
  related catalog/offer identity, with one inconsistent link. The link total
  changed during the audit while production schedulers remained active. This
  does not establish external URL availability or full frontend coverage.
- The development database is PostgreSQL 15.17 at Alembic revision
  `9a6d2f81c4b7`. It has 118 blobs and 611 links. Its `medicine_image_links`
  table predates `is_generic` (`c4a8f32d1e9b`). The source-image migration now
  reads this older schema without applying a migration or writing to it.
- A read-only source inventory validated 226 Araújo/Pacheco candidates by
  source identity, EAN, SHA-256, normalized image format and size (7,057,464
  bytes). 382 medicine associations did not pass candidate validation; generic
  classification differences in the older schema are counted and excluded.
- Production returned HTTP 404 for
  `/backend/api/v1/internal/medicine-images/migration-check`. The currently
  deployed API therefore cannot perform the mandatory per-item production
  eligibility check. No candidate was uploaded and no production row changed.
- The current HMAC image-ingest contract permits Araújo and Pacheco only.
  Indiana must not be added to that allowlist until a source-specific identity,
  SKU, URL and authorization contract is implemented and tested.

## Safe continuation order

1. Keep regular schedulers and collectors unchanged. Do not fetch pharmacy
   images to get around an HTTP 403, CAPTCHA, 429 or another source restriction.
2. Make a fresh encrypted production backup and restore it into an isolated
   database before any production data write.
3. Publish and deploy the reviewed API version that includes the authenticated,
   read-only migration check. Keep the update limited to the API container and
   retain the prior image for rollback.
4. Run the production eligibility dry-run. Review counts for exact EAN, source
   offer, existing images, conflicts and duplicates. Stop on unexpected
   identities or if the API does not return an authenticated result.
5. Transfer only `ELIGIBLE` images using `only_if_missing`; confirm the
   persisted link and blob before any local temporary download is deleted.
6. Re-audit frontend delivery and actual image bytes. Report Indiana coverage
   separately until its controlled ingestion path exists.

No production backup was created or replaced during the 2026-11-22 audit, no
production migration/deploy was performed, and no image bytes were downloaded
or removed locally in this run.
