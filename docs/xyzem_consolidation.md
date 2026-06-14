# XYZEM Consolidation and the Awareness Layer

LMC-5 is not just a recall format. It is a memory lifecycle model.

The core distinction is:

```text
raw events -> event chunks -> observations/current models -> agent response
```

Raw events preserve what happened. Chunks group those events into bounded
episodes. Observations are the reviewable layer the agent can use while
reasoning. The agent should not treat every raw event as a durable belief, and
it should not treat every old observation as current truth.

## Why Chunks Matter

Long context is not the same as continuity. If an agent simply appends more raw
conversation, the prompt becomes noisy and brittle. If it only stores isolated
facts, it loses the sequence that made those facts meaningful.

Chunks sit between those two failures:

- They preserve local narrative order.
- They give summarizers a bounded unit of evidence.
- They make temporal retrieval cheaper than scanning every event.
- They let later observations cite a source range instead of pretending to be
  self-evident truth.

Chunks are not consciousness. They are evidence windows. The awareness layer is
the structured set of observations, relations, current facts, and salience
signals built on top of them.

## Mapping Chunks Into LMC-5

| Layer | LMC-5 Role | Storage Concept |
|---|---|---|
| Raw event | Evidence | `events` |
| Event chunk | Episodic unit | `event_chunks` + `chunk_events` |
| Observation | Reviewable awareness | `memories(category='observation', thread='awareness')` |
| Relation | Y axis | `relations` |
| Fact status | Z axis | `status`, `active_fact`, `fact_key` |
| Salience | E axis | `risk_level`, `urgency`, `valence`, `arousal`, `tension` |
| Lifecycle | M axis | `consolidation_runs`, `hippocampus`, `patrol` |

## XYZEM Responsibilities

### X: Timeline

Chunks provide temporal anchors. A chunk should record its event range and
channel so later recall can answer: "where in the agent's history did this come
from?"

### Y: Relations

Observations should link to other observations and memories with explicit
relations such as `supports`, `contradicts`, `cause_effect`, or `same_project`.
Graph retrieval should traverse at most two hops by default and score each edge
with relation-type weights plus depth decay. Otherwise the graph turns into six
degrees of everything.

### Z: Fact Evolution

Facts need lifecycle state. A newer observation can supersede an older one
without deleting the older evidence. Raw events are historical evidence;
observations can be current, under review, superseded, or archived.

Contradiction handling should not jump straight from "possibly conflicts" to
"old fact is superseded." The Z path is:

```text
candidate conflict pair -> pending z_conflict_audits row -> reviewed verdict -> explicit lifecycle action
```

`lmc5 z-audit` defaults to dry-run. It lists pending candidate pairs and does
not require an external model key. It does not write audits or supersede facts
unless the operator explicitly uses `--apply`, and even then it only records
pending audit rows.

### E: Experience Signals

Open-source LMC-5 should treat E as salience, not as private roleplay. Useful
signals include:

- risk
- urgency
- tension
- confidence
- valence/arousal when relevant

These signals help decide what to surface, protect, or review. They do not prove
that an agent has human emotions.

### M: Metabolism

Consolidation is the start of M. A periodic job can:

- turn raw events into chunks
- generate candidate observations
- mark contradictions for review
- demote stale or low-signal memories
- protect stable high-value memories
- record every run in an audit table

This lets memory grow and forget deliberately instead of becoming a larger
vector dump.

## Night Hippocampus Pass

`consolidate` creates chunks. `hippocampus` is the next gate:

```text
raw events -> event chunks -> hippocampus candidates -> review memories -> relations
```

The reference implementation is conservative:

- Dry-run is the default.
- Candidates need source chunk IDs and a minimum importance score.
- Sensitive-looking candidates are rejected before write.
- Accepted candidates are written as `status='review'`, not unquestioned truth.
- Only safe Y-axis relation types are applied automatically.
- Contradiction, cause/effect, and support claims remain review plans.

External models can be used as candidate proposers. They should not write
directly to memory. A model-backed proposer may summarize chunk windows or rank
candidate importance, but local LMC-5 code should still own redaction, gates,
deduplication, fact evolution, and relation safety.

## Reference CLI

The reference implementation includes a deterministic, offline first command:

```bash
lmc5 consolidate --db demo.sqlite --window-size 20
```

It scans unconsolidated raw events, creates `event_chunks`, and optionally
promotes each chunk into a reviewable `observation` memory. The default summary
is intentionally simple and provider-free. Production systems can replace the
summarizer with an LLM while keeping the same tables and coordinates.

Use `--no-observations` when you only want chunk storage:

```bash
lmc5 consolidate --db demo.sqlite --window-size 50 --no-observations
```

Then preview or apply hippocampus promotion:

```bash
lmc5 hippocampus --db demo.sqlite
lmc5 hippocampus --db demo.sqlite --apply
```

## VPS 7*24 Hour Cycle

On a VPS, the XYZEM loop can run even when the interactive agent is asleep:

```text
X: append raw events as the day happens
Y: refresh two-hop typed relation recall from durable edges
Z: keep fact changes reviewable instead of silently rewriting truth
Z: run dry-run conflict audits before any supersession decision
E: preserve risk/urgency/tension signals for future surfacing
M: run consolidate, hippocampus, and patrol on a schedule
M: forge next-session context and keep swap snapshots for rollback
```

This is why LMC-5 is a better fit for 7*24 hour VPS survival than a prompt-only
memory notebook. The VPS gives the memory layer a stable clock, disk, and
scheduler. The safety contract stays the same: dry-run first, explicit apply,
redaction before remote providers, and no automatic destructive mutation.

Forge and swap are the operational halves of that loop. Forge builds the next
session's launch context from durable memory and recent evidence. Swap keeps
snapshots so scheduled writes can be rolled back if a model-assisted job or
migration goes wrong.

## Design Boundary

The awareness layer is a technical abstraction:

```text
current usable interpretation = reviewed observations + current facts + linked evidence
```

It should be described carefully. The project can say it models memory
consolidation and reflective state. It should not claim that chunks create
literal consciousness. That distinction keeps the architecture useful,
testable, and credible.
