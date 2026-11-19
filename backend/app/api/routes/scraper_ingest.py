"""Authenticated, bounded ingestion endpoint for independent scraper hosts."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.utils import validar_ean13
from app.models.scraper_import_batch import ScraperImportBatch
from app.schemas.oferta import ProdutoExtraidoSchema
from app.services.imagens import offer_image_delivery_rejection_reason

router = APIRouter(prefix="/internal", tags=["internal-scraper-ingest"])
_SIGNATURE_RE = re.compile(r"^sha256=([0-9a-f]{64})$")
_MAX_CLOCK_SKEW_SECONDS = 300
_PROCESSING_RECOVERY_SECONDS = 900


class ScraperBatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: UUID
    source: Literal["araujo", "pacheco"]
    observed_at: datetime
    products: list[ProdutoExtraidoSchema] = Field(min_length=1, max_length=100)

    @field_validator("observed_at")
    @classmethod
    def require_aware_observation_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must include a timezone")
        return value.astimezone(UTC)

    def validate_source_identity(self) -> None:
        expected_host = (
            "www.araujo.com.br"
            if self.source == "araujo"
            else "www.drogariaspacheco.com.br"
        )
        sku_prefix = "ARJ" if self.source == "araujo" else "PCH"
        for product in self.products:
            if not str(product.id).isdigit() or not product.name_search.strip():
                raise ValueError("product identity or name is invalid")
            parsed = urlsplit(product.link)
            if (
                parsed.scheme != "https"
                or parsed.hostname != expected_host
                or parsed.username is not None
                or parsed.password is not None
                or parsed.port not in {None, 443}
            ):
                raise ValueError("product URL does not match declared source")
            if product.sku_interno != f"{sku_prefix}{product.id}":
                raise ValueError("product SKU does not match its source ID")
            source_time = product.fonte_observado_em or self.observed_at
            if source_time.tzinfo is None or source_time.utcoffset() is None:
                raise ValueError("product observation time must include a timezone")
            product.fonte_observado_em = source_time.astimezone(UTC)
            if product.imagem_url:
                pharmacy_name = "Araujo" if self.source == "araujo" else "Pacheco"
                if offer_image_delivery_rejection_reason(
                    product.imagem_url, product.link, pharmacy_name
                ):
                    raise ValueError("product image must use an HTTPS URL")


class ScraperBatchResult(BaseModel):
    batch_id: UUID
    status: str
    duplicate: bool = False
    product_count: int
    catalog_created: int = 0
    offers_created: int = 0
    offers_updated: int = 0
    price_observations: int = 0
    price_changes: int = 0
    images_registered: int = 0
    images_missing: int = 0


def _authenticate_request(request: Request, body: bytes) -> None:
    if request.url.scheme != "https":
        raise HTTPException(status_code=400, detail="https_required")

    secret = os.getenv("SCRAPER_INGEST_HMAC_SECRET", "")
    if len(secret.encode("utf-8")) < 32:
        raise HTTPException(status_code=503, detail="scraper_ingest_not_configured")

    timestamp = request.headers.get("X-Scraper-Timestamp", "")
    signature = request.headers.get("X-Scraper-Signature", "")
    match = _SIGNATURE_RE.fullmatch(signature)
    try:
        issued_at = int(timestamp)
    except ValueError:
        issued_at = 0
    if not match or abs(int(time.time()) - issued_at) > _MAX_CLOCK_SKEW_SECONDS:
        raise HTTPException(status_code=401, detail="invalid_scraper_signature")

    signed = timestamp.encode("ascii") + b"." + body
    expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(match.group(1), expected):
        raise HTTPException(status_code=401, detail="invalid_scraper_signature")


def _scraper_for(source: str):
    if source == "araujo":
        from app.scrapers.araujo import AraujoScraper

        return AraujoScraper(num_workers=1, batch_size=50)
    from app.scrapers.pacheco import PachecoScraper

    return PachecoScraper(num_workers=1, batch_size=50)


async def _persist_batch(batch: ScraperBatchIn) -> dict[str, int]:
    scraper = _scraper_for(batch.source)
    payload = [{"validado": product, "enriquecido": None} for product in batch.products]
    await asyncio.to_thread(scraper._processar_e_salvar_lote, payload)
    return scraper.persistence_stats.copy()


def _result(record: ScraperImportBatch, *, duplicate: bool = False):
    return ScraperBatchResult(
        batch_id=UUID(record.batch_id),
        status=record.status,
        duplicate=duplicate,
        product_count=record.product_count,
        catalog_created=record.catalog_created,
        offers_created=record.offers_created,
        offers_updated=record.offers_updated,
        price_observations=record.price_observations,
        price_changes=record.price_changes,
        images_registered=record.images_registered,
        images_missing=record.images_missing,
    )


@router.post(
    "/scraper-batches",
    response_model=ScraperBatchResult,
    status_code=status.HTTP_200_OK,
)
async def ingest_scraper_batch(
    request: Request, db: Session = Depends(get_db)
) -> ScraperBatchResult:
    """Accept one HMAC-authenticated batch; never accept app-user credentials."""
    max_bytes = int(os.getenv("SCRAPER_INGEST_MAX_BODY_BYTES", str(1_000_000)))
    declared_length = request.headers.get("content-length")
    if declared_length:
        try:
            if int(declared_length) > max_bytes:
                raise HTTPException(status_code=413, detail="batch_too_large")
        except ValueError as error:
            raise HTTPException(
                status_code=400, detail="invalid_content_length"
            ) from error

    body_parts = bytearray()
    async for chunk in request.stream():
        if len(body_parts) + len(chunk) > max_bytes:
            raise HTTPException(status_code=413, detail="batch_too_large")
        body_parts.extend(chunk)
    body = bytes(body_parts)
    _authenticate_request(request, body)

    try:
        batch = ScraperBatchIn.model_validate_json(body)
        batch.validate_source_identity()
        for product in batch.products:
            if product.ean and not validar_ean13(product.ean):
                product.ean = None
    except (ValidationError, ValueError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=422, detail="invalid_scraper_batch") from error

    allowed_sources = {
        source.strip().casefold()
        for source in os.getenv("SCRAPER_INGEST_ALLOWED_SOURCES", "").split(",")
        if source.strip()
    }
    if batch.source not in allowed_sources:
        raise HTTPException(status_code=403, detail="scraper_source_not_enabled")

    digest = hashlib.sha256(body).hexdigest()
    batch_id = str(batch.batch_id)
    now = datetime.now(UTC)
    record = (
        db.query(ScraperImportBatch)
        .filter(ScraperImportBatch.batch_id == batch_id)
        .with_for_update()
        .first()
    )
    if record is not None:
        if record.payload_sha256 != digest or record.source != batch.source:
            raise HTTPException(status_code=409, detail="batch_id_payload_conflict")
        if record.status == "COMPLETE":
            return _result(record, duplicate=True)
        if (
            record.status == "PROCESSING"
            and record.started_at is not None
            and record.started_at
            > now - timedelta(seconds=_PROCESSING_RECOVERY_SECONDS)
        ):
            raise HTTPException(status_code=409, detail="batch_is_processing")
        record.status = "PROCESSING"
        record.attempt_count += 1
        record.started_at = now
        record.finished_at = None
        record.error_type = None
    else:
        record = ScraperImportBatch(
            batch_id=batch_id,
            source=batch.source,
            payload_sha256=digest,
            status="PROCESSING",
            attempt_count=1,
            product_count=len(batch.products),
            started_at=now,
        )
        db.add(record)

    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="batch_already_claimed") from error
    except SQLAlchemyError as error:
        db.rollback()
        raise HTTPException(
            status_code=503, detail="batch_audit_unavailable"
        ) from error

    try:
        metrics = await _persist_batch(batch)
        if (
            metrics.get("persistence_errors")
            or metrics.get("image_errors")
            or metrics.get("unassociated_products")
        ):
            raise RuntimeError("batch_persistence_incomplete")
        record.status = "COMPLETE"
        record.catalog_created = metrics.get("catalog_items_created", 0)
        record.offers_created = metrics.get("offers_created", 0)
        record.offers_updated = metrics.get("offers_updated", 0)
        record.price_observations = metrics.get(
            "first_price_observations", 0
        ) + metrics.get("price_changes", 0)
        record.price_changes = metrics.get("price_changes", 0)
        record.images_registered = metrics.get("images_registered", 0)
        record.images_missing = metrics.get("products_without_image", 0)
        record.finished_at = datetime.now(UTC)
        db.commit()
    except Exception as error:
        db.rollback()
        record = (
            db.query(ScraperImportBatch)
            .filter(ScraperImportBatch.batch_id == batch_id)
            .with_for_update()
            .first()
        )
        if record is not None:
            record.status = "FAILED"
            record.error_type = type(error).__name__[:80]
            record.finished_at = datetime.now(UTC)
            db.commit()
        raise HTTPException(
            status_code=503, detail="batch_persistence_failed"
        ) from error

    return _result(record)
