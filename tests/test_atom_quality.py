from lmc5.atom_quality import assess_atom_quality, build_atom_quality_report
from lmc5.consolidation import consolidate_events
from lmc5.hippocampus import _sentence_atom, deterministic_proposer, run_hippocampus
from lmc5.store import MemoryStore


def _seed_audit_chunks(store: MemoryStore) -> None:
    events = [
        (
            "user",
            "Awen reported that the atomization audit found every candidate too coarse.",
        ),
        (
            "assistant",
            "We should keep the fix in dry-run and avoid touching the production memory database.",
        ),
        (
            "user",
            "The next card is MEM-16 and it blocks recall from moving live.",
        ),
        (
            "assistant",
            "The acceptance check needs before and after ratios for needs_rewrite and too_coarse.",
        ),
    ]
    for role, content in events:
        store.log_event(role=role, content=content, channel="audit")
    consolidate_events(store, window_size=2, channel="audit", create_observations=False)


def test_atom_quality_flags_legacy_chunk_scaffolding(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        _seed_audit_chunks(store)
        chunks = store.conn.execute("SELECT * FROM event_chunks ORDER BY id").fetchall()
        legacy_candidates = [candidate.to_dict() for candidate in deterministic_proposer(chunks)]

    report = build_atom_quality_report(legacy_candidates)

    assert report.total == 2
    assert report.labels["too_coarse"] == 2
    assert report.flags["needs_rewrite"] == 2
    assert report.ratios["too_coarse"] == 1.0
    assert report.ratios["needs_rewrite"] == 1.0


def test_default_hippocampus_produces_event_level_atoms(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        _seed_audit_chunks(store)

        result = run_hippocampus(
            store,
            channel="audit",
            min_importance=1,
            max_promote=10,
            apply=False,
        )
        memory_count = store.conn.execute("SELECT count(*) FROM memories").fetchone()[0]

    report = build_atom_quality_report(result.candidates)

    assert result.chunks_seen == 2
    assert result.candidates_seen == 4
    assert result.promote_ready == 4
    assert memory_count == 0
    assert report.total == 4
    assert report.labels["pass"] == 4
    assert report.ratios["too_coarse"] == 0.0
    assert report.ratios["needs_rewrite"] == 0.0
    assert all(candidate["source_event_ids"] for candidate in result.candidates)
    assert all("Event chunk" not in candidate["content"] for candidate in result.candidates)


def test_atom_quality_rejects_generic_pipeline_title():
    assessment = assess_atom_quality(
        {
            "title": "Hippocampus observation from chunk 7",
            "content": "Event chunk 1-20 (20 events). First: hi Last: ok Keywords: memory",
            "source_chunk_ids": [7],
        }
    )

    assert assessment.label == "too_coarse"
    assert "needs_rewrite" in assessment.flags


def test_atom_quality_accepts_meaningful_chinese_without_spaces():
    assessment = assess_atom_quality(
        {
            "title": "远程向量调用增加脱敏",
            "content": "远程向量调用会先清理密钥和数据库地址再发送。",
            "evidence": "代码审计确认三条远程适配器共用同一脱敏入口。",
            "source_chunk_ids": [7],
        }
    )

    assert assessment.label == "pass"
    assert assessment.flags == []


def test_sentence_atom_splits_chinese_punctuation_without_spaces():
    text = "第一条记忆已经完成脱敏。第二条属于另一个独立事实。"

    assert _sentence_atom(text) == "第一条记忆已经完成脱敏。"
