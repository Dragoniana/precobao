"""Audit and idempotency records for externally collected scraper batches."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from .base import Base


class ScraperImportBatch(Base):
    __tablename__ = "scraper_import_batches"

    batch_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    product_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    catalog_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    offers_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    offers_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    price_observations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    price_changes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    images_registered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    images_missing: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejected_items: Mapped[list[dict] | None] = mapped_column(
        JSON, nullable=False, default=list, server_default="[]"
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_type: Mapped[str | None] = mapped_column(String(80))
