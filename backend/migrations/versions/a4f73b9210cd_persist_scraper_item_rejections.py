"""Persist per-item scraper ingestion rejections for idempotent partial batches."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a4f73b9210cd"
down_revision: Union[str, Sequence[str], None] = "c4a8f32d1e9b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "scraper_import_batches",
        sa.Column(
            "rejected_items",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )


def downgrade() -> None:
    raise RuntimeError(
        "Rejected item audit data is retained during application rollback; "
        "do not downgrade this migration."
    )
