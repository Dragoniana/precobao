# Backup and recovery evidence

## Verified restore drill

On 2026-11-24, the latest encrypted PostgreSQL backup and image-reference artifact passed SHA-256 verification and decrypted successfully. The database dump restored in a temporary PostgreSQL 15.19 container with no network or published ports. Schema, migration revision, core-table counts, and image references were checked against the backup manifest. Exact backup paths and production record counts are intentionally omitted from this public repository.

The backup resides on the production VM. No external copy or independent recovery-key custody was proven. The restore drill validates recoverability of this set, not off-site disaster recovery.

## Recovery outline

1. Preserve the source backup and hash manifest.
2. Decrypt only into a restricted, isolated host directory.
3. Restore the custom-format database into a new isolated PostgreSQL 15.19 instance with no public ports.
4. Restore the image-reference registry separately and verify its hash and record count.
5. Compare schema, migration revision, critical table counts, and image hashes.
6. Keep the previous production image and record the backup identifier before deployment. Never downgrade data migrations destructively.

RPO/RTO targets have not been formally set. An off-VM encrypted copy and documented key escrow are still required for VM-loss recovery.
