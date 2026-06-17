"""Read-only metabolism patrol checks."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict

from .models import MetabolismSuggestion, SYMMETRIC_RELATION_TYPES


def patrol(conn: sqlite3.Connection, *, split_threshold: int = 5) -> list[MetabolismSuggestion]:
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
        "SELECT id, category, tags_json FROM memories WHERE thread = 'other'"
    ).fetchall()
    category_ids: dict[str, list[int]] = defaultdict(list)
    tag_ids: dict[str, list[int]] = defaultdict(list)
    tag_counter: Counter[str] = Counter()
    for row in other_rows:
        category_ids[row["category"]].append(int(row["id"]))
        for tag in json.loads(row["tags_json"] or "[]"):
            tag_counter[tag] += 1
            tag_ids[tag].append(int(row["id"]))

    for category, ids in category_ids.items():
        if category and len(ids) >= split_threshold:
            suggestions.append(
                MetabolismSuggestion(
                    action="split_thread",
                    severity="info",
                    reason=f"`other` contains {len(ids)} memories in category `{category}`",
                    memory_ids=ids[:20],
                    thread="other",
                    category=category,
                )
            )

    for tag, count in tag_counter.items():
        if count >= split_threshold:
            suggestions.append(
                MetabolismSuggestion(
                    action="split_thread",
                    severity="info",
                    reason=f"`other` contains {count} memories tagged `{tag}`",
                    memory_ids=tag_ids[tag][:20],
                    thread="other",
                    tag=tag,
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
