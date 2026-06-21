---
name: Bug report
about: Report a reproducible LMC-5 problem
title: ""
labels: bug
assignees: ""
---

## What Happened

Describe the issue clearly.

## Reproduction

```bash
# commands or minimal Python snippet
```

If this is a recall/surfacing issue, include the exact redacted commands you ran:

```bash
lmc5 state-refresh --db path/to/lmc5.sqlite
lmc5 surface --db path/to/lmc5.sqlite "your query"
lmc5 recall --db path/to/lmc5.sqlite "your query"
lmc5 recall-traces --db path/to/lmc5.sqlite --limit 10
```

## Expected Behavior

What should have happened?

## Actual Output

Paste the relevant redacted output. For recall bugs, include:

- `score_breakdown`
- `reasons`
- `trace`
- `related_from`
- `state` output if `surface` was involved

## Recall/State Settings

- Did you run `state-refresh` before testing?
- Was `--no-relations` used?
- Was `--no-entity-boost` used?
- Was `--no-temporal-boost` used?
- Was `--no-trace` used?

## Environment

- Python version:
- SQLite version:
- LMC-5 version or commit:

## Safety Note

Do not paste real tokens, cookies, production DSNs, private logs, or user data.
