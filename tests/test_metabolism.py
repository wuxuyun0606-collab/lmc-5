from lmc5.metabolism import patrol
from lmc5.scoring import metabolic_gate
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

    assert any(
        item.action == "split_thread"
        and item.category == "research"
        and item.stage == "candidate_line"
        for item in suggestions
    )


def test_patrol_reports_other_thread_observation_clusters(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        for index in range(3):
            store.add_memory(
                title=f"Sketch note {index}",
                content=f"Observation {index}",
                thread="other",
                category="sketches",
            )

        suggestions = patrol(store.conn)

    assert any(
        item.category == "sketches" and item.stage == "observe_cluster"
        for item in suggestions
    )


def test_patrol_reports_formal_other_thread_candidates(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        ids = []
        for index in range(8):
            record, _ = store.add_memory(
                title=f"Long arc note {index}",
                content=f"Observation {index}",
                thread="other",
                category="long_arc",
            )
            ids.append(record.id)
        store.conn.execute(
            "UPDATE memories SET created_at = '2026-01-01T00:00:00Z', hit_count = 1 WHERE id = ?",
            (ids[0],),
        )
        store.conn.execute(
            "UPDATE memories SET created_at = '2026-01-20T00:00:00Z', hit_count = 1 WHERE id = ?",
            (ids[-1],),
        )
        store.conn.commit()

        suggestions = patrol(store.conn)

    assert any(
        item.category == "long_arc"
        and item.stage == "formal_line_candidate"
        and item.severity == "warning"
        for item in suggestions
    )


def test_metabolic_gate_blocks_noise_from_recall_and_surface():
    noisy = {
        "title": "Temporary debug log",
        "content": "One-off trace output.",
        "source": "debug",
        "category": "log",
        "status": "current",
    }
    assert metabolic_gate(noisy, mode="recall")["allowed"] is False
    assert metabolic_gate(noisy, mode="surface")["allowed"] is False


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
