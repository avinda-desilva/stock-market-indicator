"""initial schema: articles, tickers, ticker_mentions

Revision ID: 0001
Revises:
Create Date: 2026-05-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tickers",
        sa.Column("symbol", sa.String(10), primary_key=True),
        sa.Column("sector", sa.String(128), nullable=True),
        sa.Column("company_name", sa.String(256), nullable=True),
    )

    op.create_table(
        "articles",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("title", sa.String(512), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("url", sa.String(2048), nullable=True),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_articles_id", "articles", ["id"])
    op.create_index("ix_articles_timestamp", "articles", ["timestamp"])

    op.create_table(
        "ticker_mentions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "ticker",
            sa.String(10),
            sa.ForeignKey("tickers.symbol", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "article_id",
            sa.Integer(),
            sa.ForeignKey("articles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sentiment", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_ticker_mentions_id", "ticker_mentions", ["id"])
    op.create_index("ix_ticker_mentions_ticker", "ticker_mentions", ["ticker"])
    op.create_index("ix_ticker_mentions_article_id", "ticker_mentions", ["article_id"])


def downgrade() -> None:
    op.drop_table("ticker_mentions")
    op.drop_table("articles")
    op.drop_table("tickers")
