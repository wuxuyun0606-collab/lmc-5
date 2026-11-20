# Mem0 2026 Update Notes for LMC-5

> Snapshot date: 2026-06-21.
> Purpose: extract engineering ideas from recent Mem0 releases without
> replacing LMC-5's XYZEM model or weakening safety boundaries.

## Upstream Signals

Mem0's 2026 update is mainly an engineering pipeline rewrite, not just a new
SDK number:

- 2026-04-14: Mem0 SDK v2/v3 introduced single-pass ADD-only extraction,
  hybrid retrieval, built-in entity linking, batch operations, message
  persistence, and changed default search behavior.
- 2026-05-08 to 2026-05-13: Platform-only memory decay and temporal reasoning
  became headline features. These are not OSS memory-store features, but the
  design pattern is useful.
- 2026-06-10: OSS search gained `explain=True` score breakdowns and safer
  hybrid-search degradation warnings.
- 2026-06-17: OpenCode plugin v0.2.0 added native tools, project/session/global
  memory scope, and gated auto-dream consolidation.

Sources:

- https://github.com/mem0ai/mem0/releases
- https://docs.mem0.ai/changelog/highlights
- https://docs.mem0.ai/changelog/sdk
- https://docs.mem0.ai/migration/oss-v2-to-v3
- https://docs.mem0.ai/platform/features/temporal-reasoning
- https://docs.mem0.ai/platform/features/memory-decay

## What LMC-5 Should Copy

### 1. ADD-only ingestion as the safe default

Mem0's ADD-only extraction avoids letting one model call rewrite or delete
memory during ingestion. LMC-5 already has Z-axis supersession and review
audits, so the LMC-5 version should be stricter:

- raw events and hippocampus candidates append only;
- candidate promotion creates new memories;
- Z changes only happen through explicit audit or approved supersession;
- delete/archive remains a separate M-layer operation with snapshot support.

This matches LMC-5 better than Mem0's simpler `linked_memory_ids` chain because
LMC-5 needs historical truth, source evidence, and rollback.

### 2. Multi-signal score breakdowns

Mem0 now exposes semantic, keyword, entity, and temporal score components. LMC-5
should add an equivalent `score_breakdown` to `RecallHit.metadata` and final
debug output:

- `vector`
- `fts`
- `literal`
- `raw_events`
- `entity`
- `graph`
- `temporal`
- `emotion`
- `decay`
- `rerank`

This is high priority because LMC-5 recall already has more channels than Mem0.
Without explainability, tuning becomes folk magic in a trench coat.

Closed-loop shape:

- every recall channel writes its local score and gate reason into
  `score_breakdown`;
- merge/rerank preserves the full breakdown instead of only the max score;
- final injected memories get a `recall_trace` row with query hash, channels,
  score breakdown, injected/not-injected, and budget truncation reason;
- M-layer reinforcement only updates hit counters for final injected memories,
  not every searched candidate;
- patrol can answer "why did this surface?" and "why did this not surface?"
  without replaying the whole query.

This makes explainability part of operations, not a debug print. The closed
loop is: recall -> explain -> inject decision -> reinforcement -> patrol tuning.

### 3. Entity boost as a first-class index

LMC-5 has Y relations, but entity matching is currently implicit in relation
edges, keywords, FTS, or LLM-proposed relation hints. Mem0's entity store pattern
suggests adding an explicit entity layer:

- `lmc5_entities(entity_id, name, normalized_name, type, embedding, aliases)`
- `lmc5_memory_entities(memory_id, entity_id, role, confidence)`
- `entity_search(query) -> RecallHit(channel="entity")`
- entity hits become graph seeds only after passing confidence gates.

This should not replace Y. Entity boost is a retrieval seed; Y remains the
typed relation graph.

### 4. Soft memory decay via M-layer, not deletion

Mem0 decay is a bounded search-time multiplier based on recent access and hit
frequency. LMC-5 already stores `hit_count`, `last_hit`, `weight`,
`activation_boost`, and `protected`.

Recommended local shape:

- widen candidate pool before decay;
- compute `decay_factor` with a floor and ceiling;
- never apply decay to `protected` memories;
- never use decay to resurrect `superseded` or `archived` memories;
- record access reinforcement after recall injection, not merely after search.

This keeps decay as M-layer ranking pressure, not truth mutation.

### 5. Temporal ranking should use X + Z, not platform magic

Mem0 Platform temporal reasoning is not OSS, but the retrieval idea maps cleanly:

- `created_at` answers when the memory was stored;
- `valid_at` / `invalid_at` answer when the fact was true;
- `temporal_sequence` edges answer narrative order;
- `version_status` decides whether a fact is current, historical, or review.

LMC-5 should add a local temporal intent parser for queries like "last week",
"before X", "currently", and "upcoming", then convert that into ranking
features and eligibility labels.

### 6. Auto-dream gates and status visibility

Mem0 OpenCode's auto-dream gates are useful: time, session count, memory count,
and a lock file to avoid concurrent dream runs. LMC-5's `night_dream` should
surface equivalent readiness:

- last dream time;
- raw chunks waiting;
- candidate count;
- pending Z audits;
- relation backlog;
- whether a snapshot exists before apply;
- why dream did not run.

The key idea is not "dream more"; it is "make consolidation readiness visible".

### 7. Current-state memory as a materialized layer

Mem0 does not expose a single OSS feature literally called "state memory" in
the docs. The useful pattern is spread across three features:

- ADD-only + `linked_memory_ids` / `latest_only`: old facts remain stored, but
  search should prefer the latest linked/current memory.
- Message persistence: a small rolling session window keeps immediate runtime
  context separate from long-term memory.
- Platform temporal reasoning: queries like "right now" or "upcoming" rank
  current-state facts differently from historical facts.

LMC-5 should not copy this as another free-form memory category. The safer
shape is a derived `current_state` / startup-pack layer:

- source tables stay append-only: raw events, chunks, curated memories,
  relations, Z audits;
- nightly/boot jobs materialize a compact state pack from current facts, open
  threads, pending audits, recent raw chunks, dream status, and active persona
  boundaries;
- state records must carry provenance ids and an expiration/refresh rule;
- state can be rebuilt from durable memory and should never be the only source
  of truth;
- per-turn recall may read state first, then normal recall, then raw fallback.

This closes the continuity loop: write path records what happened, night path
distills what should persist, state pack says what is true now, recall injects
it, explain traces why it was injected, M-layer updates usage.

## What LMC-5 Should Not Copy Blindly

- Do not collapse Z-axis fact evolution into Mem0-style linked latest chains.
  LMC-5 needs explicit current, historical, review, superseded, and archived
  semantics.
- Do not let decay act as a filter. A stale but exact safety memory must still
  surface.
- Do not make entity linking auto-write dangerous semantic relations. Entity
  match can seed recall; contradiction, support, and cause/effect still require
  review.
- Do not rely on platform-only features for the OSS core. Build local analogs
  that preserve provider independence.
- Do not add telemetry or cloud callbacks to core memory paths.
- Do not store current state as hand-written truth without provenance. A state
  pack is a cache, not scripture. If it cannot be rebuilt, it is a hallucination
  with a timestamp.

## Proposed Implementation Order

1. Add `score_breakdown` to production `RecallHit` construction and final
   injection/debug output.
2. Add a local `recall_trace` table/file for explain output and injected vs
   dropped decisions.
3. Add recall reinforcement: update `hit_count`, `last_hit`, and
   `activation_boost` only for final injected curated memories.
4. Add a bounded M-layer `decay_factor` in final merge/rerank.
5. Add a derived `current_state` startup pack with provenance and TTL.
6. Add entity extraction/index tables and a separate entity recall channel.
7. Add temporal intent parser that maps query time phrases to
   `valid_at`/`invalid_at`/`created_at` ranking hints.
8. Add `night_dream status` or equivalent readiness reporting before apply.

Short version: mem0 caught up hard on practical pipeline ergonomics. LMC-5
still has the richer memory model, but it needs better observability and ranking
feedback loops. Architecture without telemetry is a cathedral with no windows.

## Local Implementation Status

Implemented on the local Mac LMC-5 path:

- Core SQLite recall now returns `score_breakdown` and a compact per-hit `trace`.
- `recall_runs` and `recall_trace_items` record query hash/preview, selected
  memory ids, rank, injected flag, score breakdown, relation provenance, and
  reasons.
- `recall-traces` CLI exposes recent explain rows for patrol/debug without
  manually querying SQLite.
- The pgvector multi-channel pipeline now normalizes channel scores into
  `metadata["score_breakdown"]`, merges breakdowns across deduped hits, marks
  final injected/rank metadata, and returns a content-light `RecallResult.trace`.

This closes the explain loop for "why did this memory enter context?" The next
loop is state/currentness: a rebuildable startup pack with provenance and TTL.

Implemented next on the local Mac LMC-5 path:

- `current_state_runs` and `current_state_items` provide a materialized,
  rebuildable state pack rather than a new source of truth.
- `refresh_current_state()` rebuilds state from current facts, active threads,
  pending Z audits, and recent raw events.
- Every state item carries `provenance`, `confidence`, and `expires_at`.
- `surface()` can include state first, while `state-refresh` / `state` expose
  the loop through the CLI.

This closes the "what should be treated as true now?" loop without weakening
Z-axis review. Old facts still live in durable memory; state is just a
time-bounded launch cache with receipts.

Implemented next on the local Mac LMC-5 path:

- `memory_entities` indexes deterministic entities from `fact_key`, tags,
  thread, titles, code-like tokens, quoted phrases, and conservative short CJK
  title terms.
- Recall now adds an `entity_boost` score component and records matched entity
  labels in each hit trace.
- Entity matches can rescue low-text-signal memories, but they stay visible in
  `reasons` and can be disabled with `--no-entity-boost`.
- `entities` CLI exposes the local entity index for debugging and patrol.

This brings over the useful part of mem0 entity boost without turning entity
linking into automatic truth. Entity is a relevance signal; Z still decides
which fact is current.

Implemented next on the local Mac LMC-5 path:

- Recall now detects lightweight recent/current intent terms such as `latest`,
  `recent`, `now`, `最近`, `刚才`, `今天`, and `当前`.
- Those control terms are stripped from first-stage text matching so they do not
  accidentally block recall.
- Matching live memories receive a bounded `temporal_boost` based on recency,
  and each hit trace records the detected temporal intent.
- `--no-temporal-boost` disables the ranking signal for debugging.

This brings over temporal ranking as a soft bias, not a filter. A stale but
exact protected/current memory can still surface; recentness only breaks ties
and improves "what is current?" queries.

Implemented next on the local Mac LMC-5 path:

- Core memories now track `last_hit_at` in addition to `hit_count`.
- `init()` performs a safe compatibility migration for older SQLite databases
  that do not yet have the column.
- Recall reinforcement updates both counters only for final injected hits.

This gives the M layer real usage evidence for future activation/decay policy.
The heavier decay policy is intentionally not guessed here; first collect the
receipts, then tune the metabolism. Fancy decay without evidence is just
spreadsheet cosplay.
