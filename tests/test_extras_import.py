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
    "extras.pgvector_backend.config",
    "extras.pgvector_backend.ob_recall",
    "extras.pgvector_backend.narrative_timeline",
    "extras.pgvector_backend.night_dream",
    "extras.pgvector_backend.perception",
    "extras.pgvector_backend.recall_pipeline",
    "extras.pgvector_backend.e_axis_scorer",
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
