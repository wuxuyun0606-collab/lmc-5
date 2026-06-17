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


def test_patrol_reports_relations_touching_non_live_memories(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        current, _ = store.add_memory(title="Current", content="Live memory.")
        old, _ = store.add_memory(
            title="Old fact",
            content="Old fact value.",
            fact_key="example.fact",
        )
        store.add_memory(
            title="New fact",
            content="New fact value.",
            fact_key="example.fact",
        )
        store.add_relation(current.id, old.id, "same_topic")

        suggestions = patrol(store.conn)

    assert any(
        current.id in item.memory_ids
        and old.id in item.memory_ids
        and "relations touch non-live memories" in item.reason
        for item in suggestions
    )


def test_patrol_reports_relation_self_loops_and_reciprocal_duplicates(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        first, _ = store.add_memory(title="First", content="A")
        second, _ = store.add_memory(title="Second", content="B")
        store.conn.execute(
            """
            INSERT INTO relations (source_id, target_id, relation_type, strength, reason)
            VALUES (?, ?, 'same_topic', 1.0, 'legacy self-loop')
            """,
            (first.id, first.id),
        )
        store.conn.execute(
            """
            INSERT INTO relations (source_id, target_id, relation_type, strength, reason)
            VALUES (?, ?, 'same_topic', 1.0, 'legacy forward')
            """,
            (first.id, second.id),
        )
        store.conn.execute(
            """
            INSERT INTO relations (source_id, target_id, relation_type, strength, reason)
            VALUES (?, ?, 'same_topic', 1.0, 'legacy reverse')
            """,
            (second.id, first.id),
        )
        store.conn.commit()

        suggestions = patrol(store.conn)

    assert any("self-loops" in item.reason and first.id in item.memory_ids for item in suggestions)
    assert any(
        "reciprocal duplicate symmetric relations" in item.reason
        and first.id in item.memory_ids
        and second.id in item.memory_ids
        for item in suggestions
    )


def test_patrol_reports_orphaned_relations_from_legacy_databases(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        store.conn.commit()
        store.conn.execute("PRAGMA foreign_keys = OFF")
        store.conn.execute(
            """
            INSERT INTO relations (source_id, target_id, relation_type, strength, reason)
            VALUES (12345, 67890, 'same_topic', 1.0, 'legacy orphan')
            """
        )
        store.conn.commit()
        store.conn.execute("PRAGMA foreign_keys = ON")

        suggestions = patrol(store.conn)

    assert any("orphaned relations" in item.reason for item in suggestions)
