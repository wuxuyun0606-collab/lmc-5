from lmc5.store import MemoryStore


def test_temporal_intent_boosts_recent_live_memories(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        old, _ = store.add_memory(
            title="Rollback status",
            content="Rollback status from the earlier deployment window.",
            thread="engineering",
        )
        new, _ = store.add_memory(
            title="Rollback status",
            content="Rollback status from the current deployment window.",
            thread="engineering",
        )
        store.conn.execute(
            """
            UPDATE memories
               SET created_at = '2020-01-01T00:00:00.000Z',
                   updated_at = '2020-01-01T00:00:00.000Z'
             WHERE id = ?
            """,
            (old.id,),
        )
        store.conn.commit()

        boosted = store.recall(
            "latest rollback",
            limit=2,
            entity_boost=False,
            trace=False,
        )
        unboosted = store.recall(
            "latest rollback",
            limit=2,
            entity_boost=False,
            temporal_boost=False,
            trace=False,
        )

    assert {row["id"] for row in boosted} == {old.id, new.id}
    new_row = next(row for row in boosted if row["id"] == new.id)
    old_row = next(row for row in boosted if row["id"] == old.id)
    assert new_row["score_breakdown"]["temporal_boost"] > 0
    assert old_row["score_breakdown"]["temporal_boost"] == 0
    assert new_row["trace"]["temporal_intent"] == "recent"

    assert all(row["score_breakdown"]["temporal_boost"] == 0 for row in unboosted)
