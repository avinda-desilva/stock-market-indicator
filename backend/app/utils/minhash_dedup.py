"""
MinHash / LSH near-duplicate detection for the ingestion pipeline.

Usage pattern in each ingestor:
    from app.utils.minhash_dedup import compute_minhash, load_recent_signatures, is_near_duplicate

    recent = await load_recent_signatures(db)
    sig = compute_minhash(content)
    if is_near_duplicate(sig, recent):
        continue   # skip syndicated copy
    # persist article with minhash_signature=sig
"""

import re
from datetime import datetime, timedelta, timezone

from datasketch import MinHash
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.article import Article

# ── Tuning constants ──────────────────────────────────────────────────────────
NUM_PERM: int = 128          # number of hash permutations — higher = more accurate
SIMILARITY_THRESHOLD: float = 0.85   # Jaccard similarity above which we consider duplicate
DEDUP_WINDOW_HOURS: int = 24         # only compare against articles ingested this recently
MIN_CONTENT_TOKENS: int = 5          # skip MinHash for very short texts (no signal)


def _shingle(text: str, k: int = 3) -> set[str]:
    """Return the set of word-level k-shingles from *text*."""
    tokens = re.findall(r"\w+", text.lower())
    if len(tokens) < k:
        return {" ".join(tokens)}
    return {" ".join(tokens[i : i + k]) for i in range(len(tokens) - k + 1)}


def compute_minhash(content: str) -> list[int]:
    """
    Compute a MinHash signature for *content*.

    Returns a list of NUM_PERM integers suitable for storage in JSONB and
    later Jaccard estimation.
    """
    m = MinHash(num_perm=NUM_PERM)
    for shingle in _shingle(content):
        m.update(shingle.encode("utf-8"))
    return [int(v) for v in m.hashvalues]


def _jaccard_from_signatures(sig_a: list[int], sig_b: list[int]) -> float:
    """Estimate Jaccard similarity from two MinHash signature arrays."""
    if len(sig_a) != len(sig_b):
        return 0.0
    matches = sum(a == b for a, b in zip(sig_a, sig_b))
    return matches / len(sig_a)


def is_near_duplicate(
    sig: list[int],
    existing_signatures: list[list[int]],
    threshold: float = SIMILARITY_THRESHOLD,
) -> bool:
    """Return True if *sig* is within *threshold* Jaccard distance of any existing signature."""
    for existing in existing_signatures:
        if _jaccard_from_signatures(sig, existing) >= threshold:
            return True
    return False


async def load_recent_signatures(
    db: AsyncSession,
    window_hours: int = DEDUP_WINDOW_HOURS,
) -> list[list[int]]:
    """
    Load MinHash signatures for articles ingested within the last *window_hours*.

    Returns only rows that have a non-null signature so callers can pass the
    result directly to is_near_duplicate() without extra filtering.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    rows = await db.execute(
        select(Article.minhash_signature).where(
            Article.created_at >= cutoff,
            Article.minhash_signature.is_not(None),
        )
    )
    return [r[0] for r in rows if r[0]]
