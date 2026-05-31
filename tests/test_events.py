from lmc5.store import MemoryStore


def test_log_event_is_idempotent(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        first, created_first = store.log_event(
            role="user",
            content="Need to recover the deployment plan.",
            channel="session-a",
            metadata={"source": "test"},
        )
        second, created_second = store.log_event(
            role="user",
            content="Need to recover the deployment plan.",
            channel="session-a",
            metadata={"source": "test"},
        )

    assert created_first is True
    assert created_second is False
    assert first.id == second.id


def test_search_events_redacts_output(tmp_path):
    db = tmp_path / "memory.sqlite"
    fake_key = "sk-" + "123456789abcdef"
    with MemoryStore(db) as store:
        store.init()
        store.log_event(
            role="tool",
            content=f"Tool failed with api_key={fake_key}",
            channel="debug",
        )

        rows = store.search_events("failed", redact=True)

    assert rows
    assert fake_key not in rows[0]["content"]
    assert "[REDACTED]" in rows[0]["content"]


def test_surface_combines_curated_memory_and_raw_events(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        memory, _ = store.add_memory(
            title="Rollback policy",
            content="Confirm rollback before deployment.",
            thread="safety",
            risk_level="high",
        )
        event, _ = store.log_event(
            role="user",
            content="Can you check the deployment rollback notes from earlier?",
            channel="session-a",
        )

        result = store.surface("deployment rollback", limit=4)

    assert result["query"] == "deployment rollback"
    assert any(row["id"] == memory.id for row in result["memories"])
    assert any(row["id"] == event.id for row in result["events"])


def test_list_events_filters_channel(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        kept, _ = store.log_event(role="user", content="A", channel="kept")
        store.log_event(role="user", content="B", channel="other")

        rows = store.list_events(channel="kept")

    assert [row.id for row in rows] == [kept.id]
