"""Compare three evidence retrievers on a pinned, paired LoCoMo pilot."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3

import numpy as np
from model2vec import StaticModel


# Conversation ID -> (state at the question's reference time, historical event).
# Zero-based indexes into the upstream qa array, chosen before ranking.
PAIRS = {
    "conv-30": (65, 0),
    "conv-41": (149, 4),
    "conv-42": (183, 3),
    "conv-44": (27, 1),
    "conv-48": (133, 0),
    "conv-50": (92, 0),
}
DATA_SHA = "79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4"
MODEL_FILES = {
    "config.json": "2a6ac0e9aaa356a68a5688070db78fc3a464fefe85d2f06a1905ce3718687553",
    "model.safetensors": "f65d0f325faadc1e121c319e2faa41170d3fa07d8c89abd48ca5358d9a223de2",
    "tokenizer.json": "e67e803f624fb4d67dea1c730d06e1067e1b14d830e2c2202569e3ef0f70bb50",
    "modules.json": "a68dcbed0429dcdd5bfdca92b0b03cc30d09122c0a3fcf4758787d4b244e45b2",
}
LIMIT = 5


def verify(path, digest):
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError(f"Unexpected SHA-256 for {path.name}")


def transcript(conversation):
    sessions = sorted(
        int(key.split("_")[1]) for key in conversation if re.fullmatch(r"session_\d+", key)
    )
    records = {}
    for session in sessions:
        date = conversation[f"session_{session}_date_time"]
        for turn in conversation[f"session_{session}"]:
            key = turn["dia_id"]
            if key in records:
                raise ValueError(f"Duplicate turn {key}")
            records[key] = f"{date} {turn['speaker']} {turn['text']}"
    return records


def compare(sample, model):
    records = transcript(sample["conversation"])
    ids = list(records)
    texts = list(records.values())
    vectors = model.encode(texts, normalize=True)
    rows = []
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE turns USING fts5(body)")
        conn.executemany("INSERT INTO turns(body) VALUES (?)", ((t,) for t in texts))
        for label, index in zip(("current", "historical"), PAIRS[sample["sample_id"]]):
            qa = sample["qa"][index]
            gold = set(qa["evidence"])
            if not gold or not gold.issubset(records):
                raise ValueError(f"Missing evidence for {sample['sample_id']}:{index}")
            question = qa["question"]
            terms = dict.fromkeys(re.findall(r"[^\W_]+", question.lower()))
            expr = " OR ".join(f'"{term}"' for term in terms)
            ranked = conn.execute(
                "SELECT rowid FROM turns WHERE turns MATCH ? ORDER BY rank, rowid", (expr,)
            )
            lexical = [i - 1 for (i,) in ranked]
            query_vec = model.encode([question], normalize=True)[0]
            dense = np.argsort(-(vectors @ query_vec), kind="stable").tolist()
            scores = [0.0] * len(ids)
            for channel in (lexical, dense):
                for rank, i in enumerate(channel, start=1):
                    scores[i] += 1 / (60 + rank)
            hybrid = sorted(range(len(ids)), key=scores.__getitem__, reverse=True)
            for name, ranking in (("lexical", lexical), ("vector", dense), ("hybrid", hybrid)):
                hits = [ids[i] for i in ranking[:LIMIT]]
                rows.append({
                    "sample": sample["sample_id"],
                    "index": index,
                    "group": label,
                    "method": name,
                    "top5": hits,
                    "gold": sorted(gold),
                    "found": len(gold.intersection(hits)),
                })
    finally:
        conn.close()
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("model", type=Path)
    parser.add_argument("--details", action="store_true", help="print turn IDs for every ranking")
    args = parser.parse_args()
    verify(args.dataset, DATA_SHA)
    for file, digest in MODEL_FILES.items():
        verify(args.model / file, digest)
    model = StaticModel.from_pretrained(args.model)
    samples = {s["sample_id"]: s for s in json.loads(args.dataset.read_text(encoding="utf-8"))}
    rows = [row for sid in PAIRS for row in compare(samples[sid], model)]
    if args.details:
        print(json.dumps(rows, indent=2))
        return
    print("group method queries_with_a_hit / queries   evidence_found / annotated")
    for group in ("current", "historical"):
        for method in ("lexical", "vector", "hybrid"):
            selected = [r for r in rows if r["group"] == group and r["method"] == method]
            hit = sum(r["found"] > 0 for r in selected)
            found = sum(r["found"] for r in selected)
            total = sum(len(r["gold"]) for r in selected)
            print(f"{group} {method} {hit}/{len(selected)}   {found}/{total}")


if __name__ == "__main__":
    main()
