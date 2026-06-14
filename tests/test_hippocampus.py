from lmc5.consolidation import consolidate_events
from lmc5.hippocampus import MemoryCandidate, run_hippocampus
from lmc5.store import MemoryStore


def _seed_chunks(store: MemoryStore) -> None:
    for index in range(4):
        store.log_event(
            role="user" if index % 2 == 0 else "assistant",
            content=f"Production rollback discussion event {index}",
            channel="nightly",
        )
    consolidate_events(store, window_size=2, channel="nightly", create_observations=False)


def test_hippocampus_dry_run_does_not_write_memories(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        _seed_chunks(store)

        result = run_hippocampus(
            store,
            channel="nightly",
            min_importance=1,
            max_promote=2,
            apply=False,
        )

        memory_count = store.conn.execute("SELECT count(*) FROM memories").fetchone()[0]
        relation_count = store.conn.execute("SELECT count(*) FROM relations").fetchone()[0]

    assert result.chunks_seen == 2
    assert result.promote_ready == 2
    assert result.inserted == 0
    assert memory_count == 0
    assert relation_count == 0


def test_hippocampus_apply_promotes_review_memories_and_safe_relations(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        _seed_chunks(store)

        result = run_hippocampus(
            store,
            channel="nightly",
            min_importance=1,
            max_promote=2,
            apply=True,
        )

        memories = store.conn.execute(
            "SELECT * FROM memories WHERE source LIKE 'hippocampus:%' ORDER BY id"
        ).fetchall()
        relations = store.conn.execute("SELECT * FROM relations ORDER BY id").fetchall()

    assert result.inserted == 2
    assert len(memories) == 2
    assert {row["status"] for row in memories} == {"review"}
    assert {row["relation_type"] for row in relations}.issubset(
        {"same_event", "same_topic", "temporal_sequence", "derived_from"}
    )


def test_hippocampus_keeps_dangerous_relation_hints_for_review(tmp_path):
    db = tmp_path / "memory.sqlite"

    def proposer(_chunks):
        return [
            MemoryCandidate(
                title="Rollback policy changed",
                content="The rollback policy may conflict with older deployment notes.",
                tags=["rollback", "deployment"],
                importance=9,
                source_chunk_ids=[1],
                relation_hints=[
                    {
                        "target_id": 1,
                        "relation_type": "contradicts",
                        "strength": 0.8,
                        "reason": "candidate says it may conflict",
                    }
                ],
            )
        ]

    with MemoryStore(db) as store:
        store.init()
        existing, _ = store.add_memory(
            title="Deployment rollback notes",
            content="Always keep a rollback plan.",
            tags=["rollback", "deployment"],
        )
        _seed_chunks(store)

        result = run_hippocampus(
            store,
            channel="nightly",
            min_importance=1,
            max_promote=1,
            apply=True,
            proposer=proposer,
        )

        relations = store.conn.execute("SELECT * FROM relations ORDER BY id").fetchall()

    assert existing.id == 1
    assert result.review_relations
    assert result.review_relations[0]["relation_type"] == "contradicts"
    assert all(row["relation_type"] != "contradicts" for row in relations)
