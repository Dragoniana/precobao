"""Content-addressed image blobs and their medicine/source associations."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .catalogo import CatalogoBase


class MedicineImageBlob(Base):
    __tablename__ = "medicine_image_blobs"
    __table_args__ = (
        CheckConstraint("size_bytes > 0", name="ck_medicine_image_blob_size"),
        CheckConstraint(
            "width > 0 AND height > 0", name="ck_medicine_image_dimensions"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    content_type: Mapped[str] = mapped_column(String(32), nullable=False)
    image_data: Mapped[bytes] = mapped_column(
        LargeBinary, nullable=False, deferred=True
    )
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    links: Mapped[list[MedicineImageLink]] = relationship(
        "MedicineImageLink", back_populates="blob"
    )


class MedicineImageLink(Base):
    __tablename__ = "medicine_image_links"
    __table_args__ = (
        UniqueConstraint("offer_id", name="uq_medicine_image_link_offer"),
        Index(
            "uq_medicine_image_link_primary_catalog",
            "catalog_id",
            unique=True,
            postgresql_where=text("is_primary"),
            sqlite_where=text("is_primary = 1"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    blob_id: Mapped[int] = mapped_column(
        ForeignKey("medicine_image_blobs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    catalog_id: Mapped[int] = mapped_column(
        ForeignKey("catalogo_base.id", ondelete="CASCADE"), nullable=False, index=True
    )
    pharmacy_id: Mapped[int] = mapped_column(
        ForeignKey("farmacias.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    offer_id: Mapped[int | None] = mapped_column(
        ForeignKey("ofertas_farmacia.id", ondelete="CASCADE"), nullable=True
    )
    sku: Mapped[str] = mapped_column(String(80), nullable=False)
    ean: Mapped[str | None] = mapped_column(String(14), nullable=True)
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    source_image_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    catalog: Mapped[CatalogoBase] = relationship(
        "CatalogoBase", back_populates="image_links"
    )
    blob: Mapped[MedicineImageBlob] = relationship(
        "MedicineImageBlob", back_populates="links", lazy="joined"
    )


class MedicineImageUpload(Base):
    """Idempotency ledger for signed image uploads (replay-safe retries)."""

    __tablename__ = "medicine_image_uploads"

    request_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    blob_id: Mapped[int] = mapped_column(
        ForeignKey("medicine_image_blobs.id", ondelete="RESTRICT"), nullable=False
    )
    link_id: Mapped[int] = mapped_column(
        ForeignKey("medicine_image_links.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
