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
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid5

import httpx
from sqlalchemy import MetaData, Table, create_engine, func, inspect, select
from sqlalchemy.orm import Session, load_only
from sqlalchemy.orm.attributes import set_committed_value

from app.core.utils import validar_ean13
from app.models import CatalogoBase, Farmacia, OfertaFarmacia
from app.models.medicine_image import MedicineImageBlob, MedicineImageLink
from app.scrapers.scraper_batch_client import (
    BatchSyncError,
    _image_endpoint,
    _retry_after_seconds,
    _signed_headers,
    post_image_upload,
)
from app.services.imagens import is_generic_image_url
from app.services.medicine_images import (
    InvalidMedicineImage,
    normalize_medicine_image,
    validate_normalized_medicine_image,
)

_UPLOAD_NAMESPACE = UUID("bc8517a9-ff04-49f3-8f2a-7b0cb8987d93")
_BATCH_SIZE = 100
_MAX_UPLOADS_PER_RUN = 50
_DEFAULT_TRANSFER_DELAY_SECONDS = 7.5


@dataclass(frozen=True)
class Candidate:
    dev_catalog_id: int
    blob_id: int | None
    source: str
    sku: str
    ean: str
    product_url: str
    source_image_url: str
    content_type: str
    content_sha256: str
    size_bytes: int
    is_generic_fallback: bool
    source_store: str = "medicine_image_blobs"
    source_image_id: int | None = None
    source_content_sha256: str | None = None
    source_size_bytes: int | None = None
    source_content_type: str | None = None

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


def _checkpoint_path() -> Path:
    return _report_path().with_name("development-image-migration-checkpoint.json")


def _candidate_key(candidate: Candidate) -> str:
    return (
        f"{candidate.source}:{candidate.sku}:{candidate.ean}:{candidate.content_sha256}"
    )


def _load_checkpoint() -> dict[str, str]:
    path = _checkpoint_path()
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("image_migration_checkpoint_invalid") from error
    if (
        not isinstance(value, dict)
        or value.get("schema") != "preco-bao.image-migration-checkpoint"
        or value.get("version") != 1
        or not isinstance(value.get("completed"), dict)
        or any(
            not isinstance(key, str) or not isinstance(status, str)
            for key, status in value["completed"].items()
        )
    ):
        raise RuntimeError("image_migration_checkpoint_invalid")
    return value["completed"]


def _write_checkpoint(completed: dict[str, str]) -> None:
    path = _checkpoint_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {
                "schema": "preco-bao.image-migration-checkpoint",
                "version": 1,
                "updated_at": datetime.now(UTC).isoformat(),
                "completed": completed,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


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
    if "indiana" in normalized:
        return "indiana"
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
    if candidate.source_store == "imagens_scrape":
        image_table = Table("imagens_scrape", MetaData(), autoload_with=db.get_bind())
        content = db.execute(
            select(image_table.c.content).where(
                image_table.c.id == candidate.source_image_id
            )
        ).scalar_one()
        if (
            len(content) != candidate.source_size_bytes
            or hashlib.sha256(content).hexdigest() != candidate.source_content_sha256
        ):
            raise RuntimeError("source_image_changed_during_migration")
        normalized = normalize_medicine_image(
            content,
            declared_content_type=candidate.source_content_type,
            max_bytes=int(os.getenv("SCRAPER_IMAGE_MAX_BYTES", str(512 * 1024))),
            max_dimension=int(os.getenv("SCRAPER_IMAGE_MAX_DIMENSION", "1200")),
        )
        if (
            normalized.sha256 != candidate.content_sha256
            or len(normalized.content) != candidate.size_bytes
            or normalized.content_type != candidate.content_type
        ):
            raise RuntimeError("source_image_changed_during_migration")
        return normalized.content

    content = db.execute(
        select(MedicineImageBlob.image_data).where(
            MedicineImageBlob.id == candidate.blob_id
        )
    ).scalar_one()
    if hashlib.sha256(content).hexdigest() != candidate.content_sha256:
        raise RuntimeError("source_image_changed_during_migration")
    return content


def _load_scraped_image_candidates(
    db: Session,
) -> tuple[list[Candidate], Counter[str]]:
    """Read the legacy raw-byte archive without changing its schema or rows."""
    counts: Counter[str] = Counter()
    inspector = inspect(db.get_bind())
    if not inspector.has_table("imagens_scrape"):
        counts["scraped_image_table_missing"] = 1
        return [], counts

    image_table = Table("imagens_scrape", MetaData(), autoload_with=db.get_bind())
    required = {"id", "image_url", "content_type", "sha256", "size_bytes", "content"}
    if not required.issubset(
        {column["name"] for column in inspector.get_columns("imagens_scrape")}
    ):
        counts["scraped_image_schema_unsupported"] = 1
        return [], counts

    counts["scraped_image_records"] = int(
        db.execute(select(func.count()).select_from(image_table)).scalar_one()
    )
    counts["scraped_image_bytes"] = int(
        db.execute(
            select(func.coalesce(func.sum(image_table.c.size_bytes), 0))
        ).scalar_one()
    )
    counts["scraped_unique_hashes"] = int(
        db.execute(select(func.count(func.distinct(image_table.c.sha256)))).scalar_one()
    )
    counts["scraped_size_mismatches"] = int(
        db.execute(
            select(func.count())
            .select_from(image_table)
            .where(func.length(image_table.c.content) != image_table.c.size_bytes)
        ).scalar_one()
    )

    # PostgreSQL's built-in SHA-256 permits a read-only integrity pass over all
    # archived BYTEA values without sending them to the client or logging bytes.
    integrity_invalid_ids: set[int] = set()
    if db.get_bind().dialect.name == "postgresql":
        counts["scraped_hash_mismatches"] = int(
            db.execute(
                select(func.count())
                .select_from(image_table)
                .where(
                    image_table.c.sha256
                    != func.encode(func.sha256(image_table.c.content), "hex")
                )
            ).scalar_one()
        )
        integrity_invalid_ids = {
            int(image_id)
            for image_id in db.execute(
                select(image_table.c.id).where(
                    (func.length(image_table.c.content) != image_table.c.size_bytes)
                    | (
                        image_table.c.sha256
                        != func.encode(func.sha256(image_table.c.content), "hex")
                    )
                )
            ).scalars()
        }
    normalized_by_source_hash: dict[tuple[str, str], tuple[str, str, int]] = {}

    joined = (
        select(
            image_table.c.id,
            image_table.c.image_url,
            image_table.c.content_type,
            image_table.c.sha256,
            image_table.c.size_bytes,
            OfertaFarmacia.id.label("offer_id"),
            OfertaFarmacia.catalogo_id,
            OfertaFarmacia.farmacia_id,
            OfertaFarmacia.sku_interno,
            OfertaFarmacia.url_origem,
            Farmacia.nome_fantasia,
            CatalogoBase.codigo_barras,
        )
        .select_from(image_table)
        .outerjoin(OfertaFarmacia, OfertaFarmacia.imagem_url == image_table.c.image_url)
        .outerjoin(Farmacia, Farmacia.id == OfertaFarmacia.farmacia_id)
        .outerjoin(CatalogoBase, CatalogoBase.id == OfertaFarmacia.catalogo_id)
        .order_by(image_table.c.id, OfertaFarmacia.id)
    )
    records: dict[int, dict[str, Any]] = {}
    for row in db.execute(joined).yield_per(200):
        item = records.setdefault(
            int(row.id),
            {
                "image_url": str(row.image_url),
                "content_type": str(row.content_type),
                "sha256": str(row.sha256),
                "size_bytes": int(row.size_bytes),
                "identities": set(),
            },
        )
        if row.offer_id is None:
            continue
        item["identities"].add(
            (
                int(row.offer_id),
                int(row.catalogo_id),
                int(row.farmacia_id),
                str(row.nome_fantasia or ""),
                str(row.sku_interno or ""),
                str(row.url_origem or ""),
                str(row.codigo_barras or ""),
            )
        )

    selected: dict[int, Candidate] = {}
    for image_id, item in records.items():
        identities = item["identities"]
        if not identities:
            counts["scraped_unmatched_offer"] += 1
            continue
        # Identical image URLs reused by contexts of the same offer identity are
        # safe; a URL shared across different products or sources is ambiguous.
        identity_values = {
            (catalog_id, pharmacy_id, name, sku, product_url, ean)
            for (
                _offer_id,
                catalog_id,
                pharmacy_id,
                name,
                sku,
                product_url,
                ean,
            ) in identities
        }
        if len(identity_values) != 1:
            counts["scraped_identity_conflicts"] += 1
            continue
        catalog_id, _pharmacy_id, pharmacy_name, sku, product_url, ean = next(
            iter(identity_values)
        )
        source = _source_from_name(pharmacy_name)
        if source is None:
            counts["scraped_unsupported_pharmacy"] += 1
            continue
        if source == "indiana":
            counts["scraped_indiana_hmac_unsupported"] += 1
            continue
        counts[f"scraped_{source}_matched"] += 1
        if not re.fullmatch(r"ARJ\d+" if source == "araujo" else r"PCH\d+", sku):
            counts[f"scraped_{source}_invalid_sku"] += 1
            continue
        if not validar_ean13(ean):
            counts[f"scraped_{source}_invalid_ean"] += 1
            continue
        if is_generic_image_url(item["image_url"]):
            counts[f"scraped_{source}_generic_excluded"] += 1
            continue
        if image_id in integrity_invalid_ids:
            counts[f"scraped_{source}_invalid_hash_or_size"] += 1
            continue
        cache_key = (item["sha256"], item["content_type"])
        normalized_metadata = normalized_by_source_hash.get(cache_key)
        if normalized_metadata is None:
            raw = db.execute(
                select(image_table.c.content).where(image_table.c.id == image_id)
            ).scalar_one()
            if (
                len(raw) != item["size_bytes"]
                or hashlib.sha256(raw).hexdigest() != item["sha256"]
            ):
                counts[f"scraped_{source}_invalid_hash_or_size"] += 1
                continue
            try:
                normalized = normalize_medicine_image(
                    raw,
                    declared_content_type=item["content_type"],
                    max_bytes=int(
                        os.getenv("SCRAPER_IMAGE_MAX_BYTES", str(512 * 1024))
                    ),
                    max_dimension=int(os.getenv("SCRAPER_IMAGE_MAX_DIMENSION", "1200")),
                )
            except (InvalidMedicineImage, ValueError):
                counts[f"scraped_{source}_invalid_image"] += 1
                continue
            normalized_metadata = (
                normalized.content_type,
                normalized.sha256,
                len(normalized.content),
            )
            normalized_by_source_hash[cache_key] = normalized_metadata
        normalized_content_type, normalized_sha256, normalized_size = (
            normalized_metadata
        )
        counts[f"scraped_{source}_valid_images"] += 1
        candidate = Candidate(
            dev_catalog_id=catalog_id,
            blob_id=None,
            source=source,
            sku=sku,
            ean=ean,
            product_url=product_url,
            source_image_url=item["image_url"],
            content_type=normalized_content_type,
            content_sha256=normalized_sha256,
            size_bytes=normalized_size,
            is_generic_fallback=False,
            source_store="imagens_scrape",
            source_image_id=image_id,
            source_content_sha256=item["sha256"],
            source_size_bytes=item["size_bytes"],
            source_content_type=item["content_type"],
        )
        current = selected.get(catalog_id)
        if current is None or current.is_generic_fallback:
            selected[catalog_id] = candidate
        else:
            counts[f"scraped_{source}_duplicate_catalog"] += 1

    counts["scraped_candidate_medicines"] = len(selected)
    counts["scraped_candidate_bytes"] = sum(
        candidate.size_bytes for candidate in selected.values()
    )
    return list(selected.values()), counts


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
        delay = 2.0
        for attempt in range(4):
            try:
                response = await client.post(
                    url,
                    content=body,
                    headers=_signed_headers(body, secret),
                )
            except httpx.HTTPError as error:
                if attempt == 3:
                    raise RuntimeError("migration_check_transport_error") from error
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)
                continue
            if response.status_code == 429:
                if attempt == 3:
                    raise RuntimeError("migration_check_rate_limited")
                retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
                await asyncio.sleep(
                    float(retry_after) if retry_after is not None else delay
                )
                delay = min(delay * 2, 30.0)
                continue
            if response.status_code >= 500 and attempt < 3:
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)
                continue
            if response.status_code != 200:
                raise RuntimeError(f"migration_check_http_{response.status_code}")
            break
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


def _catalog_image_url(catalog_id: int) -> str:
    endpoint = _image_endpoint()
    parsed = urlsplit(endpoint)
    suffix = "/api/v1/internal/medicine-images"
    path = parsed.path.rstrip("/")
    if not path.endswith(suffix):
        raise RuntimeError("production_image_api_path_invalid")
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            f"{path[: -len(suffix)]}/catalogo/medicamentos/{catalog_id}/imagem",
            "",
            "",
        )
    )


async def _verify_image_delivery(
    client: httpx.AsyncClient, *, catalog_id: int, expected_sha256: str
) -> None:
    response = await client.get(_catalog_image_url(catalog_id))
    if response.status_code != 200:
        raise RuntimeError(f"production_image_delivery_http_{response.status_code}")
    if response.headers.get("etag", "").strip('"') != expected_sha256:
        raise RuntimeError("production_image_delivery_etag_mismatch")
    if hashlib.sha256(response.content).hexdigest() != expected_sha256:
        raise RuntimeError("production_image_delivery_hash_mismatch")


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
    completed = _load_checkpoint() if is_execute else {}
    if is_execute:
        report["execution_limit"] = args.limit
        report["execution_scope"] = "all_eligible" if args.all_eligible else "capped"
        report["checkpoint_completed_before_run"] = len(completed)
        transfer_delay = float(
            os.getenv(
                "SCRAPER_IMAGE_TRANSFER_DELAY_SECONDS",
                str(_DEFAULT_TRANSFER_DELAY_SECONDS),
            )
        )
        if not 6.5 <= transfer_delay <= 60.0:
            raise RuntimeError("image_transfer_delay_outside_safe_range")
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.exec_driver_sql(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                )
                with Session(
                    bind=connection, join_transaction_mode="create_savepoint"
                ) as db:
                    candidates, counts, _distinct = _load_descriptors(db)
                    scraped_candidates, scraped_counts = _load_scraped_image_candidates(
                        db
                    )
                    counts.update(scraped_counts)
                    # The raw archive is the primary source. Keep the previous
                    # normalized blob store as a complementary source when the
                    # raw archive has no safe candidate for a local catalog.
                    combined = {
                        item.dev_catalog_id: item for item in scraped_candidates
                    }
                    for item in candidates:
                        if item.is_generic_fallback:
                            counts["development_generic_images_excluded"] += 1
                            continue
                        combined.setdefault(item.dev_catalog_id, item)
                    candidates = list(combined.values())
                    counts["candidate_medicines"] = len(candidates)
                    counts["eligible_source_candidates"] = len(candidates)
                    counts["candidate_bytes"] = sum(
                        item.size_bytes for item in candidates
                    )
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
                            attempted = 0
                            upload_limit = (
                                len(candidates)
                                if args.all_eligible
                                else int(args.limit)
                            )
                            for candidate, initial in zip(
                                candidates, checked, strict=True
                            ):
                                if initial.get("status") != "ELIGIBLE":
                                    outcomes[str(initial.get("status"))] += 1
                                    report["execution_counts"] = dict(outcomes)
                                    _write_report(report)
                                    continue
                                candidate_key = _candidate_key(candidate)
                                if candidate_key in completed:
                                    outcomes["CHECKPOINT_ALREADY_CONFIRMED"] += 1
                                    report["execution_counts"] = dict(outcomes)
                                    _write_report(report)
                                    continue
                                if attempted >= upload_limit:
                                    break
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
                                if not isinstance(fresh.get("catalog_id"), int):
                                    raise RuntimeError(
                                        "migration_check_catalog_id_missing"
                                    )
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
                                production_catalog_id = response.get("catalog_id")
                                if production_catalog_id != fresh["catalog_id"]:
                                    raise RuntimeError(
                                        "production_image_catalog_id_mismatch"
                                    )
                                expected_sha256 = str(response.get("sha256", ""))
                                if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
                                    raise RuntimeError(
                                        "production_image_upload_hash_missing"
                                    )
                                await _verify_image_delivery(
                                    client,
                                    catalog_id=production_catalog_id,
                                    expected_sha256=expected_sha256,
                                )
                                completed[candidate_key] = str(response["status"])
                                _write_checkpoint(completed)
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
                                attempted += 1
                                report["execution_counts"] = dict(outcomes)
                                _write_report(report)
                                if attempted < upload_limit:
                                    await asyncio.sleep(transfer_delay)
                            report["execution_counts"] = dict(outcomes)
                transaction.rollback()
            except Exception:
                transaction.rollback()
                raise
    except Exception as error:
        report["state"] = "ABORTED"
        report["error_type"] = type(error).__name__
        _write_report(report)
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
        help="Transfer a capped number of eligible images after backup verification.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help=(
            "Required for execution; maximum "
            f"{_MAX_UPLOADS_PER_RUN} uploads per invocation."
        ),
    )
    parser.add_argument(
        "--all-eligible",
        action="store_true",
        help="Process every eligible candidate sequentially with rate-safe pacing.",
    )
    args = parser.parse_args()
    if args.limit is not None and not 1 <= args.limit <= _MAX_UPLOADS_PER_RUN:
        parser.error(f"--limit must be between 1 and {_MAX_UPLOADS_PER_RUN}")
    if args.execute and (args.all_eligible == (args.limit is not None)):
        parser.error("--execute requires exactly one of --limit or --all-eligible")
    if not args.execute and (args.limit is not None or args.all_eligible):
        parser.error("--limit and --all-eligible require --execute")
    try:
        return asyncio.run(_run(args))
    except Exception as error:
        # Avoid leaking DSNs or request details from driver/library exceptions.
        print(
            "image migration stopped "
            f"({type(error).__name__}); credentials and URLs were not logged",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
