"""Chunk-based consolidation for the LMC-5 awareness layer."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .store import MemoryStore


_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "has",
    "have",
    "i",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "we",
    "with",
    "you",
}


@dataclass(frozen=True)
class ConsolidationResult:
    run_id: int
    chunks_created: int = 0
    observations_created: int = 0
    chunk_ids: list[int] = field(default_factory=list)
    observation_ids: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "chunks_created": self.chunks_created,
            "observations_created": self.observations_created,
            "chunk_ids": self.chunk_ids,
            "observation_ids": self.observation_ids,
        }


def _chunk_hash(event_ids: list[int], summary: str) -> str:
    payload = json.dumps({"event_ids": event_ids, "summary": summary}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _keywords(text: str, *, limit: int = 8) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", text.lower())
    counts = Counter(word for word in words if word not in _STOPWORDS)
    return [word for word, _ in counts.most_common(limit)]


def _compact(text: str, *, limit: int = 140) -> str:
    clean = " ".join(text.split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3].rstrip() + "..."


def _summarize(rows: list[sqlite3.Row]) -> tuple[str, list[str]]:
    role_counts = Counter(str(row["role"]) for row in rows)
    first = rows[0]
    last = rows[-1]
    combined = "\n".join(str(row["content"]) for row in rows)
    keywords = _keywords(combined)
    role_text = ", ".join(f"{role}:{count}" for role, count in sorted(role_counts.items()))
    summary = (
        f"Event chunk {first['id']}-{last['id']} ({len(rows)} events; {role_text}). "
        f"First: {_compact(str(first['content']))} "
        f"Last: {_compact(str(last['content']))}"
    )
    if keywords:
        summary += " Keywords: " + ", ".join(keywords)
    return summary, keywords


def _unconsolidated_events(
    conn: sqlite3.Connection,
    *,
    channel: str | None,
    max_events: int,
) -> list[sqlite3.Row]:
    channel_clause = "AND e.channel = ?" if channel else ""
    params: list[Any] = []
    if channel:
        params.append(channel)
    params.append(max_events)
    return conn.execute(
        f"""
        SELECT e.*
          FROM events e
          LEFT JOIN chunk_events ce ON ce.event_id = e.id
         WHERE ce.event_id IS NULL
           {channel_clause}
         ORDER BY e.id ASC
         LIMIT ?
        """,
        params,
    ).fetchall()


def consolidate_events(
    store: MemoryStore,
    *,
    window_size: int = 20,
    channel: str | None = None,
    max_events: int = 500,
    create_observations: bool = True,
) -> ConsolidationResult:
    """Group raw events into chunks and optionally promote chunks into observations.

    This is intentionally deterministic and provider-free. It creates an
    "awareness layer" interface without requiring an LLM summarizer. Production
    systems can replace `_summarize` with a model-backed summarizer while keeping
    the same tables and LMC-5 coordinates.
    """
    if window_size <= 0:
        raise ValueError("window_size must be positive")
    if max_events <= 0:
        raise ValueError("max_events must be positive")

    conn = store.conn
    run_cur = conn.execute(
        """
        INSERT INTO consolidation_runs (channel, window_size, notes_json)
        VALUES (?, ?, ?)
        """,
        (channel, window_size, json.dumps({"strategy": "deterministic-chunk-v1"})),
    )
    run_id = int(run_cur.lastrowid)
    rows = _unconsolidated_events(conn, channel=channel, max_events=max_events)

    chunk_ids: list[int] = []
    observation_ids: list[int] = []
    for start in range(0, len(rows), window_size):
        window = rows[start : start + window_size]
        if not window:
            continue
        event_ids = [int(row["id"]) for row in window]
        summary, keywords = _summarize(window)
        digest = _chunk_hash(event_ids, summary)
        existing = conn.execute(
            "SELECT id FROM event_chunks WHERE content_hash = ?",
            (digest,),
        ).fetchone()
        if existing:
            chunk_id = int(existing["id"])
        else:
            chunk_cur = conn.execute(
                """
                INSERT INTO event_chunks (
                    channel, start_event_id, end_event_id, event_count,
                    summary, keywords_json, content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(window[0]["channel"]),
                    event_ids[0],
                    event_ids[-1],
                    len(event_ids),
                    summary,
                    json.dumps(keywords, ensure_ascii=False),
                    digest,
                ),
            )
            chunk_id = int(chunk_cur.lastrowid)
            conn.executemany(
                "INSERT OR IGNORE INTO chunk_events (chunk_id, event_id) VALUES (?, ?)",
                [(chunk_id, event_id) for event_id in event_ids],
            )
        chunk_ids.append(chunk_id)

        if create_observations:
            memory, created = store.add_memory(
                title=f"Observation from event chunk {event_ids[0]}-{event_ids[-1]}",
                content=summary,
                thread="awareness",
                category="observation",
                tags=["chunk", "consolidated", *keywords[:3]],
                status="review",
                risk_level="normal",
                urgency="normal",
                confidence=0.5,
                source="consolidation",
            )
            if created and memory.id is not None:
                observation_ids.append(memory.id)

    conn.execute(
        """
        UPDATE consolidation_runs
           SET chunks_created = ?,
               observations_created = ?
         WHERE id = ?
        """,
        (len(chunk_ids), len(observation_ids), run_id),
    )
    conn.commit()
    return ConsolidationResult(
        run_id=run_id,
        chunks_created=len(chunk_ids),
        observations_created=len(observation_ids),
        chunk_ids=chunk_ids,
        observation_ids=observation_ids,
    )
