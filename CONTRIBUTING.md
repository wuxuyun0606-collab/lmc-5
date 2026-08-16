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

## Licensing of Contributions

LMC-5 version 0.3.0 and later is publicly licensed under
`AGPL-3.0-or-later` and may also be offered under separate commercial terms.

By default, a pull request alone grants rights only under the repository's
public AGPL license. That is not enough for the project to relicense a
third-party contribution commercially.

Until the project publishes a contributor agreement with an identified legal
licensor, maintainers must not merge third-party code into a release intended
for dual licensing without a separate explicit written grant from that
contributor. Issues, design discussion, review comments, and proposals remain
welcome.

Do not treat a Developer Certificate of Origin sign-off as permission to
relicense a contribution commercially; a separate contributor agreement is
required.
