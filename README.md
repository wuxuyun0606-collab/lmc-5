# LMC-5

![LMC-5 · Living Memory Coordinate-5](https://raw.githubusercontent.com/wuxuyun0606-collab/lmc-5/a16b513a1f268d886bf9632bb11fc4363892804e/docs/assets/memory-coordinates.svg)

**Memory for agents across sessions.** Save a release decision in SQLite and retrieve it in the next session. When the procedure changes, write the replacement under the same fact key to keep its history.

[简体中文](README.zh-CN.md)　·　[Documentation](docs/README.md)　·　[Architecture](docs/architecture.md)　·　[Deployment](docs/DEPLOYMENT.md)　·　[JEV reranking](docs/JEV.md)

X Timeline · Y Relations · Z Fact evolution · E Experience · M Metabolism

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

Write a release owner twice under one fact key. The second entry becomes current and the first stays in the history.

```sh
lmc5 add --db demo.sqlite --fact-key release.owner --title "Release owner" --content "Alice owns releases."
lmc5 add --db demo.sqlite --fact-key release.owner --title "Release owner" --content "Sam owns releases."
```

[How the layers work together](docs/CONNECTING_XYZEM.md) · [Runnable Python example](examples/demo.py)

</details>

## Connect your agent

**Local storage**　Point writes, searches and scheduled checks at the same SQLite file. [Persistent deployment](docs/DEPLOYMENT.md)

**PostgreSQL and Claude Code**　Verify full-text search with a sample record, then connect session archival and recall. [pgvector setup](extras/pgvector_backend/README.md) · [Hooks](docs/HOOKS_AND_RECALL.md)

**TypeSafe JEV**　Order retrieved candidates by relevance. You can opt in from either SQLite or PostgreSQL. [Configure JEV](docs/JEV.md)

<details>
<summary>Run the AML competition Add/Search HTTP service</summary>

The registration snapshot is `v0.3.0-aml.1`. See the code Full reconciliation, text-track status and deployment instructions in the [AML version table](extras/aml/README.md#version-and-source-correspondence).

</details>

---

[Project story](docs/KELIN.md) · [Credits](docs/credits.md) · [Contributing](CONTRIBUTING.md) · [Open-source and commercial licensing](LICENSING.md)
