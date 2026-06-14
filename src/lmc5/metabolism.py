"""Read-only metabolism patrol checks."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict

from .models import MetabolismSuggestion


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
