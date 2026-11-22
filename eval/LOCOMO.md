# LoCoMo evidence retrieval at five turns

These results come from the standalone BM25, vector and RRF baselines in `locomo.py`, which runs independently of LMC-5. The AML serving edition in `extras/aml/` has no cross-session timeline (X) or stored relation edges (Y) and is outside this evaluation.

On twelve questions from six LoCoMo conversations, lexical retrieval and hybrid retrieval found annotated evidence for **2/6 current-state questions and 4/6 historical-event questions**. The vector retriever found evidence for **1/6 and 3/6** respectively. This small paired sample measures evidence retrieval over transcripts. It does not evaluate answer generation, five-axis consolidation or leaderboard performance.

| Question group | Retrieval method | Queries with any evidence in top 5 | Annotated turns found in top 5 |
| --- | --- | ---: | ---: |
| Current state | FTS5 BM25 | 2/6 | 2/9 |
| Current state | Potion vectors | 1/6 | 1/9 |
| Current state | Reciprocal rank fusion | 2/6 | 2/9 |
| Historical event | FTS5 BM25 | 4/6 | 4/6 |
| Historical event | Potion vectors | 3/6 | 3/6 |
| Historical event | Reciprocal rank fusion | 4/6 | 4/6 |

## Sample and scoring

We selected the pairs by reading the questions before running the retrievers. Each pair comes from the same conversation. “Current” asks about a state at the time the question refers to, sometimes an explicit month or date. “Historical” asks about an earlier event or activity. These labels describe the question intent. The corpus is the complete conversation for each pair, including later sessions. There is no simulated online cutoff or inference about which statement is still true at the end.

| Conversation | Current question index | Historical question index |
| --- | ---: | ---: |
| conv-30 | 65 | 0 |
| conv-41 | 149 | 4 |
| conv-42 | 183 | 3 |
| conv-44 | 27 | 1 |
| conv-48 | 133 | 0 |
| conv-50 | 92 | 0 |

Indices are zero-based in the pinned upstream `qa` arrays. Each query searches all turns in its own conversation. Each turn contains the session date, speaker and text. Images, generated summaries, answers and gold IDs are excluded from indexing. The scorer reads the original `evidence` turn IDs. A multi-evidence question can count as a query hit even if some evidence is missing, hence the separate evidence denominator.

All methods return up to five turns. FTS5 uses BM25 over an OR of the unique question tokens. The dense method ranks normalized Potion vectors by cosine similarity. Hybrid sums reciprocal ranks from the two full candidate lists with equal weights and `k=60`. Ties follow transcript order. These are retrieval baselines. The same corpus, query and return budget are used throughout. The sample is too small to rank methods generally.

## Reproduce

Use Python 3.11 with SQLite FTS5. From the repository root, create a separate environment. Fetch the upstream data and model once. The runner verifies file hashes and operates offline.

```sh
work=$(mktemp -d)
python3.11 -m venv "$work/venv"
"$work/venv/bin/python" -m pip install model2vec==0.10.0 numpy==2.4.6 tokenizers==0.23.3
data_rev=3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376
model_rev=bf8b056651a2c21b8d2565580b8569da283cab23
curl -fLsS "https://raw.githubusercontent.com/snap-research/locomo/$data_rev/data/locomo10.json" -o "$work/locomo10.json"
mkdir "$work/model"
for file in config.json tokenizer.json model.safetensors modules.json; do
  curl -fLsS "https://huggingface.co/minishlab/potion-base-8M/resolve/$model_rev/$file" -o "$work/model/$file"
done
HF_HUB_OFFLINE=1 "$work/venv/bin/python" eval/locomo.py "$work/locomo10.json" "$work/model"
```

Add `--details` to print the retrieved turn IDs and annotated evidence for each question and method. The data is distributed upstream under CC BY-NC 4.0. The model is distributed under MIT. This repository includes the question indices and retrieval code. [LoCoMo, Maharana et al., 2024](https://arxiv.org/abs/2402.17753) · [Dataset and license](https://github.com/snap-research/locomo/tree/3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376) · [Potion model revision](https://huggingface.co/minishlab/potion-base-8M/tree/bf8b056651a2c21b8d2565580b8569da283cab23)
