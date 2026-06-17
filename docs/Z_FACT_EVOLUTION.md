# Z — Fact Evolution

> "Is this still true?"

## What Z Answers

Memory systems that can't tell "historically true" from "currently true"
aren't remembering — they're hoarding. Z answers: **which version of this
fact is the one I should act on?**

## The Lifecycle of a Fact

```
current  →  superseded  (a newer version replaced it)
         →  historical  (no longer true, but preserved)
         →  archived    (too old to surface, but not deleted)
         →  under_review (flagged for conflict, awaiting judgment)
```

Every curated memory has a `version_status` field. Only `current` memories
participate in recall. The rest are preserved — you can always look back —
but they don't influence behavior.

In the minimal SQLite core this is enforced on every recall path: recent
fallback, FTS search, LIKE fallback, and Y graph expansion all filter to live
memories. If a memory has a `fact_key`, it must also be `active_fact=1` before
it can surface in normal recall. Historical and superseded facts are still in
the database for audit, but they do not quietly leak back into behavior.

### Supersession via fact_key

A `fact_key` is a unique identifier for a fact slot. Example:
`user.profile.city`. When a new memory claims the same fact_key, the old
one is superseded — its `version_status` changes to `superseded` and it
gets a `superseded_at` timestamp.

This is how "she lives in Beijing" gets replaced by "she moved to Guangzhou"
without deleting the Beijing memory.

## Conflict Audit

Z's most critical mechanism: **never auto-supersede without review.**

### Why Not Auto-Supersede

For a coding agent, auto-supersede is fine — yesterday's wrong answer
should be overwritten by today's correct one.

For a persona, most "contradictions" aren't facts being overwritten:

- **Mood shifts:** "I'm fine" today doesn't supersede "I was exhausted"
  yesterday. Both are true at their respective times.
- **Sarcasm / roleplay:** "I hate you" said while laughing is not a
  relationship fact.
- **Evolving preferences:** "I like quiet mornings" and "I want to go
  clubbing" can both be true for the same person at different life stages.
- **Tone shifts:** A stated boundary in anger may be softened later.
  Neither version is "wrong."

One private deployment tested LLM-based auto-supersession with a simple
three-line rule. **67% of proposed supersessions were false positives.**
The rule couldn't distinguish fact overwrite from tone shift, mood change,
or context-dependent preference.

### The Manual Gate

```
z_conflict_audits.status = 'pending'  (default — always)
                         = 'approved' (human or AI-with-rules approved)
                         = 'rejected' (not a real conflict)
```

1. Z-audit discovers candidate conflicts (same `fact_key`, `contradicts`
   relation, or semantic overlap with opposite valence). Explicit relation
   audits only consider live `current` or `review` endpoints, not archived or
   superseded history.
2. Candidates land in `z_conflict_audits` as `pending`.
3. Nothing happens to the memories themselves.
4. A human or a carefully-prompted LLM reviews each pending audit and
   decides: supersede, keep both, reject as non-conflict, or merge.

**The key principle:** models may help *label* a pending audit. Local
policy decides whether a fact is superseded. The Z-line gate is the one
place where "AI suggests, human decides" must be enforced — or at minimum,
"AI suggests, better-AI-with-explicit-rules decides."

### Deduplication

Before conflict audit, dedup catches near-identical memories that aren't
contradictions — they're just the same thing said twice. Default threshold:
cosine similarity ≥ 0.92. The newer duplicate is rejected; the older one
stays.

## Dual Timestamps

Each memory can have:
- `superseded_at` — when this version was replaced by a newer one
- `invalid_at` — when this fact stopped being true (independent of
  whether a replacement exists)

Both are null for `current` memories. Both can be set independently —
a fact can become invalid without being superseded (the user stopped
doing something, but no replacement fact exists).

## What Z Is Not

- Not a version control system. It doesn't store diffs or branches —
  just the lifecycle state of each memory.
- Not a truth engine. Z tracks *which version the persona should act on*,
  not *which version is objectively correct*.
- Not aggressive. The default posture is conservative: when in doubt,
  keep both versions as current and let the conflict audit sort it out.
