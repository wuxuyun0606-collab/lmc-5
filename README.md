<h1 align="center">LMC-5</h1>
<p align="center"><strong>Living Memory Coordinate-5</strong><br>Recover decisions, current facts and their history across agent sessions.</p>
<p align="center"><a href="README.zh-CN.md">简体中文</a> · <a href="docs/architecture.md">Architecture</a> · <a href="docs/DEPLOYMENT.md">Deployment</a> · <a href="docs/JEV.md">JEV reranking</a></p>

## Quickstart

Start with Git and Python 3.9+ with SQLite FTS5. These commands run in a macOS or Linux shell. Once installed, the default CLI works offline.

```sh
git clone https://github.com/wuxuyun0606-collab/lmc-5.git
cd lmc-5
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
```

Import two example decisions, then look up the one about rollback.

```sh
lmc5 import-jsonl --db demo.sqlite examples/seed.jsonl
lmc5 recall --db demo.sqlite rollback
```

The first command creates the database. On a fresh run it reports `"created": 2`. The search returns **Confirm blast radius before production work**. Your next session can open the same file.

## From one session to the next

An agent can log a conversation with `log-event`, save a decision with `add`, and retrieve it with `recall`. When a release procedure changes, the agent can save its replacement under the same fact key. Future sessions retrieve the current procedure while the earlier version stays available for inspection.

<details>
<summary><strong>How the five memory layers fit together</strong></summary>

| Layer | Role |
| --- | --- |
| X · Timeline | Follow a thread of work across sessions. |
| Y · Relations | Find related memories through up to two hops. |
| Z · Fact evolution | Track which version of a fact is current. |
| E · Experience | Record the primary agent’s response tendency and initial priority. |
| M · Metabolism | Flag memories for review, consolidation or retirement. |

[Architecture](docs/architecture.md) · [How the layers work together](docs/CONNECTING_XYZEM.md) · [Python example](examples/demo.py)

</details>

## Deploy and extend

| Goal | Guide |
| --- | --- |
| Keep a SQLite store across sessions | [Persistent storage and scheduled checks](docs/DEPLOYMENT.md) |
| Connect an agent to PostgreSQL | [pgvector setup](extras/pgvector_backend/README.md) · [Claude Code hooks](docs/HOOKS_AND_RECALL.md) |
| Rerank recall results with TypeSafe JEV | [Enable JEV for SQLite or PostgreSQL](docs/JEV.md) |
| Run the AML competition HTTP service | [Add/Search adapter](extras/aml/README.md) |

[Project story](docs/KELIN.md) · [Credits](docs/credits.md) · [Contributing](CONTRIBUTING.md) · [Licensing (AGPL-3.0-or-later from v0.3.0)](LICENSING.md)
