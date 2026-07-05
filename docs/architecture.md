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
- `same_event`
- `same_topic`
- `in_thread`
- `same_person`
- `in_episode`
- `instance_of`
- `temporal_sequence`
- `emotional_link`
- `cause_effect`
- `supports`
- `contradicts`
- `derived_from`

Relations are used for explanation and future expansion. Recall performs
two-hop graph expansion from initially matched memories. Each edge score is
weighted by relation type and then decayed by distance, so a close
`same_topic` or `same_event` edge can surface strongly. Review relations such
as `contradicts`, `cause_effect`, and `supports` stay out of default graph
expansion and should be handled by audit/review flows.

The core graph walk is deliberately constrained: it expands only safe relation
types, live endpoints, and relation strengths above the hop threshold. This
keeps archived/superseded memories and low-confidence cross-line guesses from
leaking back into normal recall.

Nightly hippocampus jobs should only auto-apply low-risk relation types such as
`same_topic`, `same_event`, `temporal_sequence`, and `derived_from`.
Contradictions, cause/effect, and support claims are useful, but they should
enter review first because they can change how old facts are interpreted.

## Z: Fact Evolution

Z protects the system from treating every old sentence as equally true.

Each memory can have a `fact_key`. At most one memory per `fact_key` should be
the current active fact. When a new active fact is inserted for the same key,
the store marks older active facts as `superseded`.

That automatic path is intentionally narrow. Broader contradiction handling goes
through `z_conflict_audits`: same-`fact_key` review/current conflicts and
explicit `contradicts` relations become pending audit rows first. Dry-run lists
candidate pairs without requiring a provider key, without writing audits, and
without superseding records. Even `--apply` only writes pending audits. Z-axis
truth changes should be explicit, reviewable, and auditable.

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
  -> live-memory filter
  -> two-hop typed relation expansion
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

## Night Hippocampus Flow

The hippocampus pass is a gated chunk-to-memory job:

```text
event_chunks
  -> candidate proposer
  -> local importance/risk/source gates
  -> review memories
  -> safe relation plans
```

The built-in proposer is deterministic and offline. A deployment can swap in a
cheap model as a memory janitor, but that model should only propose candidates.
LMC-5 still owns the write gate, dry-run/apply boundary, deduplication,
redaction, and relation safety.

## VPS Survival Shape

The reference architecture is intentionally simple enough to survive on a small
VPS for 7*24 hour operation:

```text
append-only events
  -> scheduled chunk consolidation
  -> nightly hippocampus review
  -> Z-axis conflict audit
  -> forge next-session context
  -> swap snapshots for rollback
  -> read-only patrol checks
  -> redacted surface for the next agent session
```

The VPS is the continuity anchor. It keeps raw events, chunks, review memories,
and relation metadata available across sleeping or restarted agent windows.
That does not remove the need for review: automatic writes should stay gated,
dangerous relations should remain review plans, and the SQLite store should be
private and backed up.

### Forge And Swap

Forge treats each new agent session as a recoverable launch, not a blank start:
surface the active project, include pending Z and patrol state, and let the next
window continue from durable memory instead of stale prompt residue.

Refined Session Carryover ("精炼续窗") is the Claude Code transcript-resume
bridge: filter the previous JSONL transcript, keep high-signal state and a short
clean tail, drop engineering noise, then resume with `claude --resume`. It
replaces the old raw tail-cache pattern for live window renewal.

Swap treats the SQLite store as operational state: snapshot before scheduled
writes, keep a warm copy for quick rollback, and never let a bad nightly job be
the only copy of memory truth.
