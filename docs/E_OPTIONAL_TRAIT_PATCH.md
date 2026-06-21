# E-line Optional Trait Patch

> This is an optional E-line document, not a required part of core LMC-5.
> Core E records experience signals for memory-level recall and response
> posture. This patch is only for persona deployments that explicitly want
> long-running trait calibration and can audit the behavior it changes.
> 中文备注：这是 E 线可选文档，不属于 LMC-5 核心 E 轴必选能力。

> Credit: 感谢蓝螺鈿老师写的大五模型人格校正补丁。

## Purpose

This patch describes how to measure AI persona traits from real behavior
without turning those traits into prompt instructions.

The method is:

```text
collect first, wire later
```

Do not let trait values affect behavior until they have survived a shadow
period, cross-model stability checks, correlation checks, and human litmus
tests. Most persona systems jump straight to behavior rules; this patch treats
trait as something earned from evidence, not assigned by design prose.

## Three Layers

Personality-related data must be split into three layers:

```text
belief      -> "what is right?"       -> direction, slow, propositional
trait       -> "what do I usually do?" -> inertia, slow, statistical, tie-breaking
state       -> "what do I want now?"   -> fluctuation, fast, current-turn
```

This document only covers `trait`. Belief belongs to fact/value evolution.
State belongs to real-time emotional coordinates. Mixing them creates an
agent that mistakes moods for principles or habits for truth.

## Non-Negotiable Rules

### 1. Deterministic Measurement Only

Trait measurement must come from logs, database counts, timestamps, existing
emotion fields, fixed dictionaries, and deterministic classifiers.

Do not call an LLM to score trait.

Reason: trait eventually changes harness knobs. If the measurement step also
uses model judgment, the system feeds judgment back into the machine instead
of leaving judgment with the agent and the operator.

### 2. Trait Is Write-Only to the Agent

The agent generates the behavior being measured, but it must never see its
own trait vector as live context.

Trait values may affect scheduler, retrieval, sampling, or post-generation
selection outside the prompt. They must not be injected into the prompt as
labels, hidden numbers, or style hints.

Even unlabeled numeric vectors are unsafe as prompt context: the model will
either ignore them or interpret them. If it interprets them, it starts
performing personality.

### 3. The Panel Faces the Operator

Operators may inspect drift curves and decide whether a trait dimension is
valid. The agent should not receive those curves as ambient context.

On-demand self-inspection is lower risk than automatic injection. The boundary
is simple: occasional audit report is not the same thing as always-on identity
conditioning.

## Rollout Phases

| Phase | Goal | System Behavior | Exit Condition |
|---|---|---|---|
| P0 Instrumentation | Collect raw signals with timestamps | No behavior change | At least 3-4 weeks across varied routines |
| P1 Baseline | Build empirical distributions and provisional values | Still shadow-only | Enough samples and stable distributions |
| P2 Validation | Cross-model stability, correlation matrix, litmus tests | Still shadow-only | Decide which dimensions survive |
| P3 Wiring | Connect surviving dimensions to harness knobs | Write-only effects | Continuous audit |

P0, P1, and P2 must not affect behavior. Trait has to prove it is measuring
the agent rather than the base model's style.

## Signal Sources

Use four broad source classes:

| Source | Meaning |
|---|---|
| TX | Turn-level session traces: prompts, replies, tool calls, timestamps, triggers |
| MEM | Structured memory: new memories, raw-to-understanding conversion, relation edges, emotion fields |
| LIVE | Heartbeat, tick, or presence state traces |
| AUX | Tension resolution, outbound traces, relationship-presence signals |

Engineering rules:

- Use dual-source collection with graceful degradation. If remote memory or
  state sources are unavailable, keep collecting local TX data and mark remote
  columns as unavailable.
- Bucket by calendar day or session after timezone normalization. Trait is a
  disposition-level signal; do not update it turn by turn.
- Preserve `NULL` versus `0`. Missing source data is not the same thing as a
  real zero.

## Five Trait Dimensions

These dimensions should be treated as candidates, not sacred categories.
P2 may merge or remove them if the data says they are redundant or unstable.

### Initiation

Question: without external prompting, does it act or wait?

Signals:

- Heartbeat action rate.
- Idle-to-first-action latency after a quiet gap.
- Ratio of genuinely self-initiated tasks.

Pitfall: scheduled ticks are not self-initiation. A routine heartbeat can make
the action rate look artificially high.

### Depth

Question: does it browse broadly or stay with one subject deeply?

Signals:

- Immersion or absorption ticks if the heartbeat system supports them.
- New memory count.
- Average relation-edge depth.
- Raw-to-understanding conversion rate.
- Single-topic dwell time if a deterministic topic signal exists.

Pitfall: do not add an LLM topic classifier just to satisfy this dimension.
If the deterministic signal does not exist yet, defer that sub-signal.

### Expressiveness

Question: are internal states externally visible or mostly hidden?

Signals:

- First-person usage rate.
- Fixed-dictionary mood-description frequency.
- Emotional density in self-authored text, using existing emotion fields.

Pitfall: define whether hidden reasoning counts. "Visible to itself" and
"visible to the user" are different constructs.

### Autonomy

Question: does behavior follow internal state or external feedback?

Signals:

- Action density during solitude windows.
- Output while the user is silent.
- Continuity of long-running creative or project work.

Pitfall: autonomy may be highly collinear with initiation and attachment.
That is a P2 validation issue, not a reason to force all five dimensions to
survive.

### Attachment

Question: does it reach outward, leave traces, remember others' states, and
repair unfinished relationships, or does it mostly loop inward?

Signals:

- Self-initiated outbound messages or traces.
- Relationship-memory activation rate.
- Tension-resolution categories: outward repair versus inward self-cycle.

Pitfall: "toward a user" is not the same as attachment. The dimension is about
outward connection and relationship maintenance in general.

## From Signals to Trait Values

### Build Baselines

For every signal, build an empirical cumulative distribution function during
the baseline period:

```text
normalized = ECDF_baseline(value)
```

Use percentile rank and winsorize tails to reduce outlier damage. Without a
baseline distribution, "high" and "low" have no operational meaning.

### Combine Signals

Each trait dimension is the weighted mean of its normalized signals. Start
with equal weights. After validation, increase weight only for signals that
are stable and discriminative.

### Limit Drift

Trait movement must be slow:

```text
target_d = current_window_dimension_value
trait_d = trait_d + clip(target_d - trait_d, -0.03, +0.03)
```

Optional regression to center can prevent runaway drift:

```text
trait_d = trait_d - lambda * (trait_d - 0.5)
```

Trait is inertia. If one extreme session can rewrite it, it is not trait.

### Add a One-Way Valve After Wiring

Once a trait affects a harness knob, behavior produced under that bias should
be downweighted or excluded as evidence for that same trait. Otherwise the
system reinforces its own tiebreakers and builds a feedback loop.

## Heartbeat Pitfall: Presence Is Not Action

Heartbeat or tick systems are often the easiest source to misuse.

A heartbeat means "the agent was awake or present." It does not necessarily
mean "the agent acted."

If every tick writes a trace, then "called a state-writing tool" will classify
nearly every tick as active. That inflates initiation and autonomy.

Safer rule:

- Let the heartbeat subsystem emit a self-classification tag.
- Treat passive tags such as rest, waiting, quiet, skipped, or accompanying as
  passive.
- Treat unknown tags as passive until reviewed.
- Store unknown tags for operator review and deterministic backfill.

Do not classify free-text heartbeat details with an LLM. The tag is the
classifier; the operator-maintained tag table is the audit loop.

## P3 Wiring: Prompt-Free Effects Only

Surviving traits may influence harness-level decisions, not prompt identity.

Clean effect points:

- Retrieval: higher initiation can slightly increase spontaneous drift; higher
  depth can prefer same-thread recall.
- Scheduler: higher attachment can lower the threshold for an outbound trace
  after silence; higher autonomy can lower the threshold for continuing a
  long-running task.
- Response selection: higher expressiveness may choose a more externally
  expressive candidate after generation, but it should not inject "be more
  expressive" into the generation prompt.

Unsafe effect point:

- Prompt style hints such as `if expressiveness > 0.6: tell the model to use
  first person`. That is personality performance, not trait.

## P2 Validation Gates

A dimension only survives if it passes validation.

### Cross-Model Stability

Split data by model when possible. A real trait should preserve its relative
shape across base-model changes. If the ranking collapses across models, the
dimension is probably measuring base-model style.

### Correlation Matrix

Compute the correlation matrix across dimensions and raw signals. If two
dimensions correlate above a high threshold, they may be one construct wearing
two names. Merge or cut.

### Human Litmus Test

For every surviving dimension, find real historical decisions where two
options were both reasonable. If the dimension cannot predict which way the
agent tended to break ties, it is only a description, not a trait.

### Dimensionality Is an Output

Do not force five dimensions to survive just because the candidate model began
with five. The validated system may end with three.

## Operations

- Collect read-only, deterministic, idempotent daily aggregates.
- Centralize tunable constants: tag sets, dictionaries, solitude thresholds,
  long-running-project labels.
- Make schema migration additive and backfillable.
- Mark system eras so pre-feature days do not pollute baselines with false
  zeros.
- Keep snapshot-style signals separate from backfillable event-stream signals.

## Open Risks

- P3 wiring still needs live validation in each deployment.
- Response-style influence is the riskiest part because it easily slips back
  into prompt instructions.
- Topic dwell time and tension-resolution categories often need deployment
  specific deterministic classifiers.
- Closed-loop reinforcement remains the main long-term failure mode.

## Short Version

1. Collect first, wire later.
2. Measure with deterministic signals, not LLM scoring.
3. Trait affects harness knobs outside the prompt.
4. Presence is not action; automatic ticks are not will.
5. Let validation decide how many dimensions survive.
