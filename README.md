# Living Memory Coordinate-5

**五维活体记忆坐标，简称 LMC-5。**

[English](README.md) | [简体中文](README.zh-CN.md)

**Recoverable continuity, not infinite context.**

![A small AI robot walking home from an open-source workshop with a glowing bag of tokens and a compute lunchbox.](docs/assets/little-ai-earns-tokens.png)

Every model context window has a ceiling. Maybe it is 100k tokens. Maybe it is
1M. Maybe one day it is much larger. It is still not infinite, and the longer it
gets, the more expensive, noisy, and fragile it becomes.

Humans do not carry every sentence they have ever heard in active attention.
We keep important things. We revise old facts. We connect similar experiences.
Repeated corrections change future behavior. Pressure, risk, and unfinished
conflict become part of our working posture.

LMC-5 is a small, offline-first memory architecture for **LLM agents** built
around that idea: do not chase a magical infinite prompt. Build a memory system
that can recover continuity when it matters.

It is meant for GPT/Codex-style coding agents, personal assistant agents,
Claude-style local workflows, and other long-running LLM tools that need memory
without hard-binding themselves to one model provider.

## The Model

**Living Memory Coordinate-5**, or **LMC-5**, treats memory as five cooperating
layers instead of a bag of retrieved snippets:

| Axis | Name | What It Answers |
|---|---|---|
| **X** | Timeline | Where does this memory belong in the agent's work history? |
| **Y** | Relations | What other memories does it support, contradict, or explain? |
| **Z** | Fact Evolution | Is this fact current, historical, superseded, or under review? |
| **E** | Experience Signals | What risk, urgency, tension, and response posture came with it? |
| **M** | Metabolism | Should it be promoted, demoted, reviewed, archived, or distilled? |

The reference implementation adds a raw event journal beneath those coordinates:

```text
raw events  -> searchable black box
curated memories -> durable LMC-5 coordinates
surface()   -> redacted context from both layers
```

That split is important. Raw logs preserve what happened. Curated memories
decide what should influence future behavior. Mixing them together is how an
agent starts treating yesterday's tool error like a constitutional amendment.
Tiny architecture crime. Large downstream mess.

## Features

This repository provides a compact Python reference implementation with:

- **SQLite storage** for curated memories, relations, and raw events.
- **FTS5 recall with LIKE fallback** for offline keyword search.
- **SQLite vector index** for portable cosine-similarity search.
- **One-hop relation expansion** so connected memories surface together.
- **Raw event journal** for black-box session capture.
- **Mixed surfacing** across curated memories and raw events.
- **Fact-key supersession** so old facts can be preserved without staying current.
- **Experience signals** for risk, urgency, tension, and response posture.
- **Read-only metabolism patrols** for duplicate facts, review backlog, and thread-split candidates.
- **Redaction helpers** for recall output and embedding input.
- **JSONL import/export** for simple portability.
- **CLI and Python API** with no network calls in the core.
- **`doctor` checks** for local SQLite/FTS capability.

## Who Is It For?

LMC-5 is for builders who want a small memory layer for long-running LLM agents:

- GPT or Codex-style coding agents that need to recover project context.
- Local assistant workflows that need raw event logs plus curated memory.
- Multi-model agent setups that should not lock memory to one provider.
- Research prototypes comparing plain RAG, vector recall, and structured memory.
- Developers who need redaction and fact evolution before injecting memory into prompts.

The core is provider-free. You can use it with OpenAI models, Gemini, Voyage
embeddings, Claude-style local tools, or a fully local stack. LMC-5 stores and
surfaces memory; your agent decides how to use that context.

## Quickstart

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .

lmc5 init --db demo.sqlite
lmc5 add --db demo.sqlite \
  --title "Respect production safety boundaries" \
  --content "Before touching production data, confirm blast radius and rollback." \
  --thread "safety" \
  --category "policy" \
  --fact-key "agent.safety.production_change" \
  --risk high \
  --urgency high \
  --tag safety --tag production

lmc5 recall --db demo.sqlite production
lmc5 log-event --db demo.sqlite \
  --role user \
  --channel demo \
  --content "Can you recover the production rollback notes from earlier?"
lmc5 surface --db demo.sqlite "production rollback"
lmc5 patrol --db demo.sqlite
lmc5 doctor --db demo.sqlite
```

Run the Python demo:

```bash
PYTHONPATH=src python examples/demo.py
```

Example output:

```text
4.50 #1 Production safety boundary (fts)
2.15 #2 Post-change verification (related:1)
surface: 2 memories, 1 events
```

## Python API

```python
from lmc5 import MemoryStore

with MemoryStore("agent.sqlite") as store:
    store.init()
    policy, _ = store.add_memory(
        title="Production safety boundary",
        content="Confirm blast radius, rollback, and verification before production changes.",
        thread="safety",
        fact_key="agent.safety.production_change",
        risk_level="high",
        urgency="high",
    )
    checklist, _ = store.add_memory(
        title="Verification checklist",
        content="Verify logs, metrics, and user-facing behavior after deployment.",
        thread="engineering",
    )
    store.add_relation(policy.id, checklist.id, "supports")

    hits = store.recall("production", limit=3)
```

## Embedding Layer / 嵌入层

### English

LMC-5 works offline today with SQLite FTS5, relation expansion, and explicit
scoring. It also includes a lightweight SQLite vector index for embeddings. This
is a portable reference store, not a production ANN database. For large
deployments, you can replace it with pgvector, LanceDB, FAISS, Milvus, or
another vector backend while keeping the same LMC-5 metadata rules.

Recommended implementation:

- Keep lexical recall as the baseline: FTS5/BM25 must still work when an
  embedding provider is unavailable.
- Store vectors in a separate derived index keyed by `memory_id` or `event_id`.
- Record `provider`, `model`, `dimension`, `input_type`, and `content_hash` for
  every vector.
- Do not mix model families or dimensions inside one vector index. Rebuild the
  index when switching providers or dimensions.
- Embed curated memories and raw events separately; raw events are evidence,
  curated memories are behavioral memory.
- Use `input_type=query` for user queries and `input_type=document` for stored
  memories/events when the provider supports it.
- Fuse retrieval channels after search: lexical score + vector score + relation
  expansion + LMC-5 priority score.
- Redact before sending content to any remote embedding API.

Offline demo:

```bash
lmc5 add --db demo.sqlite \
  --title "Deployment rollback" \
  --content "Confirm rollback before deployment."

lmc5 vector-upsert --db demo.sqlite \
  --owner-type memory \
  --owner-id 1 \
  --toy-text "deployment rollback"

lmc5 vector-search --db demo.sqlite \
  --toy-text "deployment rollback" \
  --owner-type memory
```

`--toy-text` uses a deterministic local hash embedding for demos and tests. It
is not semantic search. Real retrieval should use a provider embedding and store
the returned vector with `vector-upsert --vector '[...]' --provider ... --model ...`.

Recommended providers:

- **Gemini Embedding 2** for multimodal or Google-stack deployments. For current
  text embedding APIs, Google documents `gemini-embedding-001` with flexible
  dimensions up to 3072; use `gemini-embedding-2` when that model ID is exposed
  in your target API account.
- **Voyage AI** if by `vogeya` you mean Voyage. Use `voyage-4-large` for best
  general multilingual retrieval quality, `voyage-4` as a balanced default,
  `voyage-4-lite` for lower latency/cost, and `voyage-code-3` for code-heavy
  memory.

The rule is simple: embeddings help find the right material, but they do not
decide whether a fact is current. That job belongs to Z.

### 中文

LMC-5 当前离线核心依赖 SQLite FTS5、关系扩展和显式评分。同时项目里已经有
一个轻量 SQLite 向量索引，可以存向量、做余弦相似度检索、关联 memory/event。
它是便携 reference store，不是生产级 ANN 数据库。大规模部署时可以替换成
pgvector、LanceDB、FAISS、Milvus 或其他向量后端，但 LMC-5 的元数据规则不变。

推荐实现方式：

- 保留关键词检索作为底线：embedding provider 不可用时，FTS5/BM25 仍然
  必须能工作。
- 向量单独放在派生索引里，用 `memory_id` 或 `event_id` 关联原始记录。
- 每条向量记录 `provider`、`model`、`dimension`、`input_type` 和
  `content_hash`。
- 同一个向量索引里不要混用不同模型族或不同维度；换 provider 或维度时
  重建索引。
- 精选记忆和原始事件分开 embed：raw events 是证据，curated memories 才
  是会影响行为的记忆。
- provider 支持时，用户问题用 `input_type=query`，已存记忆/事件用
  `input_type=document`。
- 搜索后再融合：关键词分数 + 向量分数 + 关系扩展 + LMC-5 priority score。
- 发送到远程 embedding API 前必须先脱敏。

离线 demo：

```bash
lmc5 add --db demo.sqlite \
  --title "Deployment rollback" \
  --content "Confirm rollback before deployment."

lmc5 vector-upsert --db demo.sqlite \
  --owner-type memory \
  --owner-id 1 \
  --toy-text "deployment rollback"

lmc5 vector-search --db demo.sqlite \
  --toy-text "deployment rollback" \
  --owner-type memory
```

`--toy-text` 用的是确定性的本地 hash embedding，只用于 demo 和测试，不是语义检索。
真实检索应该用 provider 生成的向量，再通过
`vector-upsert --vector '[...]' --provider ... --model ...` 写入。

推荐 provider：

- **Gemini Embedding 2**：适合多模态、Google 生态或需要统一文本/图像/音视频
  表征的场景。当前 Google 文本 embedding 文档里的稳定 API model code 是
  `gemini-embedding-001`，支持最高 3072 维；如果你的 API 账号已经暴露
  `gemini-embedding-2` model ID，就优先用它。
- **Voyage AI**：如果你说的 `vogeya` 是 Voyage，那推荐它做高质量文本/代码
  检索。`voyage-4-large` 适合质量优先的通用多语言检索，`voyage-4` 适合均衡
  默认，`voyage-4-lite` 适合低延迟/低成本，`voyage-code-3` 适合代码记忆。

一句话：embedding 负责“找得到”，Z 轴负责“还算不算当前事实”。别让向量相似度
替事实判断背锅，它没那个脑子，别给它升职。

## Design Goal

LMC-5 is not a chatbot persona system, and it is not a vector database wearing a
lab coat. It is a memory coordination layer for agents that need durable
collaboration, verifiable facts, low-noise recall, and explicit safety
boundaries.

The reference implementation favors boring operational properties:

- No network calls.
- No hidden model provider.
- No credentials in examples.
- No automatic deletion.
- No automatic mutation from patrol checks.
- No secret leakage from recall output.

## Repository Layout

```text
.github/workflows/
  ci.yml          # test matrix
src/lmc5/
  cli.py          # command-line interface
  models.py       # dataclasses and enums
  redact.py       # output and embedding-input redaction
  scoring.py      # explainable priority scoring
  store.py        # SQLite persistence
  metabolism.py   # read-only lifecycle suggestions
docs/
  architecture.md
  credits.md
  safety.md
examples/
  seed.jsonl
  demo.py
tests/
```

## What LMC-5 Adds Over Plain RAG

Plain RAG usually asks, "Which text chunks are similar?"

LMC-5 asks the questions an agent actually needs before acting:

- Is this fact still current?
- Does this memory conflict with another memory?
- Is it part of a stable work thread?
- Is it high risk even if it is old?
- Should it be recalled, reviewed, distilled, or archived?
- What response posture should it influence?

Similarity is useful. It is not enough. A memory system that cannot tell
"historically true" from "currently true" is not remembering. It is hoarding.

## Event Journal

LMC-5 separates two layers:

- Curated memories: compact records with X/Y/Z/E/M coordinates.
- Raw events: append-only session material used as a recoverable black box.

Use `log-event` for raw turns, tool observations, or environment notes. Use
`add` for curated memories that should influence future behavior directly.
Use `surface` when an agent needs both polished memory and supporting raw
context.

This layer is inspired by public ideas from Qizhan7's `imprint-memory`, but the
implementation here is original and intentionally uses different names and
boundaries. See `docs/credits.md`.

## Why This Exists

The goal is not to make an AI pretend it has a human biography. The goal is to
make long-running agents safer and more coherent:

- They should remember project decisions without re-reading the whole project.
- They should preserve old facts without obeying outdated ones.
- They should surface related risks before touching production, accounts, keys, or money.
- They should learn from repeated corrections instead of apologizing beautifully and changing nothing.
- They should recover the thread after compaction, restart, or tool switching.

That is recoverable continuity. Not magic. Not mysticism. Just fewer goldfish
moments with a schema.

## Status

Alpha. The API is intentionally small and may change. The current goal is to
make the coordinate model testable and easy to adapt, not to be a full memory
platform.

## Roadmap

- Add optional embedding adapters without making network calls part of the core.
- Add graph explanations for relation-expanded recall.
- Add migration helpers for existing Markdown/JSONL memory logs.
- Add benchmark fixtures for long-running coding-agent tasks.
- Add optional model-assisted extraction for fact keys and relation candidates.
