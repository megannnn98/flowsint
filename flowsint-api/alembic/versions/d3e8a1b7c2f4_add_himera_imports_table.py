"""add himera_imports table

Revision ID: d3e8a1b7c2f4
Revises: c7d1f0a92b3e
Create Date: 2026-11-14

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d3e8a1b7c2f4"
down_revision: Union[str, None] = "c7d1f0a92b3e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "himera_imports",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("sketch_id", sa.UUID(), nullable=False),
        sa.Column("owner_id", sa.UUID(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=True),
        sa.Column("pdf_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("pdf_engine", sa.Text(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("import_summary", sa.JSON(), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["sketch_id"], ["sketches.id"], onupdate="CASCADE", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["profiles.id"], onupdate="CASCADE", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_himera_imports_sketch_sha", "himera_imports", ["sketch_id", "pdf_sha256"]
    )
    op.create_index("idx_himera_imports_owner_id", "himera_imports", ["owner_id"])


def downgrade() -> None:
    op.drop_index("idx_himera_imports_owner_id", table_name="himera_imports")
    op.drop_index("idx_himera_imports_sketch_sha", table_name="himera_imports")
    op.drop_table("himera_imports")
