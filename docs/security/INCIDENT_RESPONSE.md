# Security incident response

1. Preserve availability and evidence. Record UTC time, affected component, image/commit identifier, and redacted symptoms.
2. Do not copy tokens, passwords, HMAC values, private keys, or user payloads into tickets or chat.
3. For suspected credential exposure, identify the exact principal and consumers first. Prepare a coordinated replacement, validate in staging, then rotate only the affected credential and verify dependent services.
4. For suspected unauthorized writes, stop only the affected ingestion path after capturing its state; preserve database and image-import journals. Do not run a broad rollback or delete records.
5. Restore using the documented isolated restore procedure and a verified backup. Never test restore over the production database.
6. Notify affected users or authorities only through the owner's reviewed incident and privacy process. This technical document is not a legal determination.
7. Record remediation, tests, residual exposure, and the decision to resume service.
