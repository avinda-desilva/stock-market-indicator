"""
Singleton sentence-transformer encoder for 384-dim embeddings.

Model: all-MiniLM-L6-v2 (fast, good semantic quality, CPU-friendly)

encode() offloads the CPU-bound inference to a thread-pool executor so it
never blocks the asyncio event loop.
"""
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import Sequence

import numpy as np
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="embed")


@lru_cache(maxsize=1)
def _get_model() -> SentenceTransformer:
    logger.info("Loading embedding model %s …", _MODEL_NAME)
    model = SentenceTransformer(_MODEL_NAME)
    logger.info("Embedding model loaded.")
    return model


def _encode_sync(text: str) -> list[float]:
    vec: np.ndarray = _get_model().encode(text, normalize_embeddings=True)
    return vec.tolist()


async def encode(text: str) -> list[float]:
    """Return a 384-dim unit-normalised embedding vector for *text*."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_EXECUTOR, _encode_sync, text)


async def encode_batch(texts: Sequence[str]) -> list[list[float]]:
    """Encode multiple texts in a single model call (more efficient than one-by-one)."""
    def _batch_sync(texts: Sequence[str]) -> list[list[float]]:
        vecs: np.ndarray = _get_model().encode(list(texts), normalize_embeddings=True, batch_size=32)
        return vecs.tolist()

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_EXECUTOR, _batch_sync, texts)
