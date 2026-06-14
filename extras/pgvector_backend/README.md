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
| `recall_pipeline.py` | new — closes the "store-to-conversation" gap | Multi-channel parallel recall: vector ANN, FTS fallback when top vector score is low, Y-axis graph 2-hop expansion, Russell emotional resonance, spontaneous-recall channel, optional rerank. Merge/dedup by `source_id` with channel-tag accumulation. |
| `perception.py` | new | Spontaneous-recall scheduler. Weighted random over high-vitality memories with a deliberate drift fraction, plus time-of-day shaping (night-emotional vs work-factual boost). Writes a JSON cache that the SessionStart and per-turn hooks read. |
| `hooks/session_start.py` | new — Claude Code hook | Boot-time additionalContext: identity + current facts + recent narrative + open threads + spontaneous-recall surface. |
| `hooks/user_prompt_submit.py` | new — Claude Code hook | Per-turn additionalContext: routes the prompt through `RecallPipeline.recall()`. Skips trivial messages. Attaches user-emotion coordinate as metadata. |
| `hooks/session_end.py` | new — Claude Code hook | Archives the session JSONL into `lmc5_raw_events`. Optionally triggers a daytime express dream pass (off by default). |

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

`vector_pgvector.py` is the smallest piece and the most directly swappable.
Begin there if you only want a faster vector backend.

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
