"""SQLite store for LMC-5."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

from .models import (
    EVENT_ROLES,
    FACT_STATUSES,
    RELATION_TYPES,
    RISK_LEVELS,
    URGENCY_LEVELS,
    VECTOR_OWNER_TYPES,
    EventRecord,
    MemoryRecord,
    RecallHit,
    RelationRecord,
    VectorRecord,
    validate_choice,
)
from .redact import redact_obj
from .scoring import priority_score
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


def _json_list(values: Iterable[str] | None) -> str:
    clean = [str(value).strip() for value in values or [] if str(value).strip()]
    return json.dumps(sorted(set(clean)), ensure_ascii=False)


def _sql_like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


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
            """
        )
        self.rebuild_index()
        self.conn.commit()

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
        self.conn.commit()
        return self.get_memory(memory_id), True

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
        validate_choice("relation_type", relation_type, RELATION_TYPES)
        if source_id == target_id:
            raise ValueError("relation source and target must differ")
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

    def surface(
        self,
        query: str,
        limit: int = 8,
        *,
        event_limit: int | None = None,
        memory_limit: int | None = None,
        redact: bool = True,
    ) -> dict[str, Any]:
        memory_limit = memory_limit or max(1, limit // 2)
        event_limit = event_limit or max(1, limit - memory_limit)
        return {
            "query": query,
            "memories": self.recall(query, limit=memory_limit, redact=redact),
            "events": self.search_events(query, limit=event_limit, redact=redact),
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
        terms = [term.strip() for term in query.split() if term.strip()]
        if not terms:
            rows = self.conn.execute(
                "SELECT * FROM memories WHERE status != 'archived' ORDER BY created_at DESC LIMIT ?",
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
                   AND m.status != 'archived'
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
                 WHERE status != 'archived'
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

    def _relation_expansion(self, base_ids: list[int]) -> dict[int, tuple[float, list[int]]]:
        if not base_ids:
            return {}
        placeholders = ", ".join("?" for _ in base_ids)
        rows = self.conn.execute(
            f"""
            SELECT source_id, target_id, strength
              FROM relations
             WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders})
            """,
            (*base_ids, *base_ids),
        ).fetchall()
        expanded: dict[int, tuple[float, list[int]]] = {}
        base_set = set(base_ids)
        for row in rows:
            source_id = int(row["source_id"])
            target_id = int(row["target_id"])
            if source_id in base_set:
                related_id = target_id
                from_id = source_id
            else:
                related_id = source_id
                from_id = target_id
            if related_id in base_set:
                continue
            score = min(float(row["strength"] or 1.0), 1.0) * 0.35
            previous_score, previous_from = expanded.get(related_id, (0.0, []))
            expanded[related_id] = (max(previous_score, score), sorted(set(previous_from + [from_id])))
        return expanded

    def recall_hits(
        self,
        query: str,
        limit: int = 5,
        *,
        expand_relations: bool = True,
    ) -> list[RecallHit]:
        candidates = self._recall_candidates(query, limit)
        base_ids = [int(record.id) for record, _, _ in candidates if record.id is not None]
        relation_scores = self._relation_expansion(base_ids) if expand_relations else {}

        for related_id, (_, related_from) in relation_scores.items():
            try:
                record = self.get_memory(related_id)
            except KeyError:
                continue
            candidates.append((record, 0.0, [f"related:{','.join(map(str, related_from))}"]))

        hits: list[RecallHit] = []
        for record, match_score, reasons in candidates:
            relation_score, related_from = relation_scores.get(int(record.id), (0.0, []))
            score = priority_score(record) + match_score + relation_score
            hits.append(
                RecallHit(
                    record=record,
                    score=round(score, 3),
                    match_score=match_score,
                    relation_score=round(relation_score, 3),
                    reasons=reasons,
                    related_from=related_from,
                )
            )
        hits.sort(key=lambda hit: (hit.score, hit.record.created_at or ""), reverse=True)
        selected = hits[:limit]

        if selected:
            ids = [hit.record.id for hit in selected if hit.record.id is not None]
            self.conn.executemany(
                "UPDATE memories SET hit_count = hit_count + 1 WHERE id = ?",
                [(memory_id,) for memory_id in ids],
            )
            self.conn.commit()
        return selected

    def recall(
        self,
        query: str,
        limit: int = 5,
        *,
        redact: bool = False,
        expand_relations: bool = True,
    ) -> list[dict[str, Any]]:
        selected = [
            hit.to_dict()
            for hit in self.recall_hits(query, limit=limit, expand_relations=expand_relations)
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
