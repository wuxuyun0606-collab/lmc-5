# LMC-5 · Living Memory Coordinate-5

[简体中文](README.zh-CN.md)

LMC-5 gives an agent a memory it can reopen in its next session. It keeps raw events as evidence and stores decisions and facts as separate records. When a project changes its release procedure, for example, the agent can recall the current procedure and still trace the decision it replaced.

## Try it locally

You need Git and Python 3.9+ with SQLite FTS5. Run these commands in a macOS or Linux shell. The SQLite core runs locally without API keys or a database server.

```bash
git clone https://github.com/wuxuyun0606-collab/lmc-5.git
cd lmc-5
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .

lmc5 init --db demo.sqlite
lmc5 import-jsonl --db demo.sqlite examples/seed.jsonl
lmc5 recall --db demo.sqlite rollback
```

In a fresh database, the import reports `"created": 2` and the search returns **Confirm blast radius before production work**. Open another shell and search the same file to recover the decision. The `--db` path is relative to the working directory. For a persistent setup, give your agent an absolute path.

To try the examples offline from an existing checkout, run `PYTHONPATH=src python3 examples/demo.py`. The demo creates its own temporary database. To inspect yours, run `lmc5 stats --db demo.sqlite`.

## How memory works

| Layer | What it records or does |
|---|---|
| X · Timeline | Groups memories by thread so an agent can follow a project over time. |
| Y · Relations | Links memories by topic, event or derivation. Recall can follow eligible links up to two hops. |
| Z · Fact evolution | Tracks which version of a fact is current. A new active fact with the same key supersedes the previous one. |
| E · Experience | The primary agent records how an experience should affect its response and assigns the initial priority. |
| M · Metabolism | Inspects memory quality and flags records for review, consolidation or retirement. |

Use `log-event` for a conversation turn and `add` for a decision worth keeping. Use `recall` to retrieve curated memories, or `surface` when the agent also needs the surrounding evidence. [Architecture](docs/architecture.md) · [How the layers work together](docs/CONNECTING_XYZEM.md)

## Choose a deployment

| Goal | Start here |
|---|---|
| Keep memory for a local agent or a single host | The installed `lmc5` CLI and Python library use SQLite. [Persistent storage and scheduled checks](docs/DEPLOYMENT.md) |
| Integrate with an existing PostgreSQL agent | The alpha pgvector backend in the source checkout provides SQL, recall adapters and hook modules. Start with a keyword lookup, then wire vectors or nightly jobs as needed. [PostgreSQL setup](extras/pgvector_backend/README.md) |
| Use the AML Add/Search HTTP API | The optional competition adapter has its own dependencies, credentials and launcher. [AML adapter on main](extras/aml/README.md) · [Competition maintenance branch](https://github.com/wuxuyun0606-collab/lmc-5/tree/fix/aml-serving-recovery) |

## Go further

- Connect an agent with a shell wrapper or hooks. [Claude Code integration](docs/claude_code.md) · [Recall pipeline](docs/HOOKS_AND_RECALL.md)
- Run the [Python example](examples/demo.py) or trace a [two-hop graph lookup](examples/two_hop_graph.py).
- Implement the layers in an existing system. [Implementation order](docs/IMPLEMENTATION_ORDER.md) · [What runs automatically](docs/AUTOMATION_BOUNDARIES.md) · [Safety](docs/safety.md)
- Read about the project’s origins and the people behind it. [Kelin’s account](docs/KELIN.md) · [Credits](docs/credits.md)

**Status and license.** Alpha. Versions from 0.3.0 use AGPL-3.0-or-later. Releases through v0.2.0 retain the MIT license. Separate commercial terms are available. [Licensing](LICENSING.md) · [Contributing](CONTRIBUTING.md)
