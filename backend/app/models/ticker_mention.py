from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base


class TickerMention(Base):
    __tablename__ = "ticker_mentions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    ticker: Mapped[str] = mapped_column(
        String(10), ForeignKey("tickers.symbol", ondelete="CASCADE"), index=True
    )
    article_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("articles.id", ondelete="CASCADE"), index=True
    )
    sentiment: Mapped[float | None] = mapped_column(Float)
    source_weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    llm_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    article: Mapped["Article"] = relationship("Article", back_populates="mentions")
    ticker_rel: Mapped["Ticker"] = relationship("Ticker", back_populates="mentions")
