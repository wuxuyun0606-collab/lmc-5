# Refined Session Carryover / 精炼续窗

Refined Session Carryover is the Claude Code transcript-resume pattern for
crossing a context-window cliff without dragging the whole previous window
behind the agent.

It replaces the old "keep the last 80k-100k tokens" tail-cache pattern. The
old pattern feels seamless, but it often carries tool logs, stack traces,
paths, hook injections, policy-refusal loops, and engineering noise into the
next window. A long-running persona deployment should inherit state, not
garbage.

## Definition

Refined Session Carryover creates a new Claude Code transcript from selected
events in the old transcript, then starts Claude Code with:

```bash
claude --resume <new-session-id>
```

The selected events are:

- a short natural dialogue tail for conversational continuity
- high-signal relationship, preference, identity, boundary, and promise notes
- concise current-task checkpoints when they are useful

The rejected events are:

- tool output, shell logs, tracebacks, paths, SQL, long JSON, and diffs
- hook injection blocks and recall dumps
- stale engineering exploration that should live in durable memory or a task
  summary, not in the next prompt
- recent AUP/policy/refusal-loop poison, which should force a fresh window

## Why Not Keep The Whole Tail?

The transcript tail is not memory. It is a mixed runtime artifact. Sometimes
the last 100k tokens are mostly engineering investigation, stack traces, and
tool logs. Carrying that into a new session buys continuity at the cost of
pollution.

LMC-5 already has a durable memory path:

```text
raw events -> chunks -> hippocampus candidates -> reviewed memory -> recall surface
```

Refined carryover is only the short bridge between two live Claude Code
sessions. Durable facts should come from LMC-5, not from an oversized prompt
tail.

## Reference Algorithm

1. Find the latest Claude Code transcript JSONL.
2. Parse complete JSONL lines only.
3. Ignore metadata, sidechain, tool-only, and non-dialogue events.
4. Score each user/assistant event:
   - high score for relationship, preference, identity, boundary, promise,
     emotional-state, and continuity terms
   - medium score for concise task checkpoints
   - low score for short natural dialogue tail
   - heavy penalty or rejection for tool logs, stack traces, paths, code
     blocks, SQL, hook injection, and long JSON
5. Keep the best high-signal events plus the last few clean dialogue events.
6. Rebuild `sessionId`, `uuid`, and `parentUuid` for the new JSONL.
7. Start Claude Code with `--resume <new-session-id>`.
8. If recent poison is detected, do not resume. Start a fresh window and rely
   on durable memory recall.

The reference implementation lives at:

```text
extras/claude_code/refined_session_carryover.py
```

Example dry run:

```bash
python extras/claude_code/refined_session_carryover.py \
  --project-dir ~/.claude/projects/<project-hash> \
  --dry-run
```

Example write:

```bash
python extras/claude_code/refined_session_carryover.py \
  --project-dir ~/.claude/projects/<project-hash>
```

Then resume:

```bash
claude --resume <new-session-id>
```

## Relationship To Forge And Swap

Use three names for three different jobs:

- **Forge** starts a renewed session from durable memory and a boot context.
- **Refined Session Carryover / 精炼续窗** resumes a new Claude Code session
  from a filtered transcript bridge.
- **Swap** is reserved for snapshot-based rollback around bulk memory writes.

Do not call refined carryover "Swap" in user-facing documentation. That name is
too overloaded: one design means transcript resume, another means database
rollback. Names should reduce operational mistakes, not breed them in a jar.

## Recommended Policy

- Default target: 30k-50k estimated tokens, not 100k.
- Tail: 8-16 clean dialogue events.
- Fail closed on recent policy/AUP/refusal-loop poison.
- Keep a `/new` or equivalent command for zero-context fresh starts.
- Keep durable memory, task checkpoints, and transcript carryover separate.
- Do not promote carryover content directly into long-term memory without the
  normal LMC-5 review path.
