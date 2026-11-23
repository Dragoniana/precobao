"""HMAC-authenticated, binary image ingestion for local scraper agents."""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.routes.scraper_ingest import _authenticate_request
from app.core.database import get_db
from app.core.utils import validar_ean13
from app.models import CatalogoBase, Farmacia, OfertaFarmacia
from app.models.medicine_image import (
    MedicineImageBlob,
    MedicineImageLink,
    MedicineImageUpload,
)
from app.services.imagens import (
    is_generic_image_url,
    offer_image_delivery_rejection_reason,
)
from app.services.medicine_images import (
    InvalidMedicineImage,
    validate_normalized_medicine_image,
)

router = APIRouter(prefix="/internal", tags=["internal-scraper-image-ingest"])
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_METADATA_BYTES = 16 * 1024


class MedicineImageUploadMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    upload_id: UUID
    source: Literal["araujo", "pacheco"]
    sku: str = Field(min_length=3, max_length=80)
    ean: str | None = Field(default=None, max_length=14)
    product_url: str = Field(min_length=1, max_length=500)
    source_image_url: str = Field(min_length=1, max_length=1000)
    content_type: Literal["image/jpeg", "image/webp"]
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    is_generic_fallback: bool = False
    only_if_missing: bool = False
    migration_mode: bool = False


class MedicineImageMigrationCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["araujo", "pacheco"]
    sku: str = Field(min_length=3, max_length=80)
    ean: str = Field(min_length=13, max_length=13)
    product_url: str = Field(min_length=1, max_length=500)
    source_image_url: str = Field(min_length=1, max_length=1000)
    content_type: Literal["image/jpeg", "image/webp"]
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    is_generic_fallback: bool = False


class MedicineImageMigrationCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[MedicineImageMigrationCheck] = Field(min_length=1, max_length=200)


def _source_enabled(source: str) -> bool:
    allowed = {
        item.strip().casefold()
        for item in os.getenv("SCRAPER_INGEST_ALLOWED_SOURCES", "").split(",")
        if item.strip()
    }
    return source in allowed


def _pharmacy_matches_source(name: str, source: str) -> bool:
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    normalized = normalized.casefold()
    return ("araujo" in normalized) if source == "araujo" else ("pacheco" in normalized)


def _upload_response(
    *,
    blob: MedicineImageBlob,
    link: MedicineImageLink,
    duplicate: bool,
    status: str = "STORED",
    catalog_id: int | None = None,
):
    return {
        "status": status,
        "duplicate": duplicate,
        "sha256": blob.sha256,
        "size_bytes": blob.size_bytes,
        "content_type": blob.content_type,
        "catalog_id": catalog_id if catalog_id is not None else link.catalog_id,
        "offer_id": link.offer_id,
        "primary": link.is_primary,
        "is_generic": link.is_generic,
    }


def _source_pharmacies(db: Session, source: str) -> list[Farmacia]:
    return [
        pharmacy
        for pharmacy in db.execute(select(Farmacia)).scalars()
        if _pharmacy_matches_source(pharmacy.nome_fantasia, source)
    ]


def _catalog_image_state(
    db: Session, catalog: CatalogoBase
) -> dict[str, object] | None:
    specific = db.execute(
        select(MedicineImageLink, MedicineImageBlob)
        .join(MedicineImageBlob, MedicineImageBlob.id == MedicineImageLink.blob_id)
        .where(
            MedicineImageLink.catalog_id == catalog.id,
            MedicineImageLink.is_generic.is_(False),
        )
        .order_by(MedicineImageLink.is_primary.desc(), MedicineImageLink.id)
        .limit(1)
    ).first()
    if specific is not None:
        link, blob = specific
        return {"link": link, "blob": blob, "kind": "stored"}
    # A generic offer fallback is not a real medicine photograph. It must not
    # block a validated specific image from being attached to the catalog.
    # Legacy URLs likewise do not count as a stored real photo.
    return None


def _existing_image_response(
    catalog: CatalogoBase, existing: dict[str, object]
) -> dict[str, object]:
    link = existing.get("link")
    blob = existing.get("blob")
    return {
        "status": "SKIPPED_EXISTING_IMAGE",
        "duplicate": True,
        "sha256": blob.sha256 if blob is not None else None,
        "size_bytes": blob.size_bytes if blob is not None else None,
        "content_type": blob.content_type if blob is not None else None,
        "catalog_id": catalog.id,
        "offer_id": link.offer_id if link is not None else None,
        "primary": link.is_primary if link is not None else False,
        "is_generic": link.is_generic if link is not None else False,
        "confirmed_existing_image": True,
    }


def _migration_target(
    db: Session, candidate: MedicineImageMigrationCheck
) -> tuple[CatalogoBase | None, Farmacia | None, OfertaFarmacia | None, str]:
    prefix = "ARJ" if candidate.source == "araujo" else "PCH"
    if not re.fullmatch(rf"{prefix}\d+", candidate.sku):
        return None, None, None, "INVALID_SOURCE_SKU"
    if not validar_ean13(candidate.ean):
        return None, None, None, "INVALID_EAN"
    generic = is_generic_image_url(candidate.source_image_url)
    if generic != candidate.is_generic_fallback:
        return None, None, None, "GENERIC_IMAGE_CLASSIFICATION_MISMATCH"
    rejection = offer_image_delivery_rejection_reason(
        candidate.source_image_url,
        candidate.product_url,
        "Araujo" if candidate.source == "araujo" else "Pacheco",
    )
    if rejection:
        return None, None, None, "INVALID_IMAGE_SOURCE"

    catalogs = (
        db.execute(
            select(CatalogoBase).where(CatalogoBase.codigo_barras == candidate.ean)
        )
        .scalars()
        .all()
    )
    if not catalogs:
        return None, None, None, "CATALOG_NOT_FOUND"
    if len(catalogs) != 1:
        return None, None, None, "CATALOG_IDENTITY_AMBIGUOUS"
    pharmacies = _source_pharmacies(db, candidate.source)
    if len(pharmacies) != 1:
        return catalogs[0], None, None, "SOURCE_PHARMACY_AMBIGUOUS"
    pharmacy = pharmacies[0]

    matching_offers = (
        db.execute(
            select(OfertaFarmacia).where(
                OfertaFarmacia.catalogo_id == catalogs[0].id,
                OfertaFarmacia.farmacia_id == pharmacy.id,
                OfertaFarmacia.sku_interno == candidate.sku,
                OfertaFarmacia.url_origem == candidate.product_url,
            )
        )
        .scalars()
        .all()
    )
    if len(matching_offers) > 1:
        return catalogs[0], pharmacy, None, "SOURCE_OFFER_AMBIGUOUS"
    if generic and not matching_offers:
        return catalogs[0], pharmacy, None, "GENERIC_REQUIRES_EXISTING_SOURCE_OFFER"
    return (
        catalogs[0],
        pharmacy,
        matching_offers[0] if matching_offers else None,
        "ELIGIBLE",
    )


@router.post("/medicine-images/migration-check")
async def check_medicine_image_migration(
    request: Request, db: Session = Depends(get_db)
) -> dict[str, object]:
    body = await request.body()
    _authenticate_request(request, body)
    try:
        payload = MedicineImageMigrationCheckRequest.model_validate_json(body)
    except (ValidationError, ValueError) as error:
        raise HTTPException(
            status_code=422, detail="invalid_migration_check"
        ) from error

    results = []
    for candidate in payload.candidates:
        if not _source_enabled(candidate.source):
            results.append(
                {
                    "source": candidate.source,
                    "sku": candidate.sku,
                    "ean": candidate.ean,
                    "content_sha256": candidate.content_sha256,
                    "status": "SOURCE_NOT_ENABLED",
                    "catalog_id": None,
                    "existing_image_sha256": None,
                }
            )
            continue
        catalog, _pharmacy, _offer, reason = _migration_target(db, candidate)
        existing = _catalog_image_state(db, catalog) if catalog is not None else None
        if existing is not None and reason == "ELIGIBLE":
            reason = "IMAGE_ALREADY_EXISTS"
        results.append(
            {
                "source": candidate.source,
                "sku": candidate.sku,
                "ean": candidate.ean,
                "content_sha256": candidate.content_sha256,
                "status": reason,
                "catalog_id": catalog.id if catalog is not None else None,
                "existing_image_sha256": (
                    existing["blob"].sha256
                    if existing and existing.get("blob") is not None
                    else None
                ),
            }
        )
    return {"results": results}


@router.post("/medicine-images", status_code=200)
async def ingest_medicine_image(
    request: Request, db: Session = Depends(get_db)
) -> dict[str, object]:
    max_bytes = int(os.getenv("SCRAPER_IMAGE_MAX_BYTES", str(512 * 1024)))
    max_body = max_bytes + _MAX_METADATA_BYTES + 1
    declared_length = request.headers.get("content-length")
    if declared_length:
        try:
            if int(declared_length) > max_body:
                raise HTTPException(status_code=413, detail="image_upload_too_large")
        except ValueError as error:
            raise HTTPException(
                status_code=400, detail="invalid_content_length"
            ) from error

    parts = bytearray()
    async for chunk in request.stream():
        if len(parts) + len(chunk) > max_body:
            raise HTTPException(status_code=413, detail="image_upload_too_large")
        parts.extend(chunk)
    body = bytes(parts)
    _authenticate_request(request, body)

    metadata_bytes, separator, image_bytes = body.partition(b"\n")
    if not separator or len(metadata_bytes) > _MAX_METADATA_BYTES:
        raise HTTPException(status_code=422, detail="invalid_image_upload_envelope")
    try:
        metadata = MedicineImageUploadMetadata.model_validate_json(metadata_bytes)
    except (ValidationError, ValueError) as error:
        raise HTTPException(
            status_code=422, detail="invalid_image_upload_metadata"
        ) from error
    if not image_bytes:
        raise HTTPException(status_code=422, detail="empty_image")
    if len(image_bytes) > max_bytes:
        raise HTTPException(status_code=413, detail="image_upload_too_large")
    if hashlib.sha256(image_bytes).hexdigest() != metadata.content_sha256:
        raise HTTPException(status_code=422, detail="image_sha256_mismatch")
    if not _source_enabled(metadata.source):
        raise HTTPException(status_code=403, detail="scraper_source_not_enabled")
    if metadata.ean and not validar_ean13(metadata.ean):
        raise HTTPException(status_code=422, detail="invalid_image_ean")

    try:
        normalized = validate_normalized_medicine_image(
            image_bytes,
            declared_content_type=metadata.content_type,
            max_bytes=max_bytes,
            max_dimension=int(os.getenv("SCRAPER_IMAGE_MAX_DIMENSION", "1200")),
        )
    except (InvalidMedicineImage, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    recognized_generic = is_generic_image_url(metadata.source_image_url)
    if metadata.is_generic_fallback and not recognized_generic:
        raise HTTPException(status_code=422, detail="generic_image_not_recognized")
    generic_fallback = recognized_generic
    offer: OfertaFarmacia | None
    if metadata.migration_mode:
        if not metadata.only_if_missing:
            raise HTTPException(
                status_code=422, detail="image_migration_requires_only_if_missing"
            )
        if not metadata.ean:
            raise HTTPException(status_code=422, detail="image_migration_ean_required")
        candidate = MedicineImageMigrationCheck(
            source=metadata.source,
            sku=metadata.sku,
            ean=metadata.ean or "",
            product_url=metadata.product_url,
            source_image_url=metadata.source_image_url,
            content_type=metadata.content_type,
            content_sha256=metadata.content_sha256,
            is_generic_fallback=metadata.is_generic_fallback,
        )
        catalog, pharmacy, offer, reason = _migration_target(db, candidate)
        if reason != "ELIGIBLE" or catalog is None or pharmacy is None:
            raise HTTPException(
                status_code=409,
                detail=f"image_migration_{reason.casefold()}",
            )
    else:
        prefix = "ARJ" if metadata.source == "araujo" else "PCH"
        if not re.fullmatch(rf"{prefix}\d+", metadata.sku):
            raise HTTPException(status_code=422, detail="image_sku_source_mismatch")
        offers = db.execute(
            select(OfertaFarmacia, Farmacia, CatalogoBase)
            .join(Farmacia, Farmacia.id == OfertaFarmacia.farmacia_id)
            .join(CatalogoBase, CatalogoBase.id == OfertaFarmacia.catalogo_id)
            .where(
                OfertaFarmacia.sku_interno == metadata.sku,
                OfertaFarmacia.url_origem == metadata.product_url,
            )
        ).all()
        source_offers = [
            row
            for row in offers
            if _pharmacy_matches_source(row[1].nome_fantasia, metadata.source)
        ]
        if len(source_offers) > 1 and metadata.ean:
            identity_matches = [
                row for row in source_offers if row[2].codigo_barras == metadata.ean
            ]
            if len(identity_matches) == 1:
                source_offers = identity_matches
        if len(source_offers) > 1:
            raise HTTPException(status_code=409, detail="image_offer_ambiguous")
        match = source_offers[0] if source_offers else None
        if match is None:
            raise HTTPException(status_code=409, detail="image_offer_not_ingested")
        offer, pharmacy, catalog = match
        if (
            not generic_fallback
            and metadata.ean
            and catalog.codigo_barras
            and metadata.ean != catalog.codigo_barras
        ):
            raise HTTPException(status_code=409, detail="image_ean_catalog_mismatch")
        rejection = offer_image_delivery_rejection_reason(
            metadata.source_image_url,
            metadata.product_url,
            "Araujo" if metadata.source == "araujo" else "Pacheco",
        )
        if rejection:
            raise HTTPException(status_code=422, detail="invalid_image_source")

    request_digest = hashlib.sha256(body).hexdigest()
    request_id = str(metadata.upload_id)
    previous = db.get(MedicineImageUpload, request_id)
    if previous is not None:
        if (
            previous.request_sha256 != request_digest
            or previous.source != metadata.source
        ):
            raise HTTPException(
                status_code=409, detail="image_upload_id_payload_conflict"
            )
        link = db.get(MedicineImageLink, previous.link_id)
        blob = db.get(MedicineImageBlob, previous.blob_id)
        if link is None or blob is None:
            raise HTTPException(
                status_code=503, detail="image_upload_record_incomplete"
            )
        result_status = (
            "STORED"
            if not generic_fallback or link.is_generic
            else "SKIPPED_EXISTING_IMAGE"
        )
        result = _upload_response(
            blob=blob,
            link=link,
            duplicate=True,
            status=result_status,
            catalog_id=catalog.id if not link.is_generic else None,
        )
        if result_status == "SKIPPED_EXISTING_IMAGE":
            result["confirmed_existing_image"] = True
        return result

    # Serialize all image writers on the catalog row. A scraper or migration
    # arriving concurrently will observe the committed image and become a no-op.
    catalog = db.execute(
        select(CatalogoBase).where(CatalogoBase.id == catalog.id).with_for_update()
    ).scalar_one()
    if metadata.only_if_missing:
        existing = _catalog_image_state(db, catalog)
        if existing is not None:
            return _existing_image_response(catalog, existing)

    if offer is not None:
        existing_offer_link = db.execute(
            select(MedicineImageLink, MedicineImageBlob)
            .join(MedicineImageBlob, MedicineImageBlob.id == MedicineImageLink.blob_id)
            .where(
                MedicineImageLink.offer_id == offer.id,
                MedicineImageLink.is_generic.is_(generic_fallback),
            )
            .limit(1)
        ).first()
        if existing_offer_link is not None:
            link, blob = existing_offer_link
            return _upload_response(
                blob=blob,
                link=link,
                duplicate=True,
                status="SKIPPED_EXISTING_IMAGE",
                catalog_id=catalog.id if not link.is_generic else None,
            ) | {"confirmed_existing_image": True}

    dialect = db.get_bind().dialect.name
    insert = pg_insert if dialect == "postgresql" else sqlite_insert
    statement = (
        insert(MedicineImageBlob)
        .values(
            sha256=normalized.sha256,
            content_type=normalized.content_type,
            image_data=normalized.content,
            size_bytes=len(normalized.content),
            width=normalized.width,
            height=normalized.height,
        )
        .on_conflict_do_nothing(index_elements=["sha256"])
    )
    try:
        db.execute(statement)
        blob = db.execute(
            select(MedicineImageBlob).where(
                MedicineImageBlob.sha256 == normalized.sha256
            )
        ).scalar_one()
        link = None
        if link is None:
            has_primary = None
            if not generic_fallback:
                has_primary = db.execute(
                    select(MedicineImageLink.id)
                    .where(
                        MedicineImageLink.catalog_id == catalog.id,
                        MedicineImageLink.is_primary.is_(True),
                    )
                    .limit(1)
                ).scalar_one_or_none()
            link = MedicineImageLink(
                blob_id=blob.id,
                catalog_id=None if generic_fallback else catalog.id,
                pharmacy_id=pharmacy.id,
                offer_id=offer.id if offer is not None else None,
                sku=metadata.sku,
                ean=(
                    metadata.ean
                    if generic_fallback
                    else metadata.ean or catalog.codigo_barras
                ),
                source=metadata.source,
                source_image_url=metadata.source_image_url,
                is_primary=not generic_fallback and has_primary is None,
                is_generic=generic_fallback,
            )
            db.add(link)
            db.flush()
        else:
            link.blob_id = blob.id
            link.catalog_id = None if generic_fallback else catalog.id
            link.pharmacy_id = pharmacy.id
            link.sku = metadata.sku
            link.ean = (
                metadata.ean
                if generic_fallback
                else metadata.ean or catalog.codigo_barras
            )
            link.source = metadata.source
            link.source_image_url = metadata.source_image_url
            link.is_generic = generic_fallback
            if generic_fallback:
                link.is_primary = False
            elif not link.is_primary:
                has_primary = db.execute(
                    select(MedicineImageLink.id)
                    .where(
                        MedicineImageLink.catalog_id == catalog.id,
                        MedicineImageLink.is_primary.is_(True),
                        MedicineImageLink.id != link.id,
                    )
                    .limit(1)
                ).scalar_one_or_none()
                link.is_primary = has_primary is None
        db.add(
            MedicineImageUpload(
                request_id=request_id,
                request_sha256=request_digest,
                source=metadata.source,
                blob_id=blob.id,
                link_id=link.id,
            )
        )
        db.commit()
    except IntegrityError as error:
        db.rollback()
        previous = db.get(MedicineImageUpload, request_id)
        if previous and previous.request_sha256 == request_digest:
            link = db.get(MedicineImageLink, previous.link_id)
            blob = db.get(MedicineImageBlob, previous.blob_id)
            if link and blob:
                return _upload_response(
                    blob=blob,
                    link=link,
                    duplicate=True,
                    catalog_id=catalog.id if not link.is_generic else None,
                )
        raise HTTPException(status_code=409, detail="image_link_conflict") from error
    except SQLAlchemyError as error:
        db.rollback()
        raise HTTPException(
            status_code=503, detail="image_storage_unavailable"
        ) from error

    return _upload_response(
        blob=blob,
        link=link,
        duplicate=False,
        catalog_id=catalog.id if not link.is_generic else None,
    )
