"""
Query processing pipeline: intent detection + entity extraction.

Intent classes
--------------
sector_search   — query mentions a known sector keyword (e.g. "AI stocks", "tech sector")
ticker_lookup   — query is or contains a bare ticker symbol (e.g. "AAPL", "$TSLA")
news_search     — query looks like a news/event phrase (fallback with title/content match)
general_search  — catch-all full-text search across tickers and articles
"""

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Static lookup tables
# ---------------------------------------------------------------------------

SECTOR_KEYWORDS: dict[str, str] = {
    # keyword (lower) → canonical sector name stored in tickers table
    "ai": "Technology",
    "artificial intelligence": "Technology",
    "tech": "Technology",
    "technology": "Technology",
    "software": "Technology",
    "semiconductor": "Technology",
    "chip": "Technology",
    "energy": "Energy",
    "oil": "Energy",
    "gas": "Energy",
    "renewable": "Energy",
    "solar": "Energy",
    "health": "Health Care",
    "healthcare": "Health Care",
    "pharma": "Health Care",
    "biotech": "Health Care",
    "medical": "Health Care",
    "drug": "Health Care",
    "finance": "Financials",
    "financial": "Financials",
    "bank": "Financials",
    "banking": "Financials",
    "insurance": "Financials",
    "consumer": "Consumer Discretionary",
    "retail": "Consumer Discretionary",
    "ecommerce": "Consumer Discretionary",
    "industrial": "Industrials",
    "aerospace": "Industrials",
    "defense": "Industrials",
    "materials": "Materials",
    "mining": "Materials",
    "utilities": "Utilities",
    "telecom": "Communication Services",
    "communication": "Communication Services",
    "media": "Communication Services",
    "real estate": "Real Estate",
    "reit": "Real Estate",
}

_TICKER_RE = re.compile(r"\$?([A-Z]{1,5})\b")

# Words that look like tickers but are common English words — skip them
_STOPWORDS = {
    "A", "I", "AI", "AN", "AS", "AT", "BE", "BY", "DO", "GO", "IF",
    "IN", "IS", "IT", "ME", "MY", "NO", "OF", "ON", "OR", "SO", "TO",
    "UP", "US", "WE", "THE", "AND", "FOR", "GET", "HAS", "NOT", "BUT",
    "ARE", "WAS", "FROM", "THAT", "THIS", "WITH", "WILL", "HAVE",
    "THEY", "BEEN", "WERE", "INTO", "THAN", "THEN", "WHEN", "WHICH",
    "WHO", "HOW", "WHY", "WHAT", "ALL", "ANY", "CAN", "MAY", "ETF",
    "EPS", "IPO", "CEO", "CFO", "COO", "IPO", "SEC", "FED", "GDP",
    "USA", "USD", "EUR", "GBP",
}


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class QueryIntent:
    raw_query: str
    intent: str                      # sector_search | ticker_lookup | news_search | general_search
    sectors: list[str] = field(default_factory=list)   # matched canonical sector names
    tickers: list[str] = field(default_factory=list)   # uppercase ticker candidates
    keywords: list[str] = field(default_factory=list)  # remaining search terms


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_query(query: str) -> QueryIntent:
    """Classify *query* and extract structured entities."""
    q = query.strip()
    ql = q.lower()

    sectors: list[str] = []
    tickers: list[str] = []

    # --- entity extraction: sectors ---
    for kw, canonical in SECTOR_KEYWORDS.items():
        if kw in ql and canonical not in sectors:
            sectors.append(canonical)

    # --- entity extraction: tickers ($AAPL or bare AAPL) ---
    for match in _TICKER_RE.finditer(q):
        sym = match.group(1).upper()
        if sym not in _STOPWORDS and sym not in tickers:
            tickers.append(sym)

    # --- intent classification ---
    if sectors:
        intent = "sector_search"
    elif tickers:
        intent = "ticker_lookup"
    elif any(w in ql for w in ("news", "report", "earnings", "announce", "beat", "miss")):
        intent = "news_search"
    else:
        intent = "general_search"

    # --- remaining keywords (strip sector/ticker tokens for FTS) ---
    keywords = [
        w for w in re.split(r"\W+", ql)
        if w and w not in SECTOR_KEYWORDS and len(w) > 1
    ]

    return QueryIntent(
        raw_query=q,
        intent=intent,
        sectors=sectors,
        tickers=tickers,
        keywords=keywords,
    )
