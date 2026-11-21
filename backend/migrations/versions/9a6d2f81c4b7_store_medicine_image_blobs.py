"""Store validated medicine image bytes with deduplicated source links."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "9a6d2f81c4b7"
down_revision: Union[str, Sequence[str], None] = "f81c3a2d9b10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "medicine_image_blobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("content_type", sa.String(length=32), nullable=False),
        sa.Column("image_data", sa.LargeBinary(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("size_bytes > 0", name="ck_medicine_image_blob_size"),
        sa.CheckConstraint(
            "width > 0 AND height > 0", name="ck_medicine_image_dimensions"
        ),
        sa.UniqueConstraint("sha256", name="uq_medicine_image_blobs_sha256"),
    )
    op.create_table(
        "medicine_image_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "blob_id",
            sa.Integer(),
            sa.ForeignKey("medicine_image_blobs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "catalog_id",
            sa.Integer(),
            sa.ForeignKey("catalogo_base.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "pharmacy_id",
            sa.Integer(),
            sa.ForeignKey("farmacias.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "offer_id",
            sa.Integer(),
            sa.ForeignKey("ofertas_farmacia.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("sku", sa.String(length=80), nullable=False),
        sa.Column("ean", sa.String(length=14), nullable=True),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("source_image_url", sa.String(length=1000), nullable=False),
        sa.Column(
            "is_primary", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("offer_id", name="uq_medicine_image_link_offer"),
    )
    op.create_index(
        "ix_medicine_image_links_blob_id", "medicine_image_links", ["blob_id"]
    )
    op.create_index(
        "ix_medicine_image_links_catalog_id", "medicine_image_links", ["catalog_id"]
    )
    op.create_index(
        "ix_medicine_image_links_pharmacy_id", "medicine_image_links", ["pharmacy_id"]
    )
    op.create_index(
        "uq_medicine_image_link_primary_catalog",
        "medicine_image_links",
        ["catalog_id"],
        unique=True,
        postgresql_where=sa.text("is_primary"),
        sqlite_where=sa.text("is_primary = 1"),
    )

    op.create_table(
        "medicine_image_uploads",
        sa.Column("request_id", sa.String(length=36), primary_key=True),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column(
            "blob_id",
            sa.Integer(),
            sa.ForeignKey("medicine_image_blobs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "link_id",
            sa.Integer(),
            sa.ForeignKey("medicine_image_links.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("medicine_image_uploads")
    op.drop_index(
        "uq_medicine_image_link_primary_catalog", table_name="medicine_image_links"
    )
    op.drop_index(
        "ix_medicine_image_links_pharmacy_id", table_name="medicine_image_links"
    )
    op.drop_index(
        "ix_medicine_image_links_catalog_id", table_name="medicine_image_links"
    )
    op.drop_index("ix_medicine_image_links_blob_id", table_name="medicine_image_links")
    op.drop_table("medicine_image_links")
    op.drop_table("medicine_image_blobs")
