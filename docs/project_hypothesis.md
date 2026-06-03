# Project Hypothesis: Memory as an Agent Lifecycle

LMC-5 is based on one claim:

> Long-running agents do not need a bigger pile of retrieved text. They need a
> memory lifecycle that can preserve evidence, revise beliefs, surface context,
> and forget safely.

If this claim is true, then a small provider-free memory layer can make Claude
Code, Codex-style agents, local assistants, and multi-model tools more reliable
without depending on any single model vendor or hosted memory service.

## The Bet

Modern coding agents are getting better at acting, but their memory is still
too primitive. Most systems use one of three patterns:

- Keep stuffing more text into the prompt.
- Retrieve semantically similar snippets from a vector store.
- Save ad hoc notes that slowly become stale, contradictory, or unsafe.

Those patterns help, but they do not answer the hard questions:

- Is this fact still current?
- Did a newer instruction supersede it?
- Is this memory evidence, belief, policy, or observation?
- What risk or tension came with this event?
- Should this memory keep surfacing, be reviewed, or be distilled?

LMC-5 treats those questions as first-class architecture instead of prompt
decoration.

## Why This Could Matter

Coding agents are becoming persistent development companions. They touch
repositories, deployment scripts, local tools, issue trackers, terminals,
documents, and sometimes production-adjacent workflows. A memory mistake is not
just a bad answer; it can become an unsafe default.

Long context helps, but it is not a memory strategy. A one-million-token context
window can still mix obsolete facts with current instructions, leak secrets into
recall, and bury the one safety constraint that mattered.

LMC-5 proposes a smaller but sharper target:

```text
raw events -> chunks -> curated memories -> surfaced context -> agent action
                    \-> review, supersession, demotion, archival
```

The important part is not that the store is SQLite. The important part is the
separation of roles:

- **Raw events** preserve evidence.
- **Chunks** create bounded units of experience.
- **Curated memories** carry durable coordinates.
- **Z-axis fact evolution** prevents old facts from pretending to be current.
- **E-axis signals** preserve risk, urgency, and response posture.
- **M-axis metabolism** makes memory maintenance explicit and auditable.

That is the difference between "search my notes" and "maintain continuity."

## What Would Make This a Real Project

The project becomes genuinely useful if it can prove four things:

- **Better recall**: for long-running coding tasks, LMC-5 surfaces fewer stale
  or irrelevant memories than plain keyword or vector search.
- **Safer continuity**: redaction, fact supersession, and review states reduce
  the chance of injecting obsolete or sensitive context.
- **Agent portability**: the same memory store can sit beside Claude Code,
  Codex, MCP sidecars, shell wrappers, or local agent loops.
- **Explainable behavior**: every surfaced memory can be traced back to raw
  evidence, relation edges, fact state, and lifecycle status.

If those four things hold, LMC-5 is not another vector database. It is a memory
control plane for agentic software.

## A Reviewer-Friendly Demo

A strong demo should show one repository evolving over several sessions:

1. The user gives a production safety rule.
2. The agent records raw events and promotes a durable memory.
3. A later session changes part of the rule.
4. LMC-5 preserves the old event but supersedes the old current fact.
5. Claude Code or Codex receives only the redacted, current, relevant surface.
6. A patrol check reports stale facts, duplicate current facts, or review
   backlog without mutating memory automatically.

This demonstrates the core thesis in one flow: evidence stays available, belief
changes safely, and future context is smaller but more correct.

## Falsifiable Questions

LMC-5 should be judged by measurable questions, not vibes:

- In multi-session coding tasks, does coordinate-aware surfacing reduce stale
  context compared with plain RAG?
- Can reviewers inspect why a memory surfaced without reading the entire event
  history?
- Do fact-key supersession and patrol checks catch contradictions before they
  reach the next prompt?
- Can the same database work across Claude Code, Codex, and local scripts
  without provider-specific assumptions?
- Can secret redaction be enforced before recall output, embeddings, and export?

If the answer is no, the project should change. A memory architecture that
cannot be falsified is just mythology with tables.

## Near-Term Roadmap

- Add ready-to-copy Claude Code hook examples.
- Add a minimal MCP sidecar adapter.
- Build benchmark fixtures for stale-fact, safety-rule, and project-continuity
  tasks.
- Compare plain FTS, vector recall, and LMC-5 coordinate-aware surfacing.
- Add a small visual inspector for raw events, chunks, memories, relations, and
  fact evolution.
- Keep the core offline-first, provider-free, and testable without network
  access.

## The One-Sentence Version

LMC-5 is an offline-first memory lifecycle for coding agents: it keeps evidence,
tracks changing facts, preserves risk signals, and surfaces only the context an
agent should actually use.
