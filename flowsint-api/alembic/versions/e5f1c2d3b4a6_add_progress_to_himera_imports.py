"""add progress to himera_imports

Revision ID: e5f1c2d3b4a6
Revises: d3e8a1b7c2f4
Create Date: 2026-11-14

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5f1c2d3b4a6"
down_revision: Union[str, None] = "d3e8a1b7c2f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("himera_imports", sa.Column("progress", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("himera_imports", "progress")
