"""Optional TypeSafe JEV relevance scoring for already-retrieved memories."""

from __future__ import annotations

import logging
import os
import time
from typing import Callable, TypeVar

from .redact import redact_embedding_input


Hit = TypeVar("Hit")
MODEL = "jev-1.13.0"
URL = "https://api.typesafe.ai/v1/systemone"
QUESTIONS = {
    "rel": {
        "type": "score",
        "instructions": "How useful is this memory for answering the question?",
        "criteria": ["Irrelevant", "Loosely related", "Partially answers", "Directly answers"],
    }
}
POOL_SIZE = 16


def _excerpt(text: str, byte_limit: int) -> str:
    raw = redact_embedding_input(text).encode("utf-8")
    if len(raw) <= byte_limit:
        return raw.decode("utf-8")
    marker = "\n[...]\n"
    half = (byte_limit - len(marker)) // 2
    return raw[:half].decode("utf-8", "ignore") + marker + raw[-half:].decode("utf-8", "ignore")


def _score(payload: object) -> float:
    if not isinstance(payload, dict) or payload.get("model") != MODEL:
        raise ValueError("unexpected JEV model")
    answers = payload.get("answers")
    rel = answers.get("rel") if isinstance(answers, dict) else None
    score = rel.get("score") if isinstance(rel, dict) else None
    if type(score) not in (int, float) or not 0 <= score <= 3:
        raise ValueError("invalid JEV score")
    return float(score)


def jev_reranker(text_for_hit: Callable[[Hit], str]) -> Callable[[str, list[Hit], int], list[Hit]]:
    """Create a reranker for hits supplied in their original retrieval order."""
    token = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if not token:
        raise ValueError("Set TYPESAFE_API_KEY to use JEV")
    try:
        import requests
    except ImportError as exc:
        raise ValueError("Install the JEV extra with python -m pip install '.[jev]'") from exc

    def rerank(query: str, hits: list[Hit], top_k: int) -> list[Hit]:
        if top_k <= 0 or len(hits) < 2:
            return hits[:max(0, top_k)]
        pool = hits[:POOL_SIZE]
        question = _excerpt(query, 7680)
        scores = []
        deadline = time.monotonic() + 15
        try:
            with requests.Session() as session:
                for hit in pool:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("JEV request window elapsed")
                    memory = _excerpt(text_for_hit(hit), 22000)
                    response = session.post(
                        URL,
                        headers={"Authorization": f"Bearer {token}"},
                        json={"model": MODEL, "questions": QUESTIONS,
                              "state": f"Question: {question}\nMemory: {memory}"},
                        timeout=min(5, remaining),
                        allow_redirects=False,
                    )
                    if response.status_code != 200:
                        raise ValueError(f"JEV HTTP {response.status_code}")
                    scores.append(_score(response.json()))
        except (requests.RequestException, ValueError, TimeoutError) as exc:
            logging.getLogger(__name__).warning(
                "JEV rerank incomplete (%s); keeping retrieval order", type(exc).__name__
            )
            return hits[:top_k]

        order = sorted(range(len(pool)), key=scores.__getitem__, reverse=True)
        return ([pool[i] for i in order] + hits[len(pool):top_k])[:top_k]

    return rerank
