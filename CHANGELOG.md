# Changelog

## Unreleased

### Added
- **Heartbeat detector.** New module
  `extras/pgvector_backend/heartbeat_detector.py` — automatically detects
  heartbeat moments (intimacy, physical reactions, nickname shifts) and
  emotional fragments (breakdown, crying, late-night emo, self-denial) from
  conversation chunks. Bilingual keyword gate (CN+EN) + optional LLM
  confirmation. Outputs `HeartbeatCandidate` / `EmotionCandidate` with
  `protected=True`, designed to feed into hippocampus as an extra candidate
  source alongside the LLM proposer. This closes the gap between "what the
  dream pass knows how to extract" (facts, events, preferences) and "what a
  persona deployment actually needs to preserve" (intimate moments and
  emotional peaks that define the relationship).
- **Query Expansion.** `RecallPipeline` now accepts an optional
  `query_expand` callable (Stage 0) that rewrites the user message into
  2–4 search angles before the three-tier cascade. Each expanded query
  feeds independently into vector/FTS/raw-events stages, with results
  merged by `source_id` keeping the highest score. Includes
  `query_expand_adapter()` helper for DeepSeek / any LLM.
- **Raw events FTS fallback.** Third tier in the recall cascade — searches
  the raw event journal when vector top score < 0.30. Catches keywords that
  only appeared in raw conversation turns, never promoted to curated memory.

### Fixed
- **E-axis trigger layer.** The 0.2.0 release shipped `EAxisScorer` (which
  decides *how* to score) but forgot the layer that decides *which memories
  should be scored at all*. Without it, `night_dream` never invokes the
  scorer on write, and the E columns stay NULL unless the caller wires it
  manually. New module `extras/pgvector_backend/e_axis_trigger.py`:
  - `should_score_e_axis(candidate)` — type / keyword / relation-hint gate.
    `relationship_moment`, `risk_boundary`, `preference` always fire; `fact`
    and `engineering_decision` skip unless emotion keywords or
    `emotional_link` hint say otherwise.
  - `EMOTION_TRIGGER_KEYWORDS` — bilingual (CN/EN) keyword dictionary across
    strong-emotion / relational / tension / physical-reaction categories.
  - `EAxisDispatcher` — chains gate → scorer → write-back with full
    exception isolation so a failed E score never blocks the underlying
    memory write.
  - `backfill_e_axis()` — nightly batch helper to score memories that
    landed in the last 24 hours but missed live scoring.
- **`NightDream` integration.** New optional `e_axis_dispatcher`
  constructor argument. When provided, `run(apply=True)` auto-fires the
  dispatcher for every successfully written candidate. Defaults to `None`
  (backward-compatible with 0.2.0).

## 0.2.0 — XYZEM completion: production reference impl + pipe layer

This release turns LMC-5 from a memory schema with one offline impl into a
complete memory **system** with two reference impls (minimal SQLite + production
PostgreSQL/pgvector), a documented store-to-conversation pipeline, and the
operational patterns needed for VPS 7×24 deployment.

**The minimal impl in `src/lmc5/` is unchanged.** This release adds the
production impl alongside it.

### New: production reference (`extras/pgvector_backend/`)

- `vector_pgvector.py` — PostgreSQL + pgvector halfvec + ivfflat ANN.
- `night_dream.py` — LLM-proposed hippocampus with 6-type classifier, safety
  gates (importance / risk / dedup / `max_promote` non-silent truncation),
  relation-hint-driven Y-axis expansion, semantic dedup via vector similarity
  before write to stop cross-batch synonyms.
- `narrative_timeline.py` — Weekly / monthly narrative reflection with
  injectable reflector and deterministic baseline.
- `ob_recall.py` — OB-style scoring with category half-life table, time
  ripple, Russell-distance emotional resonance.
- `e_axis_scorer.py` — Provider-agnostic LLM emotional scorer with categorized
  failure logs, exponential-backoff retry on retryable failures, `min_confidence`
  gate, `is_in_shadow_period(...)` helper.
- `perception.py` — Spontaneous-recall scheduler with high-vitality + drift
  ratios and time-of-day shaping; JSON cache for hook integration.
- `recall_pipeline.py` — Five-channel parallel recall (vector / FTS fallback /
  graph 2-hop / emotion / spontaneous) with merge, dedup, optional rerank.
- `embedders.py` — Adapters for Gemini embedding 2 (3072d), Voyage 1024d line,
  OpenAI text-embedding-3, local sentence-transformers; `get_embedder()`
  auto-picks the first available.
- `rerankers.py` — Adapters for DeepSeek, OpenAI, Voyage rerank-2;
  `get_reranker()` auto-picks.
- `config.py` — `LMC5Config` dataclass centralizes every threshold, batch size,
  top-K, retry knob. `LMC5Config.from_env()` loads from environment variables.
- `schema.sql` — Complete DDL for every table referenced anywhere in the codebase.
- `.env.example` — Environment template (PG / embedder / housekeeper LLM /
  frontend / ops).
- `hooks/{session_start,user_prompt_submit,session_end}.py` — Claude Code hook
  entrypoints.
- Construction-time `TypeError` for all injected callables (catches mistakes at
  `__init__` instead of inside a cron job).

### New documentation

- `docs/PERSONA_MODE.md` — Six policy switches for AI companion deployments
  (identity protected / Z manual gate / E shadow period / category half-lives /
  spontaneous recall / relationship moments protected). Each switch has a
  concrete configuration example.
- `docs/DEEPSEEK_INTEGRATION.md` — Housekeeper LLM role across dream / Z / Y /
  M / E / narrative. Provider-agnostic; swap rationale included.
- `docs/VECTOR_BACKENDS.md` — SQLite vs pgvector trade-offs, Gemini / Voyage
  embedder recommendations, migration notes.
- `docs/DEPLOYMENT.md` — VPS 7×24 shape, cron and systemd timer examples,
  three frontend options (Telegram / WeChat bot / self-hosted), self-grooming
  value proposition.
- `docs/FORGE_AND_SWAP.md` — Forge (session continuity from durable memory) and
  Swap (snapshot-based rollback) reference patterns.
- `docs/HOOKS_AND_RECALL.md` — Complete pipeline from store to conversation:
  three lifecycle hooks, five recall channels, perception scheduling, semantic
  dedup wiring, composed diagram.

### Other

- New cover illustration replacing the previous storybook image.
- `docs/credits.md` — XYZEM-origin note added (deliberately abstract; the
  engineering shape is what is published, not the private deployment).
- `tests/test_extras_import.py` — 19 smoke tests covering import paths, config
  defaults, ob_score basics, perception config shape, and construction-time
  callable validation across all five injectable classes.

### Status

The `extras/pgvector_backend/` subpackage is **alpha**. See
[extras/pgvector_backend/README.md](extras/pgvector_backend/README.md) for
what works end-to-end vs what is deployment-specific (graph_expand and
emotion_resonate need your relation/candidate-pool SQL) vs what is not yet
covered (integration tests against a real PG, performance benchmarks,
automated embedder migration).

## 0.1.0

- Initial LMC-5 reference implementation.
- Added SQLite storage with FTS5 recall.
- Added lightweight SQLite vector index with cosine search.
- Added one-hop relation-expanded recall.
- Added raw event journal and mixed memory/event surfacing.
- Added fact-key supersession.
- Added redacted CLI output.
- Added read-only metabolism patrol checks.
- Added JSONL import/export, demo, tests, and CI.
