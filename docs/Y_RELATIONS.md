# Y — Relations

> "What other memories does this one connect to, and how?"

## What Y Answers

A memory in isolation is a data point. A memory connected to other memories
is a thought. Y answers: **what does this remind me of, support, contradict,
or explain?**

## Relation Types

| Type | Meaning | Safety |
|------|---------|--------|
| `same_event` | Two memories about the same incident | safe — auto-link |
| `same_topic` | Thematically related | safe — auto-link |
| `temporal_sequence` | A happened before/after B | safe — auto-link |
| `derived_from` | B was distilled/promoted from A | safe — auto-link |
| `emotional_link` | They feel the same (not the same topic) | safe — auto-link |
| `in_thread` | Both belong to the same X-line thread | safe — auto-link |
| `supports` | A provides evidence for B | review — needs judgment |
| `contradicts` | A and B disagree on a fact | review — needs judgment |
| `cause_effect` | A caused or led to B | review — needs judgment |

### Safe vs Review

Safe relations can be auto-created by the dream pass. Review relations
enter a queue and wait for manual or LLM-assisted judgment.

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
- **Only live edges.** `valid_until IS NULL` — expired edges don't walk.
- **Only live endpoints.** `version_status = 'current'` — superseded
  memories don't surface through graph walk.
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

## What Y Is Not

- Not a knowledge graph. It's a memory graph — edges represent experiential
  connections, not ontological categories.
- Not a recommendation engine. The goal is recall depth, not "users who
  liked X also liked Y."
- Not append-only. Relations can expire (`valid_until`), weaken, or be
  explicitly deleted when they stop being true.
