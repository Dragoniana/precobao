"""Validate a real offline package against a disposable restored database."""

import base64
import hashlib
import json
import os
from collections import Counter
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from scripts.admin_image_import import import_item, initialize, rollback


def fingerprint(db, table):
    return db.execute(
        text(
            "SELECT md5(coalesce(string_agg(row_to_json(t)::text, '' "
            "ORDER BY id),'')) "
            f"FROM {table} t"
        )
    ).scalar_one()


def main():
    package = Path(os.environ["IMAGE_PACKAGE"])
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.begin() as connection:
        initialize(connection)
    counts = Counter()
    with Session(engine) as db:
        protected = ("catalogo_base", "ofertas_farmacia", "historico_precos", "usuario")
        before = {t: fingerprint(db, t) for t in protected}
        links_before = fingerprint(db, "medicine_image_links")
        db.commit()
        accepted = []
        for line in (package / "manifest.jsonl").open():
            item = json.loads(line)
            content = (package / "blobs" / item["content_sha256"]).read_bytes()
            assert hashlib.sha256(content).hexdigest() == item["content_sha256"]
            item["content_base64"] = base64.b64encode(content).decode()
            with db.begin():
                status = import_item(db, "real-stage-validation", item)
            counts[status] += 1
            if status.startswith(("INSERTED", "REPLACED")):
                accepted.append(item)
        assert accepted, "no_real_identity_matches"
        for item in accepted:
            with db.begin():
                assert import_item(db, "real-stage-validation", item) == "CHECKPOINT"
        with db.begin():
            assert rollback(db, "real-stage-validation") == len(accepted)
            assert fingerprint(db, "medicine_image_links") == links_before
            assert {t: fingerprint(db, t) for t in protected} == before
    print(json.dumps({"REAL_STAGING_PASSED": dict(counts), "rollback": len(accepted)}))


if __name__ == "__main__":
    main()
