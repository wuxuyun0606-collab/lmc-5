"""Read-only metabolism patrol checks."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Literal

from .models import MetabolismSuggestion, SYMMETRIC_RELATION_TYPES

Severity = Literal["info", "warning", "critical"]


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _span_days(rows: list[sqlite3.Row]) -> float:
    dates = [parsed for row in rows if (parsed := _parse_time(row["created_at"]))]
    if len(dates) < 2:
        return 0.0
    return max((max(dates) - min(dates)).total_seconds() / 86400, 0.0)


def _other_stage(
    rows: list[sqlite3.Row],
    *,
    observe_threshold: int,
    candidate_threshold: int,
    formal_threshold: int,
    formal_min_span_days: int,
    formal_min_hits: int,
) -> tuple[str | None, Severity, str]:
    count = len(rows)
    hit_total = sum(int(row["hit_count"] or 0) for row in rows)
    span_days = _span_days(rows)

    if (
        count >= formal_threshold
        and span_days >= formal_min_span_days
        and hit_total >= formal_min_hits
    ):
        return (
            "formal_line_candidate",
            "warning",
            (
                f"{count} memories over {span_days:.0f}d with {hit_total} hits "
                "meet the formal split threshold"
            ),
        )
    if count >= candidate_threshold or (count >= observe_threshold and hit_total >= 3):
        return (
            "candidate_line",
            "info",
            f"{count} memories with {hit_total} hits should be reviewed as a candidate thread",
        )
    if count >= observe_threshold:
        return (
            "observe_cluster",
            "info",
            f"{count} memories form an observation cluster; keep watching before splitting",
        )
    return None, "info", ""


def patrol(
    conn: sqlite3.Connection,
    *,
    split_threshold: int = 5,
    observe_threshold: int = 3,
    formal_threshold: int = 8,
    formal_min_span_days: int = 14,
    formal_min_hits: int = 2,
) -> list[MetabolismSuggestion]:
    """Return read-only lifecycle suggestions."""
    suggestions: list[MetabolismSuggestion] = []

    duplicate_facts = conn.execute(
        """
        SELECT fact_key, group_concat(id) AS ids, count(*) AS n
          FROM memories
         WHERE fact_key IS NOT NULL
           AND fact_key != ''
           AND active_fact = 1
           AND status = 'current'
         GROUP BY fact_key
        HAVING count(*) > 1
        """
    ).fetchall()
    for row in duplicate_facts:
        ids = [int(value) for value in row["ids"].split(",")]
        suggestions.append(
            MetabolismSuggestion(
                action="mark_review",
                severity="critical",
                reason=f"fact_key has {row['n']} current active facts",
                memory_ids=ids,
                fact_key=row["fact_key"],
            )
        )

    review_rows = conn.execute(
        "SELECT id FROM memories WHERE status = 'review' ORDER BY updated_at DESC"
    ).fetchall()
    if review_rows:
        suggestions.append(
            MetabolismSuggestion(
                action="mark_review",
                severity="warning",
                reason=f"{len(review_rows)} memories are waiting for review",
                memory_ids=[int(row["id"]) for row in review_rows[:20]],
            )
        )

    z_pending_rows = conn.execute(
        """
        SELECT left_memory_id, right_memory_id
          FROM z_conflict_audits
         WHERE status = 'pending'
           AND verdict = 'pending'
         ORDER BY created_at DESC
        """
    ).fetchall()
    if z_pending_rows:
        memory_ids: list[int] = []
        for row in z_pending_rows[:10]:
            memory_ids.extend([int(row["left_memory_id"]), int(row["right_memory_id"])])
        suggestions.append(
            MetabolismSuggestion(
                action="mark_review",
                severity="warning",
                reason=f"{len(z_pending_rows)} Z-axis conflict audits are pending",
                memory_ids=sorted(set(memory_ids)),
            )
        )

    stale_relation_rows = conn.execute(
        """
        SELECT r.source_id, r.target_id
          FROM relations r
          JOIN memories source ON source.id = r.source_id
          JOIN memories target ON target.id = r.target_id
         WHERE source.status != 'current'
            OR target.status != 'current'
            OR (source.fact_key IS NOT NULL AND source.active_fact = 0)
            OR (target.fact_key IS NOT NULL AND target.active_fact = 0)
         ORDER BY r.created_at DESC, r.id DESC
        """
    ).fetchall()
    if stale_relation_rows:
        memory_ids: list[int] = []
        for row in stale_relation_rows[:10]:
            memory_ids.extend([int(row["source_id"]), int(row["target_id"])])
        suggestions.append(
            MetabolismSuggestion(
                action="mark_review",
                severity="warning",
                reason=(
                    f"{len(stale_relation_rows)} relations touch non-live memories "
                    "and should be reviewed or expired"
                ),
                memory_ids=sorted(set(memory_ids)),
            )
        )

    orphan_relation_rows = conn.execute(
        """
        SELECT r.source_id, r.target_id
          FROM relations r
          LEFT JOIN memories source ON source.id = r.source_id
          LEFT JOIN memories target ON target.id = r.target_id
         WHERE source.id IS NULL OR target.id IS NULL
         ORDER BY r.created_at DESC, r.id DESC
        """
    ).fetchall()
    if orphan_relation_rows:
        memory_ids: list[int] = []
        for row in orphan_relation_rows[:10]:
            memory_ids.extend([int(row["source_id"]), int(row["target_id"])])
        suggestions.append(
            MetabolismSuggestion(
                action="mark_review",
                severity="critical",
                reason=f"{len(orphan_relation_rows)} orphaned relations point at missing memories",
                memory_ids=sorted(set(memory_ids)),
            )
        )

    self_loop_rows = conn.execute(
        "SELECT source_id FROM relations WHERE source_id = target_id ORDER BY id DESC"
    ).fetchall()
    if self_loop_rows:
        suggestions.append(
            MetabolismSuggestion(
                action="mark_review",
                severity="critical",
                reason=f"{len(self_loop_rows)} relation self-loops should be removed",
                memory_ids=[int(row["source_id"]) for row in self_loop_rows[:20]],
            )
        )

    symmetric_types = sorted(SYMMETRIC_RELATION_TYPES)
    placeholders = ", ".join("?" for _ in symmetric_types)
    reciprocal_rows = conn.execute(
        f"""
        SELECT r1.source_id, r1.target_id
          FROM relations r1
          JOIN relations r2
            ON r1.source_id = r2.target_id
           AND r1.target_id = r2.source_id
           AND r1.relation_type = r2.relation_type
           AND r1.id < r2.id
         WHERE r1.relation_type IN ({placeholders})
         ORDER BY r1.id DESC
        """,
        symmetric_types,
    ).fetchall()
    if reciprocal_rows:
        memory_ids: list[int] = []
        for row in reciprocal_rows[:10]:
            memory_ids.extend([int(row["source_id"]), int(row["target_id"])])
        suggestions.append(
            MetabolismSuggestion(
                action="mark_review",
                severity="warning",
                reason=f"{len(reciprocal_rows)} reciprocal duplicate symmetric relations found",
                memory_ids=sorted(set(memory_ids)),
            )
        )

    other_rows = conn.execute(
        """
        SELECT id, category, tags_json, hit_count, created_at
          FROM memories
         WHERE thread = 'other'
           AND status = 'current'
        """
    ).fetchall()
    category_rows: dict[str, list[sqlite3.Row]] = defaultdict(list)
    tag_rows: dict[str, list[sqlite3.Row]] = defaultdict(list)
    tag_counter: Counter[str] = Counter()
    for row in other_rows:
        category_rows[row["category"]].append(row)
        for tag in json.loads(row["tags_json"] or "[]"):
            tag_counter[tag] += 1
            tag_rows[tag].append(row)

    candidate_threshold = split_threshold
    for category, rows in category_rows.items():
        stage, severity, reason = _other_stage(
            rows,
            observe_threshold=observe_threshold,
            candidate_threshold=candidate_threshold,
            formal_threshold=formal_threshold,
            formal_min_span_days=formal_min_span_days,
            formal_min_hits=formal_min_hits,
        )
        if category and stage:
            ids = [int(row["id"]) for row in rows]
            suggestions.append(
                MetabolismSuggestion(
                    action="split_thread",
                    severity=severity,
                    reason=f"`other` category `{category}`: {reason}",
                    memory_ids=ids[:20],
                    thread="other",
                    category=category,
                    stage=stage,
                )
            )

    for tag in tag_counter:
        rows = tag_rows[tag]
        stage, severity, reason = _other_stage(
            rows,
            observe_threshold=observe_threshold,
            candidate_threshold=candidate_threshold,
            formal_threshold=formal_threshold,
            formal_min_span_days=formal_min_span_days,
            formal_min_hits=formal_min_hits,
        )
        if stage:
            ids = [int(row["id"]) for row in rows]
            suggestions.append(
                MetabolismSuggestion(
                    action="split_thread",
                    severity=severity,
                    reason=f"`other` tag `{tag}`: {reason}",
                    memory_ids=ids[:20],
                    thread="other",
                    tag=tag,
                    stage=stage,
                )
            )

    tense_rows = conn.execute(
        """
        SELECT id FROM memories
         WHERE tension >= 0.8
           AND (confidence IS NULL OR confidence < 0.6)
           AND status = 'current'
         ORDER BY tension DESC
        """
    ).fetchall()
    if tense_rows:
        suggestions.append(
            MetabolismSuggestion(
                action="mark_review",
                severity="warning",
                reason="current memories with high tension and low confidence should be reviewed",
                memory_ids=[int(row["id"]) for row in tense_rows[:20]],
            )
        )

    return suggestions
