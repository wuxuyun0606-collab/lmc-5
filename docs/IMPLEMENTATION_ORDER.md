# Implementation Order

> Build LMC-5 as a staged memory system, not as a pile of features.

This project is large enough that contributors often implement the visible
parts first: schema, CLI commands, a few relation rows, maybe an embedding
adapter. That is not enough. A working LMC-5 deployment needs the pieces to
close into one loop:

```text
write memory -> protect fact lifecycle -> build safe relations
             -> recall live seeds -> expand two hops with typed weights
             -> patrol drift -> feed the next session
```

If that loop is not closed, you have a database with memories in it. You do not
yet have a memory graph. Cute table, no legs.

If you are unsure what "closed loop" means in practice, read
[`CONNECTING_XYZEM.md`](CONNECTING_XYZEM.md) first. This file gives the staged
build order; `CONNECTING_XYZEM.md` explains how the write path, night path, and
recall path fit together.

## Phase 0: Install And Prove The Core Runs

Goal: prove the local package, SQLite schema, FTS, and test suite work before
you add architecture.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
PYTHONPATH=src python3 -m pytest tests
lmc5 doctor --db demo.sqlite
```

Definition of done:

- `pytest` passes locally.
- `lmc5 doctor` reports usable SQLite/FTS capability.
- `examples/demo.py` can create, recall, and surface memories.
- `examples/two_hop_graph.py` proves the Y-axis two-hop graph contract.

Do not start with pgvector, cron, or a housekeeper LLM. First make the boring
core boringly reliable.

## Phase 1: X/Z Live Memory Substrate

Goal: make recall safe before making recall clever.

Implement or verify:

- Memory rows have durable X metadata: `thread`, `status`, category, tags.
- `other` is treated as an incubator, not a trash bucket.
- `fact_key` and `active_fact` enforce one live fact per slot.
- New active facts supersede older active facts with the same `fact_key`.
- Every normal recall path returns only live memories:
  `status = 'current'` and, for fact memories, `active_fact = 1`.

Definition of done:

- Recent fallback recall does not surface archived/superseded memories.
- FTS recall does not surface archived/superseded memories.
- LIKE fallback recall does not surface archived/superseded memories.
- Inactive facts remain available for audit/export, but not normal behavior.

Recommended tests: `tests/test_store.py` and `tests/test_fact_evolution.py`.

## Phase 2: M Patrol Before Mutation

Goal: detect drift before you add more write paths.

Implement patrol as read-only first:

- Duplicate current facts.
- Review backlog.
- Oversized threads that should split.
- Relations touching non-live memories.
- Orphaned relation endpoints.
- Relation self-loops.
- Reciprocal duplicates for symmetric relation types.

Definition of done:

- Patrol reports problems without deleting, rewriting, or superseding memory.
- Every patrol warning has enough IDs for a human or later job to inspect it.

This phase prevents silent rot. It is not glamorous. Neither is a smoke alarm.

## Phase 3: Y Write Path

Goal: make relation writes valid before you rely on relation reads.

Implement or verify:

- Relation types are centralized in `src/lmc5/models.py`.
- Safe and review relation types are explicitly separated.
- Compatibility aliases normalize before storage; for example,
  `contradiction` stores as canonical `contradicts`.
- `strength` is validated in the `0.0` to `1.0` range.
- Self-loops are rejected.
- Symmetric relation types are canonicalized so A-B and B-A do not duplicate.
- CLI and hippocampus code import the shared constants instead of copying a
  stale relation list.

Definition of done:

- Every documented relation type is accepted.
- Unknown relation types fail loudly.
- Review relation types can be stored for audit, but are not treated as normal
  expansion edges.
- Directional relations such as `derived_from`, `temporal_sequence`,
  `supports`, and `cause_effect` keep their direction.

Recommended tests: relation type acceptance, alias normalization, strength
validation, self-loop rejection, symmetric duplicate prevention.

## Phase 4: Y Read Path: Real Two-Hop Typed Graph Expansion

Goal: connect recall seeds into a real graph walk.

The minimal read pipeline should be:

```text
query
  -> FTS / LIKE / recent seed memories
  -> live-memory filter
  -> hop 1 safe relation expansion
  -> hop 2 stricter safe relation expansion
  -> type weight * strength * distance decay
  -> merged ranked hits
```

Required graph-walk rules:

- Walk both directions for an edge, unless your backend has a stronger
  directional rule.
- Expand only safe relation types.
- Expand only live endpoints.
- Require stronger edges for hop 2 than hop 1.
- Never expand self-loops.
- Do not let review relations (`contradicts`, `supports`, `cause_effect`)
  participate in default recall expansion.

Use a tiny fixture before you trust a large corpus:

```text
A --same_topic(0.9)--> B --same_event(0.8)--> C
A --supports(1.0)----> D      # stored, but not default-expanded
A --same_topic(0.2)--> E      # too weak
A --same_topic(0.9)--> F      # F is superseded
```

Definition of done:

- A query that seeds `A` can surface `B` and `C`.
- `D` does not surface through default graph expansion.
- `E` does not surface because the edge is too weak.
- `F` does not surface because the endpoint is not live.
- Scores include relation type weight, strength, and distance decay.

This is the point where Y becomes an actual graph instead of a relation table.

## Phase 5: Hippocampus And Relation Build

Goal: make the background writer populate Y consistently.

Implement or verify:

- `consolidate` turns raw events into reviewable chunks.
- `hippocampus` proposes memory candidates from chunks.
- Candidate relation hints normalize aliases.
- Safe relation hints write direct edges.
- Review relation hints queue review plans/audits instead of changing recall.
- Dry-run is the default; `--apply` is explicit.

Definition of done:

- Running hippocampus in dry-run shows proposed memories and relations without
  writes.
- Running hippocampus with `--apply` writes review memories and safe relations.
- Review relations are visible in the plan/audit path, not default graph walk.

In production, this is also where the nightly relation-build pass lives. If you
skip it, `memory_relations` stays empty and graph recall is dead.

## Phase 6: E Axis In Shadow

Goal: add experience signals without letting noisy scores steer the system too
early.

Implement or verify:

- `valence` is between `-1.0` and `1.0`.
- `arousal`, `tension`, and `confidence` are between `0.0` and `1.0`.
- Invalid E-axis values fail at write time.
- New scorers run in shadow before they affect ranking.
- E signals influence posture and resonance; they do not override facts.

Definition of done:

- Bad scorer output cannot be written silently.
- Recall still works when E fields are missing.
- E scoring changes are measured before they affect user-facing ranking.

## Phase 7: Production Loop

Goal: connect the local memory layer to a durable long-running deployment.

Recommended VPS loop:

```text
agent hooks / sidecar
  -> log raw events
  -> consolidate
  -> hippocampus, including Y relation build
  -> timeline_sweep for each configured X-line
  -> z-audit
  -> patrol
  -> surface before the next session
```

Definition of done:

- The write path records raw events without needing a model call.
- The nightly path builds candidates and relations.
- `DreamSchedule()` resolves to the local-time `0 4 * * *` schedule.
- `timeline_sweep(thread)` runs for every configured X-line and isolates
  per-line failures.
- Z audit creates pending rows only.
- Patrol reports drift without mutation.
- `surface` combines live curated recall and raw event search safely.
- Backups/snapshots exist before scheduled writes.

Use `docs/DEPLOYMENT.md` for cron/systemd wiring after the staged core works.
Use `docs/AUTOMATION_BOUNDARIES.md` as the final checklist for what can run
unattended, what only runs after cron/callback wiring, and what must remain
review-only.

## What Not To Do

- Do not copy relation type lists into multiple files. Import shared constants.
- Do not use `supports`, `contradicts`, or `cause_effect` as default recall
  expansion edges.
- Do not trust a graph with many edges until a tiny fixture proves hop 1,
  hop 2, thresholds, live endpoint filtering, and review-edge exclusion.
- Do not let an LLM scorer write unbounded E-axis numbers.
- Do not run a production dream job without dry-run output and snapshots.
- Do not call a deployment complete until nightly relation build is scheduled.

## Final Acceptance Checklist

Before calling an implementation complete, verify:

- `PYTHONPATH=src python3 -m pytest tests` passes.
- `lmc5 doctor --db demo.sqlite` passes.
- Recall with no query returns only current/active memories.
- FTS and LIKE recall exclude archived, superseded, and inactive fact rows.
- A controlled Y fixture proves typed two-hop expansion.
- `PYTHONPATH=src python examples/two_hop_graph.py` prints `OK`.
- Review relation edges are stored for audit but excluded from default recall.
- Patrol flags stale/orphan/self-loop/duplicate relation issues without writes.
- Hippocampus dry-run and apply behavior are both tested.
- Production deployment has a scheduled relation-build pass.

Once these are true, the system is no longer just storing memories. It is
recovering continuity through a safe typed graph.
