# JEV reranking

TypeSafe JEV scores how well each retrieved memory answers the query. You can use it directly with SQLite or with the PostgreSQL prompt hook. The model and API are provided by TypeSafe. You supply the API key and pay for requests on your account.

**中文速览**　在仓库根目录安装下面的可选依赖，把自己的 TypeSafe 密钥通过环境变量 `TYPESAFE_API_KEY` 提供给进程。SQLite 按次加 `--reranker jev`，PostgreSQL 在 hook 环境里设置 `LMC5_RERANKER=jev`。每次最多向 TypeSafe 发送 16 个候选，逐条评分。同分保留原顺序，任一候选评分失败就整批退回原召回顺序。

## SQLite CLI

Start with the [quickstart](../README.md) and activate its virtual environment. From the checkout root, install the HTTP dependency and run a query with JEV enabled. Export `TYPESAFE_API_KEY` through your shell or secret manager before running the query.

```sh
python -m pip install '.[jev]'
lmc5 recall --db demo.sqlite --reranker jev --limit 5 rollback
```

If the sample query finds just one candidate, its order is already determined and no request is sent. In your own database, use a query that matches several memories to exercise the scorer. Omitting the flag uses local ranking.

## PostgreSQL prompt hook

Install the same extra in the virtual environment that runs the hook, and set the following alongside its existing database connection settings. Make `TYPESAFE_API_KEY` available to that process.

```sh
export LMC5_RERANKER=jev
```

An explicit JEV selection takes precedence over the existing Voyage, DeepSeek and OpenAI auto-selection. If the key or HTTP dependency is missing, the hook logs a warning and serves the original ranking. The SQLite CLI reports the configuration error when you explicitly request JEV. [PostgreSQL setup](../extras/pgvector_backend/README.md)

## What gets sent and returned

- The adapter sends the query and each candidate’s title and content to TypeSafe’s System One endpoint using model `jev-1.13.0`. It applies the project’s secret redaction and bounds the UTF-8 request excerpts. Ordinary memory text is visible to the provider.
- It scores the first 16 eligible candidates at most, one request per candidate. Scores range from 0 (irrelevant) to 3 (direct answer). Ties keep their retrieval order. For larger result sets, the unscored tail follows in its original order.
- A failed request, unexpected model or invalid score discards the partial ranking. The caller receives its original top results. The adapter makes no automatic retries. Candidate scores shown in ordinary recall output remain the original retrieval scores.

The [AML adapter](../extras/aml/README.md) uses the same model protocol with its own serving, scheduling and accounting rules.
