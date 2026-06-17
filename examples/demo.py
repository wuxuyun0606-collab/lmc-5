"""Small end-to-end LMC-5 demo."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from lmc5 import MemoryStore


def main() -> None:
    with TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "demo.sqlite"
        with MemoryStore(db_path) as store:
            store.init()
            safety, _ = store.add_memory(
                title="Production safety boundary",
                content="Before production changes, confirm blast radius, rollback, and verification.",
                thread="safety",
                category="policy",
                tags=["production", "rollback"],
                fact_key="agent.safety.production_change",
                risk_level="high",
                urgency="high",
                response_tendency="Start with risk boundaries before implementation.",
            )
            verify, _ = store.add_memory(
                title="Post-change verification",
                content="Verify logs, metrics, and user-facing behavior after risky changes.",
                thread="engineering",
                category="checklist",
                tags=["verification"],
            )
            # Default graph expansion only walks safe relations. Review
            # relations such as supports/contradicts/cause_effect are kept for
            # audit workflows, not ordinary recall expansion.
            store.add_relation(safety.id, verify.id, "same_topic", reason="shared safety workflow")
            store.log_event(
                role="user",
                content="Can you recover the production rollback notes from earlier?",
                channel="demo",
            )

            for hit in store.recall("production", limit=3):
                print(f"{hit['score']:.2f} #{hit['id']} {hit['title']} ({', '.join(hit['reasons'])})")

            surfaced = store.surface("production rollback", limit=4)
            print(f"surface: {len(surfaced['memories'])} memories, {len(surfaced['events'])} events")


if __name__ == "__main__":
    main()
