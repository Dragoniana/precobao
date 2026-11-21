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
    image_rejection_reason,
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
    *, blob: MedicineImageBlob, link: MedicineImageLink, duplicate: bool
):
    return {
        "status": "STORED",
        "duplicate": duplicate,
        "sha256": blob.sha256,
        "size_bytes": blob.size_bytes,
        "content_type": blob.content_type,
        "catalog_id": link.catalog_id,
        "offer_id": link.offer_id,
        "primary": link.is_primary,
    }


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
    match = next(
        (
            row
            for row in offers
            if _pharmacy_matches_source(row[1].nome_fantasia, metadata.source)
        ),
        None,
    )
    if match is None:
        raise HTTPException(status_code=409, detail="image_offer_not_ingested")
    offer, pharmacy, catalog = match
    if metadata.ean and catalog.codigo_barras and metadata.ean != catalog.codigo_barras:
        raise HTTPException(status_code=409, detail="image_ean_catalog_mismatch")
    rejection = image_rejection_reason(metadata.source_image_url) or (
        offer_image_delivery_rejection_reason(
            metadata.source_image_url,
            metadata.product_url,
            "Araujo" if metadata.source == "araujo" else "Pacheco",
        )
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
        return _upload_response(blob=blob, link=link, duplicate=True)

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
        link = db.execute(
            select(MedicineImageLink).where(MedicineImageLink.offer_id == offer.id)
        ).scalar_one_or_none()
        if link is None:
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
                catalog_id=catalog.id,
                pharmacy_id=pharmacy.id,
                offer_id=offer.id,
                sku=metadata.sku,
                ean=metadata.ean or catalog.codigo_barras,
                source=metadata.source,
                source_image_url=metadata.source_image_url,
                is_primary=has_primary is None,
            )
            db.add(link)
            db.flush()
        else:
            link.blob_id = blob.id
            link.catalog_id = catalog.id
            link.pharmacy_id = pharmacy.id
            link.sku = metadata.sku
            link.ean = metadata.ean or catalog.codigo_barras
            link.source = metadata.source
            link.source_image_url = metadata.source_image_url
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
                return _upload_response(blob=blob, link=link, duplicate=True)
        raise HTTPException(status_code=409, detail="image_link_conflict") from error
    except SQLAlchemyError as error:
        db.rollback()
        raise HTTPException(
            status_code=503, detail="image_storage_unavailable"
        ) from error

    return _upload_response(blob=blob, link=link, duplicate=False)
