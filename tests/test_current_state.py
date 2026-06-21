from lmc5.fact_evolution import run_z_audit
from lmc5.store import MemoryStore


def test_refresh_current_state_materializes_fact_thread_and_recent_event(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        fact, _ = store.add_memory(
            title="Current endpoint",
            content="Use the v2 endpoint for local testing.",
            thread="engineering",
            fact_key="service.endpoint",
            confidence=0.9,
        )
        note, _ = store.add_memory(
            title="Rollback checklist",
            content="Keep rollback verification visible during deploys.",
            thread="engineering",
        )
        event, _ = store.log_event(
            role="user",
            content="Please keep the current endpoint and rollback state ready.",
            channel="session-a",
        )

        result = store.refresh_current_state(
            ttl_hours=2,
            fact_limit=5,
            thread_limit=5,
            event_limit=1,
            audit_limit=0,
            source="test",
        )
        rows = store.list_current_state(limit=10)
        surfaced = store.surface("endpoint rollback", limit=4)

    assert result["items_written"] >= 3
    assert result["category_counts"]["current_fact"] == 1
    assert result["category_counts"]["active_thread"] == 1
    assert result["category_counts"]["recent_event"] == 1

    by_key = {row["state_key"]: row for row in rows}
    fact_row = by_key["fact:service.endpoint"]
    assert fact_row["category"] == "current_fact"
    assert fact_row["confidence"] == 0.9
    assert fact_row["provenance"]["memory_ids"] == [fact.id]
    assert fact_row["expires_at"]

    thread_row = by_key["thread:engineering"]
    assert thread_row["category"] == "active_thread"
    assert set(thread_row["provenance"]["memory_ids"]) == {fact.id, note.id}

    event_row = by_key[f"recent_event:{event.id}"]
    assert event_row["category"] == "recent_event"
    assert event_row["provenance"]["event_ids"] == [event.id]

    assert "state" in surfaced
    assert any(row["state_key"] == "fact:service.endpoint" for row in surfaced["state"])


def test_current_state_includes_pending_z_audits_and_filters_expired_items(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        left, _ = store.add_memory(
            title="Endpoint v1",
            content="Use endpoint v1.",
            fact_key="service.endpoint",
        )
        right, _ = store.add_memory(
            title="Endpoint review",
            content="Maybe use endpoint v2.",
            fact_key="service.endpoint",
            status="review",
        )
        audit = run_z_audit(store, apply=True, redact=False)
        result = store.refresh_current_state(
            ttl_hours=1,
            fact_limit=0,
            thread_limit=0,
            event_limit=0,
            audit_limit=5,
            source="test",
        )
        rows = store.list_current_state(limit=5, category="pending_z_audit")

        store.conn.execute(
            "UPDATE current_state_items SET expires_at = '2000-01-01T00:00:00.000Z'"
        )
        live_after_expire = store.list_current_state(limit=5)
        expired = store.list_current_state(limit=5, include_expired=True)

    assert audit.audits_inserted == 1
    assert result["category_counts"] == {"pending_z_audit": 1}
    assert len(rows) == 1
    row = rows[0]
    assert row["provenance"]["audit_ids"]
    assert set(row["provenance"]["memory_ids"]) == {left.id, right.id}
    assert live_after_expire == []
    assert len(expired) == 1
