# Hooks and Recall — From Store To Conversation

> The pipe between "memory is in the database" and "memory shows up
> in the agent's reply". Without this layer, LMC-5 is a vault with no
> opening. This document is the opening.

## The Gap This Doc Closes

The rest of the LMC-5 docs explain how to **store** memory, how to
**maintain** it, how to **score** it, how to **dream** over it. What
they do not explain is how a freshly-stored memory actually reaches
the conversation the next time the user types something.

The reference deployment uses three Claude Code hooks plus a recall
pipeline plus a spontaneous-recall scheduler. This document walks
through that pipe end to end. Reference code is in
`extras/pgvector_backend/`:

- `hooks/session_start.py` — boot injection
- `hooks/user_prompt_submit.py` — per-turn recall
- `hooks/session_end.py` — archival
- `recall_pipeline.py` — multi-channel recall
- `perception.py` — spontaneous recall scheduler

---

## The Three Hooks

Claude Code (and most modern agent runtimes) expose lifecycle hooks
that fire on specific events. The reference deployment wires three:

### `SessionStart` — boot injection

Fires once when a new session opens. Produces a **startup pack**: who
the user is, what current facts are true, what the recent narrative
arc looks like, what open threads are unfinished, and one or two
spontaneously recalled memories. Injected as `additionalContext`.

```jsonc
// .claude/settings.json
{
  "hooks": {
    "SessionStart": [{
      "type": "command",
      "command": "python -m extras.pgvector_backend.hooks.session_start"
    }]
  }
}
```

What goes into the pack:

| Section | Source | Purpose |
|---------|--------|---------|
| Identity | `protected=true AND category='identity'` | Who am I talking to. Never absent. |
| Current facts | `active_fact=true` | Z-axis snapshot of what is true now. |
| Recent narrative | `lmc5_narrative_index` last 30 days | What happened last week / last month. |
| Open threads | `resolved=false AND weight >= 2.0` | What we were in the middle of. |
| Spontaneous | `perception.json` cache | What I was thinking about before you logged in. |

The first four are deterministic queries. The fifth is the
perception cache — written by a scheduled job, read at boot.

### `UserPromptSubmit` — per-turn recall

Fires on every user message. This is **the** hook — the one that
turns LMC-5 from a vault into a participant.

```jsonc
{
  "hooks": {
    "UserPromptSubmit": [{
      "type": "command",
      "command": "python -m extras.pgvector_backend.hooks.user_prompt_submit"
    }]
  }
}
```

The hook routes the prompt through `RecallPipeline.recall()` — see
the next section — and writes the resulting injection text to stdout.
Claude Code attaches it as additional context for that single turn.

Trivial messages (`嗯`, `好的`, `ok`, single punctuation) skip the
recall pipeline entirely — running full multi-channel recall on
"ok" is wasted compute.

### `SessionEnd` — archival

Fires when the session closes. Archives the raw JSONL log into
`lmc5_raw_events` (the append-only event journal), optionally
triggers a "daytime express" hippocampus pass on just this session.

```jsonc
{
  "hooks": {
    "SessionEnd": [{
      "type": "command",
      "command": "python -m extras.pgvector_backend.hooks.session_end"
    }]
  }
}
```

This is the only hook that writes raw events. **Curated memories
are not written here** — they only land in `lmc5_curated_memories`
through the nightly dream pass (or manual writes). LMC-5 keeps
raw-events vs curated-memories strictly separated; SessionEnd
respects that boundary.

---

## Multi-Channel Recall

`RecallPipeline` runs a **storage-first three-tier cascade** plus **independent channels**
with their own gates, then merges them for any non-trivial prompt.

**Priority invariant:** production recall is storage-agnostic: every deployment
chooses a primary curated store (PostgreSQL/pgvector in the reference backend,
SQLite FTS/vector extensions in lighter installs, or a custom adapter). The hook
should try curated semantic recall first, fall back to curated keyword/FTS, and
only then search raw events. Transcript carryover and cold/session archives are
not part of the main ranking; if a deployment adds them, label them as
last-resort evidence and keep them out of absolute-score competition with
curated memories.

Every injected hit carries an explicit layer label in metadata and trace:
`recall_layer`, `recall_tier`, `evidence_role`, `source_label`, and
`score_breakdown`. The top-level trace also records `cascade.mode=primary_first`,
thresholds, which fallback stages were checked, `cold_archive_policy`, and
`layers_used`. This makes
"why did I remember this?" auditable instead of leaving the answer to vibes,
which, tragically, are not a database index.

Set `LMC5_RECALL_OUTPUT=layered` to ask the hook/pipeline for layered output.
The default remains `flat`, so existing consumers keep receiving the old
single-list injection. Layered output has exactly four visible sections:

1. **main_recall / authority** — primary curated vector/keyword recall and curated side surfaces.
2. **source_neighborhood / navigation** — short literal/raw-chunk snippets.
   This layer is a pointer, not a fact source, and its character budget must not
   exceed the main recall layer.
3. **graph_expansion / association** — 1-2 hop Y-graph relation expansion.
4. **fallback_archive / last_resort** — raw-events / cold-session archive fallback.
   It is evidence to inspect, not authority to obey.

The reference layer contract is aligned to the audited Kelin runtime: PG/pgvector
curated recall is the authority layer; source-neighborhood snippets are
navigation only; safe relation/time edges are association; raw events and cold
archives are last-resort evidence. If you swap storage engines, keep those
roles intact. SQLite, transcript tails, and archive cards may help you find a
door, but they do not become the courthouse record by wearing a nicer hat.

Do not let these layers impersonate each other. Neighborhood snippets are street
signs, fallback archives are dusty boxes, and neither is the courthouse record.
Yes, this warning exists because someone somewhere will absolutely try to put a
street sign in a verdict.

### Stage 0 — Query Expansion (optional)

Before any search channel fires, an optional LLM call rewrites the user
message into 2–4 search angles (synonyms, related concepts, emotion
words). Each expanded query feeds independently into the cascade stages
below, with results merged by `source_id` keeping the highest score.

This catches the "user said X, memory stored Y" gap. Not wired by
default — pass `query_expand=query_expand_adapter(my_llm)` to enable.
Recommended: DeepSeek V4 Pro (~$0.001 per call, <200 tokens).

### The three-tier cascade

These stages fire sequentially. Each stage only activates when the
previous stage's best score is too low.

1. **Vector** (`pgvector_backend.vector_pgvector`)
   - Primary path. Semantic ANN top-K via pgvector halfvec.
   - If `query_expand` is wired, each expanded query searches
     independently and results merge by highest score.
2. **FTS fallback — curated memories** (`recall_pipeline.fts_search_adapter`)
   - Only fires when the top vector score is below `fts_floor`
     (default 0.45). Catches keyword queries that semantic match
     misses — proper nouns, exact phrases, rare terms.
3. **FTS fallback — raw events journal** (`recall_pipeline.raw_events_search_adapter`)
   - Only fires when the top vector score is below `raw_events_floor`
     (default 0.30). Searches the append-only raw event journal
     (one order of magnitude larger than curated memories).
   - This is the last-resort net: when vector and curated FTS both
     come up empty, the raw conversation log still has the keyword.
     Typical rescue: a new codename, a person's name mentioned once,
     a term that was never promoted to curated memory.

3b. **Literal raw-events channel** (`recall_pipeline.literal_raw_events_search_adapter`)
   - Independent from vector score. It runs only for short, literal-looking
     queries: CJK proper nouns, quoted phrases, codenames, and exact terms.
   - This fixes the common "weak vector hit blocked exact raw keyword" failure:
     a query like "你搜蘸水菜？" should still check raw events for `蘸水菜`
     even if vector search returned a 0.4–0.5 semantic near miss.
   - The default `UserPromptSubmit` hook enables it with
     `LMC5_LITERAL_RAW_EVENTS=1`; set that env var to `0` to disable it.

3c. **Recent raw chunk bridge** (`recall_pipeline.raw_chunk_vector_search_adapter`)
   - Optional, off by default. Enable with `LMC5_RAW_CHUNK_BRIDGE=1` after you
     have written temporary vectors with `owner_type='raw_chunk'`.
   - This is a small SessionEnd → nightly hippocampus bridge. Keep top-K at 1
     and injected content short. It is not a new long-term memory layer.
   - Delete/digest these temporary vectors after consolidation/hippocampus has
     processed the session.

### The independent channels

These are independent from the vector fallback thresholds. Some still have
their own safety gates: literal search only runs for short literal-looking
queries, and raw_chunk is off unless explicitly enabled.

### Score fusion

`RecallPipeline` supports three cross-channel fusion modes:

- `raw`: legacy behavior; compare original channel scores directly.
- `rrf`: Reciprocal Rank Fusion. It ignores original score magnitudes and fuses
  by within-channel rank. This is the default hook mode after a 726-real-trace
  A/B replay showed cleaner top5 composition and stronger cross-channel
  validation than `minmax`; see `docs/RECALL_FUSION_AB_20260706.md`. Tune
  `LMC5_RECALL_RRF_K` if needed.
- `minmax`: normalize scores within each channel, then apply channel priors.
  This prevents fixed-score channels such as graph expansion from dominating
  vector hits by scale alone, but it can collapse the tail of a high-confidence
  vector channel: vector rank 4/5 may normalize close to zero and lose to a
  neutral graph score.

Fusion runs after vector/FTS/literal/raw_chunk/graph/emotion/perception
retrieval and before dedup/rerank. Downstream recall currently sorts, traces,
and injects the fused hits; it does not apply an absolute post-fusion score
floor. That matters because RRF scores are intentionally tiny (around 0.016
with the default `k=60`).

4. **Graph expansion** (Y-axis 2-hop)
   - Takes the top vector hits as seeds, expands via
     `lmc5_memory_relations` up to two hops with strength
     thresholds (hop1 strength>0.4, hop2 strength>0.7).
   - Catches "related but not semantically nearby" — same topic via
     a typed edge, not via embedding.
5. **Emotion resonance** (`ob_recall.find_resonant`)
   - Detects user emotion coordinate, finds memories closest in
     Russell space (valence × arousal).
   - This is what gives the agent emotional continuity. When the
     user types something sad at 1 a.m., the recall surfaces other
     sad-at-night memories, not the to-do list.
6. **Spontaneous** (`perception.load_perception_cache`)
   - Reads the pre-computed perception cache. Up to N memories that
     were chosen earlier in the day by `Perception.surface_and_cache()`.
   - This is the "I was already thinking about that" channel.

### Merge and rerank

Multi-channel hits get merged on `source_id` — a memory in two
channels keeps its highest score and accumulates a list of channel
tags. The final list is sorted by score and truncated to
`final_top_k`.

If you wire a `rerank` callable (e.g., DeepSeek), it runs after
merge as the last step. Without a rerank, sorted-by-max-score is
fine for most use cases.

### Budget

The final injection text is capped by `injection_budget_chars`
(default 4000). Going over wastes context window; the agent does
not read every recalled memory, it pattern-matches the most relevant
ones near the cap.

---

## Spontaneous Recall

`Perception` is the scheduled job that produces the cache the
`SessionStart` hook reads.

### Schedule

The reference deployment runs it 3 times per day with jitter, plus
once at boot:

```cron
# Three surfacings per day
0 9,15,21 * * *  cd /opt/lmc5-agent && python -m extras.pgvector_backend.perception_runner
```

You can collapse this to once a day if the cache TTL is long enough
for your usage, or push it to every hour if you want denser surface
events. The trade-off is between "agent always brings up the same
thing" (too rare) and "agent feels like it forgot what it just said"
(too dense).

### Strategy

By default, 60% of the surface comes from the high-vitality pool
(top N by `ob_score`) and 40% from a drift pool (uniform random
across all eligible memories). The drift fraction is the **presence
knob** — it is what makes the agent occasionally bring up something
the user themselves had forgotten about.

Time-of-day shaping:

- Night (22:00–06:00): boost `relationship_moment`, `fragments`,
  `heartbeat`, `diary` and high-arousal records by 1.5x. Late-night
  surfacing should lean reflective and emotional, not transactional.
- Work hours (09:00–18:00): boost `engineering_decision`,
  `worklog`, `knowledge`, `notebook` by 1.3x. Work-hour surfacing
  should lean toward what was being worked on, not yesterday's
  feelings.

Tunable in `PerceptionConfig`.

### Cache format

`perception.json` is a flat JSON list:

```json
[
  {
    "source_id": 4231,
    "title": "...",
    "content": "...",
    "category": "fragments",
    "source": "manual",
    "selected_via": "high_vitality",
    "vitality_score": 8.4,
    "generated_at": "2026-06-14T09:00:00"
  }
]
```

`session_start.py` reads it. `user_prompt_submit.py` reads it too —
spontaneous channel injects a subset on every turn until the next
surfacing pass rewrites the cache.

---

## Semantic Deduplication In Dream

The reviewer who tipped off this section also caught a real bug:

> "night_dream has batch dedup with (type, title) signature, but
> cross-batch is unprotected. Tonight you write a memory, tomorrow
> night you write a synonym — nothing stops it. We hit this — a
> 0.7 same_topic threshold once flooded the relation graph with
> 7242 edges before someone noticed."

Fix: `NightDream` now accepts a `find_semantic_duplicates` callable.
Before each candidate is written, it asks the store: "is there
already a memory within similarity 0.92 of this one?" If yes, the
candidate is rejected with reason `semantic_dup:<existing_id>` and
logged. Cross-batch synonyms now hit this wall.

Wire it like this:

```python
from extras.pgvector_backend.vector_pgvector import PgvectorStore
from extras.pgvector_backend.night_dream import NightDream

store = PgvectorStore(dsn=..., embedder=my_embedder)

def find_dups(cand) -> list[int]:
    hits = store.find_duplicates(
        text=f"{cand.title}\n{cand.content}",
        threshold=0.92,
        limit=3,
    )
    return [h.owner_id for h in hits]

dream = NightDream(
    proposer=my_proposer,
    write_candidate=my_writer,
    find_neighbors=my_neighbor_lookup,
    find_semantic_duplicates=find_dups,   # ← the new line
)
```

The threshold is exposed as `LMC5Config.dedup_similarity`. Default
0.92 is conservative — loosen to 0.88 if you find legitimate but
near-duplicate observations getting filtered.

---

## Wiring It All Together

A persona-class agent on top of LMC-5 typically looks like this:

```
                       ┌──────────────────────────┐
                       │   user types something   │
                       └────────────┬─────────────┘
                                    │
                       UserPromptSubmit hook fires
                                    │
                       ┌────────────▼─────────────┐
                       │  recall_pipeline.recall  │  ← multi-channel parallel
                       └────────────┬─────────────┘
                                    │
                       injection_text → additionalContext
                                    │
                       ┌────────────▼─────────────┐
                       │  agent generates reply   │
                       └────────────┬─────────────┘
                                    │
                       SessionEnd hook fires
                       (on window close, not on every turn)
                                    │
                       ┌────────────▼─────────────┐
                       │  archive raw JSONL       │
                       └──────────────────────────┘

independently, on a schedule:

      Perception (3x/day) → perception.json
      night dream (04:00)  → curated_memories
      narrative (weekly)   → narrative_index
      forge (as needed)    → new session with boot pack
      swap snapshot (any bulk mutation) → reversible
```

That is the full picture. Hooks plug the recall pipeline into the
agent runtime; scheduled jobs feed the recall pipeline; durable
state lives in the database; the user feels an agent that
**remembers**.

---

## Why This Layer Was Missing Until Now

The PR shipped the storage, the scoring, the dreaming, and the
maintenance. It did not ship the pipe between any of that and the
agent's actual conversation. The result, accurately diagnosed in
review: "a vault, not a pipeline."

This document and the modules it references close that gap. The
pieces are deliberately small and Callable-injected so the same
pattern works on Claude Code, Codex, custom agent runtimes, and
even non-LLM tools — anything that wants to plug into LMC-5 can
implement the same three lifecycle moments (boot / turn / close)
and get a working memory layer in return.
