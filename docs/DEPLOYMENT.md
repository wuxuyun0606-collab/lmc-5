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
  04:00 (local)  nightly housekeeper run (`dream_runner` covers ① ② ③ ④ ⑤ ⑦ plus read-only patrol):
                   ① archive yesterday's chunks (consolidate)
                   ② hippocampus: propose candidates from chunks
                   ③ Y-axis: write safe relations / queue review relations
                                  (built inside ②; no separate step)
                   ④ Z-axis: judge contradiction pairs to audit table
                   ⑤ X/M-axis: sweep each configured timeline thread for
                                  split/review/cleanup/reflection candidates
                   ⑥ M-axis: weight decay, dedup, condensation
                                  ⚠️ NOT in dream_runner — schedule separately
                   ⑦ narrative timeline: weekly index if Monday;
                                              monthly index first N days of month
                   ⑧ stopword learning if scheduled
                                  ⚠️ NOT in dream_runner — schedule separately
06:00 - 24:00   user-facing hours
                   - foreground agent serves queries
                   - real-time write path stores raw events
                   - real-time read path does recall + recency boost
                   - new candidates queued for tonight's housekeeper run
```

The split between foreground and background is what makes the agent
**feel coherent over weeks** without paying for an always-on housekeeper.

## Scheduling The Loop On A VPS

The whole point of the VPS shape is that **the agent grooms its own
memory on its own schedule, without you doing anything**. Once the
schedule is set, the persona quietly consolidates yesterday's chunks,
reflects on last week, and decays stale weights — all while you sleep.

Two equivalent ways to wire it up:

### Option A · cron (simple)

Drop one entry per job into the VPS user's crontab:

```cron
# nightly: full dream pipeline
#   consolidate → hippocampus (incl. Y relation build) → heartbeat
#   → e_axis_backfill → timeline_sweep(each X-line)
#   → narrative (weekly + monthly when due) → z_audit → patrol
0 4 * * *  cd /opt/lmc5-agent && /opt/lmc5-agent/.venv/bin/python -m extras.pgvector_backend.dream_runner >> logs/nightly.log 2>&1
```

The intended local-time schedule is also represented in code by
`extras.pgvector_backend.dream_runner.DreamSchedule`. Its default cron
expression is tested as `0 4 * * *`, so "nightly at 04:00" is not only a
doc comment.

> **What's NOT in `dream_runner`**: M-axis weight decay / dedup /
> condensation, and stopword learning. If you want those, schedule
> them separately (your own callables on a separate cron line, or
> point them at `lmc5 patrol` and friends).
>
> **Narrative weekly/monthly are inside `dream_runner`** — its
> `narrative_weekly` / `narrative_monthly` callables fire on the
> right days (monthly only the first N days of the month). You do
> **not** need separate cron entries for them unless you've
> deliberately left those callables unset.
>
> **Per-line cleanup is inside `dream_runner` when configured** — pass
> `timeline_sweep(thread)` plus `timeline_threads=[...]`. The runner attempts
> every X-line independently, records per-line status, and continues to Z/M
> checks even if one line fails.

Schedule jobs to your **user's quiet hours**, not UTC. The whole point
is to not collide with foreground use.

> **⚠️ The nightly `dream` pass is what builds the Y relation graph
> (`memory_relations` table).** If you skip nightly, your writes
> accumulate but the graph stays empty — `graph_activate` returns
> nothing and 2-hop recall is silently dead. This also breaks the
> cross-axis connections that ride on `memory_relations` (X temporal
> sequence, Z fact contradiction/supersession, M derivation chains).
> See [`Y_RELATIONS.md`](Y_RELATIONS.md#-how-to-actually-build-relations)
> for the verification SQL.
>
> For the axis-by-axis automation boundary, including what must remain
> review-only, see [`AUTOMATION_BOUNDARIES.md`](AUTOMATION_BOUNDARIES.md).

### Option B · systemd timer (recommended for production)

systemd gives you durable scheduling, automatic catch-up if the VPS was
down, and proper service supervision. Two unit files:

```ini
# /etc/systemd/system/lmc5-nightly.service
[Unit]
Description=LMC-5 nightly housekeeper
After=network-online.target

[Service]
Type=oneshot
User=lmc5
WorkingDirectory=/opt/lmc5-agent
ExecStart=/opt/lmc5-agent/.venv/bin/python -m extras.pgvector_backend.dream_runner
StandardOutput=append:/var/log/lmc5/nightly.log
StandardError=append:/var/log/lmc5/nightly.log
```

```ini
# /etc/systemd/system/lmc5-nightly.timer
[Unit]
Description=Run LMC-5 nightly housekeeper at 04:00 local

[Timer]
OnCalendar=*-*-* 04:00:00
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
```

`Persistent=true` is the key line — if the VPS was rebooted at 03:45,
the job still fires when it comes back. `RandomizedDelaySec=300`
jitters by up to 5 minutes so multiple agents on the same provider do
not stampede an LLM API at the exact same second.

Enable with:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now lmc5-nightly.timer
systemctl list-timers lmc5-nightly.timer
```

### What "Self-Grooming" Actually Buys You

Once this is running, you can ignore the VPS for weeks and the persona
will:

- Promote yesterday's events into curated memories (without you
  reviewing every chunk)
- Decay last month's tool noise (so recall stays sharp)
- Catch contradictions in the audit queue (waiting for you to approve)
- Index the week into a narrative summary (so "what happened last
  Tuesday" has a real answer)
- Surface forgotten threads via spontaneous recall (so the next time
  you log in, the persona may bring something up that you yourself
  had stopped thinking about)

This is the difference between **a chat app with memory** and **an
agent that maintains itself**. The VPS shape is what makes the second
option practical.

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
