# OpenAI Open-Source Application Notes

Source material for an application to the OpenAI open-source / free-Pro
program. Replace bracketed placeholders with the real maintainer identity
and OpenAI organization ID before submitting.

## Project Name

**Living Memory Coordinate-5** (LMC-5)

## Repository

<https://github.com/wuxuyun0606-collab/lmc-5>

## License

MIT

## Maintainer Role

`[primary maintainer or core maintainer]`

## Project Description

LMC-5 is an open-source memory architecture for long-running LLM agents.
It organizes memory into five cooperating axes — Timeline (X), Relations
(Y), Fact Evolution (Z), Experience Signals (E), and Metabolism (M) —
plus a raw event journal beneath them. The model treats memory as a
**lifecycle**: events become chunks, chunks become reviewable
candidates, candidates become curated memories, curated memories decay,
get superseded, or get consolidated into narrative.

The repository ships **two reference implementations** of the same
model:

- **Minimal (`src/lmc5/`)** — Offline-first Python with SQLite + FTS5,
  deterministic chunking, lightweight cosine vector index, two-hop
  typed relation expansion, JSONL import/export, redaction helpers,
  CLI, read-only metabolism patrol, full test suite, CI. Provider-free
  and network-free in the core. Suitable for prototypes, teaching, and
  agents under ~5k vectors.

- **Production (`extras/pgvector_backend/`)** — PostgreSQL + pgvector
  (halfvec, ivfflat ANN), LLM-proposed hippocampus with safety gates
  and semantic dedup, weekly/monthly narrative reflection, OB-style
  recall scoring with category half-lives and time ripple,
  provider-agnostic E-axis emotional scorer with retry and shadow-period
  policy, spontaneous-recall scheduler with time-of-day shaping,
  five-channel parallel recall pipeline (vector + FTS fallback + Y-axis
  graph 2-hop + Russell emotional resonance + spontaneous), Claude Code
  hook entrypoints (SessionStart / UserPromptSubmit / SessionEnd),
  full DDL, environment template, and pluggable embedder / reranker
  adapters for Gemini, Voyage AI, OpenAI, DeepSeek, and local
  sentence-transformers.

Both impls share the same XYZEM coordinate model so projects can start
with the minimal impl, validate the design, and adopt the production
impl when corpus size, multi-user concurrency, or VPS 7×24 deployment
requires it.

Operational patterns documented in the repository:

- **Persona Mode** (`docs/PERSONA_MODE.md`) — six policy switches for
  using LMC-5 as the foundation of a long-living AI companion
  (identity protected, Z manual gate, E shadow period, category
  half-lives, spontaneous recall, relationship moments protected).
- **Forge** (`docs/FORGE_AND_SWAP.md`) — recoverable session continuity
  when the application LLM hits its context window or quota ceiling.
- **Refined Session Carryover / 精炼续窗**
  (`docs/REFINED_SESSION_CARRYOVER.md`) — filtered Claude Code
  transcript resume that keeps high-signal state and drops engineering
  noise from the next window.
- **Swap** (`docs/FORGE_AND_SWAP.md`) — snapshot-based rollback around
  bulk-mutation passes so a bad housekeeper run is reversible.
- **VPS 7×24 deployment** (`docs/DEPLOYMENT.md`) — cron / systemd timer
  examples for nightly consolidation, weekly narrative reflection, and
  three documented frontend patterns (Telegram, WeChat bot,
  self-hosted UI).
- **Housekeeper LLM separation** (`docs/DEEPSEEK_INTEGRATION.md`) — the
  application LLM stays user-facing while a cheap structured-output
  model grooms memory on a schedule.

Conservative defaults across the board: no automatic supersession from
contradictions (pending audit rows only), no automatic deletion from
metabolism patrols, dry-run before apply, construction-time validation
of injected callables, categorized failure logs with retry on retryable
failures only.

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

API credits would be used to evaluate and improve the production
reference implementation, while keeping the minimal impl fully offline:

- **Housekeeper LLM evaluation.** Compare DeepSeek, GPT-4o-mini, and
  Anthropic Haiku on the structured-output tasks that LMC-5 routes
  through the `housekeeper` role: candidate-proposing hippocampus,
  Z-axis contradiction judgment, relation labeling, dedup decisions,
  emotional scoring, narrative reflection.
- **Recall quality benchmarks.** Compare plain RAG, vector-only recall,
  and the five-channel LMC-5 pipeline (vector + FTS fallback + Y-axis
  graph expansion + emotional resonance + spontaneous) across
  long-running coding-agent and persona-agent benchmark fixtures.
- **Stale-context evaluation.** Test how each model performs on
  contradiction discrimination — the failure mode that motivated
  Z-axis manual gating (the prior deployment hit a 67% false-positive
  rate with a naive three-line rule before the rubric was expanded).
- **Embedder migration validation.** Compare Gemini-embedding-2,
  Voyage-3-large, OpenAI text-embedding-3, and local BGE-M3 on the
  same corpus using the production recall pipeline.
- **Persona-mode stability.** Run shadow-period stability tracking on
  the E-axis scorer across model providers to validate the 30-day
  minimum.
- **Documentation and reproducibility fixtures.** Generate benchmark
  scripts and result tables that other open-source memory projects
  can reuse.

The core minimal impl in `src/lmc5/` will remain usable without any
API calls. Model calls and credits would be used to evaluate, validate,
and document the production impl in `extras/pgvector_backend/` — the
parts that benefit from being measured against real production-class
LLMs.
