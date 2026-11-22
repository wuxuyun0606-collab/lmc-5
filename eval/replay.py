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


def main():
    cases = json.loads(Path(__file__).with_name("cases.json").read_text(encoding="utf-8"))
    results = {
        "Z_without_fact_keys": fact_updates(cases["fact_updates"], False),
        "Z_with_fact_keys": fact_updates(cases["fact_updates"], True),
        "Y_without_expansion": graph_queries(cases["graph"], False),
        "Y_with_expansion": graph_queries(cases["graph"], True),
    }
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
