# pgvector backend (opt-in)

> Production-grade ANN reference for LMC-5.
> Opt-in. Does not change the offline-first SQLite default.

## Alpha Status (read this first)

This subpackage is **alpha**. Architecture is in place; some integration
plumbing is reference-only and needs deployment-specific wiring.

**What works end-to-end:**

- `vector_pgvector.PgvectorStore` — schema, write, search, find_duplicates
- `night_dream.NightDream` — proposer → gate → write → relation expansion → semantic dedup
- `narrative_timeline.NarrativeTimeline` — weekly / monthly reflection
- `ob_recall.ob_score` + decay formula + time ripple + Russell distance
- `e_axis_scorer.EAxisScorer` — retry + min_confidence gate + shadow-period helper
- `perception.Perception` — spontaneous-recall scheduler + JSON cache
- `recall_pipeline.RecallPipeline` — multi-channel parallel merge
- `config.LMC5Config` — every knob in one dataclass, env-var loadable
- `schema.sql` — full DDL for every table referenced anywhere in the codebase
- `embedders.py` — Gemini / Voyage / OpenAI / local BGE-M3 adapters with auto-pick
- `rerankers.py` — DeepSeek / OpenAI / Voyage rerank adapters with auto-pick
- `hooks/{session_start,user_prompt_submit,session_end}.py` — Claude Code hook entrypoints

**What is deployment-specific (the hook auto-builder leaves these `None`):**

- `graph_expand` — needs your relation schema's exact SQL for 2-hop expansion
- `emotion_resonate` — needs your candidate-pool SQL for Russell distance

Both are documented in `docs/HOOKS_AND_RECALL.md`. They are intentionally
left as `None` rather than guessing — wiring them with the wrong schema
silently returns garbage; leaving them off is safer.

**Not yet covered:**

- End-to-end integration tests against a real PostgreSQL instance
- Performance benchmarks (latency, recall@K, embedding cost per turn)
- Automated embedder migration (3072d → 1024d) — see `docs/VECTOR_BACKENDS.md`
  for the manual procedure

**Recommended posture:** treat this as a working starting point for
building an LMC-5-backed agent. Read the docstrings, wire your storage,
and expect to write integration tests against your own schema before
trusting it with anything you cannot afford to lose. File issues for
sharp edges you hit.

The core `src/lmc5/vector.py` uses SQLite + JSON + Python cosine — fine for
demos and small corpora, slow once you cross a few thousand vectors.

This `extras/pgvector_backend/` folder is a drop-in alternative for users who
want PostgreSQL + pgvector (with halfvec) + ivfflat index, plus several
reference implementations of axes that the core only sketches.

Nothing here is imported by the core package. You install and wire it up
yourself.

## Why opt-in

- LMC-5 stays provider-free and offline-first by default. PostgreSQL is a real
  external dependency.
- Every file here is a reference, not a finished feature. Read it like a
  worked example, not a black box.
- Picking pgvector means you accept a different operational shape: a database
  to keep running, a backup story, and an embedding provider.

## Files

| File | Replaces / Adds | Summary |
|------|----------------|---------|
| `config.py` | new | `LMC5Config` dataclass — every threshold, batch size, top-K, retry knob in one place. `LMC5Config.from_env()` loads from env vars; ships a `retry_llm_call` decorator + `RetryableLLMError` exception. |
| `schema.sql` | new | Full DDL: `lmc5_curated_memories`, `lmc5_vectors`, `lmc5_memory_relations`, `lmc5_z_audit`, `lmc5_cold_storage`, `lmc5_narrative_index`, `lmc5_e_axis_failures`, `lmc5_dynamic_stopwords`. Run before any of the Python modules. |
| `.env.example` | new | Environment template — PG DSN, embedder choice (Gemini / Voyage / local), housekeeper LLM keys (DeepSeek default), `LMC5_*` config overrides, Telegram bot token, log/backup paths. **Copy to `.env` and never commit real values.** |
| `vector_pgvector.py` | replaces `src/lmc5/vector.py` | PostgreSQL + halfvec + ivfflat ANN. Embedder injected via callable. |
| `night_dream.py` | upgrades `hippocampus.py` + `consolidation.py` | LLM proposer + 6-type classifier + safety gates + safe-relation expansion driven by `candidate.relation_hints`. All failures and `max_promote` truncations log explicitly — no silent drops. Falls back to deterministic baseline if no LLM is wired. |
| `narrative_timeline.py` | new (no core equivalent) | Weekly / monthly narrative index. Picks seeds by weight × arousal, reflects to a title + paragraph. Reflector is injected; default is deterministic. |
| `ob_recall.py` | upgrades `scoring.py` | Ombre-Brain-style score with category half-life, time ripple, Russell distance for emotional resonance. Decay formula shared between write-time and metabolism. |
| `e_axis_scorer.py` | upgrades the E axis | LLM-based emotional scoring with categorized failure logs, exponential-backoff retry on retryable failures (timeout / empty / non-JSON), `min_confidence` gate, and `is_in_shadow_period(...)` helper so the shadow window is enforced in code, not in discipline. Provider-agnostic — pass any `llm_call(prompt, timeout) -> str` callable. |
| `e_axis_trigger.py` | the missing "should we score this?" layer | `should_score_e_axis(candidate)` with type-based / keyword-based / relation-hint-based gates (CN+EN keywords). `EAxisDispatcher` chains gate → scorer → write-back. `backfill_e_axis()` for nightly batch coverage. Plugs into `NightDream(e_axis_dispatcher=...)` so the scorer actually fires on write. |
| `dream_runner.py` | new — the nightly pipeline orchestrator | Chains the full dream pass into one cron-able entry: consolidate → nap → hippocampus → heartbeat_detect → e_axis_backfill → per-thread `timeline_sweep` → narrative_weekly → narrative_monthly (first 3 days of month) → z_audit → patrol. `nap` is reused here for nightly hygiene, but `run_nap` remains an independent session-switch entrypoint. Includes `DreamSchedule` for the tested daily 04:00 local schedule. Each step is an injected callable; `None` = skip. Failure-isolated — one step or one timeline crashing doesn't block the rest. CLI: `python -m extras.pgvector_backend.dream_runner [--dry-run]`. |
| `heartbeat_detector.py` | new — the missing "what to save" layer for persona | Detects heartbeat moments (intimacy / physical reactions / nickname shifts) and emotional fragments (breakdown / crying / late-night emo / self-denial) from conversation chunks. Keyword gate (CN+EN bilingual) + optional LLM confirmation. Outputs detector candidates only; convert with `to_hippocampus_candidate_dict()` and feed hippocampus/NightDream. Do **not** directly insert detector raw text into curated memories. |
| `heartbeat_trigger.py` | new — real-time heartbeat detection for hooks | Per-message keyword gate with a default 10-turn reminder throttle. Injects a prompt into additionalContext only when a matched heartbeat signal is outside the throttle window. Optional scene_lookup surfaces the last similar heartbeat. The AI decides whether to save — trigger ≠ auto-store. |
| `recall_pipeline.py` | new — closes the "store-to-conversation" gap | Full pipeline: optional Query Expansion (LLM → 2-4 search angles) → three-tier cascade (vector → curated FTS → raw-events FTS) → three independent channels (Y-graph 2-hop / Russell emotion / spontaneous) → merge/dedup → optional rerank. Includes `query_expand_adapter` helper for DeepSeek/any LLM. |
| `perception.py` | new | Spontaneous-recall scheduler. Weighted random over high-vitality memories with a deliberate drift fraction, plus time-of-day shaping (night-emotional vs work-factual boost). Writes a JSON cache that the SessionStart and per-turn hooks read. |
| `hooks/session_start.py` | new — Claude Code hook | Boot-time additionalContext: identity + current facts + recent narrative + open threads + spontaneous-recall surface. |
| `hooks/user_prompt_submit.py` | new — Claude Code hook | Per-turn additionalContext: routes the prompt through `RecallPipeline.recall()`. Skips trivial messages. Attaches user-emotion coordinate as metadata. |
| `hooks/session_end.py` | new — Claude Code hook | Archives the session JSONL into `lmc5_raw_events`. Optionally triggers a daytime express dream pass (off by default). |

### Migrations

| File | Purpose |
|------|---------|
| `migrations/20260620_quarantine_heartbeat_detector_pollution.sql` | Archives old rows created by the invalid `source='heartbeat_detector'` direct-insert path, removes their vectors, closes their relation edges, and leaves an audit trail. Use this when detector raw transcripts polluted `lmc5_curated_memories`. |

**Semantic dedup wired into `night_dream`.** Pass a
`find_semantic_duplicates` callable (typically backed by
`PgvectorStore.find_duplicates(threshold=0.92)`) at construction time
and the dream pass will reject cross-batch synonyms before they
flood the relation graph. See `docs/HOOKS_AND_RECALL.md` for the
wiring example.

## Setup order

1. `psql -f schema.sql` against your target database
2. Copy `.env.example` to `.env` and fill in keys / DSN / overrides
3. Construct an `LMC5Config` (default or `LMC5Config.from_env()`)
4. Instantiate each module with the config + injected callables

## Provider-free philosophy

Every module here keeps LMC-5's rule: external services go through `Callable`
injection. Default behaviors stay deterministic so the modules can still run
without API keys or network access. You only pay the LLM bill where you
explicitly wire it in.

## How to read this folder

Start with the file docstrings — each one explains:

1. Which core file it corresponds to
2. What the core version does and what it does not do
3. What this version adds
4. How to integrate (pseudocode example at the top)

### Recall priority

This backend is storage-first by design:

1. A primary curated semantic adapter is the main path (`pgvector` over
   `lmc5_curated_memories` / `lmc5_vectors` in this reference backend; SQLite or
   custom adapters can expose the same role).
2. curated FTS / keyword search is the fallback when semantic confidence is weak.
3. Raw-events FTS is the last-resort journal search.
4. Literal raw-events / raw_chunk / graph / emotion / perception are gated side
   channels, not replacements for the curated main path.

Do not mix legacy SQLite rows, transcript tails, or cold/session archive cards
into the same main ranking unless you explicitly label and gate them as
last-resort evidence.

`RecallPipeline.trace` and every hit's `metadata` expose the layer decision:
`recall_layer`, `recall_tier`, `evidence_role`, `source_label`, channel
`score_breakdown`, and top-level cascade gates (`fts_checked`,
`raw_events_checked`, `cold_archive_checked`, etc.). Keep these fields when building UI/debug output;
they are the guardrail that prevents "main memory", "raw evidence", and "cold
archive hint" from wearing the same fake mustache.

Set `LMC5_RECALL_OUTPUT=layered` or `RecallPipeline(output_mode="layered")` to
return a four-section `RecallResult.layers`: `main_recall` (authority),
`source_neighborhood` (short navigation), `graph_expansion` (association), and
`fallback_archive` (last-resort raw/cold archive evidence).
`flat` is still the default; old consumers do not need to know this feature
exists until they grow up and ask for a map.

`LMC5_COLD_ARCHIVE_FALLBACK=1` optionally wires `lmc5_cold_storage` as the cold
box. The pipeline only opens it when PG/curated vector, curated FTS, raw-events,
and literal/source-neighborhood hits are all empty. If it surfaces, treat it as
evidence to inspect, not an active fact.

`vector_pgvector.py` is the smallest piece and the most directly swappable.
Begin there if you only want a faster vector backend.

### Dream result reporting

`DreamRunner.run()` returns a `DreamResult` with both a human-readable
`summary` and a structured `to_dict()` payload. The payload includes `ok`,
`step_counts`, per-step status/duration/error fields, and timeline sweep
sub-results. Use `to_dict()` for nightly logs, dashboards, or Telegram reports
so a failed X-line cleanup does not get mistaken for a failed whole dream run.

`night_dream.py` and `narrative_timeline.py` matter most for **long-running
agents** — a deployment that runs for months and needs to remember what
happened last Tuesday in narrative form, not raw chunks.

`ob_recall.py` and `e_axis_scorer.py` are the recall and emotion plumbing
that turn an XYZEM database into something that actually **ranks well at
3 a.m.**, when half the things in memory have already been forgotten by
context.

## Status

Reference implementations. Tested in private long-running deployments before
extraction. Not currently covered by the LMC-5 test suite — adapt to your
own integration tests.

## License

Same as the parent project (MIT).
