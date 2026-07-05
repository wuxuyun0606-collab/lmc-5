"""LMC-5 pgvector backend — opt-in production reference.

Public surface:
  PgvectorStore           vector_pgvector  — pgvector + halfvec + ivfflat ANN
  NightDream              night_dream      — LLM-proposed hippocampus + safety gates
  NarrativeTimeline       narrative_timeline — weekly / monthly reflection
  EAxisScorer             e_axis_scorer    — provider-agnostic emotional scorer
  DreamRunner/Schedule    dream_runner     — nightly 04:00 orchestration
  Nap                     nap              — session-switch lightweight maintenance
  Patrol                  patrol           — nightly structural cleanup + health
  RecallPipeline          recall_pipeline  — multi-channel recall
  Perception              perception       — spontaneous-recall scheduler
  LMC5Config              config           — all tunable knobs in one dataclass
  ob_score                ob_recall        — Ombre-Brain-style ranking

All modules use Callable injection so external services (embedders, LLMs,
DB connections) stay out of the import path until you wire them.

ALPHA: see README.md "Alpha Status" section before relying on this for
production data.
"""
