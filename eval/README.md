# SQLite recall experiments

These results come from the core SQLite `MemoryStore` in `replay.py`, with a hand-built graph for the Y experiment. The AML serving edition in `extras/aml/` has no cross-session timeline (X) or stored relation edges (Y) and is outside this evaluation.

Run from the repository root with Python 3.9+ and SQLite FTS5:

```sh
PYTHONPATH=src python3 eval/replay.py
```

The runner prints a JSON row per query and configuration. Each configuration replays the same synthetic [cases](cases.json) in a fresh in-memory database with a five-record return limit. It uses the public SQLite write and recall APIs and needs no model, API key or network connection.

## Quickstart seed replay

The runner also loads the two published [quickstart records](../examples/seed.jsonl) and appends a synthetic revision to the production policy. It searches for `rollback` and checks the untouched `checkpoint` note as a control. Both configurations use the same texts and a five-record limit.

| One policy update | Revised policy found | Old policy also returned | Untouched checkpoint found |
| --- | ---: | ---: | ---: |
| Ordinary notes | 1/1 | 1/1 | 1/1 |
| Shared fact keys | 1/1 | 0/1 | 1/1 |

## Z · Which fact is current?

Three subjects change over time: a drink preference, a release owner who changes twice, and a commute. After each change the runner immediately retrieves the subject. One pass writes ordinary notes; the other gives successive versions of a subject the same fact key. Wording, write order and queries are identical. The query occurs in the title in both passes.

| Four update checkpoints | New value in top 5 | Any old value in top 5 | Empty result | All previous values stored as superseded |
| --- | ---: | ---: | ---: | ---: |
| Ordinary notes | 4/4 | 4/4 | 0/4 | 0/4 |
| Shared fact key | 4/4 | 0/4 | 0/4 | 4/4 |

The last column inspects the stored records after each update. It measures preservation and labeling of earlier values. Answering a natural-language question about the past and inferring fact keys from conversation would require separate evaluations.

## Y · What does following an edge add?

The graph has eight records. Two queries need two hops to reach the labeled target. A third directly matches its target. Both runs have all the edges. Only the recall option `expand_relations` changes. The fixture lists the target and the useful supporting records for each query.

| Three queries | Two-hop targets in top 5 | Direct target in top 5 | Unrelated records / all returned |
| --- | ---: | ---: | ---: |
| Expansion off | 0/2 | 1/1 | 0/3 |
| Expansion on | 2/2 | 1/1 | 3/10 |

The rollback query also reached an irrelevant inventory record through a valid project edge. The direct query already had its answer and gained two unnecessary neighbors. A connection can be valid even when it adds no evidence for the question.

## What these experiments cover

The SQLite replay exercises Y and Z. The three unrelated records are **3 of the 10 returned with expansion enabled**, across these three queries. Two of them came from the direct query. The two-hop queries together added one unrelated record. This distinction matters when deciding which queries should expand.

| Axis | Evidence still needed |
| --- | --- |
| X · Timeline | Replay interleaved sessions on several threads with labeled event order and thread membership. Score whether recall recovers the correct thread and sequence. Message timestamps alone cannot establish this. |
| E · Experience | An offline fixture can test authorship and write permissions. Evaluating the quality of a first-person experience record requires a session in which the primary agent lives the event, authors the record and later reviews its effect. |
| M · Metabolism | A controlled clock can test weight changes or archive rules. Measuring their long-term value requires repeated recalls across days, including whether a downgraded memory is needed again. |

A separate [LoCoMo pilot](LOCOMO.md) compares lexical, vector and hybrid evidence retrieval on public conversations, with current-state and historical-event questions scored separately. The AML leaderboard covers the competition adapter.
