from lmc5.store import MemoryStore


def test_add_memory_is_idempotent(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        first, created_first = store.add_memory(title="A", content="B")
        second, created_second = store.add_memory(title="A", content="B")

    assert created_first is True
    assert created_second is False
    assert first.id == second.id


def test_current_fact_supersedes_previous_fact(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        old, _ = store.add_memory(
            title="Old fact",
            content="Old value",
            fact_key="agent.example.fact",
        )
        new, _ = store.add_memory(
            title="New fact",
            content="New value",
            fact_key="agent.example.fact",
        )
        old_after = store.get_memory(old.id)
        new_after = store.get_memory(new.id)

    assert old_after.status == "superseded"
    assert old_after.active_fact is False
    assert new_after.status == "current"
    assert new_after.active_fact is True


def test_recall_redacts_output(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        fake_dsn = "postgresql" + "://user:pass@127.0.0.1:5432/app"
        store.add_memory(
            title="Secret endpoint",
            content=f"Use {fake_dsn} only locally",
            risk_level="high",
        )
        rows = store.recall("endpoint", redact=True)

    assert rows
    assert "user:pass" not in rows[0]["content"]
    assert "127.0.0.1" not in rows[0]["content"]
    assert "postgresql://[REDACTED_DSN]" in rows[0]["content"]


def test_recall_expands_one_hop_relations(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        anchor, _ = store.add_memory(
            title="Deployment rollback policy",
            content="Always define rollback before deployment.",
            thread="safety",
            tags=["deploy"],
        )
        related, _ = store.add_memory(
            title="Verification checklist",
            content="After rollback, verify logs, metrics, and user-facing behavior.",
            thread="engineering",
        )
        store.add_relation(anchor.id, related.id, "supports", reason="verification supports rollback")

        rows = store.recall("deployment", limit=2)

    ids = [row["id"] for row in rows]
    assert anchor.id in ids
    assert related.id in ids
    related_row = next(row for row in rows if row["id"] == related.id)
    assert related_row["relation_score"] > 0
    assert related_row["related_from"] == [anchor.id]


def test_recall_expands_two_hop_relations_with_decay(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        anchor, _ = store.add_memory(
            title="Deployment rollback policy",
            content="Always define rollback before deployment.",
            thread="safety",
        )
        first_hop, _ = store.add_memory(
            title="Rollback verification",
            content="Verify logs after rollback.",
            thread="engineering",
        )
        second_hop, _ = store.add_memory(
            title="Monitoring checklist",
            content="Check metrics and user-facing behavior.",
            thread="engineering",
        )
        store.add_relation(anchor.id, first_hop.id, "same_topic", strength=1.0)
        store.add_relation(first_hop.id, second_hop.id, "same_topic", strength=1.0)

        rows = store.recall("deployment", limit=3)

    ids = [row["id"] for row in rows]
    assert second_hop.id in ids
    first_row = next(row for row in rows if row["id"] == first_hop.id)
    second_row = next(row for row in rows if row["id"] == second_hop.id)
    assert first_row["relation_score"] > second_row["relation_score"] > 0
    assert second_row["related_from"] == [first_hop.id]
    assert second_row["reasons"] == [f"related:2:same_topic:{first_hop.id}"]


def test_recall_weights_relation_types(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        anchor, _ = store.add_memory(
            title="Deployment rollback policy",
            content="Always define rollback before deployment.",
            thread="safety",
        )
        same_topic, _ = store.add_memory(
            title="Rollback playbook",
            content="Keep the rollback playbook nearby.",
            thread="engineering",
        )
        contradiction, _ = store.add_memory(
            title="Conflicting rollback note",
            content="This note conflicts with rollback guidance.",
            thread="engineering",
        )
        store.add_relation(anchor.id, same_topic.id, "same_topic", strength=0.8)
        store.add_relation(anchor.id, contradiction.id, "contradicts", strength=0.8)

        rows = store.recall("deployment", limit=3)

    same_topic_row = next(row for row in rows if row["id"] == same_topic.id)
    contradiction_row = next(row for row in rows if row["id"] == contradiction.id)
    assert same_topic_row["relation_score"] > contradiction_row["relation_score"]
    assert same_topic_row["reasons"] == [f"related:1:same_topic:{anchor.id}"]
    assert contradiction_row["reasons"] == [f"related:1:contradicts:{anchor.id}"]


def test_list_relations_filters_by_memory(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        first, _ = store.add_memory(title="First", content="A")
        second, _ = store.add_memory(title="Second", content="B")
        third, _ = store.add_memory(title="Third", content="C")
        store.add_relation(first.id, second.id, "same_issue")
        store.add_relation(second.id, third.id, "supports")

        rows = store.list_relations(first.id)

    assert len(rows) == 1
    assert rows[0].source_id == first.id


def test_stats_reports_counts_and_vector_coverage(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        memory, _ = store.add_memory(
            title="Deployment rollback policy",
            content="Confirm rollback before deployment.",
            thread="safety",
            category="policy",
            fact_key="agent.safety.rollback",
        )
        event, _ = store.log_event(
            role="user",
            content="Can you recover rollback notes?",
            channel="session-a",
        )
        store.upsert_vector(
            owner_type="memory",
            owner_id=memory.id,
            vector=[1.0, 0.0],
            provider="local",
            model="manual",
        )
        store.upsert_vector(
            owner_type="event",
            owner_id=event.id,
            vector=[0.0, 1.0],
            provider="local",
            model="manual",
        )

        stats = store.stats()

    assert stats["memory_count"] == 1
    assert stats["event_count"] == 1
    assert stats["vector_count"] == 2
    assert stats["current_fact_count"] == 1
    assert stats["status_counts"] == {"current": 1}
    assert stats["top_threads"] == {"safety": 1}
    assert stats["top_categories"] == {"policy": 1}
    assert stats["event_role_counts"] == {"user": 1}
    assert stats["top_event_channels"] == {"session-a": 1}
    assert stats["vector_owner_counts"] == {"event": 1, "memory": 1}
    assert stats["memory_vector_coverage"] == {"indexed": 1, "total": 1, "ratio": 1.0}
    assert stats["event_vector_coverage"] == {"indexed": 1, "total": 1, "ratio": 1.0}
