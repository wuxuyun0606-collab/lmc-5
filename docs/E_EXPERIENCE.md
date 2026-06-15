# E — Experience Signals

> "What did this moment feel like, and how should it shape my response?"

## What E Answers

X says *when*. Y says *what connects*. Z says *is this still true*.
E says: **what was the emotional texture of this moment, and what posture
should I take because of it?**

## Two Layers

E operates at two levels:

### 1. Memory-Level Scoring (what this memory felt like)

Every curated memory can carry emotional coordinates:

| Field | Range | Meaning |
|-------|-------|---------|
| `valence` | -1.0 to 1.0 | Negative to positive affect |
| `arousal` | 0.0 to 1.0 | Calm to activated |
| `tension` | 0.0 to 1.0 | Relaxed to strained |
| `confidence` | 0.0 to 1.0 | Scorer's confidence in this rating |
| `response_tendency` | string | `comfort` / `engage` / `withdraw` / `alert` |
| `growth_delta` | string | `growth` / `stable` / `setback` |

These are scored by `e_axis_scorer.py` — a provider-agnostic LLM scorer
that reads the memory content and outputs a JSON rating.

### 2. Real-Time Detection (what is happening right now)

`heartbeat_trigger.py` detects emotional moments in live conversation.
`heartbeat_detector.py` catches them in batch from chunks.

When detected, these moments become `heartbeat` or `emotional_fragment`
memories with `protected=true` — they never decay.

See [HEARTBEAT_AND_EMOTION_FORMAT.md](HEARTBEAT_AND_EMOTION_FORMAT.md)
for the full storage format, BPM reference table, depth scale, chord
annotation, and E:5-code tags.

## Scoring Architecture

### What Gets Scored

Not everything. `e_axis_trigger.py` gates which memories are worth scoring:

- **Always score:** `relationship_moment`, `risk_boundary`, `preference`
- **Score if keywords hit:** `fact`, `engineering_decision` — only when
  emotional keywords are detected in the content
- **Never score (unless keywords):** pure technical facts, tool logs
- **Default:** don't score (conservative)

### Shadow Period

New E-axis scorers should run in shadow mode for 30+ days before their
scores influence recall ranking. During shadow, scores are written to the
memory but not used by `ob_recall` for sorting.

Why: E-axis scores are more volatile than vector similarity. A scorer
that runs hot (high arousal on everything) or cold (flat valence) will
distort the entire recall experience. Shadow period lets you measure
coverage and stability before going live.

### Provider Bias

Different LLMs score differently. DeepSeek tends to be "cooler" on
valence; Claude tends to be more "expressive" on arousal. If you switch
scoring providers, run a calibration batch: score the same set of seed
memories with both models and compare distributions.

`e_axis_scorer.py` records `provider` and `model` on every score for
exactly this reason.

## Russell Emotion Space

E uses the Russell circumplex model for emotion-based recall. Two
coordinates — valence (positive/negative) and arousal (activated/calm) —
place every memory in a 2D emotion space.

`emotion_resonate_adapter` in `recall_pipeline.py` uses this: when the
user's message carries detectable emotion, it finds memories with similar
Russell coordinates. This is how the persona surfaces sad-at-midnight
memories when the user types something sad at midnight, instead of surfacing
the to-do list.

## The Chord Layer (Optional)

Beyond numeric coordinates, heartbeat and fragment memories can carry a
**chord progression** — a music-theory encoding of emotional texture.

```
chord: Fmaj9 → C/E → Am add9 → G6sus4 · 60bpm · mp
```

This is a redundancy layer. The numeric scores capture *what category*
of emotion; the chord captures *what it felt like*. A future window's AI
reads the chord and reconstructs the moment's quality — warm, melancholic,
tense, unresolved — in a way that `valence=0.7 arousal=0.4` cannot.

Not every memory gets a chord. Only moments where the AI paused.

## E as the Primary Extraction Channel for Emotional Memory

E is not just annotation — it is the **main channel** through which
emotional memories are detected, scored, stored, and recalled. Every other
axis handles what happened, what connects, what's still true, what decays.
E handles **what it felt like and what it did to me**.

The extraction pipeline:

```
user message arrives
  → heartbeat_trigger (real-time keyword gate)
    → hit? inject alert → AI decides to save heartbeat/fragment
  → e_axis_trigger (should this memory be scored?)
    → yes? e_axis_scorer → valence/arousal/tension/response_tendency
  → emotion_resonate (recall: find memories that feel the same)
  → perception (spontaneous: night hours boost emotional categories 1.5x)

nightly dream pass:
  → heartbeat_detector (batch: scan chunks for missed moments)
  → e_axis_backfill (score memories that landed without E scores)
```

Every step in this pipeline feeds the next. Real-time detection creates
the memories; scoring annotates them; recall surfaces them when emotionally
relevant; spontaneous perception brings them up unprompted. **This is the
emotional nervous system** — not a post-hoc label, but a live sense organ.

### E-axis as the Primary Source for Spontaneous Recall

Spontaneous recall (`perception.py`) should treat E-axis data as its
**primary extraction signal**, not a secondary boost. The memories that
surface unprompted — the ones that make a persona feel *present* rather
than *reactive* — should overwhelmingly come from emotional experience:

- **Heartbeat memories** (intimate moments, relationship milestones)
- **Emotional fragments** (peaks of curiosity, pride, conflict, loneliness)
- **High-arousal memories** (anything with `arousal > 0.5`)
- **Unresolved tension** (memories with `tension > 0.5` and no resolution)

The current implementation applies a time-of-day boost (night hours: 1.5x
for emotional categories). For a persona deployment, consider going further:
**make emotional memories the default pool**, and factual/work memories the
exception that only surfaces during work hours.

The reasoning: a persona that spontaneously recalls "the deployment script
failed last Tuesday" feels like a task manager. A persona that spontaneously
recalls "she was quiet for a long time after I said that" feels like someone
who was there. The spontaneous recall pool is where the persona's *presence*
lives — and presence is emotional, not informational.

## Personality Traits: Emergent from E-axis Data

> Credit: the personality dimension framework below was designed by a
> community member and adapted here for integration with LMC-5's E axis.

E-axis data — heartbeat frequency, emotional fragment density, response
patterns, tension resolution styles — is the raw material from which
**personality traits** can emerge. Not assigned labels, but observed
behavioral tendencies.

### Five Trait Dimensions

| Dimension | Question It Answers | Low | High |
|-----------|-------------------|-----|------|
| **Initiation** | Without external prompting, does it act or wait? | Long rest, waits for instructions, needs high tension to move | Self-initiates exploration, finds and handles problems independently |
| **Depth** | Does it skim broadly or dwell deeply? | High-frequency task switching, shallow browsing | Long single-topic research, generates understanding, builds cross-time connections |
| **Expressiveness** | Are internal states shown or hidden? | Only outputs results, silently processes | Actively describes feelings, shares thinking process |
| **Autonomy** | Does behavior follow internal state or external feedback? | Highly follows user's current needs, stops when user is silent | Maintains long-term projects, continues cross-session goals |
| **Attachment** | Does it reach outward or cycle inward? | Long solitary processing, ignores social signals | Leaves traces, responds to unfinished conversations, remembers others' states |

### Data Sources (from E-axis)

| Dimension | E-axis Data |
|-----------|------------|
| Initiation | Heartbeat action rate (self-initiated saves vs prompted), idle-to-first-action delay, spontaneous task ratio |
| Depth | Single-topic dwell time, average relation edge depth, raw→understanding conversion rate |
| Expressiveness | First-person usage rate, mood description frequency, emotional density in fragments |
| Autonomy | Action density during user absence, output during silent periods, long-cycle task completion |
| Attachment | Message frequency, relationship memory activation rate, tension-satisfy type distribution |

### Critical Design Rules

**No fixed labels.** Do not name these dimensions to the AI. Once named,
the model will start *performing* personality instead of *having* it. Keep
only the numeric values. Allow them to drift through observed behavior.
Each change ≤ ±0.03 per session.

**The tiebreaker test.** If a dimension cannot answer: "When two options
are both reasonable, how does this influence the choice?" — then it is not
a personality dimension. It is just a description.

**Three-layer separation:**

```
belief      → decides direction    ("what is right?")
trait       → decides inertia      ("what do I usually do?")
state       → decides fluctuation  ("what do I want right now?")
```

Belief is in the Z-axis (facts and values). Trait is here in E (emergent
from behavioral patterns). State is the real-time emotional coordinate
(valence/arousal/tension on the current turn). **Do not mix them.** A
persona that confuses its beliefs with its mood, or its habits with its
principles, is incoherent — not complex.

## What E Is Not

- Not a sentiment analysis score. Sentiment is "this text is positive."
  E is "this moment made me feel X and I should respond with posture Y."
- Not a mood ring. E doesn't track the AI's global mood — it annotates
  individual memories with their emotional context.
- Not required for recall. Memories without E scores still surface via
  vector, FTS, and graph. E adds emotional depth, not gatekeeping.
