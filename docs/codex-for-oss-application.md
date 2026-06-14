# Codex for OSS Application Notes

Use this as source material for an application. Replace bracketed placeholders
with the real GitHub repository URL, maintainer identity, OpenAI organization
ID, and account details before submitting.

## Project Name

LMC-5

## Repository

`[public GitHub repository URL after publishing]`

## Maintainer Role

`[primary maintainer or core maintainer]`

## Project Description

LMC-5 is a small open-source reference implementation of a long-memory
coordination model for AI agents. It organizes memory into five layers:
timeline, relation graph, fact evolution, experience signals, and metabolism.
The goal is to help agents keep durable working memory without treating old
snippets as always-current truth.

The initial implementation is an offline-first Python package with SQLite
storage, FTS5 text recall, two-hop typed relation-expanded recall, a raw event
journal, mixed surfacing across curated memories and raw events, a CLI,
redaction helpers, JSONL import/export, fact-key supersession, relation
storage, explainable scoring, a demo workflow, tests, CI, and read-only
lifecycle patrol checks. It is intentionally conservative: no network calls, no
hidden model provider, no example credentials, no automatic deletion, and no
automatic lifecycle mutation from patrol checks.

The current design is also shaped for a small VPS: append-only event capture can
run 7*24 hours, while scheduled consolidation, hippocampus dry-runs, and
read-only patrol checks maintain memory continuity across sleeping or restarted
agent windows. This makes LMC-5 a survivable memory layer, not just a local demo.

Z-axis conflict handling now follows the same conservative shape: dry-run first,
provider-free by default, pending audit rows on explicit apply, and no automatic
supersession from contradiction candidates.

The VPS deployment model includes a forge pattern for renewing sessions from
durable memory and a swap pattern for snapshot-based rollback before scheduled
writes or model-assisted maintenance.

## Why It Matters

Many agent memory systems stop at semantic retrieval. That is useful, but not
enough for long-running coding or operations agents. They also need to know
which facts are current, which memories conflict, which work thread a memory
belongs to, whether an old high-risk rule should still surface, and when a
memory should be reviewed or distilled.

LMC-5 provides a minimal, auditable pattern for those decisions. It can be used
as a teaching implementation, a prototype sidecar for coding agents, or a
foundation for richer memory backends.

The project hypothesis is documented in
[`docs/project_hypothesis.md`](project_hypothesis.md): long-running agents need
a memory lifecycle, not just a larger prompt or another vector store.

Additional application framing is documented in
[`docs/why_openai.md`](why_openai.md): Claude Code is a key workflow target, but
OpenAI / GPT-class API evaluation is useful for controlled extraction,
benchmarking, stale-context tests, and provider-neutral adapter experiments.

## API Credits Use

API credits would be used to evaluate and improve optional model-assisted
memory operations around the offline core:

- Generate candidate fact keys from session notes.
- Propose relation edges between memories.
- Summarize raw notes into redacted memory records.
- Compare recall quality across plain retrieval and LMC-5 coordinate-aware retrieval.
- Build small benchmark fixtures for long-running coding-agent workflows.

The core package will remain usable without API calls. Model calls would be
optional experiments around extraction, evaluation, and documentation.
