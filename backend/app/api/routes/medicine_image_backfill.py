"""HMAC-only production inventory and paging for image-only recovery."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.routes.medicine_image_ingest import _source_enabled
from app.api.routes.scraper_ingest import _authenticate_request
from app.core.database import get_db
from app.core.utils import validar_ean13
from app.models import CatalogoBase, OfertaFarmacia
from app.models.medicine_image import MedicineImageBlob, MedicineImageLink
from app.services.image_registry import _read_records, normalize_image_url
from app.services.imagens import (
    catalog_image_matches_record,
    embedded_eans,
    gtin_matches,
    is_generic_image_url,
    offer_image_delivery_rejection_reason,
)
from app.services.medicine_images import (
    InvalidMedicineImage,
    validate_normalized_medicine_image,
)

router = APIRouter(prefix="/internal/medicine-images", tags=["internal-image-backfill"])
_SOURCES = ("araujo", "pacheco")
_SKU_PATTERN = {"araujo": re.compile(r"^ARJ\d+$"), "pacheco": re.compile(r"^PCH\d+$")}
_PRODUCT_DOMAINS = {
    "araujo": "araujo.com.br",
    "pacheco": "drogariaspacheco.com.br",
}


class BackfillPageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    after_catalog_id: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=100)
    source: str | None = Field(default=None, pattern=r"^(araujo|pacheco)$")


def _source_for_pharmacy(name: str) -> str | None:
    normalized = name.casefold()
    for source in _SOURCES:
        if source in normalized.replace("ú", "u"):
            return source
    return None


def _product_url_matches(url: str, source: str) -> bool:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").casefold()
        port = parsed.port
    except ValueError:
        return False
    domain = _PRODUCT_DOMAINS[source]
    return bool(
        parsed.scheme == "https"
        and not parsed.username
        and not parsed.password
        and port in (None, 443)
        and (host == domain or host.endswith(f".{domain}"))
    )


def _blob_is_valid(blob: MedicineImageBlob) -> bool:
    try:
        content = blob.image_data
        if (
            not content
            or len(content) != blob.size_bytes
            or hashlib.sha256(content).hexdigest() != blob.sha256
        ):
            return False
        checked = validate_normalized_medicine_image(
            content, declared_content_type=blob.content_type
        )
        return (
            checked.sha256 == blob.sha256
            and checked.width == blob.width
            and checked.height == blob.height
        )
    except (AttributeError, InvalidMedicineImage, TypeError, ValueError, OSError):
        return False


def _stored_image_state(
    catalog: CatalogoBase, offers: list[OfertaFarmacia]
) -> tuple[bool, bool, int]:
    offers_by_id = {offer.id: offer for offer in offers}
    real = False
    generic = False
    conflicts = 0
    links_by_id = {link.id: link for link in catalog.image_links}
    for offer in offers:
        links_by_id.update({link.id: link for link in offer.image_links})
    for link in links_by_id.values():
        if not _blob_is_valid(link.blob):
            conflicts += 1
            continue
        offer = offers_by_id.get(link.offer_id) if link.offer_id is not None else None
        source = (
            _source_for_pharmacy(offer.farmacia.nome_fantasia) if offer else link.source
        )
        identity_ok = bool(
            source in _SOURCES
            and source == link.source
            and (offer is None or offer.sku_interno == link.sku)
            and (link.ean is None or gtin_matches(catalog.codigo_barras, link.ean))
            and (
                offer is not None
                or (
                    link.ean is not None
                    and gtin_matches(catalog.codigo_barras, link.ean)
                )
                or (
                    catalog.imagem_sku_origem == link.sku
                    and catalog.imagem_fonte
                    and _source_for_pharmacy(catalog.imagem_fonte) == source
                )
            )
        )
        if link.is_generic:
            identity_ok = bool(
                identity_ok
                and offer is not None
                and link.catalog_id is None
                and offer.catalogo_id == catalog.id
            )
        else:
            identity_ok = bool(
                identity_ok
                and link.catalog_id == catalog.id
                and (offer is None or offer.catalogo_id == catalog.id)
            )
        if not identity_ok:
            conflicts += 1
        elif link.is_generic:
            generic = True
        else:
            real = True
    return real, generic, conflicts


def _known_image_urls(
    catalog: CatalogoBase,
    offer: OfertaFarmacia,
    source: str,
    registry_urls: list[str] | None = None,
) -> list[dict[str, object]]:
    pharmacy_name = "Araujo" if source == "araujo" else "Pacheco"
    raw_urls: list[tuple[str, str]] = []
    if offer.imagem_url:
        raw_urls.append((offer.imagem_url, "offer"))
    if (
        catalog.imagem_url
        and catalog.imagem_fonte
        and _source_for_pharmacy(catalog.imagem_fonte) == source
        and catalog_image_matches_record(catalog)
        and catalog_image_matches_offer(catalog, offer)
    ):
        raw_urls.append((catalog.imagem_url, "catalog"))
    for url in registry_urls or []:
        raw_urls.append((url, "registry"))

    seen: set[str] = set()
    result: list[dict[str, object]] = []
    for url, recorded_in in raw_urls:
        if url in seen:
            continue
        seen.add(url)
        if offer_image_delivery_rejection_reason(url, offer.url_origem, pharmacy_name):
            continue
        embedded = embedded_eans(url)
        if (
            embedded
            and catalog.codigo_barras
            and not any(
                gtin_matches(catalog.codigo_barras, value) for value in embedded
            )
        ):
            continue
        result.append(
            {
                "url": url,
                "recorded_in": recorded_in,
                "generic": is_generic_image_url(url),
            }
        )
    return result


def _registry_images_by_offer(offers: list[OfertaFarmacia]) -> dict[int, list[str]]:
    """Index only exact, identity-confirmed registry records for this page."""
    by_id = {offer.id: offer for offer in offers}
    by_result: dict[int, list[str]] = {offer.id: [] for offer in offers}
    for record in _read_records():
        offer_id = record.get("offer_id")
        matches: list[OfertaFarmacia] = []
        if offer_id is not None:
            offer = by_id.get(int(offer_id)) if str(offer_id).isdigit() else None
            if offer is not None:
                matches = [offer]
        else:
            catalog_id = record.get("catalog_id")
            sku = record.get("sku")
            matches = [
                offer
                for offer in offers
                if offer.catalogo_id == catalog_id and sku and offer.sku_interno == sku
            ]
        if record.get("identity_confirmed") is not True:
            continue
        url = normalize_image_url(record.get("image_url"), record.get("product_url"))
        if not url:
            continue
        for offer in matches:
            catalog = offer.produto_catalogo
            if record.get("catalog_id") != catalog.id:
                continue
            if record.get("pharmacy_id") not in (None, offer.farmacia_id):
                continue
            if record.get("sku") and record.get("sku") != offer.sku_interno:
                continue
            if (
                record.get("product_url")
                and record.get("product_url") != offer.url_origem
            ):
                continue
            record_ean = record.get("ean")
            if (
                record_ean
                and catalog.codigo_barras
                and not gtin_matches(catalog.codigo_barras, str(record_ean))
            ):
                continue
            if url not in by_result[offer.id]:
                by_result[offer.id].append(url)
    return by_result


def catalog_image_matches_offer(catalog: CatalogoBase, offer: OfertaFarmacia) -> bool:
    if catalog.imagem_sku_origem and catalog.imagem_sku_origem == offer.sku_interno:
        return True
    return bool(
        catalog.imagem_match == "ean"
        and catalog.imagem_ean
        and gtin_matches(catalog.codigo_barras, catalog.imagem_ean)
    )


def _entry(
    catalog: CatalogoBase, registry_urls: dict[int, list[str]] | None = None
) -> dict[str, object]:
    offers = list(catalog.ofertas)
    real, generic, conflicts = _stored_image_state(catalog, offers)
    by_source: dict[str, list[dict[str, object]]] = {source: [] for source in _SOURCES}
    unsupported_source_found = False
    identity_conflict = False
    enabled_source_offer_count = 0
    disabled_source_offer_count = 0
    for offer in offers:
        source = _source_for_pharmacy(offer.farmacia.nome_fantasia)
        if source is None:
            unsupported_source_found = True
            continue
        if not _source_enabled(source):
            disabled_source_offer_count += 1
            by_source[source].append(
                {
                    "sku": offer.sku_interno,
                    "product_url": offer.url_origem,
                    "image_urls": [],
                    "status": "SOURCE_NOT_ENABLED",
                }
            )
            continue
        enabled_source_offer_count += 1
        prefix_ok = bool(_SKU_PATTERN[source].fullmatch(offer.sku_interno or ""))
        url_ok = _product_url_matches(offer.url_origem or "", source)
        ean_ok = bool(catalog.codigo_barras and validar_ean13(catalog.codigo_barras))
        if not (prefix_ok and url_ok and ean_ok):
            identity_conflict = True
            continue
        images = _known_image_urls(
            catalog, offer, source, (registry_urls or {}).get(offer.id, [])
        )
        by_source[source].append(
            {
                "sku": offer.sku_interno,
                "product_url": offer.url_origem,
                "image_urls": images,
            }
        )
    for source in _SOURCES:
        unique: dict[tuple[str, str], dict[str, object]] = {}
        for item in by_source[source]:
            key = (str(item["sku"]), str(item["product_url"]))
            unique[key] = item
        by_source[source] = list(unique.values())

    if real:
        status = "BYTEA_REAL_VALID"
    elif generic:
        status = "BYTEA_GENERIC_VALID"
    elif conflicts:
        status = "IDENTITY_CONFLICT"
    elif identity_conflict:
        status = "IDENTITY_CONFLICT"
    elif any(
        item["image_urls"] and any(not url["generic"] for url in item["image_urls"])
        for source_offers in by_source.values()
        for item in source_offers
    ):
        status = "EXTERNAL_URL_CANDIDATE"
    elif any(
        item["image_urls"]
        for source_offers in by_source.values()
        for item in source_offers
    ):
        status = "GENERIC_ONLY"
    elif enabled_source_offer_count:
        status = "NO_KNOWN_IMAGE_URL"
    elif disabled_source_offer_count:
        status = "SOURCE_NOT_ENABLED"
    elif unsupported_source_found:
        status = "SOURCE_NOT_ENABLED"
    else:
        status = "NO_SOURCE_OFFER"

    return {
        "catalog_id": catalog.id,
        "canonical_name": catalog.name_search,
        "ean": catalog.codigo_barras,
        "status": status,
        "identity_conflicts": conflicts + int(identity_conflict),
        "eligible_for_backfill": status
        in {"EXTERNAL_URL_CANDIDATE", "GENERIC_ONLY", "NO_KNOWN_IMAGE_URL"},
        "sources": [
            {"source": source, "offers": by_source[source]}
            for source in _SOURCES
            if by_source[source]
        ],
    }


def _load_catalogs(db: Session, after_id: int = 0, limit: int | None = None):
    statement = (
        select(CatalogoBase)
        .where(CatalogoBase.id > after_id)
        .options(
            selectinload(CatalogoBase.ofertas).joinedload(OfertaFarmacia.farmacia),
            selectinload(CatalogoBase.ofertas)
            .selectinload(OfertaFarmacia.image_links)
            .joinedload(MedicineImageLink.blob),
            selectinload(CatalogoBase.image_links).joinedload(MedicineImageLink.blob),
        )
        .order_by(CatalogoBase.id)
    )
    if limit is not None:
        statement = statement.limit(limit)
    return list(db.execute(statement).scalars().unique().all())


@router.post("/backfill-inventory")
async def backfill_inventory(request: Request, db: Session = Depends(get_db)):
    body = await request.body()
    _authenticate_request(request, body)
    if body not in (b"", b"{}"):
        raise HTTPException(status_code=422, detail="inventory_body_must_be_empty")
    counts: Counter[str] = Counter()
    by_source: dict[str, Counter[str]] = {source: Counter() for source in _SOURCES}
    total = 0
    after = 0
    page_size = 500
    while True:
        catalogs = _load_catalogs(db, after_id=after, limit=page_size)
        if not catalogs:
            break
        registry_urls = _registry_images_by_offer(
            [offer for catalog in catalogs for offer in catalog.ofertas]
        )
        for catalog in catalogs:
            entry = _entry(catalog, registry_urls)
            counts[str(entry["status"])] += 1
            total += 1
            if entry["eligible_for_backfill"]:
                for source_entry in entry["sources"]:
                    source = str(source_entry["source"])
                    offers = source_entry["offers"]
                    has_real = any(
                        any(not url["generic"] for url in offer["image_urls"])
                        for offer in offers
                    )
                    has_generic = any(
                        any(url["generic"] for url in offer["image_urls"])
                        for offer in offers
                    )
                    if has_real:
                        by_source[source]["EXTERNAL_URL_CANDIDATE"] += 1
                    elif has_generic:
                        by_source[source]["GENERIC_ONLY"] += 1
                    elif offers:
                        by_source[source]["NO_KNOWN_IMAGE_URL"] += 1
        after = catalogs[-1].id
        if len(catalogs) < page_size:
            break
    return {
        "catalog_total": total,
        "status_counts": dict(counts),
        "source_counts": {key: dict(value) for key, value in by_source.items()},
    }


@router.post("/backfill-queue")
async def backfill_queue(request: Request, db: Session = Depends(get_db)):
    body = await request.body()
    _authenticate_request(request, body)
    try:
        payload = BackfillPageRequest.model_validate_json(body)
    except (ValidationError, ValueError) as error:
        raise HTTPException(status_code=422, detail="invalid_backfill_page") from error
    scan_after = payload.after_catalog_id
    entries: list[dict[str, object]] = []
    has_more = False
    while len(entries) <= payload.limit:
        catalogs = _load_catalogs(db, after_id=scan_after, limit=500)
        if not catalogs:
            break
        registry_urls = _registry_images_by_offer(
            [offer for catalog in catalogs for offer in catalog.ofertas]
        )
        for catalog in catalogs:
            scan_after = catalog.id
            entry = _entry(catalog, registry_urls)
            if payload.source:
                entry["sources"] = [
                    item
                    for item in entry["sources"]
                    if item["source"] == payload.source
                ]
            if entry["eligible_for_backfill"] and entry["sources"]:
                entries.append(entry)
                if len(entries) > payload.limit:
                    has_more = True
                    break
        if has_more or len(catalogs) < 500:
            break
    entries = entries[: payload.limit]
    counts = Counter(str(entry["status"]) for entry in entries)
    return {
        "items": entries,
        "after_catalog_id": (
            entries[-1]["catalog_id"] if entries else payload.after_catalog_id
        ),
        "has_more": has_more,
        "page_size": len(catalogs),
        "status_counts": dict(counts),
    }
