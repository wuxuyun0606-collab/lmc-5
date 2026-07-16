"""Export a tiny Y-line graph JSON for the static viewer.

Run from the lmc-5 directory:

    PYTHONPATH=src python examples/y_line_export.py --demo > examples/y_line_sample.json

For a real SQLite store:

    PYTHONPATH=src python examples/y_line_export.py path/to/lmc5.sqlite --seed 12 > y_line_graph.json

The output shape is intentionally frontend-friendly and read-only:
`nodes` are public memory records, and `edges` carry typed relation evidence.
Review edges are exported, but the viewer does not auto-expand through them.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from lmc5 import MemoryStore
from lmc5.models import REVIEW_RELATION_TYPES


def _node_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "title": row["title"],
        "summary": row["content"],
        "thread": row["thread"],
        "category": row["category"],
        "status": row["status"],
        "risk_level": row["risk_level"],
        "urgency": row["urgency"],
        "fact_key": row["fact_key"],
        "active_fact": bool(row["active_fact"]),
        "tags": json.loads(row["tags_json"] or "[]"),
    }


def _edge_from_row(row: sqlite3.Row) -> dict[str, Any]:
    relation_type = row["relation_type"]
    return {
        "id": str(row["id"]),
        "source": str(row["source_id"]),
        "target": str(row["target_id"]),
        "relation_type": relation_type,
        "strength": float(row["strength"]),
        "review_required": relation_type in REVIEW_RELATION_TYPES,
        "source_count": 1,
        "reason": row["reason"] or "",
    }


def _collect_neighbor_ids(conn: sqlite3.Connection, seed_id: int, hops: int) -> set[int]:
    seen = {seed_id}
    frontier = {seed_id}
    for _depth in range(hops):
        if not frontier:
            break
        placeholders = ",".join("?" for _ in frontier)
        rows = conn.execute(
            f"""
            SELECT source_id, target_id, relation_type
            FROM relations
            WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders})
            """,
            tuple(frontier) + tuple(frontier),
        ).fetchall()
        next_frontier: set[int] = set()
        for row in rows:
            if row["relation_type"] in REVIEW_RELATION_TYPES:
                continue
            source_id = int(row["source_id"])
            target_id = int(row["target_id"])
            other_id = target_id if source_id in frontier else source_id
            if other_id not in seen:
                next_frontier.add(other_id)
        seen.update(next_frontier)
        frontier = next_frontier
    return seen


def export_sqlite_graph(db_path: Path, seed_id: int | None, hops: int) -> dict[str, Any]:
    if db_path is None:
        raise SystemExit("Pass a SQLite db_path, or use --demo.")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        if seed_id is None:
            row = conn.execute(
                "SELECT id FROM memories WHERE status = 'current' ORDER BY updated_at DESC, id DESC LIMIT 1"
            ).fetchone()
            if row is None:
                raise SystemExit("No memories found in SQLite store.")
            seed_id = int(row["id"])

        node_ids = _collect_neighbor_ids(conn, seed_id, hops)
        placeholders = ",".join("?" for _ in node_ids)
        node_rows = conn.execute(
            f"""
            SELECT id, title, content, thread, category, status, risk_level, urgency,
                   fact_key, active_fact, tags_json
            FROM memories
            WHERE id IN ({placeholders})
            ORDER BY id
            """,
            tuple(sorted(node_ids)),
        ).fetchall()
        edge_rows = conn.execute(
            f"""
            SELECT id, source_id, target_id, relation_type, strength, reason
            FROM relations
            WHERE source_id IN ({placeholders}) AND target_id IN ({placeholders})
            ORDER BY id
            """,
            tuple(sorted(node_ids)) + tuple(sorted(node_ids)),
        ).fetchall()
        return {
            "meta": {
                "title": "LMC-5 Y-Line Export",
                "seed_id": str(seed_id),
                "hops": hops,
                "notes": "Review edges are exported for audit, not automatic expansion.",
            },
            "nodes": [_node_from_row(row) for row in node_rows],
            "edges": [_edge_from_row(row) for row in edge_rows],
        }
    finally:
        conn.close()


def build_demo_graph() -> dict[str, Any]:
    with TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "y-line-demo.sqlite"
        with MemoryStore(db_path) as store:
            store.init()
            seed, _ = store.add_memory(
                title="生产发布前必须准备回滚与影响面检查",
                content="高风险发布前先确认回滚路径、影响范围、验证指标，再执行变更。",
                thread="安全线",
                category="policy",
                risk_level="high",
                urgency="high",
                fact_key="deploy.risk.rollback_policy",
                tags=["deployment", "rollback", "safety"],
            )
            verify, _ = store.add_memory(
                title="发布后验证日志、指标和用户可见行为",
                content="高风险变更完成后，用日志、指标和实际页面行为三方验证，不只看命令成功。",
                thread="工程线",
                category="checklist",
                risk_level="medium",
                tags=["verification", "logs", "metrics"],
            )
            frontend, _ = store.add_memory(
                title="静态前端修复后 bump 资源版本",
                content="修复 JS/CSS 后更新资源版本，避免浏览器继续读旧缓存。",
                thread="前端线",
                category="frontend",
                tags=["frontend", "cache"],
            )
            review, _ = store.add_memory(
                title="冲突边只进入审计，不自动改事实",
                content="contradicts/supports/cause_effect 属于 review 关系，展示给人看，但不默认参与自动扩展。",
                thread="工程线",
                category="review",
                risk_level="medium",
                tags=["y-axis", "review"],
            )
            store.add_relation(seed.id, verify.id, "same_issue", strength=0.92, reason="高风险发布闭环。")
            store.add_relation(verify.id, frontend.id, "same_tool", strength=0.62, reason="都涉及发布后验证。")
            store.add_relation(seed.id, review.id, "supports", strength=0.84, reason="安全约束需要审计。")
        return export_sqlite_graph(db_path, seed_id=seed.id, hops=2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db_path", nargs="?", type=Path, help="Path to an LMC-5 SQLite database.")
    parser.add_argument("--seed", type=int, help="Seed memory id. Defaults to most recently updated current memory.")
    parser.add_argument("--hops", type=int, default=1, choices=(1, 2), help="Safe-edge expansion depth.")
    parser.add_argument("--demo", action="store_true", help="Export a temporary demo graph.")
    args = parser.parse_args()

    graph = build_demo_graph() if args.demo else export_sqlite_graph(args.db_path, args.seed, args.hops)
    print(json.dumps(graph, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
