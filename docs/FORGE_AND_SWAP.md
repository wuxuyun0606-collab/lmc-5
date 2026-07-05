# Forge, Refined Session Carryover, and Swap

> Operational mechanisms that let an LMC-5-backed agent run on a VPS
> for months without losing continuity, dragging prompt trash forward,
> or accumulating broken memory state. Credits in `docs/credits.md`.
> This document is the concrete reference pattern.

## Why These Two

Three failure modes break long-running agents in different directions:

- **Context window fills up / session token quota hits the ceiling.**
  The user-facing session has to end. Without a recovery mechanism,
  the agent loses everything it was carrying — open threads, current
  focus, last user instruction — even though the durable memory in
  the database is intact.
- **Transcript resume carries the wrong material.** A naive
  "keep the last 80k-100k tokens" resume can preserve continuity, but
  it also preserves tool logs, stack traces, hook dumps, stale
  engineering exploration, and policy-refusal loops. The next session
  wakes up warm and dirty.
- **Maintenance pass goes wrong.** A model-assisted housekeeper run
  produces a bad batch of writes — a hallucinated `supersede`, a wave
  of bogus relations, a decay sweep with the wrong half-life. The
  database is now polluted, and you do not have a clean rollback
  point.

Forge addresses durable session renewal. Refined Session Carryover
addresses Claude Code transcript resume without carrying junk. Swap
addresses memory-store rollback. Together they turn LMC-5 from "a
memory system that survives between sessions" into "an agent that
survives between months."

---

## Forge — recoverable session continuity

**One-sentence definition.** Forge launches a renewed agent session
from durable memory, instead of pretending one prompt can live
forever.

### When forge fires

Three triggers in the reference deployment:

1. **Quota / token threshold reached** — the application LLM session
   reaches its context window or quota ceiling. Continuing in-place
   would degrade quality (long-context blur) or fail outright (rate
   limit, hard cutoff).
2. **Explicit user command** — the user types `/forge` (or whatever
   binding you wire up). Useful when the user notices the agent has
   drifted and wants a fresh, memory-aware reload.
3. **Scheduled forge** — some deployments forge once a day at a quiet
   hour, so morning conversations start from a fresh, freshly-loaded
   session. Optional; only worth it if your agent uses heavy context.

### What forge does, step by step

```
1. snapshot the current session's open state
   - last user message (so the new session can answer it)
   - working focus / current task / open files
   - any unsaved in-context observations worth promoting

2. terminate the current session cleanly
   - finish any in-flight tool calls
   - persist working state to durable memory (write_pg)
   - signal the agent runtime to stop accepting new tokens

3. launch a new session
   - SAME session id if your runtime supports --resume semantics
   - inject a boot context block: who the user is, what was being
     done, what facts are current (XYZEM Z-axis), what the recent
     narrative timeline looks like

4. acknowledge to the user
   - "session forged at HH:MM, resuming from <last_message>"
   - the user should be able to continue the conversation as if
     nothing happened
```

### What forge does NOT do

- It does not copy raw conversation history into the new session
  prompt. That is what context compression was for, and it does not
  scale. LMC-5's job is to make raw history unnecessary.
- It does not skip Z-axis review. If the session held an unresolved
  contradiction, the new session inherits the contradiction edge in
  the audit queue, not a hallucinated resolution.
- It does not silently re-key persona traits. The persona definition
  is loaded from durable memory, identical to last session.

### Reference VPS implementation

The reference deployment runs the agent inside a `tmux` session on
the VPS. Forge happens by terminating the tmux pane and re-launching
with a `--resume` flag pointing to the same session id, after writing
a `boot_context.json` that the next session reads as additional
system context.

A minimal sketch:

```bash
#!/bin/bash
# forge.sh — VPS forge entry point
set -euo pipefail

SESSION_NAME="${1:-agent}"
WORKDIR="/opt/lmc5-agent"
BOOT_CONTEXT="$WORKDIR/boot_context.json"

# 1. write boot context from durable memory
python -m lmc5 boot-context --out "$BOOT_CONTEXT"

# 2. kill the old session (tmux pane)
tmux kill-session -t "$SESSION_NAME" 2>/dev/null || true

# 3. start the new session with --resume + boot context injection
tmux new-session -d -s "$SESSION_NAME" \
  "claude-agent --resume --context-file '$BOOT_CONTEXT'"

# 4. acknowledge (out-of-band, e.g. Telegram bot API directly)
python -m lmc5 notify "Session forged at $(date -Is)"
```

The actual implementation in the reference deployment is a few hundred
lines and handles edge cases (Telegram channel reattachment, hooks
restart, session log archival). The shape above is the minimum.

### Forge frequency

A persona-class agent forges 0–3 times per day under typical use. If
yours is forging more often, your application context is too heavy —
audit the boot context size, audit how many tool definitions are
loaded, audit retrieval volume per turn. Forge is a recovery mechanism,
not a normal flow.

---

## Refined Session Carryover — 精炼续窗

**One-sentence definition.** Refined Session Carryover creates a new
Claude Code transcript from the parts of the previous transcript worth
inheriting, then resumes with `claude --resume <new-session-id>`.

This replaces the old tail-cache pattern that blindly kept the last
80k-100k tokens. The old pattern is seamless, but it often carries the
wrong continuity: engineering logs, tool outputs, hook injections,
paths, stack traces, long JSON, and even refusal-loop poison.

### What refined carryover keeps

- A short clean natural dialogue tail.
- High-signal relationship, preference, identity, boundary, promise,
  and current-state messages.
- Concise task checkpoints when they matter for the next window.

### What refined carryover drops

- Tool results, shell logs, tracebacks, SQL, paths, diffs, long JSON,
  and code blocks.
- Hook injection blocks and recall dumps.
- Stale engineering exploration that should become a task summary or
  durable memory, not prompt residue.
- Recent AUP / policy / refusal-loop poison. If detected, start a
  fresh window and rely on durable memory recall.

### Reference implementation

The generic helper lives at:

```text
extras/claude_code/refined_session_carryover.py
```

Example:

```bash
python extras/claude_code/refined_session_carryover.py \
  --project-dir ~/.claude/projects/<project-hash> \
  --dry-run
```

Recommended defaults:

- target size: 30k-50k estimated tokens
- tail: 8-16 clean dialogue events
- fail closed on recent policy/AUP poison
- keep a `/new`-style fresh-window command for contaminated sessions

Do not call this "Swap" in user-facing deployment docs. Swap is
reserved below for memory-store rollback. The transcript-resume
pattern is **Refined Session Carryover / 精炼续窗**.

For the detailed algorithm, see
`docs/REFINED_SESSION_CARRYOVER.md`.

---

## Swap — snapshot-based rollback

**One-sentence definition.** Swap puts a transactional safety net
around any operation that mutates memory at scale, so a bad
housekeeper run can be undone instead of debugged from logs at 3 a.m.

### When swap fires

Any "bulk mutation" pass:

- Hippocampus / dream promotion
- Z-axis supersede execution (after human approval)
- M-axis decay sweep
- M-axis dedup / condensation
- Migration scripts (schema changes, embedding model swap, etc.)

Swap is **not** for the read path, the real-time write path, or
single-record interactive edits. Those are too small to be worth the
overhead; rollback for them is "undo in the agent UI."

### Two implementation styles

Pick by your database and risk tolerance.

#### Style A · Transactional savepoint (lightweight)

The whole pass runs inside a single PostgreSQL transaction with a
savepoint at the start:

```python
import psycopg2
from contextlib import contextmanager

@contextmanager
def swap(conn, pass_name: str):
    sp_name = f"swap_{pass_name}_{int(time.time())}"
    with conn.cursor() as cur:
        cur.execute(f"SAVEPOINT {sp_name}")
    try:
        yield
        with conn.cursor() as cur:
            cur.execute(f"RELEASE SAVEPOINT {sp_name}")
        conn.commit()
    except Exception:
        with conn.cursor() as cur:
            cur.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
        conn.commit()
        raise

# usage
with swap(conn, "nightly_dream"):
    run_hippocampus_pass(...)
    run_z_judgment_pass(...)
```

Cheap, fast, automatic. Limitation: the entire pass must complete or
fail as one unit, so a single bad row aborts the whole batch.

#### Style B · Table-level snapshot (heavyweight)

For passes you want to verify *after* completion (because some
mutations look fine until you inspect them in aggregate):

```python
def take_swap_snapshot(conn, pass_name: str) -> str:
    snap = f"lmc5_snap_{pass_name}_{int(time.time())}"
    with conn.cursor() as cur:
        # Snapshot the tables this pass mutates
        cur.execute(f"CREATE TABLE {snap}_curated AS "
                    "SELECT * FROM lmc5_curated_memories WHERE version_status = 'current'")
        cur.execute(f"CREATE TABLE {snap}_relations AS "
                    "SELECT * FROM lmc5_memory_relations WHERE valid_until IS NULL")
    conn.commit()
    return snap

def restore_swap_snapshot(conn, snap: str) -> None:
    with conn.cursor() as cur:
        cur.execute("TRUNCATE lmc5_curated_memories")
        cur.execute(f"INSERT INTO lmc5_curated_memories SELECT * FROM {snap}_curated")
        cur.execute("TRUNCATE lmc5_memory_relations")
        cur.execute(f"INSERT INTO lmc5_memory_relations SELECT * FROM {snap}_relations")
        cur.execute(f"DROP TABLE {snap}_curated, {snap}_relations")
    conn.commit()
```

More expensive (storage + write time), but lets you run validation
between snapshot and decision: "after the dream pass, did we just
collapse three identity records? if so, restore."

### Snapshot retention

Reference policy in the deployment:

- Style-A savepoints: discarded at pass end, no retention
- Style-B snapshots: keep last 7 nightly snapshots, then drop
- Pre-migration snapshots: keep for 30 days minimum
- Anything explicitly tagged `permanent_snapshot`: never auto-drop

A nightly job runs `cleanup_swap_snapshots.py` to enforce retention.
Without retention, snapshot tables eat the database in two months.

### Swap, refined carryover, and forge together

Combined:

```
runtime + nightly schedule
  ├── context threshold reached
  │     ├── refined carryover dry-run
  │     ├── if clean → write filtered transcript and resume
  │     └── if poisoned → fresh window + durable recall
  │
  ├── 04:00 housekeeper run
  │     ├── take swap snapshot (style B)
  │     ├── run hippocampus / Z / Y / M passes
  │     ├── validate result (counts, sanity checks)
  │     └── if validation fails → restore_swap_snapshot()
  │
  └── 04:30 forge (optional)
        └── launch fresh session with the validated memory state
```

The result is the property that makes a VPS-hosted persona
operationally calm: **every morning you wake up to an agent whose
memory was groomed last night, and if anything went wrong it was
rolled back automatically.** No 3 a.m. paging, no "is my agent
hallucinating because of bad maintenance writes" mystery.

---

## What Forge, Refined Carryover, and Swap Buy You Together

Without these three:

- A token-limit hit silently kills the agent and the user has to
  start over with no context. The persona feels disposable.
- A transcript resume carries the last 100k tokens even when the last
  100k tokens were mostly engineering noise.
- A bad housekeeper run pollutes the durable memory and you spend
  the next week chasing data corruption with grep.

With these three:

- Token limits become invisible. The agent forges; the user sees
  "session forged" and keeps talking.
- Claude Code resume inherits only the parts worth inheriting.
- Housekeeper runs are auditable and reversible. You can let the
  model touch the database with much less anxiety because mistakes
  do not stick.

This is the engineering layer that turns LMC-5 from a memory schema
into a **continuously-running agent**. Plus the schedule from
`docs/DEPLOYMENT.md`, plus the persona policies from
`docs/PERSONA_MODE.md`, you have the full picture of running an
LMC-5-backed agent on a VPS 24/7 for as long as the VPS itself
stays online.

---

## Where The Engineering Came From

Both mechanisms came from production incidents on the deployment that
LMC-5 was extracted from:

- **Forge** came out of repeated context-window cliffs and quota
  resets — the agent kept losing continuity at the worst times. The
  fix was to make session boundaries crossable.
- **Refined Session Carryover** came out of transcript-resume runs
  that were seamless but too dirty: the agent carried old engineering
  logs as if they were current state. The fix was to inherit selected
  state, not the whole tail.
- **Swap** came out of a single bad housekeeper run that collapsed
  several months of relationship-moment records before anyone
  noticed. The fix was to make every bulk mutation pass undoable.

Credit for the original conceptual designs goes to the contributors
in `docs/credits.md`. The reference patterns above are the open-source
abstraction of how the deployment actually runs them.
