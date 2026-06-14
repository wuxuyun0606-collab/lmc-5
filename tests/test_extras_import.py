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
