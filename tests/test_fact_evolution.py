from lmc5.fact_evolution import run_z_audit
from lmc5.metabolism import patrol
from lmc5.store import MemoryStore


def test_z_audit_dry_run_lists_conflicts_without_writing(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        old, _ = store.add_memory(
            title="Old rollback policy",
            content="Rollback requires manual approval.",
            fact_key="agent.safety.rollback",
            status="review",
        )
        new, _ = store.add_memory(
            title="New rollback policy",
            content="Rollback can proceed after automated checks pass.",
            fact_key="agent.safety.rollback",
            status="review",
        )

        result = run_z_audit(store, apply=False)
        audit_count = store.conn.execute("SELECT count(*) FROM z_conflict_audits").fetchone()[0]

    assert result.candidates_seen == 1
    assert result.audits_inserted == 0
    assert audit_count == 0
    assert result.candidates[0]["left_memory_id"] == old.id
    assert result.candidates[0]["right_memory_id"] == new.id


def test_z_audit_apply_writes_pending_audit_without_superseding(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        left, _ = store.add_memory(
            title="Rollback note A",
            content="Rollback should wait for manual approval.",
            fact_key="agent.safety.rollback",
            status="review",
        )
        right, _ = store.add_memory(
            title="Rollback note B",
            content="Rollback should proceed after checks.",
            fact_key="agent.safety.rollback",
            status="review",
        )

        result = run_z_audit(store, apply=True)
        audits = store.conn.execute("SELECT * FROM z_conflict_audits").fetchall()
        left_after = store.get_memory(left.id)
        right_after = store.get_memory(right.id)

    assert result.audits_inserted == 1
    assert len(audits) == 1
    assert audits[0]["status"] == "pending"
    assert audits[0]["verdict"] == "pending"
    assert left_after.status == "review"
    assert right_after.status == "review"
    assert left_after.active_fact is True
    assert right_after.active_fact is True


def test_z_audit_uses_contradicts_relations_as_candidates(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        left, _ = store.add_memory(title="Policy A", content="Use path A.")
        right, _ = store.add_memory(title="Policy B", content="Use path B.")
        store.add_relation(left.id, right.id, "contradicts", strength=0.9)

        result = run_z_audit(store, apply=False)

    assert result.candidates_seen == 1
    assert result.candidates[0]["source"] == "contradicts_relation"
    assert result.candidates[0]["left_memory_id"] == left.id
    assert result.candidates[0]["right_memory_id"] == right.id


def test_z_audit_ignores_contradicts_relations_with_non_live_endpoints(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        current, _ = store.add_memory(title="Policy A", content="Use path A.")
        inactive, _ = store.add_memory(
            title="Inactive policy",
            content="Use the inactive path.",
            fact_key="agent.inactive_path",
            active_fact=False,
        )
        store.add_relation(current.id, inactive.id, "contradicts", strength=0.9)

        result = run_z_audit(store, apply=False)

    assert result.candidates_seen == 0


def test_patrol_reports_pending_z_audits(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        left, _ = store.add_memory(
            title="Policy A",
            content="Use path A.",
            fact_key="agent.path",
            status="review",
        )
        right, _ = store.add_memory(
            title="Policy B",
            content="Use path B.",
            fact_key="agent.path",
            status="review",
        )
        run_z_audit(store, apply=True)

        suggestions = patrol(store.conn)

    assert any(
        left.id in item.memory_ids
        and right.id in item.memory_ids
        and "Z-axis conflict audits are pending" in item.reason
        for item in suggestions
    )
