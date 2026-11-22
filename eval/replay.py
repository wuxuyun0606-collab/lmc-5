"""Paired SQLite recall experiments on the public synthetic cases."""

import json
from pathlib import Path

from lmc5 import MemoryStore


def fact_updates(cases, keyed):
    results = []
    record_count = sum(len(case["versions"]) for case in cases)
    with MemoryStore(":memory:") as store:
        store.init()
        for case in cases:
            for revision, content in enumerate(case["versions"]):
                store.add_memory(
                    title=case["query"],
                    content=content,
                    fact_key=case["key"] if keyed else None,
                )
                if revision == 0:
                    continue
                hits = store.recall(case["query"], limit=5, trace=False)
                retrieved = {hit["content"] for hit in hits}
                previous = set(case["versions"][:revision])
                archived = {
                    record.content
                    for record in store.list_recent(limit=record_count)
                    if record.title == case["query"] and record.status == "superseded"
                }
                results.append({
                    "query": case["query"], "revision": revision + 1,
                    "current_hit": content in retrieved,
                    "stale_hit": bool(previous & retrieved),
                    "empty": not hits,
                    "history_marked": previous <= archived,
                })
    return results


def graph_queries(graph, expand):
    results = []
    with MemoryStore(":memory:") as store:
        store.init()
        ids = {}
        for label, title in graph["records"].items():
            record, _ = store.add_memory(title=title, content=title)
            ids[label] = record.id
        for source, target, relation, strength in graph["edges"]:
            store.add_relation(ids[source], ids[target], relation, strength=strength)
        for case in graph["queries"]:
            hits = store.recall(case["query"], limit=5, expand_relations=expand, trace=False)
            retrieved = {hit["id"] for hit in hits}
            relevant = {ids[label] for label in case["relevant"]}
            results.append({
                "query": case["query"],
                "target_hit": ids[case["target"]] in retrieved,
                "unrelated_hits": len(retrieved - relevant),
                "returned": len(retrieved),
            })
    return results


def seed_update(keyed):
    """Replay the quickstart notes, then publish one revised production policy."""
    path = Path(__file__).resolve().parents[1] / "examples" / "seed.jsonl"
    notes = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    with MemoryStore(":memory:") as store:
        store.init()
        original = next(note for note in notes if note["fact_key"] == "agent.safety.production_change")
        checkpoint = next(note for note in notes if note["fact_key"] == "agent.workflow.compaction_checkpoint")
        revised = dict(original, content="Record the rollback owner and test a restore before editing.")
        for note in [*notes, revised]:
            store.add_memory(**dict(note, fact_key=note["fact_key"] if keyed else None))
        policy_hits = store.recall("rollback", limit=5, trace=False)
        contents = {hit["content"] for hit in policy_hits}
        control = store.recall("checkpoint", limit=5, trace=False)
        return {
            "current_hit": revised["content"] in contents,
            "stale_hit": original["content"] in contents,
            "empty": not policy_hits,
            "unmodified_checkpoint_hit": any(hit["content"] == checkpoint["content"] for hit in control),
        }


def main():
    cases = json.loads(Path(__file__).with_name("cases.json").read_text(encoding="utf-8"))
    results = {
        "quickstart_without_fact_keys": seed_update(False),
        "quickstart_with_fact_keys": seed_update(True),
        "Z_without_fact_keys": fact_updates(cases["fact_updates"], False),
        "Z_with_fact_keys": fact_updates(cases["fact_updates"], True),
        "Y_without_expansion": graph_queries(cases["graph"], False),
        "Y_with_expansion": graph_queries(cases["graph"], True),
    }
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
