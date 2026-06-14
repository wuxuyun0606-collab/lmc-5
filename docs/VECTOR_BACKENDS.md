# Vector Backends and Embedding Choices

> LMC-5 ships with one default vector backend and a documented upgrade
> path. Pick the one that matches your corpus size and ops appetite.

## Two Backends

### 1. SQLite + JSON cosine — *the default*

Shipped in `src/lmc5/vector.py`. Stores embeddings as JSON text columns,
computes cosine similarity in Python on read.

Use when:

- You have **fewer than ~5,000 memory vectors**
- You want zero external dependencies — `sqlite3` is in the stdlib
- You are running fully offline (no network at all)
- You are prototyping, demoing, or building a personal-scale agent

Trade-offs:

- Full-table scan per query. Query latency grows linearly with corpus
  size; ~10K rows is where you start feeling it on commodity hardware
- No ANN approximation — every query returns exact cosine top-K
- No index maintenance, no migrations, no daemons

### 2. PostgreSQL + pgvector + halfvec — *production*

Shipped in `extras/pgvector_backend/vector_pgvector.py`. Uses pgvector's
`halfvec` type (16-bit floats, half the storage of `vector`) and
`ivfflat` index for approximate nearest-neighbor search.

Use when:

- You expect **50,000+ vectors** in a single corpus
- You already run PostgreSQL for something else
- You need concurrent reads from multiple processes
- You are deploying a long-running agent that grows memory monotonically

Trade-offs:

- Real database to operate (backup, upgrade, vacuum)
- Approximate ANN — top-K is "good enough" not exact
- `ivfflat` with `lists=100` is a starting point; tune for your corpus
- Embedding service becomes a separate dependency

### What about the 5K–50K range?

Either backend works. SQLite is simpler; pgvector is faster. Pick by
how much you care about query latency. The hop from SQLite to pgvector
is small in code (`vector_pgvector.PgvectorStore` matches the same
`search / write / delete` shape), so deferring the decision is cheap.

## Embedding Models

LMC-5 does not bundle an embedding model. You pick one and inject a
`text -> list[float]` callable into whichever backend you use.

The reference deployment has shipped on these models. Comments are
opinions from operating them, not benchmarks.

### Gemini Embedding (recommended)

- **`gemini-embedding-001`** / **`gemini-embedding-002`** (often called
  *gemini-embedding-2*)
- Output dim: 3072 (Matryoshka — truncatable to 1536 / 768 / 256 with
  graceful quality drop)
- Multilingual; particularly strong on mixed Chinese + English
- L2-normalized output makes cosine equivalent to dot product
- Cheap per token

Recommended when your memory is bilingual or multilingual and you want
the highest dimensionality you can afford to index. `halfvec(3072)` in
pgvector is the matching storage type.

### Voyage AI 1024-dim line

- **`voyage-3`** / **`voyage-3-large`** / **`voyage-multilingual-2`**
- Output dim: 1024
- Strong retrieval precision for English and code; multilingual variant
  is solid for non-English content
- Cheap enough for bulk reindexing

Recommended when you want a smaller dimension (faster index, less
storage) without sacrificing retrieval quality. `halfvec(1024)` is the
matching storage type.

### Other options worth knowing about

- **OpenAI `text-embedding-3-large`** (3072d): well-known, decent
  multilingual; mostly a fallback if you are already paying OpenAI
- **BGE-M3** (1024d, local): a strong open-weights multilingual model
  you can run offline via sentence-transformers — pair with SQLite for
  a fully offline stack
- **Sentence-transformers MiniLM / mpnet** (384d / 768d, local): smaller,
  faster, weaker than the above. Fine for demos; underwhelming once
  your corpus crosses a few thousand entries

## Picking a Combination

| Stack | Backend | Embedder | Good fit |
|-------|---------|----------|----------|
| **Offline / personal** | SQLite | BGE-M3 (local) | Hobby agent, no network, no API budget |
| **Small hosted** | SQLite | Voyage 1024d or Gemini truncated to 1024d | Solo developer, low-volume long-running tool |
| **Production multilingual** | pgvector + halfvec(3072) | Gemini embedding 2 (3072d) | Long-running multilingual agent, larger corpus |
| **Production English/code** | pgvector + halfvec(1024) | Voyage-3-large | Coding agent at scale, English-dominant |

Mixing matters: a 1024-dim embedder requires `halfvec(1024)` columns
and matching index parameters. Do not silently truncate vectors — the
quality drop is sneaky and you will blame the wrong layer.

## Switching Embedders

A live corpus and a new embedder are not friendly. If you change
embedders mid-deployment:

1. Add a second `(model_name, dimension)` column tuple — the reference
   schema already keys on `(owner_type, owner_id, model_name, dimension)`
   so two embedding versions can coexist
2. Backfill the new model in the background
3. Switch the read path to the new model when coverage is high enough
4. Garbage-collect the old vectors once you trust the cutover

The default cost of doing this in a hurry is **silently worse recall**
for a few weeks. Plan the migration.

## What LMC-5 Does Not Try To Be

LMC-5 is not trying to replace a real vector database. If your corpus
is millions of vectors with multiple tenants and high-QPS search, use
Milvus, Qdrant, Weaviate, or a managed vector DB and treat LMC-5 as
the **memory schema** sitting on top.

LMC-5's contribution is the **shape of memory** — the X/Y/Z/E/M model
plus the audit and lifecycle policies. Vector storage is just one piece
of that, and it is the piece most easily swapped without affecting the
rest of the architecture.
