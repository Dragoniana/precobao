"""Merge production password recovery and scraper/image migrations."""

from typing import Sequence, Union

revision: str = "b7e1c4a9d602"
down_revision: Union[str, Sequence[str], None] = (
    "d30c5f9b24a1",
    "9a6d2f81c4b7",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
