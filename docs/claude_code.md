# Using LMC-5 With Claude Code

LMC-5 is compatible with Claude Code because the core is just a local CLI,
Python library, and SQLite store. It does not require a hosted service, a model
provider, or a specific Claude account.

The reference package does not install Claude Code hooks automatically yet. Use
one of these integration patterns.

## Pattern 1: Shell Wrapper

Use a wrapper script to surface memory before launching Claude Code:

```bash
#!/usr/bin/env bash
set -euo pipefail

DB="${LMC5_DB:-$HOME/.lmc5/claude-code.sqlite}"
PROJECT_QUERY="${1:-current project context}"

lmc5 init --db "$DB" >/dev/null
lmc5 surface --db "$DB" "$PROJECT_QUERY" > /tmp/lmc5-surface.json

echo "LMC-5 surface written to /tmp/lmc5-surface.json"
exec claude
```

How you inject the surface is up to your workflow: project instructions,
session-start notes, or a manual paste during early experiments.

## Pattern 2: Claude Code Hooks

Claude Code hooks can call the same CLI commands:

```bash
lmc5 log-event \
  --db "$HOME/.lmc5/claude-code.sqlite" \
  --role user \
  --channel claude-code \
  --content "$USER_PROMPT"
```

For context injection, call:

```bash
lmc5 surface \
  --db "$HOME/.lmc5/claude-code.sqlite" \
  --memory-limit 5 \
  --event-limit 3 \
  "$USER_PROMPT"
```

Keep hook output redacted. The CLI redacts common API keys, tokens, DSNs, and
password-like values before printing recall/surface results.

## Pattern 3: MCP Sidecar

An MCP adapter can expose these LMC-5 operations:

- `recall(query)`
- `surface(query)`
- `log_event(role, content, channel)`
- `consolidate(window_size, channel)`
- `hippocampus(channel, apply=False)`
- `z_audit(apply=False)`
- `patrol()`

The MCP server should be a thin adapter. The source of truth should remain the
local LMC-5 SQLite database and the provider-free Python API.

## Recommended Lifecycle

```text
Claude Code prompt/tool events
  -> lmc5 log-event
  -> lmc5 consolidate
  -> lmc5 hippocampus
  -> lmc5 z-audit
  -> review observation memories
  -> lmc5 surface before future tasks
```

`consolidate` turns raw events into reviewable observations:

```bash
lmc5 consolidate --db "$HOME/.lmc5/claude-code.sqlite" --window-size 20
lmc5 hippocampus --db "$HOME/.lmc5/claude-code.sqlite"
lmc5 z-audit --db "$HOME/.lmc5/claude-code.sqlite"
```

This gives Claude Code a memory lifecycle instead of an ever-growing prompt.
Use `lmc5 hippocampus --apply` only after you are comfortable with the dry-run
output, or from a controlled nightly job.
Use `lmc5 z-audit --apply` only to record pending conflict audits; it does not
supersede facts.

## VPS 7*24 Hour Shape

For an always-on agent, run the memory layer beside Claude Code/Codex on a small
VPS rather than tying it to a desktop window. A practical deployment is:

- Agent hooks or an MCP sidecar append raw events with `lmc5 log-event`.
- A scheduled job runs `lmc5 consolidate` to create bounded evidence chunks.
- A nightly dry-run runs `lmc5 hippocampus` and stores or reports the candidate plan.
- A Z-axis dry-run runs `lmc5 z-audit` to list contradiction candidates.
- A controlled job may run `lmc5 hippocampus --apply` after the dry-run output is trusted.
- `lmc5 patrol` stays read-only and reports backlog, duplicates, and review pressure.

This is a 7*24 hour survival pattern, not permission to let a model rewrite
memory unattended. Keep the database private, back it up, and keep redaction in
front of any remote provider.

### Forge

Forge is the session-renewal pattern. At the end of a session, or before a new
one starts, run the lifecycle checks and generate a fresh `surface` payload for
the next agent window. The process may restart; memory continuity survives.

```text
old session -> event journal -> lifecycle jobs -> forged launch context -> new session
```

### Refined Session Carryover / 精炼续窗

For Claude Code transcript resume, do not blindly keep the last 80k-100k tokens.
That old tail-cache pattern can carry engineering noise into the next window.
Use Refined Session Carryover when you need a live `claude --resume` bridge:

```text
old transcript
  -> drop tools/logs/hooks/paths/tracebacks
  -> keep high-signal memory/state + short clean tail
  -> new transcript
  -> claude --resume <new-session-id>
```

Reference helper:

```bash
python extras/claude_code/refined_session_carryover.py \
  --project-dir "$HOME/.claude/projects/<project-hash>" \
  --dry-run
```

If recent context looks AUP/policy poisoned, fail closed and start a fresh
window; let LMC-5 durable recall rebuild context. See
[`REFINED_SESSION_CARRYOVER.md`](REFINED_SESSION_CARRYOVER.md).

### Swap

Swap is the rollback pattern. Keep snapshots around scheduled writes, especially
before `hippocampus --apply`, future model-assisted Z verdicts, migrations, or
adapter changes. If a job produces noisy memory, swap back to the last known
good SQLite snapshot and replay only reviewed changes.

## Safety Boundary

Do not store real secrets. LMC-5 includes redaction helpers, but redaction is a
guardrail, not permission to log API keys, account tokens, database DSNs, or
credentials.

For production agents:

- Keep the database local or in a private trusted store.
- Redact before embedding or sending content to remote providers.
- Treat raw events as evidence, not automatically-current facts.
- Use Z-axis fact evolution before injecting old conclusions into a new task.
