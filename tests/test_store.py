import pytest

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


def test_recall_only_returns_live_current_memories(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        review, _ = store.add_memory(
            title="Review endpoint",
            content="Pending endpoint note.",
            status="review",
        )
        historical, _ = store.add_memory(
            title="Historical endpoint",
            content="Historical endpoint note.",
            status="historical",
        )
        archived, _ = store.add_memory(
            title="Archived endpoint",
            content="Archived endpoint note.",
            status="archived",
        )
        inactive, _ = store.add_memory(
            title="Inactive endpoint",
            content="Inactive endpoint note.",
            fact_key="service.inactive_endpoint",
            active_fact=False,
        )
        old, _ = store.add_memory(
            title="Old endpoint",
            content="Retired endpoint value.",
            fact_key="service.endpoint",
        )
        current, _ = store.add_memory(
            title="Current endpoint",
            content="Current endpoint value.",
            fact_key="service.endpoint",
        )

        rows = store.recall("endpoint", limit=10)

    ids = {row["id"] for row in rows}
    assert current.id in ids
    assert review.id not in ids
    assert historical.id not in ids
    assert archived.id not in ids
    assert inactive.id not in ids
    assert old.id not in ids


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("valence", -1.1),
        ("valence", 1.1),
        ("arousal", -0.1),
        ("arousal", 1.1),
        ("tension", -0.1),
        ("tension", 1.1),
        ("confidence", -0.1),
        ("confidence", 1.1),
    ],
)
def test_add_memory_validates_e_axis_ranges(tmp_path, field, value):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        with pytest.raises(ValueError, match=field):
            store.add_memory(title="Bad E axis", content="Out of range.", **{field: value})


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


def test_recall_reinforcement_updates_hit_count_and_last_hit_at(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        memory, _ = store.add_memory(
            title="Deployment note",
            content="Confirm deployment rollback.",
        )

        store.recall("deployment", trace=False)
        refreshed = store.get_memory(memory.id)

    assert refreshed.hit_count == 1
    assert refreshed.last_hit_at is not None
    assert refreshed.to_public_dict()["last_hit_at"] == refreshed.last_hit_at


def test_recall_filters_quarantined_noise_memories(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        noisy, _ = store.add_memory(
            title="Endpoint debug trace",
            content="Temporary endpoint debug output.",
            source="debug",
            category="log",
        )
        useful, _ = store.add_memory(
            title="Endpoint runbook",
            content="Use the endpoint runbook for rollback decisions.",
            category="knowledge",
        )

        rows = store.recall("endpoint", limit=10, trace=False)

    ids = {row["id"] for row in rows}
    assert useful.id in ids
    assert noisy.id not in ids
    useful_row = next(row for row in rows if row["id"] == useful.id)
    assert useful_row["score_breakdown"]["m_gate_bucket"] == "retain"


def test_surface_uses_stricter_metabolic_gate(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        raw, _ = store.add_memory(
            title="Endpoint raw conversation",
            content="Raw endpoint conversation excerpt.",
            source="conversation",
            category="conversation",
        )
        useful, _ = store.add_memory(
            title="Endpoint policy",
            content="Endpoint policy should surface for rollback planning.",
            category="knowledge",
        )

        result = store.surface(
            "endpoint",
            limit=4,
            memory_limit=4,
            event_limit=1,
            include_state=False,
        )

    ids = {row["id"] for row in result["memories"]}
    assert useful.id in ids
    assert raw.id not in ids


def test_recall_expands_one_hop_safe_relations(tmp_path):
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
        store.add_relation(anchor.id, related.id, "same_topic", reason="verification supports rollback")

        rows = store.recall("deployment", limit=2)

    ids = [row["id"] for row in rows]
    assert anchor.id in ids
    assert related.id in ids
    related_row = next(row for row in rows if row["id"] == related.id)
    assert related_row["relation_score"] > 0
    assert related_row["related_from"] == [anchor.id]


def test_recall_records_explain_trace_for_injected_hits(tmp_path):
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
            content="Verify logs, metrics, and user-facing behavior.",
            thread="engineering",
        )
        store.add_relation(anchor.id, related.id, "same_topic", reason="verification supports rollback")

        rows = store.recall("deployment", limit=2)
        traces = store.list_recall_traces(limit=10)

    assert rows
    assert all("score_breakdown" in row for row in rows)
    assert all(row["score_breakdown"]["final"] == row["score"] for row in rows)

    related_row = next(row for row in rows if row["id"] == related.id)
    assert related_row["score_breakdown"]["relation"] == related_row["relation_score"]
    assert related_row["trace"]["recall_run_id"]
    assert related_row["trace"]["rank"] >= 1

    trace_ids = {row["memory_id"] for row in traces}
    assert {anchor.id, related.id} <= trace_ids
    trace_row = next(row for row in traces if row["memory_id"] == related.id)
    assert trace_row["query_preview"] == "deployment"
    assert trace_row["injected"] is True
    assert trace_row["score_breakdown"]["relation"] == related_row["relation_score"]
    assert trace_row["related_from"] == [anchor.id]


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


def test_recall_does_not_auto_expand_review_relations(tmp_path):
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

    ids = [row["id"] for row in rows]
    same_topic_row = next(row for row in rows if row["id"] == same_topic.id)
    assert contradiction.id not in ids
    assert same_topic_row["reasons"] == [f"related:1:same_topic:{anchor.id}"]


def test_recall_does_not_expand_archived_superseded_or_inactive_fact_targets(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        anchor, _ = store.add_memory(
            title="Deployment rollback policy",
            content="Always define rollback before deployment.",
            thread="safety",
        )
        archived, _ = store.add_memory(
            title="Archived rollback note",
            content="Old archived rollback note.",
            status="archived",
        )
        old_fact, _ = store.add_memory(
            title="Old endpoint",
            content="Use the old endpoint.",
            fact_key="service.endpoint",
        )
        store.add_memory(
            title="New endpoint",
            content="Use the new endpoint.",
            fact_key="service.endpoint",
        )
        inactive_fact, _ = store.add_memory(
            title="Inactive endpoint",
            content="Use the inactive endpoint.",
            fact_key="service.inactive_endpoint",
            active_fact=False,
        )
        store.add_relation(anchor.id, archived.id, "same_topic", strength=1.0)
        store.add_relation(anchor.id, old_fact.id, "same_topic", strength=1.0)
        store.add_relation(anchor.id, inactive_fact.id, "same_topic", strength=1.0)

        rows = store.recall("deployment", limit=5)

    ids = [row["id"] for row in rows]
    assert archived.id not in ids
    assert old_fact.id not in ids
    assert inactive_fact.id not in ids


def test_recall_respects_relation_strength_thresholds(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        anchor, _ = store.add_memory(
            title="Deployment rollback policy",
            content="Always define rollback before deployment.",
        )
        weak_first_hop, _ = store.add_memory(
            title="Weakly related note",
            content="This should not surface through a weak edge.",
        )
        strong_first_hop, _ = store.add_memory(
            title="Strong relation note",
            content="Strong first hop.",
        )
        weak_second_hop, _ = store.add_memory(
            title="Weak second hop",
            content="This should not surface through a weak second hop.",
        )
        store.add_relation(anchor.id, weak_first_hop.id, "same_topic", strength=0.4)
        store.add_relation(anchor.id, strong_first_hop.id, "same_topic", strength=1.0)
        store.add_relation(strong_first_hop.id, weak_second_hop.id, "same_topic", strength=0.7)

        rows = store.recall("deployment", limit=5)

    ids = [row["id"] for row in rows]
    assert strong_first_hop.id in ids
    assert weak_first_hop.id not in ids
    assert weak_second_hop.id not in ids


def test_relation_type_docs_are_accepted_and_contradiction_alias_is_canonical(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        first, _ = store.add_memory(title="First", content="A")
        second, _ = store.add_memory(title="Second", content="B")
        relation = store.add_relation(first.id, second.id, "in_thread")
        contradiction = store.add_relation(first.id, second.id, "contradiction")

    assert relation.relation_type == "in_thread"
    assert contradiction.relation_type == "contradicts"


def test_symmetric_relations_are_canonicalized_but_directional_relations_are_not(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        first, _ = store.add_memory(title="First", content="A")
        second, _ = store.add_memory(title="Second", content="B")

        forward = store.add_relation(first.id, second.id, "same_topic")
        reverse = store.add_relation(second.id, first.id, "same_topic")
        directional = store.add_relation(second.id, first.id, "derived_from")

        relations = store.list_relations()

    assert forward.id == reverse.id
    assert reverse.source_id == first.id
    assert reverse.target_id == second.id
    assert directional.source_id == second.id
    assert directional.target_id == first.id
    assert len(relations) == 2


def test_add_relation_validates_strength_range(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        first, _ = store.add_memory(title="First", content="A")
        second, _ = store.add_memory(title="Second", content="B")

        with pytest.raises(ValueError, match="strength"):
            store.add_relation(first.id, second.id, "same_topic", strength=-0.1)
        with pytest.raises(ValueError, match="strength"):
            store.add_relation(first.id, second.id, "same_topic", strength=1.1)


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
