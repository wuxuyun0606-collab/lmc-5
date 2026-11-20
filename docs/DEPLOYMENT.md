# Deploying LMC-5

For one agent on one computer, start with SQLite. It needs a persistent file and a Python process whenever the agent reads or writes memory. Choose an always-on host if maintenance must run while your laptop sleeps. PostgreSQL is a separate integration path. The AML HTTP service has its own launcher. [SQLite quickstart](../README.md) · [PostgreSQL setup](../extras/pgvector_backend/README.md) · [AML](../extras/aml/README.md)

## Keep a SQLite store between sessions

本地部署只需固定数据库路径。让写入、查询和定时任务都指向同一个文件。下面在用户目录创建演示库，写入一条决定，再用另一次 CLI 调用读回来。

After installing the CLI, choose a data directory owned by the account that runs the agent. Use the same absolute database path from the agent, hooks and scheduled jobs.

```sh
mkdir -p "$HOME/.local/share/lmc5"
db="$HOME/.local/share/lmc5/example.sqlite"
lmc5 init --db "$db"
lmc5 add --db "$db" --thread releases --title "Release checklist" \
  --content "Run the rollback rehearsal before a release."
lmc5 recall --db "$db" rollback
lmc5 doctor --db "$db"
```

The result should include **Release checklist**, and `doctor` should show `"ok": true` for SQLite and FTS5. If it reports `no such module: fts5`, use a Python distribution linked to SQLite with FTS5 enabled. If recall is empty in a different session, compare the absolute paths and the memory counts reported by `lmc5 stats --db "$db"`.

## Capture and review a session

| Operation | Effect on the SQLite store |
|---|---|
| `log-event` | Appends a raw turn or tool observation. Pass a channel to group related events. |
| `consolidate --no-observations` | Groups events into chunks. The default without this flag also creates observation memories when eligible. |
| `hippocampus` | Previews proposed memories from existing chunks. `--apply` writes accepted candidates and eligible relations. |
| `z-audit` | Reports potential fact conflicts. `--apply` records pending audits for review. |
| `patrol` | Reports lifecycle and relation issues for review. |
| `surface` | Retrieves curated records and raw evidence for the next session. |

For example, run these against the same file as above. A small sample can legitimately produce zero promotion candidates.

```sh
lmc5 log-event --db "$db" --channel releases --role user --content "Which rollback check do we run?"
lmc5 consolidate --db "$db" --channel releases --no-observations
lmc5 hippocampus --db "$db" --channel releases
lmc5 surface --db "$db" rollback
```

Keep a consistent database backup before migrations or scheduled writes. SQLite’s online backup API or the SQLite shell’s `.backup` command includes committed data even when the database is in use. The JSONL export contains curated memories and serves a different purpose. [Recovery and snapshots](FORGE_AND_SWAP.md)

## Schedule a job

Start by running the exact command interactively as the account that will own the job. For example, on a Unix host with cron, the following entry runs the SQLite patrol at 04:00 in the scheduler’s timezone. Substitute the actual CLI and database paths, and create a writable log directory first.

```cron
0 4 * * * /opt/lmc5/.venv/bin/lmc5 patrol --db /var/lib/lmc5/agent.sqlite >> /var/log/lmc5/patrol.log 2>&1
```

For a PostgreSQL deployment, schedule **your application’s entry point** after it can process a known sample. The bundled `python -m extras.pgvector_backend.dream_runner --dry-run` shows the step names on an empty runner. Every step prints `skipped`. The same unconfigured runner also skips every step during a normal invocation.

Connect `consolidate` to your event loader and `hippocampus` to a `NightDream` job with the callbacks your deployment uses. For relation building it needs neighbor lookup and relation writers. Inspect the result of `run(dry_run=True)` for `would_run` on required steps, then perform a sample run, check for `ok` on those steps and verify the database effects. An all-skipped run can report overall `ok`. Cron or a systemd timer should launch the verified application with the same environment and interpreter.

夜间任务全是 `skipped` 表示还没有接线。用一组已知相关的样例检查“读入事件 → 生成候选 → 写入关系”，确认结果后再启用定时任务。关系核验 SQL 和自动化边界见下面两份说明。[Y relations](Y_RELATIONS.md) · [Automation boundaries](AUTOMATION_BOUNDARIES.md)
