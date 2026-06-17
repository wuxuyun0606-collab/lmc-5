"""Executable check for typed two-hop Y-axis graph expansion.

Run with:

    PYTHONPATH=src python examples/two_hop_graph.py

This is intentionally tiny. It proves the core graph contract before a larger
deployment hides mistakes behind thousands of rows.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from lmc5 import MemoryStore


def main() -> None:
    with TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "two-hop.sqlite"
        with MemoryStore(db_path) as store:
            store.init()

            seed, _ = store.add_memory(
                title="Seed: production rollback policy",
                content="Before production deployment, prepare rollback and blast-radius checks.",
                thread="safety",
                category="policy",
                risk_level="high",
                urgency="high",
            )
            hop1, _ = store.add_memory(
                title="Hop 1: verification checklist",
                content="After risky changes, verify logs, metrics, and user-facing behavior.",
                thread="engineering",
                category="checklist",
            )
            hop2, _ = store.add_memory(
                title="Hop 2: incident retrospective",
                content="Retrospectives should connect the original policy to the verification result.",
                thread="engineering",
                category="review",
            )
            review_only, _ = store.add_memory(
                title="Blocked: review-only support claim",
                content="A support claim may be useful evidence, but it needs audit before graph recall.",
                thread="engineering",
            )
            weak, _ = store.add_memory(
                title="Blocked: weak relation",
                content="This memory is only loosely related and should not cross the hop threshold.",
                thread="engineering",
            )
            superseded, _ = store.add_memory(
                title="Blocked: superseded endpoint",
                content="Old rollback notes are preserved, but should not surface through live graph walk.",
                thread="engineering",
                status="superseded",
            )

            store.add_relation(seed.id, hop1.id, "same_topic", strength=0.9)
            store.add_relation(hop1.id, hop2.id, "same_event", strength=0.8)
            store.add_relation(seed.id, review_only.id, "supports", strength=1.0)
            store.add_relation(seed.id, weak.id, "same_topic", strength=0.2)
            store.add_relation(seed.id, superseded.id, "same_topic", strength=0.9)

            hits = store.recall("rollback", limit=10)
            titles = {hit["title"] for hit in hits}
            required = {
                "Seed: production rollback policy",
                "Hop 1: verification checklist",
                "Hop 2: incident retrospective",
            }
            blocked = {
                "Blocked: review-only support claim",
                "Blocked: weak relation",
                "Blocked: superseded endpoint",
            }

            missing = required - titles
            leaked = blocked & titles
            if missing or leaked:
                raise SystemExit(f"graph contract failed: missing={missing}, leaked={leaked}")

            print("OK: two-hop typed graph expansion is working")
            for hit in hits:
                print(
                    f"{hit['score']:.3f} relation={hit['relation_score']:.3f} "
                    f"{hit['title']} :: {', '.join(hit['reasons'])}"
                )


if __name__ == "__main__":
    main()
