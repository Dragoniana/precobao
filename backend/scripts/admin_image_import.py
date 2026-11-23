"""Restricted offline image import. Run against an isolated database first.

Input is JSONL with Candidate fields and base64 normalized bytes, never URLs
to download. DATABASE_URL is supplied by the administrator, never a CLI flag.
The database journal is both the checkpoint and conflict-aware rollback log.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.api.routes.medicine_image_ingest import _pharmacy_matches_source
from app.core.utils import validar_ean13
from app.models import CatalogoBase, Farmacia, OfertaFarmacia
from app.models.medicine_image import MedicineImageBlob, MedicineImageLink
from app.services.imagens import is_generic_image_url
from app.services.medicine_images import validate_normalized_medicine_image

FIELDS = (
    "blob_id",
    "catalog_id",
    "pharmacy_id",
    "offer_id",
    "sku",
    "ean",
    "source",
    "source_image_url",
    "is_primary",
    "is_generic",
    "updated_at",
)


def snapshot(link):
    return {
        key: (
            getattr(link, key).isoformat()
            if key == "updated_at"
            else getattr(link, key)
        )
        for key in FIELDS
    }


def initialize(connection):
    connection.execute(
        text("""CREATE TABLE IF NOT EXISTS admin_image_import_journal (
        run_id text NOT NULL, item_key text NOT NULL, link_id integer NOT NULL,
        before_state jsonb, after_state jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        rolled_back boolean NOT NULL DEFAULT false,
        PRIMARY KEY(run_id,item_key))""")
    )
    connection.execute(
        text(
            "ALTER TABLE admin_image_import_journal ADD COLUMN IF NOT EXISTS "
            "rollback_status text NOT NULL DEFAULT 'PENDING'"
        )
    )


def import_item(db, run_id, item):
    key = hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()
    if db.execute(
        text(
            "SELECT 1 FROM admin_image_import_journal WHERE run_id=:r AND item_key=:k"
        ),
        {"r": run_id, "k": key},
    ).first():
        return "CHECKPOINT"
    content = base64.b64decode(item["content_base64"], validate=True)
    image = validate_normalized_medicine_image(
        content, declared_content_type=item["content_type"]
    )
    if image.sha256 != item["content_sha256"]:
        raise ValueError("hash_mismatch")
    if not validar_ean13(item["ean"]):
        return "INVALID_EAN"
    rows = db.execute(
        select(OfertaFarmacia, Farmacia, CatalogoBase)
        .join(Farmacia, Farmacia.id == OfertaFarmacia.farmacia_id)
        .join(CatalogoBase, CatalogoBase.id == OfertaFarmacia.catalogo_id)
        .where(
            OfertaFarmacia.sku_interno == item["sku"],
            OfertaFarmacia.url_origem == item["product_url"],
            CatalogoBase.codigo_barras == item["ean"],
        )
    ).all()
    rows = [
        r for r in rows if _pharmacy_matches_source(r[1].nome_fantasia, item["source"])
    ]
    if len(rows) != 1:
        return "IDENTITY_UNRESOLVED"
    offer, pharmacy, catalog = rows[0]
    db.execute(
        select(CatalogoBase.id).where(CatalogoBase.id == catalog.id).with_for_update()
    ).scalar_one()
    generic = is_generic_image_url(item["source_image_url"])
    if generic != item["is_generic_fallback"]:
        raise ValueError("generic_classification_mismatch")
    blob = db.execute(
        select(MedicineImageBlob).where(MedicineImageBlob.sha256 == image.sha256)
    ).scalar_one_or_none()
    reused = blob is not None
    if blob is None:
        blob = MedicineImageBlob(
            sha256=image.sha256,
            content_type=image.content_type,
            image_data=content,
            size_bytes=len(content),
            width=image.width,
            height=image.height,
        )
        db.add(blob)
        db.flush()
    link = db.execute(
        select(MedicineImageLink)
        .where(
            MedicineImageLink.offer_id == offer.id,
            MedicineImageLink.is_generic.is_(generic),
        )
        .with_for_update(of=MedicineImageLink)
    ).scalar_one_or_none()
    before = snapshot(link) if link else None
    if link is None:
        primary = (
            not generic
            and db.execute(
                select(MedicineImageLink.id).where(
                    MedicineImageLink.catalog_id == catalog.id,
                    MedicineImageLink.is_primary.is_(True),
                )
            ).first()
            is None
        )
        link = MedicineImageLink(
            offer_id=offer.id,
            pharmacy_id=pharmacy.id,
            catalog_id=None if generic else catalog.id,
            sku=item["sku"],
            ean=item["ean"],
            source=item["source"],
            source_image_url=item["source_image_url"],
            is_generic=generic,
            is_primary=primary,
            blob_id=blob.id,
        )
        db.add(link)
    else:
        if (
            link.catalog_id not in (None, catalog.id)
            or link.pharmacy_id != pharmacy.id
            or link.ean != item["ean"]
        ):
            return "EXISTING_LINK_IDENTITY_CONFLICT"
        link.blob_id = blob.id
        link.source_image_url = item["source_image_url"]
    db.flush()
    db.refresh(link)
    db.execute(
        text(
            "INSERT INTO admin_image_import_journal"
            "(run_id,item_key,link_id,before_state,after_state) "
            "VALUES (:r,:k,:l,CAST(:b AS jsonb),CAST(:a AS jsonb))"
        ),
        {
            "r": run_id,
            "k": key,
            "l": link.id,
            "b": json.dumps(before),
            "a": json.dumps(snapshot(link)),
        },
    )
    return ("REPLACED" if before else "INSERTED") + (
        "_REUSED_BLOB" if reused else "_NEW_BLOB"
    )


def rollback(db, run_id, item_key=None):
    rows = (
        db.execute(
            text(
                "SELECT item_key,link_id,before_state,after_state "
                "FROM admin_image_import_journal WHERE run_id=:r "
                "AND NOT rolled_back AND (:k IS NULL OR item_key=:k) "
                "ORDER BY created_at DESC FOR UPDATE"
            ),
            {"r": run_id, "k": item_key},
        )
        .mappings()
        .all()
    )
    for row in rows:
        link = db.execute(
            select(MedicineImageLink)
            .where(MedicineImageLink.id == row["link_id"])
            .with_for_update(of=MedicineImageLink)
        ).scalar_one_or_none()
        if link is None or snapshot(link) != row["after_state"]:
            db.execute(
                text(
                    "UPDATE admin_image_import_journal SET rollback_status='CONFLICT' "
                    "WHERE run_id=:r AND item_key=:k"
                ),
                {"r": run_id, "k": row["item_key"]},
            )
            continue
        if row["before_state"] is None:
            db.delete(link)
        else:
            from datetime import datetime

            for key, value in row["before_state"].items():
                setattr(
                    link,
                    key,
                    datetime.fromisoformat(value) if key == "updated_at" else value,
                )
        db.flush()
        db.execute(
            text(
                "UPDATE admin_image_import_journal SET rolled_back=true, "
                "rollback_status='ROLLED_BACK' "
                "WHERE run_id=:r AND item_key=:k"
            ),
            {"r": run_id, "k": row["item_key"]},
        )
    return db.execute(
        text(
            "SELECT count(*) FROM admin_image_import_journal "
            "WHERE run_id=:r AND rolled_back"
        ),
        {"r": run_id},
    ).scalar_one()


def rollback_transactions(connection, run_id):
    """Commit each journal entry independently; durable conflicts remain retryable."""
    keys = (
        connection.execute(
            text(
                "SELECT item_key FROM admin_image_import_journal "
                "WHERE run_id=:r AND NOT rolled_back ORDER BY created_at DESC"
            ),
            {"r": run_id},
        )
        .scalars()
        .all()
    )
    connection.commit()
    for key in keys:
        try:
            with Session(bind=connection) as db, db.begin():
                rollback(db, run_id, key)
        except Exception:
            with connection.begin():
                connection.execute(
                    text(
                        "UPDATE admin_image_import_journal "
                        "SET rollback_status='FAILED' "
                        "WHERE run_id=:r AND item_key=:k AND NOT rolled_back"
                    ),
                    {"r": run_id, "k": key},
                )
            raise
    result = dict(
        connection.execute(
            text(
                "SELECT rollback_status,count(*) FROM admin_image_import_journal "
                "WHERE run_id=:r GROUP BY rollback_status"
            ),
            {"r": run_id},
        ).all()
    )
    connection.commit()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--manifest")
    parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args()
    package = None
    if not args.rollback:
        manifest = Path(args.manifest)
        package = json.loads((manifest.parent / "package.json").read_text())
        if package.get("format") != "preco-bao.admin-images.v1":
            raise ValueError("unsupported_package")
        if (
            hashlib.sha256(manifest.read_bytes()).hexdigest()
            != package["manifest_sha256"]
        ):
            raise ValueError("manifest_hash_mismatch")
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as connection:
        if not connection.execute(
            text("SELECT pg_try_advisory_lock(718932001)")
        ).scalar_one():
            raise RuntimeError("import_already_running")
        initialize(connection)
        connection.commit()
        counts = {}
        with Session(bind=connection) as db:
            if args.rollback:
                print(json.dumps(rollback_transactions(connection, args.run_id)))
            else:
                with open(args.manifest, encoding="utf-8") as stream:
                    for line in stream:
                        item = json.loads(line)
                        digest = item["content_sha256"]
                        if len(digest) != 64 or any(
                            c not in "0123456789abcdef" for c in digest
                        ):
                            raise ValueError("invalid_blob_digest")
                        content = (manifest.parent / "blobs" / digest).read_bytes()
                        item["content_base64"] = base64.b64encode(content).decode(
                            "ascii"
                        )
                        with db.begin():
                            status = import_item(db, args.run_id, item)
                        counts[status] = counts.get(status, 0) + 1
                print(json.dumps(counts))
    engine.dispose()


if __name__ == "__main__":
    main()
