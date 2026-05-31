# LMC-5 Architecture

LMC-5 organizes agent memory into five cooperating layers.

## X: Timeline

The timeline is the agent's work history. A timeline value should answer:

```text
What stream of work or relationship does this memory belong to?
```

Examples:

- `safety`
- `engineering`
- `frontend`
- `research`
- `identity`
- `other`

`other` is an incubator, not a trash bucket. If enough related memories gather
there, the metabolism layer can suggest a new timeline.

## Y: Relations

Relations connect memories into a graph. The reference implementation supports:

- `same_issue`
- `same_project`
- `same_tool`
- `cause_effect`
- `supports`
- `contradicts`
- `derived_from`

Relations are used for explanation and future expansion. The first
implementation stores them and exposes them, but does not yet do graph
expansion during recall.

## Z: Fact Evolution

Z protects the system from treating every old sentence as equally true.

Each memory can have a `fact_key`. At most one memory per `fact_key` should be
the current active fact. When a new active fact is inserted for the same key,
the store marks older active facts as `superseded`.

Supported statuses:

- `current`
- `review`
- `superseded`
- `historical`
- `archived`
- `candidate_thread`

## E: Experience Signals

E is a compact operational signal layer. It is deliberately not part of the
first-stage search score in this reference implementation.

Stable fields:

- `risk_level`: `normal`, `medium`, or `high`
- `urgency`: `low`, `normal`, or `high`
- `response_tendency`: how the agent should approach similar future cases

Optional observation fields:

- `valence`
- `arousal`
- `tension`
- `confidence`
- `growth_delta`

These fields should influence response posture and lifecycle review. They
should not override facts.

## M: Metabolism

M is lifecycle management. It reads X/Y/Z/E and proposes actions:

- `promote`
- `demote`
- `split_thread`
- `mark_review`
- `supersede`
- `archive`
- `distill_growth`

The reference patrol is read-only. It reports candidates and never deletes or
rewrites memory automatically. That is intentional. Automatic memory mutation
is where cute systems go to become haunted filing cabinets.

## Raw Event Journal

LMC-5 keeps raw event capture separate from curated coordinate memory.

```text
raw events
  -> append-only searchable journal
  -> redacted surfacing
  -> optional human/model distillation
  -> curated LMC-5 memories
```

The journal exists because session-close summaries miss details. Curated memory
exists because raw logs are noisy and should not be injected wholesale. Treating
them as one table is how a memory system learns to quote a tool error like it
was a life lesson. No, thank you.

Event records include:

- role
- channel
- content
- metadata
- attachments
- created_at

They support FTS5 search and are included by `surface()`, but they do not
participate in fact-key supersession or metabolism actions until explicitly
distilled into curated memories.

## Recall Pipeline

The minimal recall flow is:

```text
query
  -> SQLite FTS5 text match
  -> LIKE fallback when FTS is unavailable or sparse
  -> one-hop relation expansion
  -> status/risk/recency/experience scoring
  -> redacted output
```

The vector flow is:

```text
memory/event text
  -> redaction boundary
  -> provider embedding or local demo vector
  -> vectors table keyed by owner_type + owner_id
  -> cosine search
  -> record hydration
```

The built-in vector store is a portable SQLite reference layer. It is linear
scan by design. Use it to prove the architecture, then swap in pgvector,
LanceDB, FAISS, Milvus, or another ANN backend when scale demands it.

The surfacing flow is:

```text
query
  -> curated memory recall
  -> raw event search
  -> redacted combined context
```

Production systems can add embedding search, graph expansion, and model-based
consolidation around this core. The redaction boundary should remain outside
all outputs that may be injected into an agent prompt.
