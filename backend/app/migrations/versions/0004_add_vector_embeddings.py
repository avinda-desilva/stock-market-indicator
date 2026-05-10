"""add pgvector extension and embedding column to articles

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_EMBEDDING_DIM = 384


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.add_column("articles", sa.Column("embedding", Vector(_EMBEDDING_DIM), nullable=True))
    # IVFFlat index for approximate nearest-neighbour cosine search.
    # lists=100 is a reasonable starting point for up to ~1M rows.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_articles_embedding "
        "ON articles USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_articles_embedding")
    op.drop_column("articles", "embedding")
    op.execute("DROP EXTENSION IF EXISTS vector")
