"""Small vector helpers for LMC-5.

The reference implementation stores vectors in SQLite as JSON. That is not a
replacement for pgvector, LanceDB, FAISS, or a production ANN index; it is a
portable baseline that makes the embedding layer real and testable.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from hashlib import sha256
from typing import Iterable


def normalize_vector(values: Iterable[float]) -> list[float]:
    vector = [float(value) for value in values]
    if not vector:
        raise ValueError("vector must not be empty")
    if any(not math.isfinite(value) for value in vector):
        raise ValueError("vector values must be finite")
    return vector


def vector_to_json(values: Iterable[float]) -> str:
    return json.dumps(normalize_vector(values), separators=(",", ":"))


def vector_from_json(value: str) -> list[float]:
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        raise ValueError("vector JSON must be a list")
    return normalize_vector(parsed)


def vector_hash(values: Iterable[float]) -> str:
    return sha256(vector_to_json(values).encode("utf-8")).hexdigest()


def cosine_similarity(left: Iterable[float], right: Iterable[float]) -> float:
    a = normalize_vector(left)
    b = normalize_vector(right)
    if len(a) != len(b):
        raise ValueError(f"vector dimensions differ: {len(a)} != {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def toy_embed(text: str, *, dimensions: int = 64) -> list[float]:
    """Deterministic local embedding for tests and demos.

    This is intentionally not a semantic model. It is a hashed bag-of-tokens
    vector so the vector store can be exercised offline without API keys.
    """
    if dimensions <= 0:
        raise ValueError("dimensions must be positive")
    tokens = [token.lower() for token in text.replace("_", " ").split() if token.strip()]
    counts = Counter(tokens)
    vector = [0.0] * dimensions
    for token, count in counts.items():
        bucket = int(sha256(token.encode("utf-8")).hexdigest(), 16) % dimensions
        vector[bucket] += float(count)
    return vector
