# Y — Relations

> "What other memories does this one connect to, and how?"

## What Y Answers

A memory in isolation is a data point. A memory connected to other memories
is a thought. Y answers: **what does this remind me of, support, contradict,
or explain?**

## Relation Types

| Type | Meaning | Safety | Example |
|------|---------|--------|---------|
| `same_issue` | Same problem or bug family | safe | Two fixes for the same failing command |
| `same_project` | Same project or repository | safe | Memories from the same codebase |
| `same_tool` | Same tool or runtime | safe | Two notes about Claude Code hooks |
| `same_event` | Two memories about the same incident | safe | Two accounts of the same debugging session |
| `same_topic` | Thematically related | safe | Multiple memories about API key management |
| `temporal_sequence` | A happened before/after B | safe | Setup → deployment → rollback sequence |
| `derived_from` | B was distilled/promoted from A | safe | Condensed memory ← original fragments |
| `emotional_link` | They feel the same (not same topic) | safe | Two late-night moments with similar loneliness |
| `in_thread` | Both on the same X-line narrative | safe | Both on the "engineering line" or "relationship line" |
| `same_person` | Both involve the same person | safe | All memories mentioning a specific friend/colleague |
| `in_episode` | Part of the same scene/episode | safe | All memories from "the night of the jasmine tea" |
| `instance_of` | Specific instance of a general pattern | safe | "She said 'forget it' on June 3" → general pattern "she says 'forget it' when suppressing" |
| `supports` | A provides evidence for B | review | A correction that validates an existing rule |
| `contradicts` | A and B disagree on a fact | review | "She likes mornings" vs "She hates mornings" |
| `cause_effect` | A caused or led to B | review | A broken promise that led to a trust conversation |

`contradiction` is accepted as a compatibility alias and stored as the
canonical `contradicts` relation type.

### Safe vs Review

Safe relations can be auto-created by the dream pass and can participate in
default graph expansion. Review relations enter a queue and wait for manual or
LLM-assisted judgment; they do not auto-expand through normal recall.

Why the split? Because `contradicts` is the most dangerous edge in a
persona's memory. Auto-creating a contradiction edge between "she likes
being interrupted" and "she hates being interrupted" — when the real
situation is mood-dependent — corrupts the persona's understanding of
the user. One private deployment measured a **67% false-positive rate**
on LLM-judged contradictions before expanding the judgment rules.

## Graph Walk

When a memory is recalled, Y expands it: "you found this one — here are
its neighbors."

### Two-Hop Expansion

```
seed memories (from vector/FTS hit)
  → hop 1: neighbors with strength > 0.4
    → hop 2: neighbors of hop1 with strength > 0.7
```

Hop 2 is stricter because noise compounds. A memory two hops away needs
a strong connection to be worth surfacing.

### Walk Rules

- **Bidirectional.** source→target and target→source both count.
- **Only live edges.** Production pgvector deployments check
  `valid_until IS NULL` — expired edges don't walk. The minimal SQLite core
  does not store `valid_until`; use review/patrol or your backend to retire
  edges that should stop walking.
- **Only live endpoints.** Minimal core requires `status = 'current'`; fact
  memories must also have `active_fact = 1`. Superseded, archived, review,
  and inactive fact memories don't surface through graph walk.
- **Only safe relation types.** Review relations (`contradicts`,
  `cause_effect`, `supports`) don't auto-expand — they need explicit handling.
- **Hub avoidance.** Nodes of type `thread` or `concept` have high degree
  and would flood the walk. Skip them as intermediaries.
- **No self-loops.**

### Strength

Relations have a `strength` field (0.0–1.0). This is not just similarity —
it's *relevance strength*. Two memories can be highly similar but weakly
related (two different dinners), or moderately similar but strongly related
(a promise and the moment it was broken).

Strength is initially set by the creator (LLM proposer or manual) and can
be adjusted by metabolism over time.

The minimal core validates `strength` at write time and uses stricter hop-2
thresholds because noise compounds across graph expansion.

### Implementation Checklist

If you add, rename, or reclassify a relation type, update all of these in the
same patch:

- `src/lmc5/models.py`: `RELATION_TYPES`, safe/review classification,
  symmetric classification, and aliases.
- CLI/API entry points: they should import the shared model constants instead
  of copying a stale list.
- `src/lmc5/hippocampus.py`: relation hints must normalize aliases and route
  safe versus review relations correctly.
- Tests: cover accepted types, review types not auto-expanding, strength
  thresholds, live endpoint filtering, and symmetric duplicate prevention.
- Run the [two-hop example](../examples/two_hop_graph.py) to inspect relation expansion.

## What Y Is Not

- Not a knowledge graph. It's a memory graph — edges represent experiential
  connections, not ontological categories.
- Not a recommendation engine. The goal is recall depth, not "users who
  liked X also liked Y."
- Not append-only. Relations can expire (`valid_until`), weaken, or be
  explicitly deleted when they stop being true.

## ⚠️ How to Actually Build Relations

**This is the section most people miss — and it's the difference between
a working memory graph and a pile of disconnected rows.**

The PostgreSQL `lmc5_memory_relations` table starts empty. Inserting
into `lmc5_curated_memories` does nothing for Y. The graph stays empty until
you explicitly run the relation-build pass.

### The Pass

The nighttime relation-build pass lives in
`extras/pgvector_backend/night_dream.py` and runs **inside the `hippocampus`
step of `dream_runner`** — there is no standalone "build relations" step in
the pipeline. The pass does:

1. Yesterday's `conversation_chunks` are passed to the housekeeper LLM
   proposer.
2. The proposer outputs structured candidates: `title / content / type /
   importance / risk / relation_hints` — the LLM commits to relation types
   **at proposal time**, not afterward.
3. Gate chain filters candidates: noise → safety/PII → importance threshold
   → risk classification → batch dedup. Only promoted candidates survive.
4. For each promoted candidate, `find_neighbors` fetches the top-K nearest
   neighbors by vector. Relations are written using the candidate's own
   `relation_hints`: types in `SAFE_RELATION_TYPES` become direct edges;
   types in `REVIEW_RELATION_TYPES` go to the audit queue.

### Run and verify a batch

Have your application's scheduled job call a configured `DreamRunner`. Supply
`consolidate` and `hippocampus` callbacks. In the latter, pass a batch of
chunks to `NightDream.run()` and provide the candidate writer, neighbor lookup,
safe-edge writer and review queue callbacks. Preview the candidates first.
[Scheduling and verifying a job](DEPLOYMENT.md)

After applying a test batch with a known safe pair, check the two memory IDs,
relation type and strength in the reference PostgreSQL schema:

```sql
SELECT source_id, target_id, relation_type, strength
FROM lmc5_memory_relations
WHERE valid_until IS NULL
ORDER BY created_at DESC LIMIT 20;
```

A batch with no eligible neighbors can produce zero edges. If an expected
pair is missing, inspect the candidate gate results, neighbor lookup and
relation writer logs. To test the SQLite graph independently, run
`PYTHONPATH=src python3 examples/two_hop_graph.py` from the checkout root.
