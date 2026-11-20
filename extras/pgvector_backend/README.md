# PostgreSQL and pgvector

Use this backend when your agent already uses PostgreSQL or needs an approximate vector index. It ships in the source checkout as a set of Python modules. The installed `lmc5` command always opens SQLite.

下面三步可以在空的测试库里验证关键词召回，全程不用模型密钥。先跑通这一条，再接向量、hook 或夜间任务。以下命令从仓库根目录执行。

## 1. Prepare a database

You need a PostgreSQL server with the pgvector extension installed for **that server’s major version**, the `psql` client, and a role allowed to create the extension and tables in a dedicated database. The supplied schema uses stored generated columns and `halfvec` (pgvector 0.7+). Use a new database for the walkthrough.

With the virtual environment from the [quickstart](../../README.md) active, install the PostgreSQL driver. This example uses local PostgreSQL authentication and the current role. If your administrator already created a database for you, skip `createdb` and set the connection string to that database.

```bash
python -m pip install psycopg2-binary
createdb lmc5_demo
export LMC5_PG_DSN='dbname=lmc5_demo'
```

For a remote server, use a connection service or host/database settings and a PostgreSQL password file for authentication. [.env.example](.env.example) lists optional settings. Export settings into the process that starts the hooks. The modules do not automatically load a file named `.env`.

## 2. Apply the schema

Confirm the database and role printed by the first command before running the second. On an existing installation, review schema changes and take a backup first. Adding a stored search column to a populated table can rewrite it.

```bash
psql -X --dbname="$LMC5_PG_DSN" -c 'SELECT current_database(), current_user'
psql -X --dbname="$LMC5_PG_DSN" -v ON_ERROR_STOP=1 --single-transaction -f extras/pgvector_backend/schema.sql
psql -X --dbname="$LMC5_PG_DSN" -c "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
```

`extension "vector" is not available` means the server needs pgvector installed. An unknown `halfvec` type usually means the extension in this database needs upgrading. The default vector column is `halfvec(3072)`.

## 3. Recall one record

The following check uses the same keyword adapter as the prompt hook. It inserts a sample in a transaction, searches for it, and rolls the transaction back when the connection closes. Run from the checkout root so Python can import `extras`.

```bash
python - <<'PY'
import os
from contextlib import closing

import psycopg2
from extras.pgvector_backend.recall_pipeline import fts_search_adapter

with closing(psycopg2.connect(os.environ["LMC5_PG_DSN"])) as conn:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO lmc5_curated_memories (source, category, title, content)
            VALUES ('demo', 'note', 'Release checklist', 'Run the rollback rehearsal.')
            RETURNING id
        """)
        sample_id = cur.fetchone()[0]
    hits = fts_search_adapter(conn)("rollback", 10)
    assert any(hit.source_id == sample_id for hit in hits), "Sample missing from recall"
    print("PostgreSQL keyword recall OK")
PY
```

Seeing **PostgreSQL keyword recall OK** confirms the schema, driver and curated text search work together. For a keyword-only hook, use this driver-only virtual environment. If `sentence-transformers` is already installed, the hook tries to load BGE-M3 when no remote embedder is configured. The first load may download model files.

The shipped analyzer uses PostgreSQL’s `simple` tokenization. Applications that need Chinese word segmentation can configure a matching text index and query analyzer.

## Add the components your agent needs

| Component | What to connect | How to check it |
|---|---|---|
| Semantic recall | Supply an embedder to `PgvectorStore`, or pass vectors directly. Match the store’s model name and dimension to the indexed vectors and the SQL column. The default hook constructs the store with its defaults. A different model or dimension needs matching hook configuration in code. | Write a vector for a curated row with `owner_type="curated"`. Query it with the same model and verify the returned owner ID. [Vector backend](vector_pgvector.py) · [Embedder adapters](embedders.py) |
| Session hooks | Run modules from the checkout with the same interpreter and exported DSN. The prompt hook wires curated/raw text search, graph expansion and emotion resonance. Semantic search and reranking depend on the installed adapters and available keys. Remote HTTP adapters use `requests`. | Send a sample prompt and inspect the returned text. For archival, verify a sample transcript produced a row in `lmc5_raw_events`. [Hook code](hooks/) · [Recall pipeline](../../docs/HOOKS_AND_RECALL.md) |
| Nightly consolidation | Supply the chunk reader, candidate writer and relation callbacks to your application’s `NightDream` job. Pass that job to `DreamRunner`. Review candidate output before enabling writes. | Check each configured step in `result.to_dict()` and the edges written for a known sample. [Scheduling](../../docs/DEPLOYMENT.md) · [Y relations](../../docs/Y_RELATIONS.md) |

Installing the schema gives you empty tables. The host application supplies the memories, embeds the rows it wants to search, and schedules any background work. The two [migration scripts](migrations/) target specific earlier deployments. Read their preconditions before applying one to existing data.

[Automation boundaries](../../docs/AUTOMATION_BOUNDARIES.md) · [Vector choices](../../docs/VECTOR_BACKENDS.md) · [License](../../LICENSING.md)
