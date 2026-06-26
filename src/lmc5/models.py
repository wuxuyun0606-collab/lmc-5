"""Core dataclasses for LMC-5."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

FactStatus = Literal["current", "review", "superseded", "historical", "archived", "candidate_thread"]
RiskLevel = Literal["normal", "medium", "high"]
Urgency = Literal["low", "normal", "high"]
EventRole = Literal["system", "user", "assistant", "tool", "environment", "note"]
VectorOwnerType = Literal["memory", "event"]
RelationType = Literal[
    "same_issue",
    "same_project",
    "same_tool",
    "same_event",
    "same_topic",
    "temporal_sequence",
    "emotional_link",
    "in_thread",
    "same_person",
    "in_episode",
    "instance_of",
    "cause_effect",
    "supports",
    "contradicts",
    "derived_from",
]
MetabolismAction = Literal[
    "promote",
    "demote",
    "split_thread",
    "mark_review",
    "supersede",
    "archive",
    "distill_growth",
]

FACT_STATUSES = {
    "current",
    "review",
    "superseded",
    "historical",
    "archived",
    "candidate_thread",
}
RISK_LEVELS = {"normal", "medium", "high"}
URGENCY_LEVELS = {"low", "normal", "high"}
EVENT_ROLES = {"system", "user", "assistant", "tool", "environment", "note"}
VECTOR_OWNER_TYPES = {"memory", "event"}
RELATION_TYPES = {
    "same_issue",
    "same_project",
    "same_tool",
    "same_event",
    "same_topic",
    "temporal_sequence",
    "emotional_link",
    "in_thread",
    "same_person",
    "in_episode",
    "instance_of",
    "cause_effect",
    "supports",
    "contradicts",
    "derived_from",
}
SAFE_RELATION_TYPES = {
    "same_issue",
    "same_project",
    "same_tool",
    "same_event",
    "same_topic",
    "temporal_sequence",
    "emotional_link",
    "in_thread",
    "same_person",
    "in_episode",
    "instance_of",
    "derived_from",
}
REVIEW_RELATION_TYPES = {"contradicts", "cause_effect", "supports"}
SYMMETRIC_RELATION_TYPES = {
    "same_issue",
    "same_project",
    "same_tool",
    "same_event",
    "same_topic",
    "emotional_link",
    "in_thread",
    "same_person",
    "in_episode",
    "instance_of",
    "contradicts",
}
RELATION_TYPE_ALIASES = {
    # The public Y-axis docs call this edge "contradiction"; the core schema
    # historically stored it as "contradicts". Keep the DB canonical stable.
    "contradiction": "contradicts",
}


@dataclass(frozen=True)
class MemoryRecord:
    id: int | None
    title: str
    content: str
    thread: str = "other"
    category: str = "note"
    tags: list[str] = field(default_factory=list)
    fact_key: str | None = None
    active_fact: bool = True
    status: FactStatus = "current"
    risk_level: RiskLevel = "normal"
    urgency: Urgency = "normal"
    response_tendency: str = ""
    valence: float | None = None
    arousal: float | None = None
    tension: float | None = None
    confidence: float | None = None
    growth_delta: str = ""
    source: str = ""
    created_at: str | None = None
    updated_at: str | None = None
    hit_count: int = 0
    last_hit_at: str | None = None
    content_hash: str | None = None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "content": self.content,
            "thread": self.thread,
            "category": self.category,
            "tags": self.tags,
            "fact_key": self.fact_key,
            "active_fact": self.active_fact,
            "status": self.status,
            "risk_level": self.risk_level,
            "urgency": self.urgency,
            "response_tendency": self.response_tendency,
            "valence": self.valence,
            "arousal": self.arousal,
            "tension": self.tension,
            "confidence": self.confidence,
            "growth_delta": self.growth_delta,
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "hit_count": self.hit_count,
            "last_hit_at": self.last_hit_at,
        }


@dataclass(frozen=True)
class RelationRecord:
    id: int | None
    source_id: int
    target_id: int
    relation_type: RelationType
    strength: float = 1.0
    reason: str = ""
    created_at: str | None = None


@dataclass(frozen=True)
class EventRecord:
    id: int | None
    role: EventRole
    content: str
    channel: str = "default"
    metadata: dict[str, Any] = field(default_factory=dict)
    attachments: list[dict[str, Any]] = field(default_factory=list)
    created_at: str | None = None
    content_hash: str | None = None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "channel": self.channel,
            "metadata": self.metadata,
            "attachments": self.attachments,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class VectorRecord:
    id: int | None
    owner_type: VectorOwnerType
    owner_id: int
    provider: str
    model: str
    dimension: int
    input_type: str = "document"
    vector_hash: str | None = None
    content_hash: str | None = None
    created_at: str | None = None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "owner_type": self.owner_type,
            "owner_id": self.owner_id,
            "provider": self.provider,
            "model": self.model,
            "dimension": self.dimension,
            "input_type": self.input_type,
            "vector_hash": self.vector_hash,
            "content_hash": self.content_hash,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class MetabolismSuggestion:
    action: MetabolismAction
    severity: Literal["info", "warning", "critical"]
    reason: str
    memory_ids: list[int] = field(default_factory=list)
    fact_key: str | None = None
    thread: str | None = None
    category: str | None = None
    tag: str | None = None
    stage: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "severity": self.severity,
            "reason": self.reason,
            "memory_ids": self.memory_ids,
            "fact_key": self.fact_key,
            "thread": self.thread,
            "category": self.category,
            "tag": self.tag,
            "stage": self.stage,
        }


@dataclass(frozen=True)
class RecallHit:
    record: MemoryRecord
    score: float
    match_score: float
    relation_score: float = 0.0
    score_breakdown: dict[str, Any] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    related_from: list[int] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = self.record.to_public_dict()
        data["score"] = self.score
        data["match_score"] = self.match_score
        data["relation_score"] = self.relation_score
        data["score_breakdown"] = self.score_breakdown
        data["reasons"] = self.reasons
        data["related_from"] = self.related_from
        data["trace"] = self.trace
        return data


def normalize_relation_type(value: str) -> str:
    clean = str(value).strip()
    return RELATION_TYPE_ALIASES.get(clean, clean)


def validate_choice(name: str, value: str, allowed: set[str]) -> str:
    if value not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ValueError(f"{name} must be one of: {choices}")
    return value
