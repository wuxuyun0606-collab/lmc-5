from lmc5.consolidation import consolidate_events
from lmc5.store import MemoryStore


def test_consolidate_events_creates_chunks_and_observations(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        for index in range(5):
            store.log_event(
                role="user" if index % 2 == 0 else "assistant",
                content=f"Deployment discussion event {index}",
                channel="session-a",
            )

        result = consolidate_events(store, window_size=2, channel="session-a")

        chunks = store.conn.execute("SELECT * FROM event_chunks ORDER BY id").fetchall()
        links = store.conn.execute("SELECT * FROM chunk_events ORDER BY event_id").fetchall()
        observations = store.conn.execute(
            "SELECT * FROM memories WHERE thread = 'awareness' AND category = 'observation'"
        ).fetchall()

    assert result.chunks_created == 3
    assert result.observations_created == 3
    assert len(chunks) == 3
    assert len(links) == 5
    assert len(observations) == 3
    assert "Event chunk 1-2" in observations[0]["content"]


def test_consolidate_events_is_incremental(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        for index in range(3):
            store.log_event(role="user", content=f"Event {index}", channel="session-a")

        first = consolidate_events(store, window_size=10, channel="session-a")
        second = consolidate_events(store, window_size=10, channel="session-a")

        chunk_count = store.conn.execute("SELECT count(*) FROM event_chunks").fetchone()[0]
        observation_count = store.conn.execute(
            "SELECT count(*) FROM memories WHERE category = 'observation'"
        ).fetchone()[0]

    assert first.chunks_created == 1
    assert second.chunks_created == 0
    assert chunk_count == 1
    assert observation_count == 1


def test_consolidate_can_create_chunks_without_observations(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        store.log_event(role="user", content="Only keep chunk evidence.", channel="session-a")

        result = consolidate_events(
            store,
            window_size=10,
            channel="session-a",
            create_observations=False,
        )

        observation_count = store.conn.execute(
            "SELECT count(*) FROM memories WHERE category = 'observation'"
        ).fetchone()[0]

    assert result.chunks_created == 1
    assert result.observations_created == 0
    assert observation_count == 0

