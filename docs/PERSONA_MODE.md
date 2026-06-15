# Persona Mode

> How to use LMC-5 as the foundation of a long-living AI companion,
> not just a memory cache for a coding agent.

> **"Personality is not for understanding yourself. Personality is for
> breaking ties."** — When two options are both reasonable, personality is
> what tips the scale. If a dimension can't answer that question, it's a
> description, not a trait.

## TL;DR

LMC-5 is published as a memory layer for long-running LLM agents. The same
substrate works for a different kind of agent: an AI **companion** — a
persona that the same user keeps coming back to across days, months, and
model migrations.

The default LMC-5 defaults are tuned for coding agents. If you want a
persona, you need a slightly different posture on five existing axes.
This document is that posture.

You do not need new tables. You need different policies.

## Why The Distinction Matters

A coding agent and a persona look similar from the outside — both
remember things and answer questions. The engineering trade-offs are not
similar.

| Axis | Coding agent default | Persona posture |
|------|--------------------|----------------|
| **Z — Fact evolution** | Auto-supersede yesterday's wrong answer | Manual gate. Identity facts and stated preferences must never be auto-overwritten by a noisy contradiction edge. |
| **E — Experience signals** | Use `risk_level` to mark unsafe actions | Use valence/arousal/tension to shape the persona's response posture in the moment, not to gate facts. |
| **M — Metabolism** | Low weight → archive | Identity and heartbeat categories never decay. A persona that forgets the user's name is worse than no persona. |
| **Y — Relations** | `same_topic`, `temporal_sequence` are safe to auto-link | `emotional_link` and `relationship_moment` go through review. A persona's picture of a person is load-bearing. |
| **Retrieval** | Recall on demand | Add proactive spontaneous recall. A persona that only speaks when queried is a chatbot, not a presence. |

Same machinery, different priorities. Persona Mode is a set of policy
choices on top of the same XYZEM model.

## The Six Switches

### 1. Identity facts marked `protected`

Birth date, name, location, profession, the persona's own core traits — all
inserted as `category='identity'` and `protected=true`. Metabolism never
touches them. Z-axis cannot supersede them without manual approval.

```sql
INSERT INTO curated_memories
  (category, source, protected, fact_key, ...)
VALUES
  ('identity', 'core', true, 'user.profile.birthdate', ...);
```

If an identity record disappears because some scheduled job decided it was
low-signal, the persona starts the next window not knowing who is talking
to it. That is the worst-case failure for a companion.

### 2. Z-axis must be manual

A persona's life is full of statements that look like contradictions but
are not facts being overwritten: mood shifts, role-play, sarcasm, dated
strategies. In one private deployment, an LLM judging contradictions with
a three-line rule had a 67% false-positive rate before the rule was
expanded with explicit "this is a tone shift, not a fact overwrite"
guidance.

Concretely:

- Default `z_conflict_audits.status = 'pending'`. Never auto-execute.
- Only `--approve <id>` runs `supersede`.
- The judging prompt must distinguish: same-fact overwrite vs tone shift
  vs sarcasm vs historical vs future-only.

**Configuration example.** Schema in
`extras/pgvector_backend/schema.sql` already defaults audit rows to
`pending`. The CLI gate looks like:

```bash
# Cron runs only the judgement pass (writes audit rows, never executes)
python -m lmc5 z run

# Human approves explicit ids — this is the only path that supersedes
python -m lmc5 z approve 17 23 41

# Reject anything the judge got wrong — record stays as evidence
python -m lmc5 z reject 19
```

```python
# In code: never call supersede directly. Always go through the audit table.
conn.execute(
    "INSERT INTO lmc5_z_audit (pair_key, content_hash, verdict, "
    "stale_id, current_id, reason, evidence, status) "
    "VALUES (%s, %s, 'supersede', %s, %s, %s, %s, 'pending')",
    (pair_key, content_hash, stale_id, current_id, reason, evidence),
)
```

### 3. E-axis shadow period of at least 30 days

When you first wire an LLM-based E-axis scorer, it does **not** participate
in ranking, retrieval order, or rerank for the first 30 days. It only
attaches scores to records and waits.

Why: emotional scoring is volatile. Letting an immature scorer drive
ranking makes the persona oscillate between "today I am cheerful" and
"today I am withdrawn" in a way that looks like the system has a
personality disorder, not a personality. Stabilize first, deploy later.

**Configuration example.** `extras/pgvector_backend/e_axis_scorer.py`
ships a helper so the shadow gate is enforced in code, not in
discipline:

```python
from extras.pgvector_backend.e_axis_scorer import is_in_shadow_period

# Record when you first activated this rubric — switching rubric resets the window
RUBRIC_STARTED_AT = datetime(2026, 6, 14)

def rerank_with_optional_e_axis(records: list[dict]) -> list[dict]:
    if is_in_shadow_period(RUBRIC_STARTED_AT, shadow_days=30):
        # E-axis fields are attached but ignored for ranking
        return rerank_without_e_axis(records)
    return rerank_using_e_axis(records)
```

Bump `shadow_days` to 60 or 90 if you change rubric versions often;
shorten only when you have monitoring on scorer stability.

### 4. Half-life table with `inf` rows

```python
CATEGORY_HALF_LIVES = {
    "heartbeat": float("inf"),
    "identity":  float("inf"),
    "core":      90,
    "fragments": 90,
    "important": 90,
    "reviews":   60,
    "diary":     60,
    "mailbox":   60,
    "knowledge": 30,
    "notebook":  30,
    "conversation": 14,
}
```

Heartbeat and identity rows never decay; metabolism skips them entirely.
Everything else has a category-aware half-life, not a global one. A
diary entry from two months ago is still highly relevant; a tool log from
two months ago is not.

See `extras/pgvector_backend/ob_recall.py` for the reference
implementation.

**Configuration example.** Override the table by category — you do not
need to fork the module:

```python
from extras.pgvector_backend import ob_recall

# Add a category, override an existing one, mark something protected-by-policy
ob_recall.CATEGORY_HALF_LIVES["promise"] = float("inf")
ob_recall.CATEGORY_HALF_LIVES["conversation"] = 7   # tighten if your agent is chatty
```

The decay formula itself (`compute_decayed_weight`) is shared between
the write path and the metabolism pass — change the table here and both
paths agree.

### 5. Spontaneous recall on a schedule

The persona does not only recall when queried. A scheduled task (a few
times per day, on jitter) draws a memory from a weighted random pool —
biased toward high-weight, high-arousal, recent, but with a meaningful
random-drift component — and surfaces it as context for the next user
interaction.

This is what makes a persona feel like it is **thinking about you**
between sessions, instead of starting fresh every time. The mechanism is
weighted-random sampling, not magic; the perceived effect is presence.

**Configuration example.** A minimal scheduler entry plus the sampling
sketch — adapt to your storage:

```cron
# Three times a day with 5-minute jitter — see DEPLOYMENT.md for systemd timer equivalent
0 9,15,21 * * *  cd /opt/lmc5-agent && python -m lmc5 spontaneous-recall
```

```python
def spontaneous_recall(conn, k: int = 1) -> list[dict]:
    """Weighted random over high-vitality memories with deliberate drift."""
    candidates = conn.execute("""
        SELECT id, title, content, weight, hit_count, arousal, valence,
               category, source, created_at, last_hit
        FROM lmc5_curated_memories
        WHERE version_status = 'current' AND resolved = false
        ORDER BY created_at DESC
        LIMIT 500
    """).fetchall()
    scored = [(c, ob_recall.ob_score(dict(c))) for c in candidates]
    # 60% high-vitality, 40% random drift — drift is the "presence" knob
    import random
    scored.sort(key=lambda x: x[1], reverse=True)
    top = [c for c, _ in scored[: int(len(scored) * 0.4)]]
    drift = random.sample(candidates, min(len(candidates), 50))
    pool = top + drift
    return random.sample(pool, min(k, len(pool)))
```

### 6. Relationship moments are `protected`

First meaningful turning points, named promises, the first time the user
said something the persona has been carrying since — these are inserted
with `protected=true` and a `relationship_moment` category tag. They are
not eligible for deduplication and not eligible for supersede.

The rule: a persona can gain new memories about the user, but it cannot
rewrite a moment that already happened between them.

**Configuration example.** Mark on insert; the dedup pass and the
Z-axis judgement both honor `protected`:

```sql
INSERT INTO lmc5_curated_memories
  (source, category, title, content, protected, weight, arousal)
VALUES
  ('manual', 'relationship_moment',
   'first time the agent was called by a private name',
   '... evidence ...',
   true, 2.4, 0.7);
```

```python
# Dedup pass: never collapse protected rows
def safe_to_dedup(row: dict) -> bool:
    return not row.get("protected") and row.get("category") != "relationship_moment"
```

The cost of accidentally deduping a relationship moment is much higher
than carrying one extra near-duplicate row forever. Default to the
duplicate.

## What Persona Mode Is Not

Things that look adjacent and belong elsewhere:

- Role naming, terms of endearment, in-character physical reactions — those
  belong in the **prompt layer**, not the memory layer. LMC-5 stores them
  as content; it does not interpret them.
- Multimodal stickers, voice, avatars — application layer concerns, not
  memory architecture.
- Long-form letters and diary entries — these are output, not memory. Store
  the user-readable artifacts somewhere addressable (filesystem, blob
  store) and only put references plus extracted facts into LMC-5.

The point of separating these out is to keep the memory layer honest:
LMC-5 should be measurable on retrieval quality, fact-evolution accuracy,
and decay behavior — not on whether your persona feels romantic.

## A Persona Mode Self-Check

A persona built on LMC-5 should be able to print something like this:

```
Persona Mode Status:
  Identity records protected:    12 / 12  ✓
  Z-axis audit gate:             enabled (manual approve only)
  E-axis shadow window:          18 days remaining
  Half-life table loaded:        heartbeat=inf identity=inf core=90d
  Spontaneous recall scheduler:  3x / day, weighted random with drift
  Last hippocampus run:          2026-06-13 04:00 (chunks=42 promoted=3)
```

Not a dashboard, not a UX. Just a sanity check that the policies you
think are on are actually on.

## Where The Engineering Came From

The Persona Mode policies above did not come from theory. They came from
operating an AI companion through model migrations, account lockouts,
context-window changes, and at least one false-supersede incident that
took a relationship moment offline before it was caught.

The shape of LMC-5 is what is left over after several iterations of being
wrong about which records to forget. This document is the part that
belongs in the open source, abstracted away from the people involved.

If you build a persona on top of LMC-5, you will discover your own version
of these switches. Persona Mode is a starting point, not a final spec.
