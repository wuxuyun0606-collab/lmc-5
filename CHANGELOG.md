# Changelog

## Unreleased

### Licensing

- **0.3.0 license transition.** Public releases from 0.3.0 onward use
  `AGPL-3.0-or-later`, with separate commercial licensing available for
  proprietary use. Releases through `v0.2.0` remain under their original MIT
  License; the change does not revoke prior grants.
- Added `LICENSING.md`, `COMMERCIAL-LICENSE.md`, and `NOTICE`, plus contributor
  safeguards for future dual licensing.

### Added
- **Primary-agent E-axis authorship.** E content now records
  `e_authored_by` and `e_initial_priority`. The primary/user-facing agent
  writes the experience and chooses its initial order; M-axis automation may
  manage it only afterward. Housekeeper/scorer output is isolated in
  `lmc5_e_axis_proposals`.
- **Refined Session Carryover / 精炼续窗.** New Claude Code helper
  `extras/claude_code/refined_session_carryover.py` plus
  `docs/REFINED_SESSION_CARRYOVER.md`. This replaces the old
  "keep the last 80k-100k tokens" transcript tail-cache recommendation with a
  filtered resume bridge: keep high-signal memory/state and a short natural
  tail, drop tool logs / tracebacks / hook dumps / paths / long JSON, and fail
  closed on recent AUP/policy poison.
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

### Changed
- **Recall fusion default: RRF.** `RecallPipeline` and the production hook now
  default to `fusion="rrf"` after a 726-real-trace A/B replay showed better top5
  composition than `minmax`. `minmax` remains selectable, with docs noting its
  tail-collapse trade-off.

### Fixed
- **Same-turn duplicate-content recall.** Recall now has a default content
  fingerprint pass after id-level channel fusion and before rerank/top-k.
  Copies with different ids but the same normalized body share one slot,
  including a common `knowledge_base`-prefixed vs unprefixed split. Known
  source labels are stripped only at the beginning, digits remain significant,
  duplicate rows do not inflate fusion score, and fingerprint failures fail
  open. Pass `content_fingerprint=None` or set
  `LMC5_RECALL_CONTENT_DEDUP=0` in the reference hook to opt out.
- **Cross-turn recall repetition.** `RecallPipeline` can now accept a
  session-scoped injection history and filter `namespace+source_id` values
  already injected on earlier turns before rerank/top-k, so novel candidates
  backfill instead of the same winner consuming a context slot repeatedly.
  The reference `UserPromptSubmit` hook enables a hash-only file history by
  default, passes the runtime `session_id`, isolates new sessions, and expires
  stale state after two days.
- **E-axis ownership correction.** The earlier trigger design incorrectly
  allowed a housekeeping scorer to author E fields. The corrected
  `extras/pgvector_backend/e_axis_trigger.py` is proposal-only:
  - `should_propose_e_axis(candidate)` — type / keyword / relation-hint gate.
    `relationship_moment`, `risk_boundary`, `preference` always fire; `fact`
    and `engineering_decision` skip unless emotion keywords or
    `emotional_link` hint say otherwise.
  - `EMOTION_TRIGGER_KEYWORDS` — bilingual (CN/EN) keyword dictionary across
    strong-emotion / relational / tension / physical-reaction categories.
  - `EAxisProposalDispatcher` routes suggestions to a review queue.
  - Direct E write-back and automated `backfill_e_axis()` are disabled.
  - `NightDream` and `DreamRunner` no longer expose automatic E-write steps.

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
- `e_axis_scorer.py` — Provider-agnostic, non-authoritative E proposal helper
  with categorized failure logs and retry handling. It does not write E.
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
  (identity protected / Z manual gate / primary-authored E order / category half-lives /
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
