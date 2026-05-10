"""add minhash_signature column to articles

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "articles",
        sa.Column("minhash_signature", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("articles", "minhash_signature")
