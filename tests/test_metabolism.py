from lmc5.metabolism import patrol
from lmc5.store import MemoryStore


def test_patrol_reports_other_thread_split_candidates(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        for index in range(5):
            store.add_memory(
                title=f"Research note {index}",
                content=f"Observation {index}",
                thread="other",
                category="research",
                tags=["papers"],
            )

        suggestions = patrol(store.conn)

    assert any(item.action == "split_thread" and item.category == "research" for item in suggestions)


def test_patrol_reports_high_tension_low_confidence(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        record, _ = store.add_memory(
            title="Unresolved risk",
            content="This needs another review.",
            tension=0.9,
            confidence=0.4,
        )

        suggestions = patrol(store.conn)

    assert any(record.id in item.memory_ids and item.action == "mark_review" for item in suggestions)
