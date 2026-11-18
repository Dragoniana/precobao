"""Add single-use password recovery codes.

Revision ID: d30c5f9b24a1
Revises: c42a83d1710e
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from fastapi_users_db_sqlalchemy.generics import GUID

revision: str = "d30c5f9b24a1"
down_revision: Union[str, None] = "c42a83d1710e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "password_recovery_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            GUID(),
            sa.ForeignKey("usuario.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint(
            "attempt_count >= 0", name="ck_password_recovery_attempt_count"
        ),
    )
    op.create_index(
        "ix_password_recovery_codes_user_id",
        "password_recovery_codes",
        ["user_id"],
    )
    op.create_index(
        "uq_password_recovery_codes_one_active_per_user",
        "password_recovery_codes",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("used_at IS NULL AND revoked_at IS NULL"),
        sqlite_where=sa.text("used_at IS NULL AND revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_password_recovery_codes_one_active_per_user",
        table_name="password_recovery_codes",
    )
    op.drop_index(
        "ix_password_recovery_codes_user_id", table_name="password_recovery_codes"
    )
    op.drop_table("password_recovery_codes")
