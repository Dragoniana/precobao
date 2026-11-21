"""Create audit and idempotency records for scraper ingestion batches."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f81c3a2d9b10"
down_revision: Union[str, Sequence[str], None] = "c42a83d1710e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scraper_import_batches",
        sa.Column("batch_id", sa.String(length=36), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("product_count", sa.Integer(), nullable=False),
        sa.Column("catalog_created", sa.Integer(), nullable=False),
        sa.Column("offers_created", sa.Integer(), nullable=False),
        sa.Column("offers_updated", sa.Integer(), nullable=False),
        sa.Column("price_observations", sa.Integer(), nullable=False),
        sa.Column("price_changes", sa.Integer(), nullable=False),
        sa.Column("images_registered", sa.Integer(), nullable=False),
        sa.Column("images_missing", sa.Integer(), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_type", sa.String(length=80), nullable=True),
        sa.PrimaryKeyConstraint("batch_id"),
    )
    op.create_index(
        "ix_scraper_import_batches_source", "scraper_import_batches", ["source"]
    )
    op.create_index(
        "ix_scraper_import_batches_status", "scraper_import_batches", ["status"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_scraper_import_batches_status", table_name="scraper_import_batches"
    )
    op.drop_index(
        "ix_scraper_import_batches_source", table_name="scraper_import_batches"
    )
    op.drop_table("scraper_import_batches")
