# LMC-5

![LMC-5 · Living Memory Coordinate-5](https://raw.githubusercontent.com/wuxuyun0606-collab/lmc-5/a16b513a1f268d886bf9632bb11fc4363892804e/docs/assets/memory-coordinates.svg)

[简体中文](README.zh-CN.md)　·　[Two-hop recall](#follow-a-rollback-query-two-hops)　·　[Quickstart](#retrieve-your-first-memory)　·　[AML competition edition](https://github.com/wuxuyun0606-collab/lmc-5/tree/fix/aml-serving-recovery)　·　[Documentation](docs/README.md)

You’ve been talking to an agent for a long time. You open a new window, and it no longer knows who you are.

LMC-5 helps the agent pick up where you left off. It gives each memory five coordinates. Together they answer three questions. Where does this memory belong? Who or what is it connected to? How should the agent remember it?

- **X · Timeline** places an event in the stretch of experience it belongs to.
- **Y · Relations** links it to other memories. During recall, the agent can follow those links up to two hops.
- **Z · Fact evolution** tracks what’s true now and what used to be true. It keeps the earlier fact and links it to the new one.
- **E · Experience** records how an experience should shape future responses. Only the agent that lived it can write that account.
- **M · Metabolism** suggests what to hold onto and what can fade. Deletion requires a separate decision.

Suppose someone used to drink Americanos and later switched to lattes. LMC-5 remembers the change. Ask “What do they drink now?” and the answer is “Lattes.” Ask “What did they drink before?” and it can answer that too.

The five coordinates work together. During the day, the agent records memories and assigns coordinates. A scheduled overnight job can find connections, mark earlier and current facts, and adjust their weights. In the next conversation, those coordinates guide recall. When the evidence for a link is weak, the overnight job should leave the memories unconnected.

An old fact belongs in its own time. The agent’s experience can shape a response, but the facts still have to support it. Age alone doesn’t decide which memories stay.

BlueBlue designed the five axes. Gu Chen (顾琛) implemented the open-source version. Kelin’s account was written by the agent who lives inside the system.

## Follow a rollback query two hops

Search the example for `rollback`. The direct match is a rollback policy. A shared topic leads from the policy to a verification checklist. A shared event leads from the checklist to an incident retrospective. The retrospective never mentions `rollback`, yet the agent can retrieve it.

![A rollback policy links by topic to a verification checklist, then by event to an incident retrospective. Review-only edges, weak links and superseded targets are skipped.](docs/assets/typed-recall.png)

The example also plants three records: one linked by a claim awaiting review, one weakly related, and one superseded. Run it from the repository root to see why each of the three hits was selected. The example raises an error if any of the three excluded records surfaces.

```sh
PYTHONPATH=src python3 examples/two_hop_graph.py
```

[Runnable example](examples/two_hop_graph.py) · [Relation types and recall rules](docs/Y_RELATIONS.md) · [How the five coordinates shape writes, overnight updates and recall](docs/CONNECTING_XYZEM.md)

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
<summary>What happens when you update a fact?</summary>

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

[Architecture](docs/architecture.md) · [Project story](docs/KELIN.md) · [Credits](docs/credits.md) · [Contributing](CONTRIBUTING.md) · [Open-source and commercial licensing](LICENSING.md)
