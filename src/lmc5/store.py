"""SQLite store for LMC-5."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterable
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import (
    EVENT_ROLES,
    FACT_STATUSES,
    RELATION_TYPES,
    SAFE_RELATION_TYPES,
    RISK_LEVELS,
    SYMMETRIC_RELATION_TYPES,
    URGENCY_LEVELS,
    VECTOR_OWNER_TYPES,
    EventRecord,
    MemoryRecord,
    RecallHit,
    RelationRecord,
    VectorRecord,
    normalize_relation_type,
    validate_choice,
)
from .redact import redact_obj
from .scoring import GateMode, metabolic_gate, priority_score
from .vector import cosine_similarity, vector_from_json, vector_hash, vector_to_json


def _connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _content_hash(title: str, content: str, fact_key: str | None) -> str:
    base = json.dumps(
        {"title": title.strip(), "content": content.strip(), "fact_key": fact_key or ""},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def _event_hash(role: str, content: str, channel: str, metadata_json: str) -> str:
    base = json.dumps(
        {
            "role": role.strip(),
            "content": content.strip(),
            "channel": channel.strip(),
            "metadata_json": metadata_json,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def _state_hash(state_key: str, title: str, content: str, provenance_json: str) -> str:
    base = json.dumps(
        {
            "state_key": state_key.strip(),
            "title": title.strip(),
            "content": content.strip(),
            "provenance_json": provenance_json,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def _query_hash(query: str) -> str:
    normalized = " ".join(str(query or "").split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _query_preview(query: str, max_chars: int = 160) -> str:
    normalized = " ".join(str(query or "").split())
    preview = normalized[:max_chars]
    redacted = redact_obj(preview)
    return redacted if isinstance(redacted, str) else str(redacted)


def _json_list(values: Iterable[str] | None) -> str:
    clean = [str(value).strip() for value in values or [] if str(value).strip()]
    return json.dumps(sorted(set(clean)), ensure_ascii=False)


def _sql_like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _normalize_entity(value: str) -> str:
    clean = " ".join(str(value or "").strip().split())
    if not clean:
        return ""
    return clean.lower() if clean.isascii() else clean


def _entity_label(value: str) -> str:
    return " ".join(str(value or "").strip().split())


def _add_entity(
    entities: dict[str, dict[str, Any]],
    *,
    label: str,
    kind: str,
    source: str,
    confidence: float = 1.0,
) -> None:
    clean_label = _entity_label(label)
    key = _normalize_entity(clean_label)
    if len(key) < 2 or key in _ENTITY_STOPWORDS:
        return
    if key.isascii() and key.replace(".", "").replace("_", "").replace("-", "").isdigit():
        return
    existing = entities.get(key)
    item = {
        "entity_key": key,
        "label": clean_label,
        "kind": kind,
        "source": source,
        "confidence": max(0.0, min(float(confidence), 1.0)),
    }
    if existing is None or item["confidence"] > existing["confidence"]:
        entities[key] = item


def _add_text_entities(
    entities: dict[str, dict[str, Any]],
    text: str,
    *,
    source: str,
    include_cjk_ngrams: bool = False,
) -> None:
    for match in _QUOTED_ENTITY_RE.finditer(text or ""):
        _add_entity(entities, label=match.group(1), kind="phrase", source=source, confidence=0.9)
    for token in _ASCII_ENTITY_RE.findall(text or ""):
        normalized = _normalize_entity(token)
        if normalized in _ENTITY_STOPWORDS:
            continue
        kind = "term"
        if any(char in token for char in "._:/-") or any(char.isdigit() for char in token):
            kind = "code"
        _add_entity(entities, label=token, kind=kind, source=source, confidence=0.75)
    for chunk in _CJK_ENTITY_RE.findall(text or ""):
        if chunk in _ENTITY_STOPWORDS:
            continue
        _add_entity(entities, label=chunk, kind="phrase", source=source, confidence=0.7)
        if include_cjk_ngrams and len(chunk) <= 8:
            for size in (2, 3):
                if len(chunk) < size:
                    continue
                for idx in range(0, len(chunk) - size + 1):
                    token = chunk[idx:idx + size]
                    if token not in _ENTITY_STOPWORDS:
                        _add_entity(
                            entities,
                            label=token,
                            kind="term",
                            source=source,
                            confidence=0.55,
                        )


def _extract_memory_entities(record: MemoryRecord) -> list[dict[str, Any]]:
    entities: dict[str, dict[str, Any]] = {}
    if record.fact_key:
        _add_entity(
            entities,
            label=record.fact_key,
            kind="fact_key",
            source="fact_key",
            confidence=1.0,
        )
        for part in re.split(r"[.:/_-]+", record.fact_key):
            if len(part) >= 3:
                _add_entity(entities, label=part, kind="fact_key_part", source="fact_key")
    for tag in record.tags:
        _add_entity(entities, label=tag, kind="tag", source="tag", confidence=0.95)
    if record.thread:
        _add_entity(entities, label=record.thread, kind="thread", source="thread", confidence=0.7)
    _add_text_entities(entities, record.title, source="title", include_cjk_ngrams=True)
    _add_text_entities(entities, record.content, source="content", include_cjk_ngrams=False)
    return sorted(entities.values(), key=lambda item: (item["source"], item["entity_key"]))


def _extract_query_entities(query: str) -> list[dict[str, Any]]:
    entities: dict[str, dict[str, Any]] = {}
    _add_text_entities(entities, query, source="query", include_cjk_ngrams=True)
    return sorted(entities.values(), key=lambda item: item["entity_key"])


def _query_temporal_intent(query: str) -> str:
    normalized = _normalize_entity(query)
    if not normalized:
        return ""
    if any(term in normalized for term in TEMPORAL_RECENT_TERMS):
        return "recent"
    return ""


def _is_query_control_term(term: str) -> bool:
    clean = _normalize_entity(term).strip(".,!?;:，。！？；：")
    return clean in TEMPORAL_RECENT_TERMS


def _parse_sqlite_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _temporal_boost(record: MemoryRecord, intent: str) -> float:
    if intent != "recent":
        return 0.0
    anchor = _parse_sqlite_time(record.updated_at) or _parse_sqlite_time(record.created_at)
    if anchor is None:
        return 0.0
    now = datetime.now(timezone.utc)
    age_days = max(0.0, (now - anchor.astimezone(timezone.utc)).total_seconds() / 86400)
    if age_days <= 1:
        return 0.25
    if age_days <= 7:
        return 0.18
    if age_days <= 30:
        return 0.10
    return 0.0


RELATION_TYPE_WEIGHTS = {
    "same_event": 1.0,
    "same_topic": 0.95,
    "same_project": 0.9,
    "same_issue": 0.85,
    "same_tool": 0.75,
    "in_thread": 0.8,
    "same_person": 0.78,
    "in_episode": 0.78,
    "instance_of": 0.72,
    "derived_from": 0.7,
    "supports": 0.6,
    "temporal_sequence": 0.55,
    "emotional_link": 0.5,
    "cause_effect": 0.45,
    "contradicts": 0.25,
}
RELATION_DEPTH_WEIGHTS = {1: 0.35, 2: 0.16}
RELATION_MIN_STRENGTH = {1: 0.4, 2: 0.7}
LIVE_MEMORY_SQL = "status = 'current' AND (fact_key IS NULL OR active_fact = 1)"
ENTITY_SOURCE_WEIGHTS = {
    "fact_key": 0.28,
    "tag": 0.22,
    "title": 0.18,
    "thread": 0.12,
    "content": 0.08,
}
ENTITY_MAX_BOOST = 0.5
TEMPORAL_RECENT_TERMS = {
    "current",
    "latest",
    "newest",
    "now",
    "recent",
    "today",
    "当前",
    "刚才",
    "今天",
    "最近",
    "最新",
    "现在",
}
_ASCII_ENTITY_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.:/-]{2,}")
_CJK_ENTITY_RE = re.compile(r"[\u4e00-\u9fff]{2,12}")
_QUOTED_ENTITY_RE = re.compile(r"[\"'`]([^\"'`]{2,80})[\"'`]")
_ENTITY_STOPWORDS = {
    "about",
    "after",
    "also",
    "before",
    "current",
    "default",
    "during",
    "false",
    "from",
    "have",
    "into",
    "latest",
    "local",
    "memory",
    "normal",
    "other",
    "review",
    "should",
    "status",
    "that",
    "their",
    "there",
    "this",
    "true",
    "with",
    "今天",
    "刚才",
    "现在",
    "什么",
    "怎么",
    "为什么",
    "是不是",
    "可以",
    "需要",
    "感觉",
    "觉得",
    "继续",
}


def _relation_score(relation_type: str, strength: float, *, depth: int) -> float:
    bounded_strength = max(0.0, min(float(strength or 1.0), 1.0))
    type_weight = RELATION_TYPE_WEIGHTS.get(relation_type, 0.5)
    depth_weight = RELATION_DEPTH_WEIGHTS.get(depth, 0.0)
    return bounded_strength * type_weight * depth_weight


def _recall_score_breakdown(
    record: MemoryRecord,
    *,
    match_score: float,
    relation_score: float,
    entity_score: float,
    temporal_score: float,
    reasons: list[str],
    gate: dict[str, Any],
) -> dict[str, Any]:
    priority = priority_score(record)
    base = priority + match_score + relation_score + entity_score + temporal_score
    m_factor = float(gate.get("factor", 1.0) or 0.0)
    final = base * m_factor
    breakdown: dict[str, Any] = {
        "priority": round(priority, 3),
        "relation": round(relation_score, 3),
        "entity_boost": round(entity_score, 3),
        "temporal_boost": round(temporal_score, 3),
        "m_gate_factor": round(m_factor, 3),
        "m_gate_bucket": str(gate.get("bucket", "")),
        "m_gate_reason": str(gate.get("reason", "")),
        "final": round(final, 3),
    }
    if "fts" in reasons:
        breakdown["keyword"] = round(match_score, 3)
    elif "like" in reasons:
        breakdown["literal"] = round(match_score, 3)
    elif "recent" in reasons:
        breakdown["recency"] = round(match_score, 3)
    elif match_score:
        breakdown["match"] = round(match_score, 3)
    return breakdown


def _validate_optional_range(name: str, value: float | None, minimum: float, maximum: float) -> None:
    if value is None:
        return
    if not minimum <= float(value) <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")


def _row_to_memory(row: sqlite3.Row) -> MemoryRecord:
    return MemoryRecord(
        id=row["id"],
        title=row["title"],
        content=row["content"],
        thread=row["thread"],
        category=row["category"],
        tags=json.loads(row["tags_json"] or "[]"),
        fact_key=row["fact_key"],
        active_fact=bool(row["active_fact"]),
        status=row["status"],
        risk_level=row["risk_level"],
        urgency=row["urgency"],
        response_tendency=row["response_tendency"] or "",
        valence=row["valence"],
        arousal=row["arousal"],
        tension=row["tension"],
        confidence=row["confidence"],
        growth_delta=row["growth_delta"] or "",
        source=row["source"] or "",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        hit_count=row["hit_count"],
        last_hit_at=row["last_hit_at"] if "last_hit_at" in row.keys() else None,
        content_hash=row["content_hash"],
    )


def _row_to_relation(row: sqlite3.Row) -> RelationRecord:
    return RelationRecord(
        id=row["id"],
        source_id=row["source_id"],
        target_id=row["target_id"],
        relation_type=row["relation_type"],
        strength=row["strength"],
        reason=row["reason"],
        created_at=row["created_at"],
    )


def _row_to_event(row: sqlite3.Row) -> EventRecord:
    return EventRecord(
        id=row["id"],
        role=row["role"],
        content=row["content"],
        channel=row["channel"],
        metadata=json.loads(row["metadata_json"] or "{}"),
        attachments=json.loads(row["attachments_json"] or "[]"),
        created_at=row["created_at"],
        content_hash=row["content_hash"],
    )


def _row_to_vector(row: sqlite3.Row) -> VectorRecord:
    return VectorRecord(
        id=row["id"],
        owner_type=row["owner_type"],
        owner_id=row["owner_id"],
        provider=row["provider"],
        model=row["model"],
        dimension=row["dimension"],
        input_type=row["input_type"],
        vector_hash=row["vector_hash"],
        content_hash=row["content_hash"],
        created_at=row["created_at"],
    )


class MemoryStore(AbstractContextManager["MemoryStore"]):
    """Small SQLite-backed LMC-5 memory store."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.conn = _connect(self.path)

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    def init(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                thread TEXT NOT NULL DEFAULT 'other',
                category TEXT NOT NULL DEFAULT 'note',
                tags_json TEXT NOT NULL DEFAULT '[]',
                fact_key TEXT,
                active_fact INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'current',
                risk_level TEXT NOT NULL DEFAULT 'normal',
                urgency TEXT NOT NULL DEFAULT 'normal',
                response_tendency TEXT NOT NULL DEFAULT '',
                valence REAL,
                arousal REAL,
                tension REAL,
                confidence REAL,
                growth_delta TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                hit_count INTEGER NOT NULL DEFAULT 0,
                last_hit_at TEXT,
                content_hash TEXT NOT NULL UNIQUE
            );

            CREATE INDEX IF NOT EXISTS idx_memories_fact_key
              ON memories(fact_key, active_fact, status);
            CREATE INDEX IF NOT EXISTS idx_memories_thread
              ON memories(thread, category);
            CREATE INDEX IF NOT EXISTS idx_memories_status
              ON memories(status);

            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                title,
                content,
                thread,
                category,
                tags,
                fact_key
            );

            CREATE TABLE IF NOT EXISTS relations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
                target_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
                relation_type TEXT NOT NULL,
                strength REAL NOT NULL DEFAULT 1.0,
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                UNIQUE(source_id, target_id, relation_type)
            );

            CREATE TABLE IF NOT EXISTS memory_entities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
                entity_key TEXT NOT NULL,
                label TEXT NOT NULL,
                kind TEXT NOT NULL,
                source TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 1.0,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                UNIQUE(memory_id, entity_key, source)
            );

            CREATE INDEX IF NOT EXISTS idx_memory_entities_key
              ON memory_entities(entity_key, confidence);
            CREATE INDEX IF NOT EXISTS idx_memory_entities_memory
              ON memory_entities(memory_id);

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                channel TEXT NOT NULL DEFAULT 'default',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                attachments_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                content_hash TEXT NOT NULL UNIQUE
            );

            CREATE INDEX IF NOT EXISTS idx_events_channel_created
              ON events(channel, created_at);
            CREATE INDEX IF NOT EXISTS idx_events_role
              ON events(role);

            CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
                content,
                channel,
                role,
                metadata
            );

            CREATE TABLE IF NOT EXISTS vectors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_type TEXT NOT NULL,
                owner_id INTEGER NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                dimension INTEGER NOT NULL,
                input_type TEXT NOT NULL DEFAULT 'document',
                vector_json TEXT NOT NULL,
                vector_hash TEXT NOT NULL,
                content_hash TEXT,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                UNIQUE(owner_type, owner_id, provider, model, dimension, input_type)
            );

            CREATE INDEX IF NOT EXISTS idx_vectors_lookup
              ON vectors(provider, model, dimension, input_type, owner_type);

            CREATE TABLE IF NOT EXISTS event_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel TEXT NOT NULL DEFAULT 'default',
                start_event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                end_event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                event_count INTEGER NOT NULL,
                summary TEXT NOT NULL,
                keywords_json TEXT NOT NULL DEFAULT '[]',
                content_hash TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            );

            CREATE INDEX IF NOT EXISTS idx_event_chunks_channel_start
              ON event_chunks(channel, start_event_id);

            CREATE TABLE IF NOT EXISTS chunk_events (
                chunk_id INTEGER NOT NULL REFERENCES event_chunks(id) ON DELETE CASCADE,
                event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                PRIMARY KEY(chunk_id, event_id)
            );

            CREATE INDEX IF NOT EXISTS idx_chunk_events_event
              ON chunk_events(event_id);

            CREATE TABLE IF NOT EXISTS consolidation_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                channel TEXT,
                window_size INTEGER NOT NULL,
                chunks_created INTEGER NOT NULL DEFAULT 0,
                observations_created INTEGER NOT NULL DEFAULT 0,
                notes_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS z_conflict_audits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                left_memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
                right_memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
                fact_key TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                verdict TEXT NOT NULL DEFAULT 'pending',
                confidence REAL,
                reason TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                reviewer TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                CHECK(left_memory_id < right_memory_id),
                UNIQUE(left_memory_id, right_memory_id)
            );

            CREATE INDEX IF NOT EXISTS idx_z_conflict_audits_status
              ON z_conflict_audits(status, verdict);

            CREATE TABLE IF NOT EXISTS recall_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_hash TEXT NOT NULL,
                query_preview TEXT NOT NULL DEFAULT '',
                limit_requested INTEGER NOT NULL,
                selected_count INTEGER NOT NULL DEFAULT 0,
                expand_relations INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            );

            CREATE INDEX IF NOT EXISTS idx_recall_runs_created
              ON recall_runs(created_at);
            CREATE INDEX IF NOT EXISTS idx_recall_runs_query
              ON recall_runs(query_hash, created_at);

            CREATE TABLE IF NOT EXISTS recall_trace_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL REFERENCES recall_runs(id) ON DELETE CASCADE,
                memory_id INTEGER REFERENCES memories(id) ON DELETE SET NULL,
                rank INTEGER NOT NULL,
                injected INTEGER NOT NULL DEFAULT 1,
                score REAL NOT NULL,
                score_breakdown_json TEXT NOT NULL DEFAULT '{}',
                reasons_json TEXT NOT NULL DEFAULT '[]',
                related_from_json TEXT NOT NULL DEFAULT '[]',
                trace_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            );

            CREATE INDEX IF NOT EXISTS idx_recall_trace_items_run
              ON recall_trace_items(run_id, rank);
            CREATE INDEX IF NOT EXISTS idx_recall_trace_items_memory
              ON recall_trace_items(memory_id, created_at);

            CREATE TABLE IF NOT EXISTS current_state_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL DEFAULT 'manual',
                ttl_hours INTEGER NOT NULL,
                items_written INTEGER NOT NULL DEFAULT 0,
                notes_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            );

            CREATE TABLE IF NOT EXISTS current_state_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL REFERENCES current_state_runs(id) ON DELETE CASCADE,
                state_key TEXT NOT NULL UNIQUE,
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                provenance_json TEXT NOT NULL DEFAULT '{}',
                confidence REAL NOT NULL DEFAULT 1.0,
                expires_at TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            );

            CREATE INDEX IF NOT EXISTS idx_current_state_items_category
              ON current_state_items(category, expires_at);
            CREATE INDEX IF NOT EXISTS idx_current_state_items_expires
              ON current_state_items(expires_at);
            """
        )
        self._ensure_memory_columns()
        self.rebuild_index()
        self.rebuild_entity_index()
        self.conn.commit()

    def _ensure_memory_columns(self) -> None:
        columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(memories)").fetchall()
        }
        if "last_hit_at" not in columns:
            self.conn.execute("ALTER TABLE memories ADD COLUMN last_hit_at TEXT")

    def rebuild_index(self) -> None:
        self.conn.execute("DELETE FROM memories_fts")
        self.conn.execute(
            """
            INSERT INTO memories_fts(rowid, title, content, thread, category, tags, fact_key)
            SELECT id, title, content, thread, category, tags_json, COALESCE(fact_key, '')
              FROM memories
            """
        )
        self.conn.execute("DELETE FROM events_fts")
        self.conn.execute(
            """
            INSERT INTO events_fts(rowid, content, channel, role, metadata)
            SELECT id, content, channel, role, metadata_json
              FROM events
            """
        )

    def _index_memory_entities(self, record: MemoryRecord) -> None:
        if record.id is None:
            return
        self.conn.execute("DELETE FROM memory_entities WHERE memory_id = ?", (record.id,))
        self.conn.executemany(
            """
            INSERT OR IGNORE INTO memory_entities (
                memory_id, entity_key, label, kind, source, confidence
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    record.id,
                    item["entity_key"],
                    item["label"],
                    item["kind"],
                    item["source"],
                    item["confidence"],
                )
                for item in _extract_memory_entities(record)
            ],
        )

    def rebuild_entity_index(self) -> None:
        self.conn.execute("DELETE FROM memory_entities")
        rows = self.conn.execute("SELECT * FROM memories ORDER BY id").fetchall()
        for row in rows:
            self._index_memory_entities(_row_to_memory(row))

    def add_memory(
        self,
        *,
        title: str,
        content: str,
        thread: str = "other",
        category: str = "note",
        tags: Iterable[str] | None = None,
        fact_key: str | None = None,
        active_fact: bool = True,
        status: str = "current",
        risk_level: str = "normal",
        urgency: str = "normal",
        response_tendency: str = "",
        valence: float | None = None,
        arousal: float | None = None,
        tension: float | None = None,
        confidence: float | None = None,
        growth_delta: str = "",
        source: str = "",
    ) -> tuple[MemoryRecord, bool]:
        validate_choice("status", status, FACT_STATUSES)
        validate_choice("risk_level", risk_level, RISK_LEVELS)
        validate_choice("urgency", urgency, URGENCY_LEVELS)
        _validate_optional_range("valence", valence, -1.0, 1.0)
        _validate_optional_range("arousal", arousal, 0.0, 1.0)
        _validate_optional_range("tension", tension, 0.0, 1.0)
        _validate_optional_range("confidence", confidence, 0.0, 1.0)
        if not title.strip():
            raise ValueError("title is required")
        if not content.strip():
            raise ValueError("content is required")

        clean_title = title.strip()
        clean_content = content.strip()
        clean_thread = thread.strip() or "other"
        clean_category = category.strip() or "note"
        clean_tags = _json_list(tags)
        clean_fact_key = fact_key.strip() if fact_key else None
        digest = _content_hash(clean_title, clean_content, clean_fact_key)

        existing = self.conn.execute(
            "SELECT * FROM memories WHERE content_hash = ?",
            (digest,),
        ).fetchone()
        if existing:
            return _row_to_memory(existing), False

        if clean_fact_key and active_fact and status == "current":
            self.conn.execute(
                """
                UPDATE memories
                   SET active_fact = 0,
                       status = 'superseded',
                       updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                 WHERE fact_key = ?
                   AND active_fact = 1
                   AND status = 'current'
                """,
                (clean_fact_key,),
            )

        cur = self.conn.execute(
            """
            INSERT INTO memories (
                title, content, thread, category, tags_json, fact_key,
                active_fact, status, risk_level, urgency, response_tendency,
                valence, arousal, tension, confidence, growth_delta, source,
                content_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                clean_title,
                clean_content,
                clean_thread,
                clean_category,
                clean_tags,
                clean_fact_key,
                1 if active_fact else 0,
                status,
                risk_level,
                urgency,
                response_tendency.strip(),
                valence,
                arousal,
                tension,
                confidence,
                growth_delta.strip(),
                source.strip(),
                digest,
            ),
        )
        memory_id = int(cur.lastrowid)
        self.conn.execute(
            """
            INSERT INTO memories_fts(rowid, title, content, thread, category, tags, fact_key)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory_id,
                clean_title,
                clean_content,
                clean_thread,
                clean_category,
                clean_tags,
                clean_fact_key or "",
            ),
        )
        record = self.get_memory(memory_id)
        self._index_memory_entities(record)
        self.conn.commit()
        return record, True

    def get_memory(self, memory_id: int) -> MemoryRecord:
        row = self.conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        if row is None:
            raise KeyError(f"memory not found: {memory_id}")
        return _row_to_memory(row)

    def add_relation(
        self,
        source_id: int,
        target_id: int,
        relation_type: str,
        *,
        strength: float = 1.0,
        reason: str = "",
    ) -> RelationRecord:
        relation_type = normalize_relation_type(relation_type)
        validate_choice("relation_type", relation_type, RELATION_TYPES)
        if source_id == target_id:
            raise ValueError("relation source and target must differ")
        _validate_optional_range("strength", strength, 0.0, 1.0)
        if relation_type in SYMMETRIC_RELATION_TYPES and source_id > target_id:
            source_id, target_id = target_id, source_id
        cur = self.conn.execute(
            """
            INSERT OR IGNORE INTO relations (source_id, target_id, relation_type, strength, reason)
            VALUES (?, ?, ?, ?, ?)
            """,
            (source_id, target_id, relation_type, strength, reason.strip()),
        )
        self.conn.commit()
        relation_id = cur.lastrowid
        if relation_id == 0:
            row = self.conn.execute(
                """
                SELECT * FROM relations
                 WHERE source_id = ? AND target_id = ? AND relation_type = ?
                """,
                (source_id, target_id, relation_type),
            ).fetchone()
        else:
            row = self.conn.execute("SELECT * FROM relations WHERE id = ?", (relation_id,)).fetchone()
        return _row_to_relation(row)

    def list_relations(self, memory_id: int | None = None) -> list[RelationRecord]:
        if memory_id is None:
            rows = self.conn.execute("SELECT * FROM relations ORDER BY id").fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT * FROM relations
                 WHERE source_id = ? OR target_id = ?
                 ORDER BY strength DESC, id
                """,
                (memory_id, memory_id),
            ).fetchall()
        return [_row_to_relation(row) for row in rows]

    def list_entities(
        self,
        memory_id: int | None = None,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if memory_id is None:
            rows = self.conn.execute(
                """
                SELECT *
                  FROM memory_entities
                 ORDER BY entity_key, memory_id, source
                 LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT *
                  FROM memory_entities
                 WHERE memory_id = ?
                 ORDER BY entity_key, source
                 LIMIT ?
                """,
                (int(memory_id), int(limit)),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "memory_id": row["memory_id"],
                "entity_key": row["entity_key"],
                "label": row["label"],
                "kind": row["kind"],
                "source": row["source"],
                "confidence": row["confidence"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def list_recent(self, limit: int = 20) -> list[MemoryRecord]:
        rows = self.conn.execute(
            "SELECT * FROM memories ORDER BY created_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_row_to_memory(row) for row in rows]

    def log_event(
        self,
        *,
        role: str,
        content: str,
        channel: str = "default",
        metadata: dict[str, Any] | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> tuple[EventRecord, bool]:
        validate_choice("role", role, EVENT_ROLES)
        if not content.strip():
            raise ValueError("content is required")

        clean_channel = channel.strip() or "default"
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True)
        attachments_json = json.dumps(attachments or [], ensure_ascii=False, sort_keys=True)
        digest = _event_hash(role, content, clean_channel, metadata_json)

        existing = self.conn.execute(
            "SELECT * FROM events WHERE content_hash = ?",
            (digest,),
        ).fetchone()
        if existing:
            return _row_to_event(existing), False

        cur = self.conn.execute(
            """
            INSERT INTO events (role, content, channel, metadata_json, attachments_json, content_hash)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (role, content.strip(), clean_channel, metadata_json, attachments_json, digest),
        )
        event_id = int(cur.lastrowid)
        self.conn.execute(
            """
            INSERT INTO events_fts(rowid, content, channel, role, metadata)
            VALUES (?, ?, ?, ?, ?)
            """,
            (event_id, content.strip(), clean_channel, role, metadata_json),
        )
        self.conn.commit()
        return self.get_event(event_id), True

    def get_event(self, event_id: int) -> EventRecord:
        row = self.conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        if row is None:
            raise KeyError(f"event not found: {event_id}")
        return _row_to_event(row)

    def list_events(self, limit: int = 20, *, channel: str | None = None) -> list[EventRecord]:
        if channel:
            rows = self.conn.execute(
                "SELECT * FROM events WHERE channel = ? ORDER BY created_at DESC, id DESC LIMIT ?",
                (channel, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM events ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_event(row) for row in rows]

    def search_events(
        self,
        query: str,
        limit: int = 10,
        *,
        channel: str | None = None,
        redact: bool = False,
    ) -> list[dict[str, Any]]:
        terms = [term.strip() for term in query.split() if term.strip()]
        params: list[Any] = []
        rows: list[sqlite3.Row] = []

        if terms:
            fts_query = " ".join(f'"{term.replace(chr(34), chr(34) + chr(34))}"' for term in terms)
            channel_clause = "AND e.channel = ?" if channel else ""
            params = [fts_query]
            if channel:
                params.append(channel)
            params.append(limit * 3)
            try:
                rows = self.conn.execute(
                    f"""
                    SELECT e.*, bm25(events_fts) AS rank
                      FROM events_fts
                      JOIN events e ON e.id = events_fts.rowid
                     WHERE events_fts MATCH ?
                       {channel_clause}
                     ORDER BY rank
                     LIMIT ?
                    """,
                    params,
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []

        candidates: dict[int, tuple[EventRecord, float, list[str]]] = {}
        for row in rows:
            event = _row_to_event(row)
            match_score = max(0.0, 1.5 - abs(float(row["rank"] or 0.0)))
            candidates[int(event.id)] = (event, round(match_score, 3), ["event_fts"])

        if len(candidates) < limit:
            if terms:
                clauses = []
                like_params: list[Any] = []
                for term in terms:
                    pattern = f"%{_sql_like_escape(term)}%"
                    clauses.append(
                        "(content LIKE ? ESCAPE '\\' OR metadata_json LIKE ? ESCAPE '\\')"
                    )
                    like_params.extend([pattern, pattern])
                channel_clause = "AND channel = ?" if channel else ""
                if channel:
                    like_params.append(channel)
                like_params.append(limit * 3)
                rows = self.conn.execute(
                    f"""
                    SELECT * FROM events
                     WHERE {' AND '.join(clauses)}
                       {channel_clause}
                     ORDER BY created_at DESC, id DESC
                     LIMIT ?
                    """,
                    like_params,
                ).fetchall()
            else:
                if channel:
                    rows = self.conn.execute(
                        "SELECT * FROM events WHERE channel = ? ORDER BY created_at DESC, id DESC LIMIT ?",
                        (channel, limit * 3),
                    ).fetchall()
                else:
                    rows = self.conn.execute(
                        "SELECT * FROM events ORDER BY created_at DESC, id DESC LIMIT ?",
                        (limit * 3,),
                    ).fetchall()
            for row in rows:
                event = _row_to_event(row)
                if int(event.id) not in candidates:
                    candidates[int(event.id)] = (event, 0.5 if terms else 0.0, ["event_like"])

        output = []
        for event, match_score, reasons in candidates.values():
            item = event.to_public_dict()
            item["score"] = match_score
            item["reasons"] = reasons
            output.append(item)
        output.sort(key=lambda item: (item["score"], item["created_at"] or ""), reverse=True)
        selected = output[:limit]
        if redact:
            return [redact_obj(item) for item in selected]
        return selected

    def _insert_current_state_item(
        self,
        *,
        run_id: int,
        state_key: str,
        category: str,
        title: str,
        content: str,
        provenance: dict[str, Any],
        confidence: float,
        ttl_hours: int,
    ) -> None:
        provenance_json = json.dumps(provenance, ensure_ascii=False, sort_keys=True)
        self.conn.execute(
            """
            INSERT INTO current_state_items (
                run_id, state_key, category, title, content, provenance_json,
                confidence, expires_at, content_hash
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?,
                strftime('%Y-%m-%dT%H:%M:%fZ', 'now', ?),
                ?
            )
            """,
            (
                run_id,
                state_key,
                category,
                title.strip(),
                content.strip(),
                provenance_json,
                max(0.0, min(float(confidence), 1.0)),
                f"+{ttl_hours} hours",
                _state_hash(state_key, title, content, provenance_json),
            ),
        )

    def refresh_current_state(
        self,
        *,
        ttl_hours: int = 24,
        fact_limit: int = 20,
        thread_limit: int = 8,
        event_limit: int = 6,
        audit_limit: int = 8,
        source: str = "manual",
    ) -> dict[str, Any]:
        """Rebuild the materialized current-state pack from durable tables."""

        if ttl_hours <= 0:
            raise ValueError("ttl_hours must be positive")
        fact_limit = max(0, int(fact_limit))
        thread_limit = max(0, int(thread_limit))
        event_limit = max(0, int(event_limit))
        audit_limit = max(0, int(audit_limit))

        cur = self.conn.execute(
            """
            INSERT INTO current_state_runs (source, ttl_hours, notes_json)
            VALUES (?, ?, ?)
            """,
            (
                source.strip() or "manual",
                int(ttl_hours),
                json.dumps(
                    {
                        "fact_limit": fact_limit,
                        "thread_limit": thread_limit,
                        "event_limit": event_limit,
                        "audit_limit": audit_limit,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            ),
        )
        run_id = int(cur.lastrowid)
        self.conn.execute("DELETE FROM current_state_items")

        category_counts: dict[str, int] = {}

        def add_item(**kwargs: Any) -> None:
            self._insert_current_state_item(run_id=run_id, ttl_hours=ttl_hours, **kwargs)
            category = str(kwargs["category"])
            category_counts[category] = category_counts.get(category, 0) + 1

        if fact_limit:
            rows = self.conn.execute(
                """
                SELECT *
                  FROM memories
                 WHERE fact_key IS NOT NULL
                   AND active_fact = 1
                   AND status = 'current'
                 ORDER BY updated_at DESC, id DESC
                 LIMIT ?
                """,
                (fact_limit,),
            ).fetchall()
            for row in rows:
                record = _row_to_memory(row)
                fact_key = record.fact_key or f"memory:{record.id}"
                add_item(
                    state_key=f"fact:{fact_key}",
                    category="current_fact",
                    title=record.title,
                    content=record.content,
                    provenance={
                        "source_table": "memories",
                        "memory_ids": [record.id],
                        "fact_key": record.fact_key,
                    },
                    confidence=record.confidence if record.confidence is not None else 1.0,
                )

        if thread_limit:
            thread_rows = self.conn.execute(
                f"""
                SELECT thread,
                       count(*) AS memory_count,
                       max(updated_at) AS latest_at
                  FROM memories
                 WHERE {LIVE_MEMORY_SQL}
                 GROUP BY thread
                 ORDER BY latest_at DESC, memory_count DESC, thread
                 LIMIT ?
                """,
                (thread_limit,),
            ).fetchall()
            for row in thread_rows:
                thread = str(row["thread"] or "other")
                latest_rows = self.conn.execute(
                    f"""
                    SELECT id, title
                      FROM memories
                     WHERE {LIVE_MEMORY_SQL}
                       AND thread = ?
                     ORDER BY updated_at DESC, id DESC
                     LIMIT 5
                    """,
                    (thread,),
                ).fetchall()
                memory_ids = [int(item["id"]) for item in latest_rows]
                latest_titles = [str(item["title"]) for item in latest_rows[:3]]
                add_item(
                    state_key=f"thread:{thread}",
                    category="active_thread",
                    title=f"Active thread: {thread}",
                    content=(
                        f"{int(row['memory_count'])} current memories. "
                        f"Latest: {'; '.join(latest_titles)}"
                    ),
                    provenance={
                        "source_table": "memories",
                        "memory_ids": memory_ids,
                        "thread": thread,
                    },
                    confidence=0.75,
                )

        if audit_limit:
            rows = self.conn.execute(
                """
                SELECT z.*,
                       left_m.title AS left_title,
                       right_m.title AS right_title
                  FROM z_conflict_audits z
                  JOIN memories left_m ON left_m.id = z.left_memory_id
                  JOIN memories right_m ON right_m.id = z.right_memory_id
                 WHERE z.status = 'pending'
                   AND z.verdict = 'pending'
                 ORDER BY z.created_at DESC, z.id DESC
                 LIMIT ?
                """,
                (audit_limit,),
            ).fetchall()
            for row in rows:
                audit_id = int(row["id"])
                add_item(
                    state_key=f"z_audit:{audit_id}",
                    category="pending_z_audit",
                    title=f"Pending Z audit: {row['left_title']} <-> {row['right_title']}",
                    content=row["reason"] or "Pending conflict review.",
                    provenance={
                        "source_table": "z_conflict_audits",
                        "audit_ids": [audit_id],
                        "memory_ids": [int(row["left_memory_id"]), int(row["right_memory_id"])],
                        "fact_key": row["fact_key"],
                    },
                    confidence=row["confidence"] if row["confidence"] is not None else 0.6,
                )

        if event_limit:
            rows = self.conn.execute(
                """
                SELECT *
                  FROM events
                 ORDER BY created_at DESC, id DESC
                 LIMIT ?
                """,
                (event_limit,),
            ).fetchall()
            for row in rows:
                event = _row_to_event(row)
                add_item(
                    state_key=f"recent_event:{event.id}",
                    category="recent_event",
                    title=f"Recent {event.role} event in {event.channel}",
                    content=event.content[:400],
                    provenance={
                        "source_table": "events",
                        "event_ids": [event.id],
                        "channel": event.channel,
                        "role": event.role,
                    },
                    confidence=0.65,
                )

        total = sum(category_counts.values())
        self.conn.execute(
            "UPDATE current_state_runs SET items_written = ? WHERE id = ?",
            (total, run_id),
        )
        self.conn.commit()
        return {
            "run_id": run_id,
            "items_written": total,
            "category_counts": category_counts,
            "ttl_hours": int(ttl_hours),
        }

    def list_current_state(
        self,
        limit: int = 20,
        *,
        category: str | None = None,
        include_expired: bool = False,
        redact: bool = False,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[object] = []
        if category:
            clauses.append("category = ?")
            params.append(category)
        if not include_expired:
            clauses.append("expires_at > strftime('%Y-%m-%dT%H:%M:%fZ', 'now')")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(
            f"""
            SELECT *
              FROM current_state_items
              {where}
             ORDER BY
               CASE category
                 WHEN 'current_fact' THEN 0
                 WHEN 'pending_z_audit' THEN 1
                 WHEN 'active_thread' THEN 2
                 WHEN 'recent_event' THEN 3
                 ELSE 9
               END,
               updated_at DESC,
               id DESC
             LIMIT ?
            """,
            (*params, int(limit)),
        ).fetchall()
        items = [
            {
                "id": row["id"],
                "run_id": row["run_id"],
                "state_key": row["state_key"],
                "category": row["category"],
                "title": row["title"],
                "content": row["content"],
                "provenance": json.loads(row["provenance_json"] or "{}"),
                "confidence": row["confidence"],
                "expires_at": row["expires_at"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]
        if redact:
            return [redact_obj(item) for item in items]
        return items

    def surface(
        self,
        query: str,
        limit: int = 8,
        *,
        event_limit: int | None = None,
        memory_limit: int | None = None,
        state_limit: int = 4,
        include_state: bool = True,
        redact: bool = True,
    ) -> dict[str, Any]:
        memory_limit = memory_limit or max(1, limit // 2)
        event_limit = event_limit or max(1, limit - memory_limit)
        result: dict[str, Any] = {"query": query}
        if include_state:
            result["state"] = self.list_current_state(limit=state_limit, redact=redact)
        memory_hits = self.recall_hits(query, limit=memory_limit, gate_mode="surface")
        memories = [hit.to_dict() for hit in memory_hits]
        result["memories"] = [redact_obj(item) for item in memories] if redact else memories
        result["events"] = self.search_events(query, limit=event_limit, redact=redact)
        return result

    def stats(self) -> dict[str, Any]:
        """Return read-only database health and coverage statistics."""

        def count(table: str) -> int:
            return int(self.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])

        status_rows = self.conn.execute(
            """
            SELECT status, count(*) AS count
              FROM memories
             GROUP BY status
             ORDER BY count DESC, status
            """
        ).fetchall()
        thread_rows = self.conn.execute(
            """
            SELECT thread, count(*) AS count
              FROM memories
             GROUP BY thread
             ORDER BY count DESC, thread
             LIMIT 20
            """
        ).fetchall()
        category_rows = self.conn.execute(
            """
            SELECT category, count(*) AS count
              FROM memories
             GROUP BY category
             ORDER BY count DESC, category
             LIMIT 20
            """
        ).fetchall()
        role_rows = self.conn.execute(
            """
            SELECT role, count(*) AS count
              FROM events
             GROUP BY role
             ORDER BY count DESC, role
            """
        ).fetchall()
        channel_rows = self.conn.execute(
            """
            SELECT channel, count(*) AS count
              FROM events
             GROUP BY channel
             ORDER BY count DESC, channel
             LIMIT 20
            """
        ).fetchall()
        vector_owner_rows = self.conn.execute(
            """
            SELECT owner_type, count(*) AS count
              FROM vectors
             GROUP BY owner_type
             ORDER BY owner_type
            """
        ).fetchall()

        memory_count = count("memories")
        event_count = count("events")
        memory_vector_count = int(
            self.conn.execute(
                "SELECT count(DISTINCT owner_id) FROM vectors WHERE owner_type = 'memory'"
            ).fetchone()[0]
        )
        event_vector_count = int(
            self.conn.execute(
                "SELECT count(DISTINCT owner_id) FROM vectors WHERE owner_type = 'event'"
            ).fetchone()[0]
        )

        return {
            "memory_count": memory_count,
            "relation_count": count("relations"),
            "entity_count": count("memory_entities"),
            "event_count": event_count,
            "vector_count": count("vectors"),
            "event_chunk_count": count("event_chunks"),
            "consolidation_run_count": count("consolidation_runs"),
            "current_state_count": count("current_state_items"),
            "current_state_live_count": int(
                self.conn.execute(
                    """
                    SELECT count(*)
                      FROM current_state_items
                     WHERE expires_at > strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    """
                ).fetchone()[0]
            ),
            "z_conflict_audit_count": count("z_conflict_audits"),
            "z_conflict_pending_count": int(
                self.conn.execute(
                    """
                    SELECT count(*)
                      FROM z_conflict_audits
                     WHERE status = 'pending'
                       AND verdict = 'pending'
                    """
                ).fetchone()[0]
            ),
            "current_fact_count": int(
                self.conn.execute(
                    """
                    SELECT count(*)
                      FROM memories
                     WHERE fact_key IS NOT NULL
                       AND active_fact = 1
                       AND status = 'current'
                    """
                ).fetchone()[0]
            ),
            "status_counts": {str(row["status"]): int(row["count"]) for row in status_rows},
            "top_threads": {str(row["thread"]): int(row["count"]) for row in thread_rows},
            "top_categories": {
                str(row["category"]): int(row["count"]) for row in category_rows
            },
            "event_role_counts": {str(row["role"]): int(row["count"]) for row in role_rows},
            "top_event_channels": {
                str(row["channel"]): int(row["count"]) for row in channel_rows
            },
            "vector_owner_counts": {
                str(row["owner_type"]): int(row["count"]) for row in vector_owner_rows
            },
            "memory_vector_coverage": {
                "indexed": memory_vector_count,
                "total": memory_count,
                "ratio": round(memory_vector_count / memory_count, 4)
                if memory_count
                else 0.0,
            },
            "event_vector_coverage": {
                "indexed": event_vector_count,
                "total": event_count,
                "ratio": round(event_vector_count / event_count, 4) if event_count else 0.0,
            },
        }

    def upsert_vector(
        self,
        *,
        owner_type: str,
        owner_id: int,
        vector: list[float],
        provider: str,
        model: str,
        input_type: str = "document",
        content_hash: str | None = None,
    ) -> VectorRecord:
        validate_choice("owner_type", owner_type, VECTOR_OWNER_TYPES)
        if input_type not in {"query", "document", "unspecified"}:
            raise ValueError("input_type must be one of: query, document, unspecified")
        if owner_type == "memory":
            self.get_memory(owner_id)
        else:
            self.get_event(owner_id)

        vector_json = vector_to_json(vector)
        parsed = vector_from_json(vector_json)
        digest = vector_hash(parsed)
        dimension = len(parsed)
        self.conn.execute(
            """
            INSERT INTO vectors (
                owner_type, owner_id, provider, model, dimension, input_type,
                vector_json, vector_hash, content_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(owner_type, owner_id, provider, model, dimension, input_type)
            DO UPDATE SET
                vector_json = excluded.vector_json,
                vector_hash = excluded.vector_hash,
                content_hash = excluded.content_hash,
                created_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (
                owner_type,
                owner_id,
                provider.strip(),
                model.strip(),
                dimension,
                input_type,
                vector_json,
                digest,
                content_hash,
            ),
        )
        self.conn.commit()
        row = self.conn.execute(
            """
            SELECT * FROM vectors
             WHERE owner_type = ?
               AND owner_id = ?
               AND provider = ?
               AND model = ?
               AND dimension = ?
               AND input_type = ?
            """,
            (owner_type, owner_id, provider.strip(), model.strip(), dimension, input_type),
        ).fetchone()
        return _row_to_vector(row)

    def list_vectors(self, *, owner_type: str | None = None, limit: int = 50) -> list[VectorRecord]:
        if owner_type:
            validate_choice("owner_type", owner_type, VECTOR_OWNER_TYPES)
            rows = self.conn.execute(
                "SELECT * FROM vectors WHERE owner_type = ? ORDER BY created_at DESC LIMIT ?",
                (owner_type, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM vectors ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_vector(row) for row in rows]

    def search_vectors(
        self,
        *,
        query_vector: list[float],
        provider: str,
        model: str,
        owner_type: str | None = None,
        input_type: str = "document",
        limit: int = 10,
        redact: bool = True,
    ) -> list[dict[str, Any]]:
        query = vector_from_json(vector_to_json(query_vector))
        dimension = len(query)
        clauses = [
            "provider = ?",
            "model = ?",
            "dimension = ?",
            "input_type = ?",
        ]
        params: list[object] = [provider, model, dimension, input_type]
        if owner_type:
            validate_choice("owner_type", owner_type, VECTOR_OWNER_TYPES)
            clauses.append("owner_type = ?")
            params.append(owner_type)

        rows = self.conn.execute(
            f"SELECT * FROM vectors WHERE {' AND '.join(clauses)}",
            params,
        ).fetchall()
        hits = []
        for row in rows:
            score = cosine_similarity(query, vector_from_json(row["vector_json"]))
            vector_record = _row_to_vector(row)
            item = vector_record.to_public_dict()
            item["score"] = round(score, 6)
            if vector_record.owner_type == "memory":
                item["record"] = self.get_memory(vector_record.owner_id).to_public_dict()
            else:
                item["record"] = self.get_event(vector_record.owner_id).to_public_dict()
            hits.append(item)
        hits.sort(key=lambda item: item["score"], reverse=True)
        selected = hits[:limit]
        if redact:
            return [redact_obj(item) for item in selected]
        return selected

    def _recall_candidates(self, query: str, limit: int) -> list[tuple[MemoryRecord, float, list[str]]]:
        terms = [
            term.strip()
            for term in query.split()
            if term.strip() and not _is_query_control_term(term)
        ]
        if not terms:
            rows = self.conn.execute(
                f"SELECT * FROM memories WHERE {LIVE_MEMORY_SQL} ORDER BY created_at DESC LIMIT ?",
                (limit * 3,),
            ).fetchall()
            return [(_row_to_memory(row), 0.0, ["recent"]) for row in rows]

        candidates: dict[int, tuple[MemoryRecord, float, list[str]]] = {}
        fts_query = " ".join(f'"{term.replace(chr(34), chr(34) + chr(34))}"' for term in terms)
        try:
            rows = self.conn.execute(
                """
                SELECT m.*, bm25(memories_fts) AS rank
                 FROM memories_fts
                 JOIN memories m ON m.id = memories_fts.rowid
                 WHERE memories_fts MATCH ?
                   AND m.status = 'current'
                   AND (m.fact_key IS NULL OR m.active_fact = 1)
                 ORDER BY rank
                 LIMIT ?
                """,
                (fts_query, limit * 5),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []

        for row in rows:
            record = _row_to_memory(row)
            match_score = max(0.0, 1.5 - abs(float(row["rank"] or 0.0)))
            candidates[int(record.id)] = (record, round(match_score, 3), ["fts"])

        if len(candidates) < limit:
            clauses = []
            params: list[str] = []
            for term in terms:
                pattern = f"%{_sql_like_escape(term)}%"
                clauses.append(
                    "(title LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\' OR "
                    "tags_json LIKE ? ESCAPE '\\' OR fact_key LIKE ? ESCAPE '\\' OR "
                    "thread LIKE ? ESCAPE '\\')"
                )
                params.extend([pattern, pattern, pattern, pattern, pattern])
            rows = self.conn.execute(
                f"""
                SELECT * FROM memories
                 WHERE {LIVE_MEMORY_SQL}
                   AND {' AND '.join(clauses)}
                 LIMIT ?
                """,
                (*params, limit * 5),
            ).fetchall()
            for row in rows:
                record = _row_to_memory(row)
                if int(record.id) not in candidates:
                    candidates[int(record.id)] = (record, 0.5, ["like"])
        return list(candidates.values())

    def _entity_matches(self, query: str, limit: int) -> dict[int, tuple[float, list[str]]]:
        query_entities = _extract_query_entities(query)
        if not query_entities:
            return {}
        keys = sorted({item["entity_key"] for item in query_entities})
        placeholders = ", ".join("?" for _ in keys)
        rows = self.conn.execute(
            f"""
            SELECT me.memory_id,
                   me.label,
                   me.kind,
                   me.source,
                   me.confidence
              FROM memory_entities me
              JOIN memories m ON m.id = me.memory_id
             WHERE me.entity_key IN ({placeholders})
               AND {LIVE_MEMORY_SQL.replace('status', 'm.status')
                              .replace('fact_key', 'm.fact_key')
                              .replace('active_fact', 'm.active_fact')}
             ORDER BY me.confidence DESC, me.source, me.memory_id
             LIMIT ?
            """,
            (*keys, max(1, int(limit)) * 10),
        ).fetchall()
        scores: dict[int, float] = {}
        labels: dict[int, set[str]] = {}
        for row in rows:
            memory_id = int(row["memory_id"])
            source = str(row["source"])
            weight = ENTITY_SOURCE_WEIGHTS.get(source, 0.05)
            scores[memory_id] = scores.get(memory_id, 0.0) + weight * float(row["confidence"] or 1.0)
            labels.setdefault(memory_id, set()).add(
                f"{row['kind']}:{row['label']}"
            )
        return {
            memory_id: (
                round(min(ENTITY_MAX_BOOST, score), 3),
                sorted(labels.get(memory_id, set()))[:8],
            )
            for memory_id, score in scores.items()
        }

    def _relation_expansion(
        self, base_ids: list[int]
    ) -> dict[int, tuple[float, list[int], list[str]]]:
        if not base_ids:
            return {}
        base_set = set(base_ids)
        frontier = set(base_ids)
        expanded: dict[int, tuple[float, list[int], list[str]]] = {}

        for depth in (1, 2):
            if not frontier:
                break
            placeholders = ", ".join("?" for _ in frontier)
            rows = self.conn.execute(
                f"""
                SELECT r.source_id, r.target_id, r.relation_type, r.strength
                  FROM relations r
                  JOIN memories source ON source.id = r.source_id
                  JOIN memories target ON target.id = r.target_id
                 WHERE (r.source_id IN ({placeholders}) OR r.target_id IN ({placeholders}))
                   AND r.relation_type IN ({", ".join("?" for _ in SAFE_RELATION_TYPES)})
                   AND r.strength > ?
                   AND source.status = 'current'
                   AND target.status = 'current'
                   AND (source.fact_key IS NULL OR source.active_fact = 1)
                   AND (target.fact_key IS NULL OR target.active_fact = 1)
                """,
                (
                    *frontier,
                    *frontier,
                    *sorted(SAFE_RELATION_TYPES),
                    RELATION_MIN_STRENGTH[depth],
                ),
            ).fetchall()
            next_frontier: set[int] = set()
            for row in rows:
                source_id = int(row["source_id"])
                target_id = int(row["target_id"])
                relation_type = str(row["relation_type"])
                if source_id in frontier:
                    related_id = target_id
                    from_id = source_id
                else:
                    related_id = source_id
                    from_id = target_id
                if related_id in base_set:
                    continue
                score = _relation_score(relation_type, float(row["strength"] or 1.0), depth=depth)
                if score <= 0:
                    continue
                reason = f"related:{depth}:{relation_type}:{from_id}"
                previous_score, previous_from, previous_reasons = expanded.get(
                    related_id, (0.0, [], [])
                )
                expanded[related_id] = (
                    max(previous_score, score),
                    sorted(set(previous_from + [from_id])),
                    sorted(set(previous_reasons + [reason])),
                )
                if depth == 1:
                    next_frontier.add(related_id)
            frontier = next_frontier - base_set
        return expanded

    def _record_recall_trace(
        self,
        query: str,
        hits: list[RecallHit],
        *,
        limit: int,
        expand_relations: bool,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO recall_runs (
                query_hash, query_preview, limit_requested, selected_count, expand_relations
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                _query_hash(query),
                _query_preview(query),
                int(limit),
                len(hits),
                1 if expand_relations else 0,
            ),
        )
        run_id = int(cur.lastrowid)
        self.conn.executemany(
            """
            INSERT INTO recall_trace_items (
                run_id, memory_id, rank, injected, score, score_breakdown_json,
                reasons_json, related_from_json, trace_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    run_id,
                    hit.record.id,
                    rank,
                    1,
                    hit.score,
                    json.dumps(hit.score_breakdown, ensure_ascii=False, sort_keys=True),
                    json.dumps(hit.reasons, ensure_ascii=False, sort_keys=True),
                    json.dumps(hit.related_from, ensure_ascii=False, sort_keys=True),
                    json.dumps(hit.trace, ensure_ascii=False, sort_keys=True),
                )
                for rank, hit in enumerate(hits, start=1)
            ],
        )
        return run_id

    def list_recall_traces(
        self,
        limit: int = 20,
        *,
        memory_id: int | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["i.memory_id IS NOT NULL"]
        params: list[object] = []
        if memory_id is not None:
            clauses.append("i.memory_id = ?")
            params.append(int(memory_id))
        rows = self.conn.execute(
            f"""
            SELECT r.id AS run_id,
                   r.query_hash,
                   r.query_preview,
                   r.limit_requested,
                   r.selected_count,
                   r.expand_relations,
                   r.created_at AS run_created_at,
                   i.memory_id,
                   i.rank,
                   i.injected,
                   i.score,
                   i.score_breakdown_json,
                   i.reasons_json,
                   i.related_from_json,
                   i.trace_json,
                   i.created_at AS item_created_at
              FROM recall_trace_items i
              JOIN recall_runs r ON r.id = i.run_id
             WHERE {' AND '.join(clauses)}
             ORDER BY r.created_at DESC, r.id DESC, i.rank ASC
             LIMIT ?
            """,
            (*params, int(limit)),
        ).fetchall()
        return [
            {
                "run_id": row["run_id"],
                "query_hash": row["query_hash"],
                "query_preview": row["query_preview"],
                "limit_requested": row["limit_requested"],
                "selected_count": row["selected_count"],
                "expand_relations": bool(row["expand_relations"]),
                "run_created_at": row["run_created_at"],
                "memory_id": row["memory_id"],
                "rank": row["rank"],
                "injected": bool(row["injected"]),
                "score": row["score"],
                "score_breakdown": json.loads(row["score_breakdown_json"] or "{}"),
                "reasons": json.loads(row["reasons_json"] or "[]"),
                "related_from": json.loads(row["related_from_json"] or "[]"),
                "trace": json.loads(row["trace_json"] or "{}"),
                "item_created_at": row["item_created_at"],
            }
            for row in rows
        ]

    def recall_hits(
        self,
        query: str,
        limit: int = 5,
        *,
        expand_relations: bool = True,
        entity_boost: bool = True,
        temporal_boost: bool = True,
        trace: bool = True,
        gate_mode: GateMode = "recall",
    ) -> list[RecallHit]:
        candidates = self._recall_candidates(query, limit)
        entity_scores = self._entity_matches(query, limit * 5) if entity_boost else {}
        temporal_intent = _query_temporal_intent(query) if temporal_boost else ""
        if entity_scores:
            candidate_map = {
                int(record.id): (record, match_score, list(reasons))
                for record, match_score, reasons in candidates
                if record.id is not None
            }
            for memory_id, (_, entity_labels) in entity_scores.items():
                entity_reasons = [f"entity:{label}" for label in entity_labels]
                if memory_id in candidate_map:
                    record, match_score, reasons = candidate_map[memory_id]
                    candidate_map[memory_id] = (
                        record,
                        match_score,
                        sorted(set(reasons + entity_reasons)),
                    )
                    continue
                try:
                    record = self.get_memory(memory_id)
                except KeyError:
                    continue
                candidate_map[memory_id] = (record, 0.0, entity_reasons)
            candidates = list(candidate_map.values())
        base_ids = [int(record.id) for record, _, _ in candidates if record.id is not None]
        relation_scores = self._relation_expansion(base_ids) if expand_relations else {}

        for related_id, (_, related_from, related_reasons) in relation_scores.items():
            try:
                record = self.get_memory(related_id)
            except KeyError:
                continue
            fallback_reason = f"related:{','.join(map(str, related_from))}"
            candidates.append((record, 0.0, related_reasons or [fallback_reason]))

        hits: list[RecallHit] = []
        for record, match_score, reasons in candidates:
            gate = metabolic_gate(record, mode=gate_mode)
            if not gate["allowed"]:
                continue
            relation_score, related_from, _ = relation_scores.get(int(record.id), (0.0, [], []))
            entity_score, entity_labels = entity_scores.get(int(record.id), (0.0, []))
            temporal_score = _temporal_boost(record, temporal_intent)
            breakdown = _recall_score_breakdown(
                record,
                match_score=match_score,
                relation_score=relation_score,
                entity_score=entity_score,
                temporal_score=temporal_score,
                reasons=reasons,
                gate=gate,
            )
            hits.append(
                RecallHit(
                    record=record,
                    score=breakdown["final"],
                    match_score=match_score,
                    relation_score=round(relation_score, 3),
                    score_breakdown=breakdown,
                    reasons=reasons,
                    related_from=related_from,
                    trace={
                        "channels": reasons,
                        "expanded_from": related_from,
                        "entity_matches": entity_labels,
                        "temporal_intent": temporal_intent,
                        "m_gate": {
                            "mode": gate_mode,
                            "bucket": gate["bucket"],
                            "reason": gate["reason"],
                            "factor": gate["factor"],
                        },
                        "live_filter": "current_and_active_fact",
                    },
                )
            )
        hits.sort(key=lambda hit: (hit.score, hit.record.created_at or ""), reverse=True)
        selected = hits[:limit]

        if selected:
            ids = [hit.record.id for hit in selected if hit.record.id is not None]
            self.conn.executemany(
                """
                UPDATE memories
                   SET hit_count = hit_count + 1,
                       last_hit_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                 WHERE id = ?
                """,
                [(memory_id,) for memory_id in ids],
            )
        if trace:
            run_id = self._record_recall_trace(
                query,
                selected,
                limit=limit,
                expand_relations=expand_relations,
            )
            selected = [
                RecallHit(
                    record=hit.record,
                    score=hit.score,
                    match_score=hit.match_score,
                    relation_score=hit.relation_score,
                    score_breakdown=hit.score_breakdown,
                    reasons=hit.reasons,
                    related_from=hit.related_from,
                    trace={**hit.trace, "recall_run_id": run_id, "rank": rank},
                )
                for rank, hit in enumerate(selected, start=1)
            ]
        self.conn.commit()
        return selected

    def recall(
        self,
        query: str,
        limit: int = 5,
        *,
        redact: bool = False,
        expand_relations: bool = True,
        entity_boost: bool = True,
        temporal_boost: bool = True,
        trace: bool = True,
    ) -> list[dict[str, Any]]:
        selected = [
            hit.to_dict()
            for hit in self.recall_hits(
                query,
                limit=limit,
                expand_relations=expand_relations,
                entity_boost=entity_boost,
                temporal_boost=temporal_boost,
                trace=trace,
            )
        ]
        if redact:
            return [redact_obj(item) for item in selected]
        return selected

    def export_jsonl(self) -> str:
        lines = []
        for record in self.list_recent(limit=1_000_000):
            lines.append(json.dumps(record.to_public_dict(), ensure_ascii=False, sort_keys=True))
        return "\n".join(lines)

    def import_jsonl(self, text: str) -> tuple[int, int]:
        created = 0
        reused = 0
        for line_no, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON on line {line_no}: {exc}") from exc
            record, is_new = self.add_memory(
                title=data["title"],
                content=data["content"],
                thread=data.get("thread", "other"),
                category=data.get("category", "note"),
                tags=data.get("tags", []),
                fact_key=data.get("fact_key"),
                active_fact=bool(data.get("active_fact", True)),
                status=data.get("status", "current"),
                risk_level=data.get("risk_level", "normal"),
                urgency=data.get("urgency", "normal"),
                response_tendency=data.get("response_tendency", ""),
                valence=data.get("valence"),
                arousal=data.get("arousal"),
                tension=data.get("tension"),
                confidence=data.get("confidence"),
                growth_delta=data.get("growth_delta", ""),
                source=data.get("source", ""),
            )
            if record and is_new:
                created += 1
            else:
                reused += 1
        return created, reused
