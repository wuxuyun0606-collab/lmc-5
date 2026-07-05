"""Night patrol · safe pgvector memory-system maintenance.

Patrol is the nightly guardrail layer. It may expire structural garbage such as
orphan/duplicate relation edges, but it does not delete emotional/event memories
automatically. Higher-risk cleanup is reported for human review.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class PatrolFinding:
    check: str
    severity: str
    message: str
    count: int = 0
    sample: list[Any] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "severity": self.severity,
            "message": self.message,
            "count": self.count,
            "sample": self.sample,
        }


@dataclass
class PatrolResult:
    findings: list[PatrolFinding] = field(default_factory=list)
    duplicate_relations_expired: int = 0
    orphan_relations_expired: int = 0
    dead_contradiction_relations_expired: int = 0
    health_review: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(f.severity == "critical" for f in self.findings) and not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "findings": [f.to_dict() for f in self.findings],
            "duplicate_relations_expired": self.duplicate_relations_expired,
            "orphan_relations_expired": self.orphan_relations_expired,
            "dead_contradiction_relations_expired": self.dead_contradiction_relations_expired,
            "health_review": self.health_review,
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


def collect_health_snapshot(conn: Any) -> dict[str, Any]:
    """Collect count-level health signals for the memory system."""
    snapshot: dict[str, Any] = {}
    with conn.cursor() as cur:
        for key, sql in {
            "curated_current": "SELECT count(*) AS n FROM lmc5_curated_memories WHERE version_status='current'",
            "vectors": "SELECT count(*) AS n FROM lmc5_vectors",
            "active_relations": "SELECT count(*) AS n FROM lmc5_memory_relations WHERE valid_until IS NULL",
            "raw_events_24h": "SELECT count(*) AS n FROM lmc5_raw_events WHERE created_at >= NOW() - INTERVAL '24 hours'",
            "z_pending": "SELECT count(*) AS n FROM lmc5_z_audit WHERE status='pending'",
        }.items():
            cur.execute(sql)
            snapshot[key] = int(_fetch_dicts(cur)[0]["n"])
        cur.execute(
            """
            SELECT count(*) AS n
              FROM lmc5_curated_memories m
             WHERE m.version_status='current'
               AND NOT EXISTS (
                   SELECT 1 FROM lmc5_vectors v
                    WHERE v.owner_type='curated' AND v.owner_id=m.id
               )
            """
        )
        snapshot["curated_missing_vectors"] = int(_fetch_dicts(cur)[0]["n"])
        cur.execute(
            """
            SELECT count(*) AS n
              FROM lmc5_memory_relations r
              LEFT JOIN lmc5_curated_memories s ON s.id=r.source_id
              LEFT JOIN lmc5_curated_memories t ON t.id=r.target_id
             WHERE r.valid_until IS NULL AND (s.id IS NULL OR t.id IS NULL)
            """
        )
        snapshot["orphan_relations"] = int(_fetch_dicts(cur)[0]["n"])
        cur.execute(
            """
            SELECT count(*) AS n
              FROM lmc5_memory_relations r
              JOIN lmc5_curated_memories s ON s.id = r.source_id
              JOIN lmc5_curated_memories t ON t.id = r.target_id
             WHERE r.valid_until IS NULL
               AND r.relation_type IN ('contradiction', 'contradicts')
               AND (
                    s.version_status <> 'current'
                 OR t.version_status <> 'current'
                 OR coalesce(s.active_fact, FALSE) = FALSE
                 OR coalesce(t.active_fact, FALSE) = FALSE
               )
            """
        )
        snapshot["dead_contradiction_relations"] = int(_fetch_dicts(cur)[0]["n"])
    return snapshot


def find_duplicate_relation_ids(conn: Any, *, limit: int = 200) -> list[int]:
    """Return extra active relation IDs for duplicate source/target/type groups."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id
              FROM (
                    SELECT id,
                           row_number() OVER (
                               PARTITION BY source_id, target_id, relation_type
                               ORDER BY created_at ASC, id ASC
                           ) AS rn
                      FROM lmc5_memory_relations
                     WHERE valid_until IS NULL
                   ) ranked
             WHERE rn > 1
             LIMIT %s
            """,
            (limit,),
        )
        return [int(row["id"]) for row in _fetch_dicts(cur)]


def find_orphan_relation_ids(conn: Any, *, limit: int = 200) -> list[int]:
    """Return active relation IDs pointing at missing curated memories."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT r.id
              FROM lmc5_memory_relations r
              LEFT JOIN lmc5_curated_memories s ON s.id = r.source_id
              LEFT JOIN lmc5_curated_memories t ON t.id = r.target_id
             WHERE r.valid_until IS NULL
               AND (s.id IS NULL OR t.id IS NULL)
             ORDER BY r.created_at ASC, r.id ASC
             LIMIT %s
            """,
            (limit,),
        )
        return [int(row["id"]) for row in _fetch_dicts(cur)]


def find_dead_contradiction_relation_ids(conn: Any, *, limit: int = 200) -> list[int]:
    """Return active contradiction edge IDs whose endpoints are no longer live facts."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT r.id
              FROM lmc5_memory_relations r
              JOIN lmc5_curated_memories s ON s.id = r.source_id
              JOIN lmc5_curated_memories t ON t.id = r.target_id
             WHERE r.valid_until IS NULL
               AND r.relation_type IN ('contradiction', 'contradicts')
               AND (
                    s.version_status <> 'current'
                 OR t.version_status <> 'current'
                 OR coalesce(s.active_fact, FALSE) = FALSE
                 OR coalesce(t.active_fact, FALSE) = FALSE
               )
             ORDER BY r.created_at ASC, r.id ASC
             LIMIT %s
            """,
            (limit,),
        )
        return [int(row["id"]) for row in _fetch_dicts(cur)]


def expire_relation_ids(conn: Any, ids: list[int], *, reason: str) -> int:
    if not ids:
        return 0
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE lmc5_memory_relations
               SET valid_until = NOW(),
                   reason = concat(coalesce(reason, ''), %s)
             WHERE id = ANY(%s)
               AND valid_until IS NULL
            """,
            (f" | expired:{reason}", ids),
        )
        count = int(cur.rowcount or 0)
    conn.commit()
    return count


def make_health_review_prompt(snapshot: dict[str, Any], findings: list[PatrolFinding]) -> str:
    """Prompt for an optional DeepSeek/housekeeper health reviewer."""
    payload = {
        "snapshot": snapshot,
        "findings": [f.to_dict() for f in findings],
        "rules": [
            "Return concise JSON or a short Chinese report.",
            "Do not propose deleting emotional/event memories automatically.",
            "Separate urgent operational issues from normal growth.",
        ],
    }
    return "LMC-5 memory-system nightly health patrol:\n" + json.dumps(payload, ensure_ascii=False, indent=2)


def run_patrol(
    conn: Any,
    *,
    apply: bool = False,
    max_expire: int = 200,
    health_reviewer: Optional[Callable[[str], str]] = None,
) -> PatrolResult:
    """Run nightly structural cleanup + health checks.

    `apply=False` is a dry run. `apply=True` expires only duplicate/orphan
    relation edges. It never deletes curated memories.
    """
    result = PatrolResult()
    try:
        snapshot = collect_health_snapshot(conn)
        if snapshot.get("curated_missing_vectors", 0):
            result.findings.append(PatrolFinding(
                check="curated_missing_vectors",
                severity="warning",
                message="current curated memories are missing vectors; run nap or backfill embeddings",
                count=int(snapshot["curated_missing_vectors"]),
            ))
        if snapshot.get("orphan_relations", 0):
            result.findings.append(PatrolFinding(
                check="orphan_relations",
                severity="critical",
                message="active relations point at missing curated memories",
                count=int(snapshot["orphan_relations"]),
            ))
        if snapshot.get("dead_contradiction_relations", 0):
            result.findings.append(PatrolFinding(
                check="dead_contradiction_relations",
                severity="warning",
                message=(
                    "contradiction edges whose endpoints are no longer current facts "
                    "can be expired before Z-audit"
                ),
                count=int(snapshot["dead_contradiction_relations"]),
            ))
        if snapshot.get("z_pending", 0):
            result.findings.append(PatrolFinding(
                check="z_pending",
                severity="info",
                message="Z-axis audits are waiting for review",
                count=int(snapshot["z_pending"]),
            ))

        duplicate_ids = find_duplicate_relation_ids(conn, limit=max_expire)
        orphan_ids = find_orphan_relation_ids(conn, limit=max_expire)
        dead_contradiction_ids = find_dead_contradiction_relation_ids(conn, limit=max_expire)
        if duplicate_ids:
            result.findings.append(PatrolFinding(
                check="duplicate_relations",
                severity="warning",
                message="duplicate active relation edges can be expired safely",
                count=len(duplicate_ids),
                sample=duplicate_ids[:10],
            ))
        if orphan_ids:
            # Finding may already exist from snapshot; this one carries sample ids.
            result.findings.append(PatrolFinding(
                check="orphan_relation_ids",
                severity="critical",
                message="orphan active relation edge ids can be expired safely",
                count=len(orphan_ids),
                sample=orphan_ids[:10],
            ))
        if dead_contradiction_ids:
            result.findings.append(PatrolFinding(
                check="dead_contradiction_relation_ids",
                severity="warning",
                message="dead contradiction edge ids can be expired safely",
                count=len(dead_contradiction_ids),
                sample=dead_contradiction_ids[:10],
            ))

        if apply:
            result.duplicate_relations_expired = expire_relation_ids(
                conn, duplicate_ids, reason="duplicate-relation"
            )
            result.orphan_relations_expired = expire_relation_ids(
                conn, orphan_ids, reason="orphan-relation"
            )
            result.dead_contradiction_relations_expired = expire_relation_ids(
                conn, dead_contradiction_ids, reason="dead-contradiction-relation"
            )

        if health_reviewer is not None:
            prompt = make_health_review_prompt(snapshot, result.findings)
            result.health_review = str(health_reviewer(prompt) or "").strip()
    except Exception as exc:  # pragma: no cover - defensive isolation
        result.errors.append(f"{type(exc).__name__}: {exc}")
    return result
