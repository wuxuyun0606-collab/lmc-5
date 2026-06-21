from lmc5.store import MemoryStore


def test_memory_entities_are_indexed_and_visible(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        memory, _ = store.add_memory(
            title="Claude-p unblock note",
            content="Keep the local Mac runner separate from VPS runner.",
            thread="engineering",
            tags=["claude-p"],
            fact_key="agent.runner.claude_p",
        )

        entities = store.list_entities(memory_id=memory.id)
        stats = store.stats()

    keys = {row["entity_key"] for row in entities}
    assert "claude-p" in keys
    assert "agent.runner.claude_p" in keys
    assert "engineering" in keys
    assert stats["entity_count"] == len(entities)


def test_entity_boost_is_explained_and_can_be_disabled(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        memory, _ = store.add_memory(
            title="Claude-p unblock note",
            content="Keep the local Mac runner separate from VPS runner.",
            thread="engineering",
            tags=["claude-p"],
        )

        boosted = store.recall("claude-p", limit=1, trace=False)
        unboosted = store.recall(
            "claude-p",
            limit=1,
            entity_boost=False,
            trace=False,
        )

    assert boosted[0]["id"] == memory.id
    assert boosted[0]["score_breakdown"]["entity_boost"] > 0
    assert "entity:tag:claude-p" in boosted[0]["reasons"]
    assert boosted[0]["trace"]["entity_matches"] == ["tag:claude-p"]

    assert unboosted[0]["id"] == memory.id
    assert unboosted[0]["score_breakdown"]["entity_boost"] == 0
    assert "entity:tag:claude-p" not in unboosted[0]["reasons"]
