import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ticker import Ticker

# Matches optional $ prefix + 2-5 uppercase letters (word-boundary anchored)
_TICKER_RE = re.compile(r"\$([A-Z]{2,5})\b|\b([A-Z]{2,5})\b")

# Legal/corporate suffixes stripped before building company-name keywords
_LEGAL_SUFFIXES = re.compile(
    r"\b(Inc\.?|Corp\.?|Co\.?|Ltd\.?|LLC|plc|Group|Company|Corporation|"
    r"Incorporated|Limited|Holdings?|Laboratories?|Systems?|Technologies?|"
    r"Platforms?|Partners?|Bancorp|Bancshares?|Financial|Services?|"
    r"Pharmaceuticals?|Energy|Motors?|Airlines?|Networks?|Solutions?|"
    r"Enterprises?|Industries?|International|Global|Worldwide|Capital|"
    r"Ventures?|Associates?|The)\b\.?",
    re.IGNORECASE,
)

# Common English words that look like tickers — filtered to reduce false positives
_STOPWORDS: frozenset[str] = frozenset(
    {
        "A", "I", "AM", "AN", "AS", "AT", "BE", "BY", "DO", "GO", "IF", "IN",
        "IS", "IT", "ME", "MY", "NO", "OF", "ON", "OR", "SO", "TO", "UP", "US",
        "WE", "AND", "ARE", "FOR", "GET", "HAS", "HAD", "HIM", "HIS", "HOW",
        "ITS", "LET", "MAY", "NEW", "NOT", "NOW", "OFF", "OUR", "OUT", "OWN",
        "SAY", "SEE", "SET", "SHE", "THE", "TOO", "TWO", "WAS", "WAY", "WHO",
        "WHY", "YET", "YOU", "ALL", "BIG", "CAN", "DID", "HER", "ILL", "OLD",
        "ONE", "OWE", "PUT", "RAN", "RUN", "SIT", "SIX", "TEN", "YES", "YOUR",
        "BEEN", "BOTH", "CALL", "CAME", "COME", "DOES", "EACH", "EVEN", "FROM",
        "GAVE", "GIVE", "GOES", "GONE", "GOOD", "HAVE", "HELP", "HERE", "HIGH",
        "INTO", "JUST", "KEEP", "KIND", "KNOW", "LAST", "LESS", "LIKE", "LONG",
        "LOOK", "MADE", "MAKE", "MANY", "MORE", "MOST", "MOVE", "MUCH", "MUST",
        "NEED", "NEXT", "ONLY", "OPEN", "OVER", "PART", "PAST", "PLAN", "PLAY",
        "READ", "REAL", "SAID", "SAME", "SEEM", "SHOW", "SIDE", "SOME", "SOON",
        "STAY", "SUCH", "TAKE", "TELL", "THAN", "THAT", "THEM", "THEN", "THEY",
        "THIS", "THUS", "TIME", "TOLD", "TOOK", "TURN", "USED", "VERY", "WANT",
        "WELL", "WENT", "WERE", "WHAT", "WHEN", "WITH", "WORD", "WORK", "YEAR",
        # Finance-specific common words
        "IPO", "ETF", "CEO", "CFO", "COO", "CTO", "SEC", "NYSE", "NASDAQ",
        "GDP", "FED", "IMF", "EUR", "USD", "GBP", "YEN", "OTC", "ALT",
    }
)

# Ambiguous short keywords that appear in too many non-company contexts
_NAME_KEYWORD_BLOCKLIST: frozenset[str] = frozenset(
    {"linde", "deere", "ford", "general", "advanced", "american", "next",
     "united", "home", "procter", "gamble", "walt", "morgan", "bank", "visa"}
)


def _company_keywords(name: str) -> list[str]:
    """Extract meaningful search keywords from a company name.

    Strips legal suffixes and punctuation, returns tokens ≥4 chars that aren't
    in the blocklist — ordered longest-first so the most specific token matches first.
    """
    cleaned = _LEGAL_SUFFIXES.sub(" ", name)
    cleaned = re.sub(r"[^A-Za-z0-9 ]", " ", cleaned)
    tokens = [t.lower() for t in cleaned.split() if len(t) >= 4]
    tokens = [t for t in tokens if t not in _NAME_KEYWORD_BLOCKLIST]
    return sorted(set(tokens), key=len, reverse=True)


def _build_name_patterns(tickers: list[tuple[str, str]]) -> list[tuple[re.Pattern[str], str]]:
    """Build (compiled pattern, symbol) pairs for company-name matching."""
    patterns: list[tuple[re.Pattern[str], str]] = []
    for symbol, company_name in tickers:
        for kw in _company_keywords(company_name):
            try:
                pat = re.compile(rf"\b{re.escape(kw)}\b", re.IGNORECASE)
                patterns.append((pat, symbol))
            except re.error:
                pass
    return patterns


def extract_tickers_regex(text: str) -> list[str]:
    """Return deduplicated tickers found via regex, filtered by stopword list."""
    found: list[str] = []
    seen: set[str] = set()
    for m in _TICKER_RE.finditer(text):
        symbol = (m.group(1) or m.group(2)).upper()
        if symbol not in _STOPWORDS and symbol not in seen:
            seen.add(symbol)
            found.append(symbol)
    return found


async def extract_tickers_db(text: str, db: AsyncSession) -> list[str]:
    """Return tickers that exist in the DB, matched by symbol or company name."""
    all_tickers = await db.execute(select(Ticker.symbol, Ticker.company_name))
    ticker_rows: list[tuple[str, str]] = list(all_tickers.all())

    matched: set[str] = set()

    # 1. Regex symbol match (fast path — catches $TSLA, bare AAPL, etc.)
    candidates = extract_tickers_regex(text)
    if candidates:
        known_symbols = {row[0] for row in ticker_rows}
        matched.update(s for s in candidates if s in known_symbols)

    # 2. Company name keyword match (catches truncated NewsAPI content where
    #    the full article text isn't available, only title + short preview)
    name_patterns = _build_name_patterns(ticker_rows)
    for pat, symbol in name_patterns:
        if symbol not in matched and pat.search(text):
            matched.add(symbol)

    return list(matched)
