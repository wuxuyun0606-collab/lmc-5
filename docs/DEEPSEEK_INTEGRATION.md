# DeepSeek Integration (Reference Pattern)

> The LMC-5 core stays provider-free. This document describes the role
> DeepSeek plays in the **reference deployment** that LMC-5 was extracted
> from, so you can see why a small auxiliary LLM matters for a memory
> system — and replace it with any equivalent model if you want.

## Why an Auxiliary LLM At All

LMC-5 separates **the application LLM** (the agent talking to the user)
from **the housekeeper LLM** (the model that grooms memory). Trying to
run everything off the application model — using the same Claude / GPT
session to also judge contradictions, propose candidates, and rerank
recall — runs into three problems:

1. **Cost.** The application model is usually the most expensive tier you
   pay for. Doing maintenance with it is wasteful.
2. **Confusion.** Asking the same session to play user-facing agent
   **and** memory janitor pollutes its in-context instructions.
3. **Throughput.** Nightly maintenance wants to process hundreds of
   memory edges in a row. Cheap, JSON-mode-capable, structured-output
   models are better at this than frontier-class conversational ones.

The reference deployment chose **DeepSeek** (`deepseek-chat` for cheap
batched work, `deepseek-v4-pro` for the harder judgments) for the
housekeeper role. The fit was practical: low per-token cost, reliable
JSON output, reasoning-mode option for ambiguous cases, multilingual
quality good enough for Chinese-and-English mixed memory.

Nothing about LMC-5 requires DeepSeek specifically. Anywhere the
reference patterns say "DeepSeek," substitute the cheap-structured-
output model you trust.

## What The Housekeeper Does

The reference deployment calls this auxiliary role **D-Manager**
(*D管家*, "D housekeeper"). It runs across multiple subsystems:

### 1. Dreaming · Night Hippocampus

After raw events accumulate during the day, the housekeeper proposes
**memory candidates** from event chunks. It:

- Reads bounded chunks (no infinite history)
- Outputs strict JSON: `type / title / content / importance /
  source_chunk_ids / evidence / risk`
- Classifies into six types (`event / fact / preference /
  engineering_decision / relationship_moment / risk_boundary`)
- Tags sensitive content with `risk='review'` so it never auto-promotes

Local gates then decide which candidates actually become curated memory.
The housekeeper proposes; LMC-5 decides.

Reference implementation:
[`extras/pgvector_backend/night_dream.py`](../extras/pgvector_backend/night_dream.py).

### 2. Z-Axis · Contradiction Judgment

When the relation builder flags two memories as a possible
contradiction edge, the housekeeper judges **direction**:

- `supersede` → which one is the stale version, with quoted evidence
- `both_valid` → these are not the same fact (mood shift, sarcasm,
  historical vs future, scope mismatch)
- `parse_fail` → the model could not produce structured output

The judging prompt encodes four real false-positive categories the
reference deployment caught during validation (early versions hit a
67% false-positive rate before the rule expansion). Decisions land in
an audit table with `status='pending'`; only a human `--approve`
performs the actual `supersede`. The housekeeper never directly
overwrites facts.

### 3. Y-Axis · Relation Naming

For pairs of memories that vector retrieval surfaces as similar, the
housekeeper labels the relation type (`same_topic`, `temporal_sequence`,
`cause_effect`, `emotional_link`, ...) and a confidence strength
(0.0–1.0).

Safe relation types auto-write; risky types (`contradicts`,
`cause_effect`, `supports`) queue for review. `contradiction` is accepted
as a compatibility alias, but new code should emit the canonical
`contradicts`. Same pattern: the housekeeper proposes, LMC-5 gates.

### 4. M-Axis · Deduplication, Condensation, Decay

For memories that exceed a vector similarity threshold (default 0.92),
the housekeeper decides whether they are duplicates worth merging,
near-duplicates worth condensing into a single anchor, or distinct
items that should remain separate.

For long-lived working notes that pile up under one topic, the
housekeeper composes a condensed summary (fragments → one anchor
memory + supporting evidence).

Memory decay itself does **not** go through an LLM — it is a
deterministic formula (Ebbinghaus B-curve with category-aware
half-lives). The housekeeper only decides edge cases, not bulk decay.

### 5. E-Axis · Emotional Scoring

For records whose application context warrants emotional metadata, the
housekeeper scores (`valence`, `arousal`, `tension`, `confidence`,
`response_tendency`, `growth_delta`) against a rubric. Failure modes
are categorized (`http_timeout / parse_fail / schema_fail / range_fail
/ ...`) and logged, so you can audit which records are stable and
which ones are scorer-unstable.

Reference implementation:
[`extras/pgvector_backend/e_axis_scorer.py`](../extras/pgvector_backend/e_axis_scorer.py).

### 6. Narrative Timeline · Reflection

Weekly / monthly narrative indexing also runs the housekeeper as a
reflector — turning a list of high-weight events into a one-line
title + paragraph summary that captures the **arc** of the period,
not just a list of events.

Reference implementation:
[`extras/pgvector_backend/narrative_timeline.py`](../extras/pgvector_backend/narrative_timeline.py).

## Operational Notes

- **Batch sizes.** DeepSeek's pricing rewards batching. The reference
  deployment runs hippocampus at batches of 8 chunks and Z-axis at one
  pair per call (because the judgment is denser).
- **Retries vs persistence.** Network failures retry next run; JSON
  parse failures are recorded with `status='skip'` so they do not get
  re-judged forever.
- **Auditability.** Every housekeeper-driven write goes through a
  table with `judged_at / reviewed_at / verdict / evidence`. You can
  trace any memory mutation back to a specific housekeeper call.

## Swapping DeepSeek Out

If you want to use a different model, swap the call site, not the
shape:

- Anything that takes a prompt and returns a JSON-coercible string works
- Reasoning-capable models (DeepSeek V4 Pro, OpenAI o-series, Anthropic
  extended thinking) are better at Z-axis judgments
- Cheap baseline models (`deepseek-chat`, `gpt-4o-mini`, Claude Haiku)
  are fine for hippocampus proposing and relation naming
- Local models (Llama 3.1 / Qwen 2.5 / GLM-4 via Ollama or vLLM) work if
  you can host them; JSON-output reliability is the bottleneck

The `Callable` injection pattern in `extras/pgvector_backend/` is
deliberately small so this swap is cheap.
