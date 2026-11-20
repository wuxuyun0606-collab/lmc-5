![LMC-5 · Living Memory Coordinate-5](docs/assets/memory-coordinates.svg)

**Memory for agents across sessions.** Save a release decision in SQLite and retrieve it in the next session. When the procedure changes, write the replacement under the same fact key to keep its history.

[简体中文](README.zh-CN.md)　·　[Architecture](docs/architecture.md)　·　[Deployment](docs/DEPLOYMENT.md)　·　[JEV reranking](docs/JEV.md)

## Retrieve your first memory

Start with Git and Python 3.9+ with SQLite FTS5. Run these commands in a macOS or Linux shell.

```sh
git clone https://github.com/wuxuyun0606-collab/lmc-5.git
cd lmc-5
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .

lmc5 import-jsonl --db demo.sqlite examples/seed.jsonl
lmc5 recall --db demo.sqlite rollback
```

**What should you see?** A fresh database reports `"created": 2`. Look for **Confirm blast radius before production work** in the search results. A later session can retrieve the decision from the same database file.

<details>
<summary>How do the five layers use this memory?</summary>

- **X · Timeline** places the decision on a thread of work.
- **Y · Relations** connects it to related events and follow-up checks. Recall can follow eligible links up to two hops.
- **Z · Fact evolution** tracks the current version under a fact key and preserves earlier ones.
- **E · Experience** records how the primary agent intends to respond next time and the initial priority it assigns.
- **M · Metabolism** flags accumulated memories for review, consolidation or retirement.

[How the layers work together](docs/CONNECTING_XYZEM.md) · [Runnable Python example](examples/demo.py)

</details>

## Connect your agent

**Local storage**　Point writes, searches and scheduled checks at the same SQLite file. [Persistent deployment](docs/DEPLOYMENT.md)

**PostgreSQL and Claude Code**　Verify full-text search with a sample record, then connect session archival and recall. [pgvector setup](extras/pgvector_backend/README.md) · [Hooks](docs/HOOKS_AND_RECALL.md)

**TypeSafe JEV**　Order retrieved candidates by relevance. You can opt in from either SQLite or PostgreSQL. [Configure JEV](docs/JEV.md)

<details>
<summary>Run the AML competition Add/Search HTTP service</summary>

The competition adapter has its own dependencies, credentials and launcher. [AML deployment](extras/aml/README.md)

</details>

---

[Project story](docs/KELIN.md) · [Credits](docs/credits.md) · [Contributing](CONTRIBUTING.md) · [Open-source and commercial licensing](LICENSING.md)
