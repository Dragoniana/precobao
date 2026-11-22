"""Safely migrate already normalized development images through the HMAC API.

Dry-run is the default. The source database is opened read-only and no source
records are changed. A real transfer additionally requires an explicit flag
and a locally verified production-backup marker.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid5

import httpx
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.orm import Session, load_only
from sqlalchemy.orm.attributes import set_committed_value

from app.core.utils import validar_ean13
from app.models import CatalogoBase, Farmacia, OfertaFarmacia
from app.models.medicine_image import MedicineImageBlob, MedicineImageLink
from app.scrapers.scraper_batch_client import (
    BatchSyncError,
    _image_endpoint,
    _signed_headers,
    post_image_upload,
)
from app.services.imagens import is_generic_image_url
from app.services.medicine_images import (
    InvalidMedicineImage,
    validate_normalized_medicine_image,
)

_UPLOAD_NAMESPACE = UUID("bc8517a9-ff04-49f3-8f2a-7b0cb8987d93")
_BATCH_SIZE = 100


@dataclass(frozen=True)
class Candidate:
    dev_catalog_id: int
    blob_id: int
    source: str
    sku: str
    ean: str
    product_url: str
    source_image_url: str
    content_type: str
    content_sha256: str
    size_bytes: int
    is_generic_fallback: bool

    def public(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "sku": self.sku,
            "ean": self.ean,
            "content_sha256": self.content_sha256,
            "is_generic_fallback": self.is_generic_fallback,
        }


def _report_path() -> Path:
    state = Path(os.getenv("SCRAPER_COLLECTOR_STATE_DIR", "data/scraper_collector"))
    return state.resolve() / "development-image-migration-report.json"


def _write_report(report: dict[str, Any]) -> None:
    path = _report_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temp_path.replace(path)


def _migration_check_url() -> str:
    endpoint = _image_endpoint()
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or not parsed.hostname:
        raise BatchSyncError("scraper_image_ingest_url_must_be_https")
    path = parsed.path.rstrip("/")
    if not path.endswith("/medicine-images"):
        raise BatchSyncError("scraper_image_ingest_url_path_invalid")
    return urlunsplit((parsed.scheme, parsed.netloc, f"{path}/migration-check", "", ""))


def _source_from_name(name: str) -> str | None:
    normalized = name.casefold()
    if "araujo" in normalized or "araújo" in normalized:
        return "araujo"
    if "pacheco" in normalized:
        return "pacheco"
    return None


def _load_descriptors(db: Session) -> tuple[list[Candidate], Counter[str], int]:
    counts: Counter[str] = Counter()
    counts["development_blobs"] = db.query(MedicineImageBlob.id).count()
    image_catalog_id = func.coalesce(
        MedicineImageLink.catalog_id, OfertaFarmacia.catalogo_id
    )
    link_columns = {
        column["name"]
        for column in inspect(db.get_bind()).get_columns(
            MedicineImageLink.__tablename__
        )
    }
    # Development databases can still be on 9a6d2f81c4b7, before generic
    # offer fallbacks were added. Read only the common columns in that case;
    # no migration or write to the source database is needed.
    link_attributes = [
        MedicineImageLink.id,
        MedicineImageLink.blob_id,
        MedicineImageLink.catalog_id,
        MedicineImageLink.offer_id,
        MedicineImageLink.pharmacy_id,
        MedicineImageLink.sku,
        MedicineImageLink.ean,
        MedicineImageLink.source,
        MedicineImageLink.source_image_url,
        MedicineImageLink.is_primary,
    ]
    has_generic_column = "is_generic" in link_columns
    if has_generic_column:
        link_attributes.append(MedicineImageLink.is_generic)
    statement = (
        select(
            MedicineImageLink,
            MedicineImageBlob,
            CatalogoBase,
            OfertaFarmacia,
            Farmacia,
        )
        .join(MedicineImageBlob, MedicineImageBlob.id == MedicineImageLink.blob_id)
        .outerjoin(OfertaFarmacia, OfertaFarmacia.id == MedicineImageLink.offer_id)
        .join(CatalogoBase, CatalogoBase.id == image_catalog_id)
        .join(Farmacia, Farmacia.id == MedicineImageLink.pharmacy_id)
        .where(MedicineImageLink.source.in_(("araujo", "pacheco")))
        .options(load_only(*link_attributes))
    )

    selected: dict[int, Candidate] = {}
    invalid_by_catalog: set[int] = set()
    distinct_catalogs: set[int] = set()
    last_link_id = 0
    while True:
        rows = db.execute(
            statement.where(MedicineImageLink.id > last_link_id)
            .order_by(MedicineImageLink.id)
            .limit(_BATCH_SIZE)
        ).all()
        if not rows:
            break
        last_link_id = int(rows[-1][0].id)
        for link, blob, catalog, offer, pharmacy in rows:
            if not has_generic_column:
                set_committed_value(link, "is_generic", False)
            catalog_id = int(catalog.id)
            distinct_catalogs.add(catalog_id)
            source = str(link.source)
            if _source_from_name(pharmacy.nome_fantasia) != source:
                counts["invalid_source_association"] += 1
                invalid_by_catalog.add(catalog_id)
                continue
            ean = str(catalog.codigo_barras or "")
            if not validar_ean13(ean):
                counts["invalid_ean"] += 1
                invalid_by_catalog.add(catalog_id)
                continue
            link_ean = str(link.ean or "")
            if link_ean and link_ean != ean:
                counts["divergent_identity_ean"] += 1
                invalid_by_catalog.add(catalog_id)
                continue
            if str(link.sku or "")[:3] != ("ARJ" if source == "araujo" else "PCH"):
                counts["invalid_source_sku"] += 1
                invalid_by_catalog.add(catalog_id)
                continue
            image_data = db.execute(
                select(MedicineImageBlob.image_data).where(
                    MedicineImageBlob.id == blob.id
                )
            ).scalar_one()
            digest = hashlib.sha256(image_data).hexdigest()
            try:
                validated = validate_normalized_medicine_image(
                    image_data,
                    declared_content_type=blob.content_type,
                    max_bytes=int(
                        os.getenv("SCRAPER_IMAGE_MAX_BYTES", str(512 * 1024))
                    ),
                    max_dimension=int(os.getenv("SCRAPER_IMAGE_MAX_DIMENSION", "1200")),
                )
            except (InvalidMedicineImage, ValueError):
                counts["invalid_image"] += 1
                invalid_by_catalog.add(catalog_id)
                continue
            if (
                digest != blob.sha256
                or digest != validated.sha256
                or len(image_data) != blob.size_bytes
            ):
                counts["invalid_hash_or_size"] += 1
                invalid_by_catalog.add(catalog_id)
                continue
            generic = is_generic_image_url(link.source_image_url)
            if generic != bool(link.is_generic):
                counts["invalid_generic_classification"] += 1
                invalid_by_catalog.add(catalog_id)
                continue
            product_url = str(offer.url_origem or "").strip() if offer else ""
            if offer and (
                offer.catalogo_id != catalog.id
                or offer.farmacia_id != link.pharmacy_id
                or offer.sku_interno != link.sku
            ):
                counts["invalid_source_offer_association"] += 1
                invalid_by_catalog.add(catalog_id)
                continue
            if not product_url and not generic:
                source_urls = set(
                    db.execute(
                        select(OfertaFarmacia.url_origem).where(
                            OfertaFarmacia.catalogo_id == catalog_id,
                            OfertaFarmacia.farmacia_id == link.pharmacy_id,
                            OfertaFarmacia.sku_interno == link.sku,
                        )
                    ).scalars()
                )
                source_urls.discard(None)
                source_urls.discard("")
                if len(source_urls) == 1:
                    product_url = source_urls.pop()
                else:
                    counts["missing_or_ambiguous_source_product_url"] += 1
                    invalid_by_catalog.add(catalog_id)
                    continue
            if not product_url:
                counts["missing_source_product_url"] += 1
                invalid_by_catalog.add(catalog_id)
                continue
            candidate = Candidate(
                dev_catalog_id=catalog_id,
                blob_id=int(blob.id),
                source=source,
                sku=str(link.sku),
                ean=ean,
                product_url=product_url,
                source_image_url=str(link.source_image_url),
                content_type=validated.content_type,
                content_sha256=digest,
                size_bytes=len(image_data),
                is_generic_fallback=generic,
            )
            current = selected.get(catalog_id)
            # A real, validated image is preferred over a generic fallback.
            if current is None or (current.is_generic_fallback and not generic):
                selected[catalog_id] = candidate

    counts["development_medicines_with_images"] = len(distinct_catalogs)
    counts["candidate_medicines"] = len(selected)
    counts["invalid_medicines"] = len(invalid_by_catalog)
    counts["eligible_source_candidates"] = len(selected)
    counts["candidate_bytes"] = sum(item.size_bytes for item in selected.values())
    return list(selected.values()), counts, len(distinct_catalogs)


def _source_image_bytes(db: Session, candidate: Candidate) -> bytes:
    content = db.execute(
        select(MedicineImageBlob.image_data).where(
            MedicineImageBlob.id == candidate.blob_id
        )
    ).scalar_one()
    if hashlib.sha256(content).hexdigest() != candidate.content_sha256:
        raise RuntimeError("source_image_changed_during_migration")
    return content


async def _check_candidates(
    client: httpx.AsyncClient, candidates: list[Candidate], secret: str
) -> list[dict[str, Any]]:
    url = _migration_check_url()
    results: list[dict[str, Any]] = []
    for offset in range(0, len(candidates), _BATCH_SIZE):
        batch = candidates[offset : offset + _BATCH_SIZE]
        payload = {
            "candidates": [
                candidate.public()
                | {
                    "product_url": candidate.product_url,
                    "source_image_url": candidate.source_image_url,
                    "content_type": candidate.content_type,
                }
                for candidate in batch
            ]
        }
        body = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        response = await client.post(
            url,
            content=body,
            headers=_signed_headers(body, secret),
        )
        if response.status_code != 200:
            raise RuntimeError(f"migration_check_http_{response.status_code}")
        decoded = response.json()
        remote = decoded.get("results")
        if not isinstance(remote, list) or len(remote) != len(batch):
            raise RuntimeError("migration_check_response_mismatch")
        for candidate, result in zip(batch, remote, strict=True):
            if (
                result.get("source") != candidate.source
                or result.get("sku") != candidate.sku
                or result.get("ean") != candidate.ean
                or result.get("content_sha256") != candidate.content_sha256
            ):
                raise RuntimeError("migration_check_identity_mismatch")
            results.append(result)
    return results


def _status_counts_by_source(
    candidates: list[Candidate], results: list[dict[str, Any]]
) -> dict[str, dict[str, int]]:
    if len(candidates) != len(results):
        raise RuntimeError("migration_check_response_mismatch")
    grouped: dict[str, Counter[str]] = {}
    for candidate, result in zip(candidates, results, strict=True):
        source_counts = grouped.setdefault(candidate.source, Counter())
        source_counts[str(result.get("status", "UNKNOWN"))] += 1
    return {
        source: dict(sorted(source_counts.items()))
        for source, source_counts in sorted(grouped.items())
    }


async def _run(args: argparse.Namespace) -> int:
    source_url = os.getenv("PRECOBAO_DEVELOPMENT_DATABASE_URL", "").strip()
    if not source_url:
        raise RuntimeError("PRECOBAO_DEVELOPMENT_DATABASE_URL_not_configured")
    engine = create_engine(source_url, pool_pre_ping=True)
    if engine.dialect.name != "postgresql":
        engine.dispose()
        raise RuntimeError("development_source_must_be_postgresql")

    is_execute = bool(args.execute)
    if is_execute and os.getenv("PRECOBAO_PRODUCTION_BACKUP_VERIFIED") != "1":
        engine.dispose()
        raise RuntimeError("verified_production_backup_required_for_execute")
    secret = os.getenv("SCRAPER_INGEST_HMAC_SECRET", "")
    if len(secret.encode("utf-8")) < 32:
        engine.dispose()
        raise RuntimeError("scraper_ingest_secret_not_configured")
    if is_execute:
        target = urlsplit(_image_endpoint())
        if (
            target.scheme != "https"
            or target.hostname is None
            or target.hostname.casefold() != "precobao.app"
            or target.port not in (None, 443)
        ):
            engine.dispose()
            raise RuntimeError("production_image_api_host_not_verified")

    report: dict[str, Any] = {
        "created_at": datetime.now(UTC).isoformat(),
        "mode": "execute" if is_execute else "dry_run",
    }
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                with Session(
                    bind=connection, join_transaction_mode="create_savepoint"
                ) as db:
                    candidates, counts, _distinct = _load_descriptors(db)
                    report["source_counts"] = dict(counts)
                    # Do not log full URLs, database DSNs, hashes or credentials.
                    async with httpx.AsyncClient(
                        timeout=httpx.Timeout(30.0), verify=True, follow_redirects=False
                    ) as client:
                        checked = await _check_candidates(client, candidates, secret)
                        states = Counter(str(item.get("status")) for item in checked)
                        report["production_check_counts"] = dict(states)
                        report["production_check_counts_by_source"] = (
                            _status_counts_by_source(candidates, checked)
                        )
                        report["identity_safe_candidates"] = sum(
                            item.get("status") == "ELIGIBLE" for item in checked
                        )
                        report["estimated_transfer_bytes"] = sum(
                            candidate.size_bytes
                            for candidate, result in zip(
                                candidates, checked, strict=True
                            )
                            if result.get("status") == "ELIGIBLE"
                        )
                        _write_report(report)
                        if is_execute:
                            outcomes = Counter()
                            for candidate, initial in zip(
                                candidates, checked, strict=True
                            ):
                                if initial.get("status") != "ELIGIBLE":
                                    outcomes[str(initial.get("status"))] += 1
                                    report["execution_counts"] = dict(outcomes)
                                    _write_report(report)
                                    continue
                                # Recheck immediately before each write to close
                                # races with the regular scraper.
                                fresh = (
                                    await _check_candidates(client, [candidate], secret)
                                )[0]
                                if fresh.get("status") != "ELIGIBLE":
                                    outcomes[str(fresh.get("status"))] += 1
                                    report["execution_counts"] = dict(outcomes)
                                    _write_report(report)
                                    continue
                                image_bytes = _source_image_bytes(db, candidate)
                                upload_id = str(
                                    uuid5(
                                        _UPLOAD_NAMESPACE,
                                        f"{candidate.source}:{candidate.sku}:{candidate.ean}:{candidate.content_sha256}",
                                    )
                                )
                                metadata = {
                                    "upload_id": upload_id,
                                    "source": candidate.source,
                                    "sku": candidate.sku,
                                    "ean": candidate.ean,
                                    "product_url": candidate.product_url,
                                    "source_image_url": candidate.source_image_url,
                                    "content_type": candidate.content_type,
                                    "content_sha256": candidate.content_sha256,
                                    "is_generic_fallback": (
                                        candidate.is_generic_fallback
                                    ),
                                    "only_if_missing": True,
                                    "migration_mode": True,
                                }
                                response = await post_image_upload(
                                    client, metadata, image_bytes
                                )
                                report.setdefault("confirmed", []).append(
                                    {
                                        "source": candidate.source,
                                        "sku": candidate.sku,
                                        "ean": candidate.ean,
                                        "sha256": candidate.content_sha256,
                                        "catalog_id": response.get("catalog_id"),
                                        "status": response["status"],
                                    }
                                )
                                outcomes[str(response["status"])] += 1
                                report["execution_counts"] = dict(outcomes)
                                _write_report(report)
                                await asyncio.sleep(0.25)
                            report["execution_counts"] = dict(outcomes)
                transaction.rollback()
            except Exception:
                transaction.rollback()
                raise
    finally:
        engine.dispose()

    _write_report(report)
    print(
        json.dumps(
            {key: value for key, value in report.items() if key != "confirmed"},
            ensure_ascii=False,
        )
    )
    print(f"Sanitized report saved: {_report_path()}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", action="store_true", help="Read-only production check (default)."
    )
    mode.add_argument(
        "--execute",
        action="store_true",
        help="Transfer eligible images after backup verification.",
    )
    args = parser.parse_args()
    try:
        return asyncio.run(_run(args))
    except Exception as error:
        # Avoid leaking DSNs or request details from driver/library exceptions.
        try:
            _write_report(
                {
                    "created_at": datetime.now(UTC).isoformat(),
                    "mode": "execute" if args.execute else "dry_run",
                    "state": "ABORTED",
                    "error_type": type(error).__name__,
                }
            )
        except OSError:
            pass
        print(
            "image migration stopped "
            f"({type(error).__name__}); credentials and URLs were not logged",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
