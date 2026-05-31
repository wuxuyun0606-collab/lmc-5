# Contributing

LMC-5 is early and intentionally small. Contributions should preserve the
project's main constraint: memory systems must be auditable before they are
clever.

## Good First Contributions

- Improve redaction test coverage.
- Add new read-only patrol checks.
- Add storage adapters behind the same model API.
- Improve documentation and examples.
- Add graph expansion for relations without bypassing redaction.

## Design Rules

- Keep the default implementation offline and zero-dependency.
- Never add network calls to the core package.
- Never add examples with real tokens, DSNs, cookies, or credentials.
- Patrol checks must remain read-only unless a separate audited mutation path is added.
- Experience signals must not override verified facts.

## Development

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
PYTHONPATH=src python3 -m pytest tests
```
