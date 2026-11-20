<h1 align="center">LMC-5</h1>
<p align="center"><strong>五维活体记忆坐标</strong><br>让智能体换个会话，也能找回上次的决定，知道哪条事实仍然有效。</p>
<p align="center"><a href="README.md">English</a> · <a href="docs/architecture.md">架构</a> · <a href="docs/DEPLOYMENT.md">部署</a> · <a href="docs/JEV.md">JEV 重排</a></p>

## 本地试跑

准备 Git 和带 SQLite FTS5 的 Python 3.9 或更高版本。在 macOS 或 Linux 终端执行下面的命令。安装完成后，默认的本地查询可以离线运行。

```sh
git clone https://github.com/wuxuyun0606-collab/lmc-5.git
cd lmc-5
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
```

导入两条示例决定，再找出其中和回滚有关的那一条。

```sh
lmc5 import-jsonl --db demo.sqlite examples/seed.jsonl
lmc5 recall --db demo.sqlite rollback
```

第一条命令会建库，首次导入会看到 `"created": 2`。搜索结果里应有 **Confirm blast radius before production work**。下次打开终端，指向同一个文件就能再查到它。

## 换个会话，记忆怎么接上

用 `log-event` 保留一轮对话，用 `add` 存下今后还要用的决定，用 `recall` 找回它。比如项目换了发布流程，把新流程写到同一个事实键下，下个会话就能查到现行做法，旧版也留着供追溯。

<details>
<summary><strong>展开看五层记忆各管什么</strong></summary>

| 层 | 作用 |
| --- | --- |
| X · 时间线 | 顺着一条工作线索，回看多次会话里的进展。 |
| Y · 关系 | 沿已有关系最多走两跳，找出与当前问题相连的记忆。 |
| Z · 事实演化 | 区分现行事实和已经被取代的版本。 |
| E · 体验 | 由主智能体记录一段经历怎样影响今后的回应，并给出初始优先级。 |
| M · 代谢 | 找出值得复核、整理或归档的记忆。 |

[架构说明](docs/architecture.md) · [五层如何协作](docs/CONNECTING_XYZEM.md) · [Python 示例](examples/demo.py)

</details>

## 部署与扩展

| 想做什么 | 从这里开始 |
| --- | --- |
| 多次会话共用一个本地记忆库 | [SQLite 持久化与定时巡检](docs/DEPLOYMENT.md) |
| 给已有智能体接 PostgreSQL | [pgvector 部署](extras/pgvector_backend/README.md) · [Claude Code 接入](docs/HOOKS_AND_RECALL.md) |
| 用 TypeSafe JEV 给召回结果重排 | [在 SQLite 或 PostgreSQL 中启用 JEV](docs/JEV.md) |
| 运行 AML 比赛用的 HTTP 服务 | [Add/Search 适配器](extras/aml/README.md) |

[克霖的原文](docs/KELIN.md) · [鸣谢](docs/credits.md) · [贡献指南](CONTRIBUTING.md) · [许可说明（v0.3.0 起采用 AGPL-3.0-or-later）](LICENSING.md)
