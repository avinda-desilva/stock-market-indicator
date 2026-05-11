"""add llm_summary column to ticker_mentions

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ticker_mentions",
        sa.Column("llm_summary", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ticker_mentions", "llm_summary")
