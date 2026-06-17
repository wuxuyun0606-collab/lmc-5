"""Z-axis fact evolution review helpers.

This module is intentionally provider-free. It can find fact conflicts and
record pending audits, but it does not call a model and never supersedes facts.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from .redact import redact_obj
from .store import MemoryStore


@dataclass(frozen=True)
class ZConflictCandidate:
    left_memory_id: int
    right_memory_id: int
    fact_key: str | None = None
    reason: str = ""
    source: str = "deterministic"
    confidence: float = 0.5

    def to_dict(self) -> dict[str, Any]:
        return {
            "left_memory_id": self.left_memory_id,
            "right_memory_id": self.right_memory_id,
            "fact_key": self.fact_key,
            "reason": self.reason,
            "source": self.source,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class ZAuditResult:
    candidates_seen: int
    pending_ready: int
    audits_inserted: int = 0
    audits_reused: int = 0
    candidates: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidates_seen": self.candidates_seen,
            "pending_ready": self.pending_ready,
            "audits_inserted": self.audits_inserted,
            "audits_reused": self.audits_reused,
            "candidates": self.candidates,
        }


def _pair(left_id: int, right_id: int) -> tuple[int, int]:
    if left_id == right_id:
        raise ValueError("conflict pair cannot point to the same memory")
    return (left_id, right_id) if left_id < right_id else (right_id, left_id)


def _row_to_candidate(row: sqlite3.Row, *, reason: str, source: str) -> ZConflictCandidate:
    left_id, right_id = _pair(int(row["left_id"]), int(row["right_id"]))
    return ZConflictCandidate(
        left_memory_id=left_id,
        right_memory_id=right_id,
        fact_key=row["fact_key"] if "fact_key" in row.keys() else None,
        reason=reason,
        source=source,
        confidence=0.75 if source == "contradicts_relation" else 0.6,
    )


def find_z_conflict_candidates(
    store: MemoryStore,
    *,
    limit: int = 100,
    include_existing: bool = False,
) -> list[ZConflictCandidate]:
    """Find candidate fact conflicts without mutating memory."""

    if limit <= 0:
        raise ValueError("limit must be positive")

    candidates: dict[tuple[int, int], ZConflictCandidate] = {}
    params: list[Any] = []
    existing_clause = ""
    if not include_existing:
        existing_clause = """
          AND NOT EXISTS (
                SELECT 1
                  FROM z_conflict_audits z
                 WHERE z.left_memory_id = MIN(m1.id, m2.id)
                   AND z.right_memory_id = MAX(m1.id, m2.id)
          )
        """

    same_fact_rows = store.conn.execute(
        f"""
        SELECT m1.id AS left_id,
               m2.id AS right_id,
               m1.fact_key AS fact_key
          FROM memories m1
          JOIN memories m2
            ON m1.fact_key = m2.fact_key
           AND m1.id < m2.id
         WHERE m1.fact_key IS NOT NULL
           AND m1.fact_key != ''
           AND m1.status IN ('current', 'review')
           AND m2.status IN ('current', 'review')
           AND m1.content_hash != m2.content_hash
           {existing_clause}
         ORDER BY m1.updated_at DESC, m2.updated_at DESC
         LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    for row in same_fact_rows:
        candidate = _row_to_candidate(
            row,
            reason="same fact_key has multiple non-identical current/review memories",
            source="same_fact_key",
        )
        candidates[(candidate.left_memory_id, candidate.right_memory_id)] = candidate

    remaining = max(limit - len(candidates), 0)
    if remaining:
        existing_relation_clause = ""
        if not include_existing:
            existing_relation_clause = """
              AND NOT EXISTS (
                    SELECT 1
                      FROM z_conflict_audits z
                     WHERE z.left_memory_id = MIN(r.source_id, r.target_id)
                       AND z.right_memory_id = MAX(r.source_id, r.target_id)
              )
            """
        relation_rows = store.conn.execute(
            f"""
            SELECT r.source_id AS left_id,
                   r.target_id AS right_id,
                   COALESCE(m1.fact_key, m2.fact_key) AS fact_key
             FROM relations r
             JOIN memories m1 ON m1.id = r.source_id
             JOIN memories m2 ON m2.id = r.target_id
             WHERE r.relation_type = 'contradicts'
               AND m1.status IN ('current', 'review')
               AND m2.status IN ('current', 'review')
               AND (m1.fact_key IS NULL OR m1.active_fact = 1)
               AND (m2.fact_key IS NULL OR m2.active_fact = 1)
               {existing_relation_clause}
             ORDER BY r.created_at DESC, r.id DESC
             LIMIT ?
            """,
            (remaining,),
        ).fetchall()
        for row in relation_rows:
            candidate = _row_to_candidate(
                row,
                reason="explicit contradicts relation needs Z-axis review",
                source="contradicts_relation",
            )
            candidates.setdefault((candidate.left_memory_id, candidate.right_memory_id), candidate)

    return list(candidates.values())[:limit]


def _insert_pending_audit(store: MemoryStore, candidate: ZConflictCandidate) -> bool:
    before = store.conn.total_changes
    store.conn.execute(
        """
        INSERT OR IGNORE INTO z_conflict_audits (
            left_memory_id, right_memory_id, fact_key, status, verdict,
            confidence, reason, source
        ) VALUES (?, ?, ?, 'pending', 'pending', ?, ?, ?)
        """,
        (
            candidate.left_memory_id,
            candidate.right_memory_id,
            candidate.fact_key,
            candidate.confidence,
            candidate.reason,
            candidate.source,
        ),
    )
    return store.conn.total_changes > before


def run_z_audit(
    store: MemoryStore,
    *,
    limit: int = 100,
    apply: bool = False,
    include_existing: bool = False,
    redact: bool = True,
) -> ZAuditResult:
    """Preview or record pending Z-axis conflict audits.

    Dry-run is the default and does not require any provider key. ``apply=True``
    only inserts pending audit rows; it does not call an API and does not
    supersede, archive, or rewrite memories.
    """

    candidates = find_z_conflict_candidates(
        store,
        limit=limit,
        include_existing=include_existing,
    )
    inserted = 0
    reused = 0
    if apply:
        for candidate in candidates:
            if _insert_pending_audit(store, candidate):
                inserted += 1
            else:
                reused += 1
        store.conn.commit()

    output = [candidate.to_dict() for candidate in candidates]
    result = ZAuditResult(
        candidates_seen=len(candidates),
        pending_ready=len(candidates),
        audits_inserted=inserted,
        audits_reused=reused,
        candidates=output,
    )
    if redact:
        data = redact_obj(result.to_dict())
        return ZAuditResult(
            candidates_seen=int(data["candidates_seen"]),
            pending_ready=int(data["pending_ready"]),
            audits_inserted=int(data["audits_inserted"]),
            audits_reused=int(data["audits_reused"]),
            candidates=list(data["candidates"]),
        )
    return result
