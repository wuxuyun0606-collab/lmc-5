"""Nap pass · lightweight session-switch maintenance.

A nap is intentionally smaller than the nightly dream run. It does not promote
new durable memories by itself. It only closes the common fresh-memory gap:

- add embeddings for curated memories that do not have vectors yet
- connect newly promoted / orphan curated memories to nearby neighbors

All external work is injected as callables so deployments can choose their
embedding provider, vector store, and relation policy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence


@dataclass
class NapResult:
    scanned_memories: int = 0
    vectors_written: int = 0
    orphan_memories_scanned: int = 0
    relations_written: int = 0
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "scanned_memories": self.scanned_memories,
            "vectors_written": self.vectors_written,
            "orphan_memories_scanned": self.orphan_memories_scanned,
            "relations_written": self.relations_written,
            "skipped": list(self.skipped),
            "errors": list(self.errors),
        }


def _fetch_dicts(cursor: Any) -> list[dict[str, Any]]:
    rows = cursor.fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            out.append(dict(row))
            continue
        if hasattr(row, "keys"):
            out.append({key: row[key] for key in row.keys()})
            continue
        desc = getattr(cursor, "description", None) or []
        keys = [d[0] for d in desc]
        out.append({key: value for key, value in zip(keys, row)})
    return out


def load_curated_without_vectors(
    conn: Any,
    *,
    owner_type: str = "curated",
    model_name: str = "gemini-embedding-2",
    dimension: int = 3072,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Return current curated memories that are missing an embedding."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT m.id, m.title, m.content
              FROM lmc5_curated_memories m
             WHERE m.version_status = 'current'
               AND NOT EXISTS (
                   SELECT 1 FROM lmc5_vectors v
                    WHERE v.owner_type = %s
                      AND v.owner_id = m.id
                      AND v.model_name = %s
                      AND v.dimension = %s
               )
             ORDER BY m.created_at DESC, m.id DESC
             LIMIT %s
            """,
            (owner_type, model_name, dimension, limit),
        )
        return _fetch_dicts(cur)


def load_orphan_curated_memories(conn: Any, *, limit: int = 25) -> list[int]:
    """Return current curated memory IDs with no active relation edges."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT m.id
              FROM lmc5_curated_memories m
             WHERE m.version_status = 'current'
               AND NOT EXISTS (
                   SELECT 1 FROM lmc5_memory_relations r
                    WHERE r.valid_until IS NULL
                      AND (r.source_id = m.id OR r.target_id = m.id)
               )
             ORDER BY m.created_at DESC, m.id DESC
             LIMIT %s
            """,
            (limit,),
        )
        return [int(row["id"]) for row in _fetch_dicts(cur)]


def run_nap(
    conn: Any,
    *,
    vector_writer: Optional[Callable[[str, int, str], None]] = None,
    neighbor_finder: Optional[Callable[[int, int], Sequence[int]]] = None,
    relation_writer: Optional[Callable[[int, int, str, float, str], None]] = None,
    vector_limit: int = 25,
    orphan_limit: int = 25,
    neighbor_top_k: int = 3,
    relation_type: str = "same_topic",
    relation_strength: float = 0.45,
    owner_type: str = "curated",
    model_name: str = "gemini-embedding-2",
    dimension: int = 3072,
) -> NapResult:
    """Run the lightweight nap pass.

    `vector_writer` should usually be `PgvectorStore.write_vector`-shaped:
    `(owner_type, owner_id, text) -> None`. `neighbor_finder` and
    `relation_writer` mirror `NightDream` relation hooks.
    """
    result = NapResult()

    if vector_writer is None:
        result.skipped.append("vectors: vector_writer not configured")
    else:
        try:
            missing = load_curated_without_vectors(
                conn,
                owner_type=owner_type,
                model_name=model_name,
                dimension=dimension,
                limit=vector_limit,
            )
            result.scanned_memories = len(missing)
            for row in missing:
                text = f"{row.get('title') or ''}\n{row.get('content') or ''}".strip()
                vector_writer(owner_type, int(row["id"]), text)
                result.vectors_written += 1
        except Exception as exc:  # pragma: no cover - defensive isolation
            result.errors.append(f"vectors: {type(exc).__name__}: {exc}")

    if neighbor_finder is None or relation_writer is None:
        result.skipped.append("relations: neighbor_finder/relation_writer not configured")
    else:
        try:
            orphan_ids = load_orphan_curated_memories(conn, limit=orphan_limit)
            result.orphan_memories_scanned = len(orphan_ids)
            seen: set[tuple[int, int, str]] = set()
            for memory_id in orphan_ids:
                for neighbor_id in neighbor_finder(memory_id, neighbor_top_k) or []:
                    if int(neighbor_id) == int(memory_id):
                        continue
                    a, b = sorted((int(memory_id), int(neighbor_id)))
                    key = (a, b, relation_type)
                    if key in seen:
                        continue
                    relation_writer(a, b, relation_type, relation_strength, "nap:orphan-link")
                    seen.add(key)
                    result.relations_written += 1
        except Exception as exc:  # pragma: no cover - defensive isolation
            result.errors.append(f"relations: {type(exc).__name__}: {exc}")

    return result
