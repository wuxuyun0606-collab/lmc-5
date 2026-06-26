# M — Metabolism

> "Should this memory be promoted, demoted, archived, or left alone?"

## What M Answers

A memory system without metabolism is a hoarder. Everything stays at the
same priority forever. The first test log from three months ago sits next
to yesterday's breakthrough. M answers: **what should happen to this memory
over time?**

## The Decay Formula

Memory weight decays over time, but not uniformly. The formula:

```
score = time_weight × importance × activation^0.3 × decay × emotion_weight × resolved_factor
```

### Key Components

**Time weight:** Recent memories score higher. Rapid decay in the first
3 days (short-term dominant), then emotion takes over (long-term dominant).

```
≤ 1 day:  1.3x
≤ 3 days: 1.2x
≤ 7 days: 1.1x
> 30 days: 0.9x
```

**Decay:** Ebbinghaus-inspired exponential decay with category-specific
half-lives (see below).

**Emotion weight:** High-arousal memories decay slower. `1.0 + arousal × 0.8`
means a memory with `arousal=0.8` gets a 1.64x retention boost.

**Resolved factor:** Resolved/completed memories decay faster (0.7x).
Unresolved tensions stick around — that's by design.

## Category Half-Lives

Different types of memory deserve different lifespans:

| Category | Half-Life | Rationale |
|----------|-----------|-----------|
| `heartbeat` | ∞ (never) | Intimate moments define the relationship |
| `identity` | ∞ (never) | Who the user is; who the AI is |
| `core` | 90 days | Important but not eternal |
| `fragments` | 90 days | Emotional snapshots |
| `important` | 90 days | Behavioral lessons |
| `reviews` | 60 days | Pending review items |
| `diary` | 60 days | Daily records |
| `mailbox` | 60 days | Correspondence |
| `knowledge` | 30 days | How-to; system config |
| `notebook` | 30 days | Reference notes |
| `conversation` | 14 days | Chat excerpts (raw) |

**Default** for unlisted categories: 45 days.

### Why ∞ for Heartbeat and Identity

A persona that forgets the user's name is worse than no persona. A persona
that forgets its first kiss is a different person.

`protected=true` memories skip metabolism entirely. The patrol job doesn't
touch them. The decay formula returns infinity for their half-life. They
are architecturally immortal.

## Cold Storage

Memories that decay below a threshold (weight < 0.3, age > half-life × 3)
are candidates for cold storage. Cold storage means:

- Not included in recall results
- Not expanded via graph walk
- Preserved in the database (never deleted)
- Restorable if needed

This is archival, not deletion. A persona should never lose a memory
permanently — but it should stop being distracted by three-month-old
tool debugging logs.

## Recall Gate vs. Surface Gate

M does not only decide how long a memory lives. It also decides whether a
memory is allowed to enter an output channel.

LMC-5 uses two gates:

- **`recall` gate:** used when the user or agent explicitly searches memory.
  It is lenient. `retain` memories are fully eligible; `cold` memories can
  return with a lower score; `quarantine` memories are excluded.
- **`surface` gate:** used when the system proactively surfaces memory into
  the current context. It is stricter. `cold` and `quarantine` memories do
  not interrupt the active window.

The default buckets are:

| Bucket | Meaning | Recall | Surface |
|---|---|---|---|
| `retain` | Useful, reliable, or protected memory | yes | yes |
| `cold` | Preserved but low-activity or low-priority memory | down-ranked | no |
| `quarantine` | Debug logs, scratch notes, transient noise, unsafe status | no | no |

This distinction matters because "can be found if asked" and "should
interrupt the current context" are different questions. A one-off debug trace
may be worth preserving for audit, but it should not keep jumping into recall
or spontaneous surfacing.

The minimal implementation exposes this through `lmc5.scoring.metabolic_gate`.
`MemoryStore.recall()` uses the recall gate; `MemoryStore.surface()` uses the
surface gate for curated memories.

## Deduplication

Before a new memory is written, check if a near-identical one already
exists. Default threshold: cosine similarity ≥ 0.92.

If a duplicate is found:
- Keep the existing memory (it has history: hit count, relations, scores)
- Reject the new one with reason `semantic_dup:<existing_id>`
- Log the rejection

This prevents the "night after night the dream pass writes the same
observation" failure. One private deployment accumulated 7,242 near-duplicate
relation edges before dedup was added.

## Condensation

When multiple memories about the same topic accumulate (same thread,
similar content, spanning weeks), they can be condensed into a single
higher-weight memory that captures the pattern.

Example: Five separate memories about "she prefers short messages on
Telegram" condense into one strong preference memory.

Condensation is a metabolism *suggestion*, not an automatic action. The
patrol reports candidates; the AI or user decides whether to condense.

## Patrol (Read-Only)

`metabolism.py` / `patrol` runs periodic read-only checks:

- **Duplicate current facts:** Same `fact_key`, both `current` — one should
  be superseded
- **Review backlog:** How many `pending` audits, `review` memories,
  unresolved conflicts
- **Thread splits:** A thread has grown too large and should be split
- **Other-thread incubation:** `other` is checked in three stages —
  observation cluster, candidate line, formal split candidate
- **Decay candidates:** Memories below threshold, ready for cold storage
- **Stale/non-live relations:** Edges touching `superseded`, `archived`,
  `review`, or inactive fact memories
- **Orphaned relations:** Edges pointing at missing memories, usually from
  legacy/manual database edits
- **Relation self-loops:** A memory connected to itself
- **Reciprocal duplicate relations:** Symmetric relation types stored twice as
  both A→B and B→A

Patrol **never writes**. It reports. The dream runner or a human acts on
the report.

## What M Is Not

- Not garbage collection. Nothing is deleted. Everything is preserved.
- Not automatic. Patrol suggests; the system or human decides.
- Not uniform. Different categories, different lifespans. This is the
  whole point — a persona's metabolism mirrors how human memory works:
  some things fade, some things don't, and the things that don't fade
  are the ones that define you.
