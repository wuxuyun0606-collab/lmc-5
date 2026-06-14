# Deployment Shapes

> Where to actually run an LMC-5-backed agent. The short answer: a
> long-running VPS, not a laptop.

## Why VPS Is The Right Default

A persona-class agent on top of LMC-5 needs **offline time to consolidate
memory**. Hippocampus runs, narrative reflection, Z-axis judgment, M-axis
decay — these are background jobs that benefit from a few uninterrupted
minutes at a quiet hour. They are not user-facing; they should not
contend with foreground latency.

A laptop is not the right host because:

- It sleeps. Scheduled jobs miss their windows.
- It reboots. Background tasks die mid-flight.
- It is online for the user's working hours, which is the worst time to
  run consolidation passes.

A small VPS (1–2 vCPU, 2–4 GB RAM is enough for a single-persona deploy
with corpus under ~50k vectors) is the right shape because:

- It is awake 24/7. Cron / systemd timers actually fire.
- It is reachable from any client — Telegram on your phone, web UI from
  a browser, CLI from your laptop, all pointing at the same memory.
- It can run the housekeeper LLM (DeepSeek / equivalent) on a schedule
  during quiet hours when API quota and pricing are friendliest.

If you only want LMC-5 as an SDK to embed in a desktop agent, fine —
none of this is required. The VPS shape is the **recommended deployment
for a persona-class long-running agent**, which is what the architecture
was extracted from.

## The Daily Loop

A typical day on a VPS-hosted LMC-5 deployment looks like this:

```
00:00 - 06:00   user usually offline
  04:00 (local)  nightly housekeeper run:
                   - archive yesterday's chunks
                   - hippocampus: propose candidates from chunks
                   - relation graph: name and queue Y-axis edges
                   - Z-axis: judge contradiction pairs to audit table
                   - M-axis: weight decay, dedup proposals, condensation
                   - narrative timeline: weekly index if Monday
                   - stopword learning if scheduled
06:00 - 24:00   user-facing hours
                   - foreground agent serves queries
                   - real-time write path stores raw events
                   - real-time read path does recall + recency boost
                   - new candidates queued for tonight's housekeeper run
```

The split between foreground and background is what makes the agent
**feel coherent over weeks** without paying for an always-on housekeeper.

## Three Frontend Options

LMC-5 is the memory layer, not a frontend. You choose how users reach
the agent. Three known-good patterns:

### 1. Telegram (recommended for personal use)

If you are running an agent built on Claude Code (which has an official
Telegram plugin channel), Telegram is the lowest-friction option:

- One bot per user, owned by you
- Native mobile push, native voice / image attachments
- Long-poll or webhook, both supported by the Telegram Bot API
- Conversation thread per chat, which maps cleanly onto session IDs

Pair this with a session bridge on the VPS: incoming Telegram messages
hand off to the agent, the agent's reply goes back through the bot. No
custom client to maintain.

### 2. WeChat bot (for Chinese-context deployments)

For deployments where the user lives on WeChat, a personal WeChat bot
adapter works. Options vary in legality and longevity by region —
official-account API for compliant deployments, personal-account
bridges for personal use where you control both sides.

Same architectural shape: incoming message → agent → reply. The memory
layer does not care which channel delivered the prompt.

### 3. Self-hosted frontend (for richer interaction)

If you want avatars, Live2D, web-UI memory inspectors, or a desktop
companion shell, host a small frontend on the VPS:

- Web UI: Flask / FastAPI + a JS frontend, served behind a reverse proxy
- Desktop shell: PyWebView, Electron, or Tauri wrapping the same web UI
- Both can read directly from the same database the housekeeper writes
  to — you get a "memory inspector" view for free

This is the heaviest option but the one that lets you visualize memory
state, browse historical reflections, and manually approve Z-axis
supersede candidates from a UI instead of CLI.

## Choosing Between Them

| Frontend | Best for | Effort |
|----------|----------|--------|
| Telegram | A single user (you), mobile-first, lowest infra | Low |
| WeChat bot | Chinese-context user, mobile-first | Low–medium |
| Self-hosted UI | Power user wanting visibility into memory, multiple users, richer interaction | Medium–high |

You can run more than one frontend against the same VPS deployment —
the memory layer is shared, so adding a web UI on top of an existing
Telegram bot does not force you to rebuild memory storage.

## Operational Reminders

- **Backups.** Whatever database you choose (SQLite file, Postgres dump,
  or a managed DB's snapshot), back it up. Memory loss is the worst
  failure mode for a persona-class agent.
- **Secrets.** API keys, bot tokens, database passwords — none of these
  belong in the application config that lives in the repo. Use
  environment variables or a secret store.
- **Quiet hours.** Pick consolidation times that match your timezone's
  *user-asleep* window, not UTC defaults. The point of background jobs
  is to not interrupt foreground use.
- **Monitoring.** A persona-class agent feels broken in subtle ways —
  retrieval got duller, contradiction count silently grew, last
  hippocampus run failed three nights ago. A small daily log mailer or
  health endpoint catches these before the user notices.

The architecture handles long-running deployments by design. The ops
work is yours.
