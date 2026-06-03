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
- `patrol()`

The MCP server should be a thin adapter. The source of truth should remain the
local LMC-5 SQLite database and the provider-free Python API.

## Recommended Lifecycle

```text
Claude Code prompt/tool events
  -> lmc5 log-event
  -> lmc5 consolidate
  -> review observation memories
  -> lmc5 surface before future tasks
```

`consolidate` turns raw events into reviewable observations:

```bash
lmc5 consolidate --db "$HOME/.lmc5/claude-code.sqlite" --window-size 20
```

This gives Claude Code a memory lifecycle instead of an ever-growing prompt.

## Safety Boundary

Do not store real secrets. LMC-5 includes redaction helpers, but redaction is a
guardrail, not permission to log API keys, account tokens, database DSNs, or
credentials.

For production agents:

- Keep the database local or in a private trusted store.
- Redact before embedding or sending content to remote providers.
- Treat raw events as evidence, not automatically-current facts.
- Use Z-axis fact evolution before injecting old conclusions into a new task.

