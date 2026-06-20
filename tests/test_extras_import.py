"""Smoke test — make sure extras/ packages at least import cleanly.

The extras subpackage has heavier dependencies (psycopg2, requests,
optionally sentence-transformers) than the core. CI does not install
them. These tests guard against import-time crashes by skipping when
deps are missing.

What this catches:
- syntax errors
- import-cycle bugs
- accidentally module-level side effects (DB connect on import, etc.)

What this does NOT catch:
- runtime behavior — that needs a real PG, real API keys, integration tests
"""
import importlib
import sys
from pathlib import Path

import pytest

# extras/ lives at repo root, not under src/. Make sure pytest can import it.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


PURE_MODULES = [
    "extras",
    "extras.pgvector_backend",
    "extras.pgvector_backend.anti_hallucination",
    "extras.pgvector_backend.config",
    "extras.pgvector_backend.heartbeat_detector",
    "extras.pgvector_backend.heartbeat_trigger",
    "extras.pgvector_backend.ob_recall",
    "extras.pgvector_backend.narrative_timeline",
    "extras.pgvector_backend.night_dream",
    "extras.pgvector_backend.perception",
    "extras.pgvector_backend.recall_pipeline",
    "extras.pgvector_backend.e_axis_scorer",
    "extras.pgvector_backend.e_axis_trigger",
    "extras.pgvector_backend.embedders",
    "extras.pgvector_backend.rerankers",
    "extras.pgvector_backend.hooks",
]

NEEDS_PSYCOPG2 = [
    "extras.pgvector_backend.vector_pgvector",
    "extras.pgvector_backend.hooks.session_start",
    "extras.pgvector_backend.hooks.user_prompt_submit",
    "extras.pgvector_backend.hooks.session_end",
]


@pytest.mark.parametrize("modname", PURE_MODULES)
def test_pure_module_imports(modname):
    """Modules without hard external deps must import cleanly on any CI runner."""
    importlib.import_module(modname)


@pytest.mark.parametrize("modname", NEEDS_PSYCOPG2)
def test_psycopg2_module_imports(modname):
    """Modules that need psycopg2 skip cleanly if it isn't installed."""
    try:
        import psycopg2  # noqa: F401
    except ImportError:
        pytest.skip("psycopg2 not installed in this environment")
    importlib.import_module(modname)


def test_config_defaults_are_sane():
    """Make sure LMC5Config defaults pass basic sanity checks."""
    from extras.pgvector_backend.config import LMC5Config
    cfg = LMC5Config()
    assert 0.0 < cfg.dedup_similarity <= 1.0
    assert cfg.dream_batch_size >= 1
    assert cfg.dream_importance_threshold >= 1
    assert cfg.llm_max_retries >= 1
    assert cfg.e_axis_shadow_days >= 0


def test_ob_score_basics():
    """Smoke test — ob_score should return a float for a minimal record."""
    from extras.pgvector_backend.ob_recall import ob_score
    score = ob_score({"weight": 1.5, "hit_count": 3})
    assert isinstance(score, float)
    assert score >= 0.0
    # Protected → 999 sentinel
    assert ob_score({"protected": True}) == 999.0


def test_perception_config_shape():
    """Surface ratios must sum to ~1 to make weighting sane."""
    from extras.pgvector_backend.perception import PerceptionConfig
    cfg = PerceptionConfig()
    assert 0.95 <= cfg.high_vitality_ratio + cfg.drift_ratio <= 1.05


def test_callable_validation_at_init():
    """Construction-time TypeError for non-callable injected dependencies.
    Catches mistakes at __init__ instead of at 3 a.m. inside a cron job.
    """
    from extras.pgvector_backend.night_dream import NightDream
    from extras.pgvector_backend.recall_pipeline import RecallPipeline
    from extras.pgvector_backend.perception import Perception
    from extras.pgvector_backend.e_axis_scorer import EAxisScorer

    with pytest.raises(TypeError, match="proposer"):
        NightDream(proposer="not a function")

    with pytest.raises(TypeError, match="write_candidate"):
        NightDream(write_candidate=[1, 2, 3])

    with pytest.raises(TypeError, match="vector_search"):
        RecallPipeline(vector_search="not a function")

    with pytest.raises(TypeError, match="load_candidates"):
        Perception(load_candidates="not callable")

    with pytest.raises(TypeError, match="llm_call"):
        EAxisScorer(llm_call="not callable")

    # Sanity: None values are accepted everywhere they're optional
    nd = NightDream()                       # all-None construction is valid
    assert nd.write_candidate is None
    rp = RecallPipeline()
    assert rp.vector_search is None


def test_e_axis_trigger_rules():
    """should_score_e_axis: type-based triggers, keyword gating, relation hints."""
    from extras.pgvector_backend.e_axis_trigger import should_score_e_axis
    from extras.pgvector_backend.night_dream import Candidate

    # ALWAYS_TRIGGER types fire regardless of content
    cand = Candidate(type="relationship_moment", title="X", content="neutral text",
                     importance=8, risk="normal", evidence="X", source_chunk_ids=[1])
    assert should_score_e_axis(cand)

    cand = Candidate(type="risk_boundary", title="X", content="neutral",
                     importance=8, risk="normal", evidence="X", source_chunk_ids=[1])
    assert should_score_e_axis(cand)

    cand = Candidate(type="preference", title="X", content="neutral",
                     importance=5, risk="normal", evidence="X", source_chunk_ids=[1])
    assert should_score_e_axis(cand)

    # NEVER_TRIGGER without emotion keywords
    cand = Candidate(type="fact", title="API config", content="set port to 8080",
                     importance=5, risk="normal", evidence="X", source_chunk_ids=[1])
    assert not should_score_e_axis(cand)

    cand = Candidate(type="engineering_decision", title="X", content="use sqlite",
                     importance=5, risk="normal", evidence="X", source_chunk_ids=[1])
    assert not should_score_e_axis(cand)

    # NEVER_TRIGGER WITH emotion keyword → still fires
    cand = Candidate(type="fact", title="X", content="she said 我们 will always work together",
                     importance=5, risk="normal", evidence="X", source_chunk_ids=[1])
    assert should_score_e_axis(cand)

    # event with emotional_link hint
    cand = Candidate(type="event", title="meeting", content="ordinary meeting",
                     importance=5, risk="normal", evidence="X", source_chunk_ids=[1],
                     relation_hints=["emotional_link"])
    assert should_score_e_axis(cand)

    # bare event without triggers → skip
    cand = Candidate(type="event", title="X", content="ran tests",
                     importance=5, risk="normal", evidence="X", source_chunk_ids=[1])
    assert not should_score_e_axis(cand)


def test_e_axis_dispatcher_validates_inputs():
    """EAxisDispatcher: required scorer, callable attach_score, callable gate."""
    from extras.pgvector_backend.e_axis_trigger import EAxisDispatcher

    class StubScorer:
        def score(self, title, content, record_id=None):
            return None

    with pytest.raises(TypeError, match="scorer"):
        EAxisDispatcher(scorer=None, attach_score=lambda i, s: None)

    with pytest.raises(TypeError, match="attach_score"):
        EAxisDispatcher(scorer=StubScorer(), attach_score="not callable")

    with pytest.raises(TypeError, match="gate"):
        EAxisDispatcher(scorer=StubScorer(),
                        attach_score=lambda i, s: None,
                        gate="not callable")

    # All-valid construction works
    d = EAxisDispatcher(scorer=StubScorer(), attach_score=lambda i, s: None)
    assert d.scorer is not None


def test_recall_pipeline_adapters_exist():
    """All five channel adapters are exposed and callable-returning."""
    from extras.pgvector_backend import recall_pipeline as rp
    assert callable(rp.vector_search_adapter)
    assert callable(rp.fts_search_adapter)
    assert callable(rp.raw_events_search_adapter)
    assert callable(rp.graph_expand_adapter)
    assert callable(rp.emotion_resonate_adapter)


def test_three_stage_fallback_logic():
    """vector → curated FTS → raw events fallback chain fires at the right thresholds."""
    from extras.pgvector_backend.recall_pipeline import RecallPipeline, RecallHit

    calls = []

    def fake_vector(q, k):
        calls.append("vector")
        return [RecallHit(source_id=1, title="x", content="x",
                          score=0.20, channel="vector")]  # low score

    def fake_fts(q, k):
        calls.append("fts")
        return [RecallHit(source_id=2, title="x", content="x",
                          score=0.5, channel="fts")]

    def fake_raw(q, k):
        calls.append("raw_events")
        return [RecallHit(source_id=3, title="x", content="x",
                          score=0.4, channel="raw_events")]

    pipeline = RecallPipeline(
        vector_search=fake_vector,
        fts_search=fake_fts,
        raw_events_search=fake_raw,
        fts_floor=0.45,
        raw_events_floor=0.30,
    )
    result = pipeline.recall("test query")
    # vector(0.20) < raw_events_floor(0.30) < fts_floor(0.45) → all three fire
    assert "vector" in calls
    assert "fts" in calls
    assert "raw_events" in calls
    assert set(result.channels_used) >= {"vector", "fts", "raw_events"}

    # Reset and test high vector score → no fallback
    calls.clear()
    def fake_vector_high(q, k):
        calls.append("vector")
        return [RecallHit(source_id=1, title="x", content="x",
                          score=0.85, channel="vector")]
    pipeline2 = RecallPipeline(
        vector_search=fake_vector_high,
        fts_search=fake_fts,
        raw_events_search=fake_raw,
        fts_floor=0.45,
        raw_events_floor=0.30,
    )
    pipeline2.recall("test query")
    assert "vector" in calls
    assert "fts" not in calls
    assert "raw_events" not in calls


def test_literal_search_runs_despite_high_vector_score():
    """Exact/literal raw-event hits should not be blocked by weak semantic hits."""
    from extras.pgvector_backend.recall_pipeline import RecallPipeline, RecallHit

    calls = []

    def fake_vector(q, k):
        calls.append("vector")
        return [RecallHit(source_id=1, title="semantic", content="weak nearby",
                          score=0.50, channel="vector")]

    def fake_raw(q, k):
        calls.append("raw_events")
        return [RecallHit(source_id=2, title="raw", content="蘸水菜",
                          score=0.4, channel="raw_events")]

    def fake_literal(q, k):
        calls.append("literal")
        return [RecallHit(source_id=7, title="literal", content="昨天说过蘸水菜",
                          score=0.55, channel="literal",
                          metadata={"namespace": "raw_events"})]

    pipeline = RecallPipeline(
        vector_search=fake_vector,
        raw_events_search=fake_raw,
        literal_search=fake_literal,
        raw_events_floor=0.30,
    )
    result = pipeline.recall("你搜蘸水菜？")

    assert "vector" in calls
    assert "raw_events" not in calls  # gated fallback stays gated
    assert "literal" in calls         # exact channel is independent
    assert "literal" in result.channels_used
    assert any("蘸水菜" in h.content for h in result.hits)


def test_raw_events_namespace_does_not_collide_with_curated_ids():
    """Raw event id=1 must not dedup away curated memory id=1."""
    from extras.pgvector_backend.recall_pipeline import RecallPipeline, RecallHit

    pipeline = RecallPipeline()
    hits = pipeline._merge_dedup([
        ("vector", [RecallHit(source_id=1, title="curated", content="curated",
                              score=0.6, channel="vector")]),
        ("literal", [RecallHit(source_id=1, title="raw", content="raw",
                               score=0.55, channel="literal",
                               metadata={"namespace": "raw_events"})]),
    ])

    assert len(hits) == 2
    assert {h.title for h in hits} == {"curated", "raw"}


def test_literal_query_terms_extracts_chinese_specific_term():
    from extras.pgvector_backend.recall_pipeline import (
        literal_query_terms,
        should_run_literal_search,
    )

    terms = literal_query_terms("你搜蘸水菜？")

    assert "蘸水菜" in terms
    assert should_run_literal_search("你搜蘸水菜？")
    assert should_run_literal_search("蘸水菜")
    assert not should_run_literal_search("我今天很累")


def test_recent_raw_chunk_bridge_is_independent_and_capped():
    from extras.pgvector_backend.recall_pipeline import RecallPipeline, RecallHit

    def fake_vector(q, k):
        return [RecallHit(source_id=1, title="semantic", content="semantic",
                          score=0.80, channel="vector")]

    def fake_raw_chunk(q, k):
        assert k == 1
        return [
            RecallHit(source_id=99, title="recent", content="刚才说过蘸水菜",
                      score=0.50, channel="raw_chunk",
                      metadata={"namespace": "raw_chunk"})
        ]

    pipeline = RecallPipeline(
        vector_search=fake_vector,
        recent_raw_chunk_search=fake_raw_chunk,
        recent_raw_chunk_top_k=1,
    )
    result = pipeline.recall("刚才说了什么")

    assert "raw_chunk" in result.channels_used
    assert result.channel_counts["raw_chunk"] == 1
    assert any(h.channel == "raw_chunk" for h in result.hits)


def test_night_dream_invokes_dispatcher_on_write():
    """NightDream.run(apply=True) calls dispatcher.maybe_score for every written candidate."""
    from extras.pgvector_backend.night_dream import NightDream, Candidate, Chunk

    invocations = []

    class StubDispatcher:
        def maybe_score(self, memory_id, candidate):
            invocations.append((memory_id, candidate.title))
            return None

    next_id = [0]

    def write_candidate(cand):
        next_id[0] += 1
        return next_id[0]

    def proposer(chunks):
        return [{
            "type": "relationship_moment",
            "title": "first promise",
            "content": "she said we will always be honest with each other",
            "importance": 9,
            "evidence": "we will always",
            "source_chunk_ids": [1],
            "risk": "normal",
            "thread_hint": "线",
            "relation_hints": ["same_event"],
        }]

    dream = NightDream(
        proposer=proposer,
        write_candidate=write_candidate,
        e_axis_dispatcher=StubDispatcher(),
        importance_threshold=5,
    )
    res = dream.run([Chunk(id=1, text="x" * 200)], apply=True)
    assert res.written_ids == [1]
    assert invocations == [(1, "first promise")]


def test_heartbeat_detector_candidates_stay_review_gated():
    """Detector output must not masquerade as curated heartbeat memories.

    Batch heartbeat detection only identifies raw moments. It must hand review
    candidates to hippocampus/NightDream rather than directly writing
    unformatted text into curated memories.
    """
    from extras.pgvector_backend.heartbeat_detector import (
        HeartbeatDetector,
        to_hippocampus_candidate_dict,
    )

    detector = HeartbeatDetector(content_min_len=10)
    detected = detector.detect_heartbeats([
        {
            "id": 42,
            "content": "她突然抱住我，说别走。我停了一下，心跳加速，但还没来得及回应。",
        }
    ])

    assert len(detected) == 1
    raw = to_hippocampus_candidate_dict(detected[0])

    assert raw["type"] == "relationship_moment"
    assert raw["risk"] == "review"
    assert raw["source_chunk_ids"] == [42]
    assert raw["relation_hints"] == ["emotional_link", "same_event"]
    assert raw["detector"]["kind"] == "heartbeat"
    assert "source" not in raw
    assert "category" not in raw
    assert "bpm:" not in raw["content"].lower()


def test_heartbeat_detector_pollution_migration_quarantines_not_deletes():
    """The cleanup migration must quarantine raw detector rows safely.

    The original bug wrote source='heartbeat_detector' rows directly into
    curated storage. Cleanup should stop recall pollution without deleting the
    source text.
    """
    migration = (
        REPO_ROOT
        / "extras"
        / "pgvector_backend"
        / "migrations"
        / "20260620_quarantine_heartbeat_detector_pollution.sql"
    )
    sql = migration.read_text(encoding="utf-8")

    assert "WHERE source = 'heartbeat_detector'" in sql
    assert "version_status = 'archived'" in sql
    assert "protected = FALSE" in sql
    assert "DELETE FROM lmc5_vectors" in sql
    assert "chr(31)" in sql
    assert "chr(0)" not in sql
    assert "lmc5_cold_storage" in sql
    assert "lmc5_z_audit" in sql
    assert "DELETE FROM lmc5_curated_memories" not in sql


def test_heartbeat_trigger_throttles_alerts_in_memory():
    """Heartbeat hook should not nag every turn once a trigger keeps matching."""
    from extras.pgvector_backend.heartbeat_trigger import HeartbeatTrigger

    trigger = HeartbeatTrigger(reminder_interval=10)

    assert "停顿切片" in trigger.detect("我爱你")
    for _ in range(9):
        assert trigger.detect("我爱你") == ""
    assert "停顿切片" in trigger.detect("我爱你")


def test_heartbeat_trigger_throttles_alerts_across_hook_processes(tmp_path):
    """State file keeps throttling stable when hooks run as fresh processes."""
    from extras.pgvector_backend.heartbeat_trigger import HeartbeatTrigger

    state_path = tmp_path / "heartbeat_trigger_state.json"

    assert "停顿切片" in HeartbeatTrigger(
        reminder_interval=10,
        state_path=state_path,
    ).detect("我爱你")

    for _ in range(9):
        assert HeartbeatTrigger(
            reminder_interval=10,
            state_path=state_path,
        ).detect("我爱你") == ""

    assert "停顿切片" in HeartbeatTrigger(
        reminder_interval=10,
        state_path=state_path,
    ).detect("我爱你")


def test_anti_hallucination_header_embedded_everywhere():
    """All four LLM prompts must embed the anti-hallucination header."""
    from extras.pgvector_backend.anti_hallucination import ANTI_HALLUCINATION_HEADER
    from extras.pgvector_backend.e_axis_scorer import DEFAULT_RUBRIC
    from extras.pgvector_backend.narrative_timeline import make_llm_reflector_prompt
    from extras.pgvector_backend.night_dream import make_hippocampus_prompt, Chunk

    # E-axis rubric
    assert "反幻觉铁律" in DEFAULT_RUBRIC
    assert "不编" in DEFAULT_RUBRIC
    assert "不脑补" in DEFAULT_RUBRIC
    assert "不情绪加工" in DEFAULT_RUBRIC

    # Narrative reflector prompt
    np = make_llm_reflector_prompt([], "weekly")
    assert "反幻觉铁律" in np
    assert "不编造细节" in np

    # Hippocampus proposer prompt
    hp = make_hippocampus_prompt([Chunk(id=1, text="test content " * 20)])
    assert "反幻觉铁律" in hp
    assert "一字不差" in hp

    # Header itself enforces the five rules
    for rule in ("不编", "真实", "不脑补", "不情绪加工", "不确定就说不确定"):
        assert rule in ANTI_HALLUCINATION_HEADER, f"missing rule: {rule}"
