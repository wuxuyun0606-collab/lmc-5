# Recall Fusion A/B Replay — 2026-07-06

> Follow-up to `fix/recall-score-fusion`.
> Trace issue contributor: 乌桕.
> A/B replay runner: 乌桕家的 Clavis.

## Setup

- 726 real recall traces from a Chinese companionship deployment.
- Each recall was replayed through the full `RecallPipeline` three times:
  `fusion=raw`, `fusion=minmax`, and `fusion=rrf`.
- Query expansion reused stored `expanded_queries`; no new LLM calls were made.
- Embeddings were cached by query text.
- Run result: `726 recalls × 3 fusions`, `0 errors`.

## Top-5 Summary

| Metric | raw* | minmax | rrf |
|---|---:|---:|---:|
| pure graph hits in top5 | 0.2% | **26.9%** | 0.0% |
| pure emotion hits in top5 | 1.2% | 0.0% | 0.0% |
| cross-channel validation in top5 | 12.0% | 13.8% | **21.3%** |
| top5 containing vector hits | 98.7% | 70.2% | 80.9% |
| top1 occupied by pure graph | 0.1% | 0.0% | 0.0% |
| top1 occupied by pure emotion | 2.8% | 0.0% | 0.0% |
| top5 fully occupied by pure graph | 1 | 0 | 0 |

`raw*` in this replay already included a local production mitigation
(`graph score_scale=0.85`), so it is the mitigated production baseline, not
the original bug state.

## Findings

### Fixed channel coefficients are not enough

In a representative query, a correct vector hit had an original cosine score
below `0.765`, while an off-topic graph hit scored `0.9 × 0.85 = 0.765`.
The coefficient mitigation only protected high-scoring vector hits; lower-score
but semantically correct vector hits were still vulnerable. This is exactly the
case where recall needs protection most.

### Minmax has tail collapse

`minmax` fixes the top1 domination problem, but it can collapse the tail of a
high-confidence channel. Vector rank 4/5 can normalize close to zero even when
its original score is still useful, then lose to a neutral fixed-score graph
hit. In this replay, `minmax` raised pure-graph top5 composition to `26.9%`.

### RRF was the cleanest default

`rrf` eliminated pure graph/emotion domination in top1 and full-top5 cases,
while increasing top5 cross-channel validation from `12.0%` to `21.3%`.
It also kept vector participation at `80.9%`, leaving auxiliary channels room
to contribute without letting them take over.

## Decision

LMC-5 now defaults recall fusion to `rrf` with `rrf_k=60`.

`minmax` remains available for deployments that prefer within-channel score
normalization, but the docs should describe its top5 tail-collapse trade-off.

## Pending

The replay script (`tools/ab_fusion_replay.py` in the contributor fork) should
be imported once the contributor shares the source.
