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
| Lifecycle | M axis | `consolidation_runs`, `patrol`, future promotion/demotion jobs |

## XYZEM Responsibilities

### X: Timeline

Chunks provide temporal anchors. A chunk should record its event range and
channel so later recall can answer: "where in the agent's history did this come
from?"

### Y: Relations

Observations should link to other observations and memories with explicit
relations such as `supports`, `contradicts`, `cause_effect`, or `same_project`.
Graph retrieval should prefer relation types that match the query. Otherwise
the graph turns into six degrees of everything.

### Z: Fact Evolution

Facts need lifecycle state. A newer observation can supersede an older one
without deleting the older evidence. Raw events are historical evidence;
observations can be current, under review, superseded, or archived.

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

## Design Boundary

The awareness layer is a technical abstraction:

```text
current usable interpretation = reviewed observations + current facts + linked evidence
```

It should be described carefully. The project can say it models memory
consolidation and reflective state. It should not claim that chunks create
literal consciousness. That distinction keeps the architecture useful,
testable, and credible.

