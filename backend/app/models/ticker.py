from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Ticker(Base):
    __tablename__ = "tickers"

    symbol: Mapped[str] = mapped_column(String(10), primary_key=True)
    sector: Mapped[str | None] = mapped_column(String(128))
    company_name: Mapped[str | None] = mapped_column(String(256))

    mentions: Mapped[list["TickerMention"]] = relationship(
        "TickerMention", back_populates="ticker_rel"
    )
