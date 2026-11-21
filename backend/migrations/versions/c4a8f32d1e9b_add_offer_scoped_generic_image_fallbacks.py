"""Allow pharmacy generic image fallbacks scoped to one offer."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4a8f32d1e9b"
down_revision: Union[str, Sequence[str], None] = "b7e1c4a9d602"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("medicine_image_links") as batch:
        batch.drop_constraint("uq_medicine_image_link_offer", type_="unique")
        batch.alter_column(
            "catalog_id",
            existing_type=sa.Integer(),
            nullable=True,
        )
        batch.add_column(
            sa.Column(
                "is_generic",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.create_check_constraint(
            "ck_medicine_image_link_generic_offer_fallback",
            "(is_generic = false AND catalog_id IS NOT NULL) OR "
            "(is_generic = true AND catalog_id IS NULL AND offer_id IS NOT NULL "
            "AND is_primary = false)",
        )
        batch.alter_column("is_generic", server_default=None)
    op.create_index(
        "uq_medicine_image_link_offer_specific",
        "medicine_image_links",
        ["offer_id"],
        unique=True,
        postgresql_where=sa.text("offer_id IS NOT NULL AND is_generic = false"),
        sqlite_where=sa.text("offer_id IS NOT NULL AND is_generic = 0"),
    )
    op.create_index(
        "uq_medicine_image_link_offer_generic",
        "medicine_image_links",
        ["offer_id"],
        unique=True,
        postgresql_where=sa.text("offer_id IS NOT NULL AND is_generic = true"),
        sqlite_where=sa.text("offer_id IS NOT NULL AND is_generic = 1"),
    )


def downgrade() -> None:
    # Never associate offer-scoped fallbacks to a catalog medicine implicitly.
    connection = op.get_bind()
    if connection.execute(
        sa.text(
            "SELECT 1 FROM medicine_image_links WHERE catalog_id IS NULL LIMIT 1"
        )
    ).first():
        raise RuntimeError(
            "Cannot downgrade while offer-scoped generic image fallbacks exist."
        )
    op.drop_index(
        "uq_medicine_image_link_offer_generic", table_name="medicine_image_links"
    )
    op.drop_index(
        "uq_medicine_image_link_offer_specific", table_name="medicine_image_links"
    )
    with op.batch_alter_table("medicine_image_links") as batch:
        batch.drop_constraint(
            "ck_medicine_image_link_generic_offer_fallback", type_="check"
        )
        batch.drop_column("is_generic")
        batch.alter_column(
            "catalog_id",
            existing_type=sa.Integer(),
            nullable=False,
        )
        batch.create_unique_constraint("uq_medicine_image_link_offer", ["offer_id"])
