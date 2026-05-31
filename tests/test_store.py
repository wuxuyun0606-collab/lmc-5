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
