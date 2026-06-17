"""Nightly hippocampus pass for LMC-5.

The hippocampus pass turns event chunks into gated memory candidates. It is
provider-free by default: external models can propose candidates, but local
LMC-5 code owns filtering, writes, and relation safety.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from .models import (
    RELATION_TYPES,
    REVIEW_RELATION_TYPES,
    SAFE_RELATION_TYPES,
    normalize_relation_type,
)
from .redact import redact_obj
from .store import MemoryStore

DEFAULT_REJECT_PATTERNS = (
    re.compile(r"\b(api[_-]?key|secret|token|password|passwd|private[_-]?key)\b", re.I),
    re.compile(r"\bpostgres(?:ql)?://[^\s]+", re.I),
)

CandidateProposer = Callable[[Sequence[sqlite3.Row]], Sequence["MemoryCandidate"]]


@dataclass(frozen=True)
class MemoryCandidate:
    """A reviewable candidate produced from one or more event chunks."""

    title: str
    content: str
    thread: str = "awareness"
    category: str = "observation"
    tags: list[str] = field(default_factory=list)
    fact_key: str | None = None
    status: str = "review"
    risk_level: str = "normal"
    urgency: str = "normal"
    confidence: float | None = 0.55
    importance: int = 5
    source_chunk_ids: list[int] = field(default_factory=list)
    evidence: str = ""
    relation_hints: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "content": self.content,
            "thread": self.thread,
            "category": self.category,
            "tags": self.tags,
            "fact_key": self.fact_key,
            "status": self.status,
            "risk_level": self.risk_level,
            "urgency": self.urgency,
            "confidence": self.confidence,
            "importance": self.importance,
            "source_chunk_ids": self.source_chunk_ids,
            "evidence": self.evidence,
            "relation_hints": self.relation_hints,
        }


@dataclass(frozen=True)
class RelationPlan:
    """A relation proposed by the hippocampus pass."""

    source_id: int | str
    target_id: int | str
    relation_type: str
    strength: float = 0.5
    reason: str = ""
    action: str = "apply"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "relation_type": self.relation_type,
            "strength": self.strength,
            "reason": self.reason,
            "action": self.action,
        }


@dataclass(frozen=True)
class HippocampusResult:
    chunks_seen: int
    candidates_seen: int
    promote_ready: int
    inserted: int = 0
    reused: int = 0
    relations_inserted: int = 0
    candidates: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    relations: list[dict[str, Any]] = field(default_factory=list)
    review_relations: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunks_seen": self.chunks_seen,
            "candidates_seen": self.candidates_seen,
            "promote_ready": self.promote_ready,
            "inserted": self.inserted,
            "reused": self.reused,
            "relations_inserted": self.relations_inserted,
            "candidates": self.candidates,
            "rejected": self.rejected,
            "relations": self.relations,
            "review_relations": self.review_relations,
        }


def _keywords(text: str, *, limit: int = 8) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", text.lower())
    stop = {"and", "the", "for", "with", "that", "this", "from", "event", "chunk"}
    counts = Counter(word for word in words if word not in stop)
    return [word for word, _ in counts.most_common(limit)]


def _compact(text: str, *, limit: int = 180) -> str:
    clean = " ".join(text.split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3].rstrip() + "..."


def _has_sensitive_material(text: str) -> bool:
    return any(pattern.search(text) for pattern in DEFAULT_REJECT_PATTERNS)


def _chunk_rows(
    conn: sqlite3.Connection,
    *,
    channel: str | None,
    limit_chunks: int,
) -> list[sqlite3.Row]:
    if limit_chunks <= 0:
        raise ValueError("limit_chunks must be positive")
    if channel:
        return conn.execute(
            """
            SELECT *
              FROM event_chunks
             WHERE channel = ?
             ORDER BY id DESC
             LIMIT ?
            """,
            (channel, limit_chunks),
        ).fetchall()
    return conn.execute(
        """
        SELECT *
          FROM event_chunks
         ORDER BY id DESC
         LIMIT ?
        """,
        (limit_chunks,),
    ).fetchall()


def deterministic_proposer(chunks: Sequence[sqlite3.Row]) -> list[MemoryCandidate]:
    """Build conservative candidates without calling a model."""

    candidates: list[MemoryCandidate] = []
    for row in chunks:
        chunk_id = int(row["id"])
        summary = str(row["summary"])
        keywords = json.loads(row["keywords_json"] or "[]")
        tags = [str(item) for item in keywords[:5] if str(item).strip()]
        importance = 5 + min(len(tags), 3)
        if any(word in summary.lower() for word in ("risk", "rollback", "production", "secret")):
            importance += 1
        risk = "medium" if _has_sensitive_material(summary) else "normal"
        candidates.append(
            MemoryCandidate(
                title=f"Hippocampus observation from chunk {chunk_id}",
                content=summary,
                thread="awareness",
                category="observation",
                tags=["hippocampus", "chunk", *tags],
                status="review",
                risk_level=risk,
                urgency="normal",
                confidence=0.55,
                importance=min(10, importance),
                source_chunk_ids=[chunk_id],
                evidence=(
                    f"event_chunks.id={chunk_id}; "
                    f"events={row['start_event_id']}-{row['end_event_id']}; "
                    f"channel={row['channel']}"
                ),
            )
        )
    return candidates


def _reject_reason(candidate: MemoryCandidate, *, min_importance: int) -> str | None:
    if not candidate.title.strip() or not candidate.content.strip():
        return "empty title/content"
    if candidate.importance < min_importance:
        return f"importance<{min_importance}"
    if candidate.risk_level != "normal":
        return f"risk_level={candidate.risk_level}"
    if not candidate.source_chunk_ids:
        return "missing source_chunk_ids"
    if _has_sensitive_material(candidate.content):
        return "sensitive_material"
    return None


def _candidate_trace(candidate: MemoryCandidate) -> str:
    chunk_text = ",".join(str(chunk_id) for chunk_id in sorted(candidate.source_chunk_ids))
    return f"hippocampus:chunks={chunk_text}"


def _keyword_set(title: str, content: str, tags: Sequence[str]) -> set[str]:
    return set(_keywords(f"{title} {content} {' '.join(tags)}", limit=20))


def _safe_relation_plans(
    store: MemoryStore,
    promoted: list[tuple[int | str, MemoryCandidate]],
    *,
    max_existing: int = 300,
) -> tuple[list[RelationPlan], list[RelationPlan]]:
    plans: list[RelationPlan] = []
    review: list[RelationPlan] = []
    existing = store.conn.execute(
        """
        SELECT *
          FROM memories
         WHERE status != 'archived'
         ORDER BY created_at DESC, id DESC
         LIMIT ?
        """,
        (max_existing,),
    ).fetchall()

    existing_keywords = {
        int(row["id"]): _keyword_set(
            str(row["title"]),
            str(row["content"]),
            json.loads(row["tags_json"] or "[]"),
        )
        for row in existing
    }
    for source_id, candidate in promoted:
        candidate_keys = _keyword_set(candidate.title, candidate.content, candidate.tags)
        for row in existing:
            target_id = int(row["id"])
            if source_id == target_id:
                continue
            overlap = candidate_keys.intersection(existing_keywords[target_id])
            if len(overlap) < 2:
                continue
            relation_type = "same_topic"
            if str(row["thread"]) == candidate.thread:
                relation_type = "same_event"
            strength = min(0.85, 0.35 + 0.1 * len(overlap))
            plans.append(
                RelationPlan(
                    source_id=source_id,
                    target_id=target_id,
                    relation_type=relation_type,
                    strength=round(strength, 2),
                    reason="keyword/thread overlap: " + ", ".join(sorted(overlap)[:5]),
                )
            )
            break

        for hint in candidate.relation_hints:
            relation_type = normalize_relation_type(str(hint.get("relation_type", "")))
            if relation_type not in RELATION_TYPES:
                continue
            target_id = hint.get("target_id")
            if target_id is None:
                continue
            plan = RelationPlan(
                source_id=source_id,
                target_id=int(target_id),
                relation_type=relation_type,
                strength=float(hint.get("strength", 0.5)),
                reason=str(hint.get("reason", "model hint")).strip(),
                action="apply" if relation_type in SAFE_RELATION_TYPES else "review",
            )
            if relation_type in SAFE_RELATION_TYPES:
                plans.append(plan)
            elif relation_type in REVIEW_RELATION_TYPES:
                review.append(plan)

    promoted_sorted = sorted(
        promoted,
        key=lambda item: min(item[1].source_chunk_ids) if item[1].source_chunk_ids else 0,
    )
    for left, right in zip(promoted_sorted, promoted_sorted[1:]):
        left_id, left_candidate = left
        right_id, right_candidate = right
        if left_id == right_id:
            continue
        plans.append(
            RelationPlan(
                source_id=left_id,
                target_id=right_id,
                relation_type="temporal_sequence",
                strength=0.45,
                reason=(
                    f"chunk order {left_candidate.source_chunk_ids} -> "
                    f"{right_candidate.source_chunk_ids}"
                ),
            )
        )
    return plans, review


def run_hippocampus(
    store: MemoryStore,
    *,
    channel: str | None = None,
    limit_chunks: int = 50,
    min_importance: int = 7,
    max_promote: int = 10,
    apply: bool = False,
    create_relations: bool = True,
    proposer: CandidateProposer | None = None,
    redact: bool = True,
) -> HippocampusResult:
    """Run a gated chunk-to-memory pass.

    Default mode is dry-run. Passing ``apply=True`` promotes accepted candidates
    into review memories and applies only safe relation types. Contradictions,
    cause/effect, and support claims stay as review plans unless the caller
    handles them explicitly.
    """

    if min_importance < 0 or min_importance > 10:
        raise ValueError("min_importance must be between 0 and 10")
    if max_promote <= 0:
        raise ValueError("max_promote must be positive")

    chunks = _chunk_rows(store.conn, channel=channel, limit_chunks=limit_chunks)
    propose = proposer or deterministic_proposer
    candidates = list(propose(chunks))

    accepted: list[MemoryCandidate] = []
    rejected: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, tuple[int, ...]]] = set()
    for candidate in candidates:
        reason = _reject_reason(candidate, min_importance=min_importance)
        key = (candidate.content.strip(), tuple(sorted(candidate.source_chunk_ids)))
        if reason is None and key in seen_keys:
            reason = "duplicate_candidate"
        if reason is None:
            seen_keys.add(key)
            accepted.append(candidate)
        else:
            item = candidate.to_dict()
            item["reject_reason"] = reason
            rejected.append(item)

    selected = accepted[:max_promote]
    inserted = 0
    reused = 0
    promoted: list[tuple[int | str, MemoryCandidate]] = []
    candidate_output: list[dict[str, Any]] = []
    for index, candidate in enumerate(selected, start=1):
        item = candidate.to_dict()
        item["trace"] = _candidate_trace(candidate)
        if apply:
            record, created = store.add_memory(
                title=candidate.title,
                content=candidate.content,
                thread=candidate.thread,
                category=candidate.category,
                tags=candidate.tags,
                fact_key=candidate.fact_key,
                status=candidate.status,
                risk_level=candidate.risk_level,
                urgency=candidate.urgency,
                confidence=candidate.confidence,
                source=_candidate_trace(candidate),
            )
            memory_id = int(record.id)
            item["memory_id"] = memory_id
            if created:
                inserted += 1
            else:
                reused += 1
            promoted.append((memory_id, candidate))
        else:
            temp_id = f"candidate:{index}"
            item["memory_id"] = temp_id
            promoted.append((temp_id, candidate))
        candidate_output.append(item)

    relation_plans: list[RelationPlan] = []
    review_plans: list[RelationPlan] = []
    relations_inserted = 0
    if create_relations and promoted:
        relation_plans, review_plans = _safe_relation_plans(store, promoted)
        if apply:
            for plan in relation_plans:
                if not isinstance(plan.source_id, int) or not isinstance(plan.target_id, int):
                    continue
                if plan.relation_type not in SAFE_RELATION_TYPES:
                    review_plans.append(
                        RelationPlan(
                            source_id=plan.source_id,
                            target_id=plan.target_id,
                            relation_type=plan.relation_type,
                            strength=plan.strength,
                            reason=plan.reason,
                            action="review",
                        )
                    )
                    continue
                before = store.conn.total_changes
                store.add_relation(
                    plan.source_id,
                    plan.target_id,
                    plan.relation_type,
                    strength=plan.strength,
                    reason=plan.reason,
                )
                if store.conn.total_changes > before:
                    relations_inserted += 1

    result = HippocampusResult(
        chunks_seen=len(chunks),
        candidates_seen=len(candidates),
        promote_ready=len(selected),
        inserted=inserted,
        reused=reused,
        relations_inserted=relations_inserted,
        candidates=candidate_output,
        rejected=rejected,
        relations=[plan.to_dict() for plan in relation_plans],
        review_relations=[plan.to_dict() for plan in review_plans],
    )
    if redact:
        data = redact_obj(result.to_dict())
        return HippocampusResult(
            chunks_seen=int(data["chunks_seen"]),
            candidates_seen=int(data["candidates_seen"]),
            promote_ready=int(data["promote_ready"]),
            inserted=int(data["inserted"]),
            reused=int(data["reused"]),
            relations_inserted=int(data["relations_inserted"]),
            candidates=list(data["candidates"]),
            rejected=list(data["rejected"]),
            relations=list(data["relations"]),
            review_relations=list(data["review_relations"]),
        )
    return result
