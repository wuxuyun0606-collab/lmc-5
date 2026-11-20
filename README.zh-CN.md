# LMC-5 · 五维活体记忆坐标

[English](README.md)

LMC-5 让智能体换个会话也能接着做事。它把对话和工具记录保留为原始证据，把值得沿用的决定和事实单独存下来。比如项目改了发布流程，下次问“现在怎么发布”，智能体可以找回现行做法，也能追溯它替代了哪条旧决定。

## 先在本机跑通一次

准备 Git 和带 SQLite FTS5 的 Python 3.9 或更高版本。在 macOS 或 Linux 终端执行下面的命令。本地版用 SQLite 文件保存记忆，无需 API key 或数据库服务。

```bash
git clone https://github.com/wuxuyun0606-collab/lmc-5.git
cd lmc-5
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .

lmc5 init --db demo.sqlite
lmc5 import-jsonl --db demo.sqlite examples/seed.jsonl
lmc5 recall --db demo.sqlite rollback
```

首次导入空库会看到 `"created": 2`。搜索结果里应有 **Confirm blast radius before production work**，这是样例里那条关于回滚的决定。关掉终端再打开，指向同一个数据库文件，仍能找回它。`--db` 的相对路径从当前目录算起，接入自己的智能体时建议用绝对路径。

已有源码、想离线试跑，可以直接执行 `PYTHONPATH=src python3 examples/demo.py`。这个示例使用临时库。要查看刚才写入的两条记忆，运行 `lmc5 stats --db demo.sqlite`。

## 五个维度各管什么

| 层 | 作用 |
|---|---|
| X · 时间线 | 把记忆归入工作线索，方便沿着一个项目回看经过。 |
| Y · 关系 | 记录哪些记忆属于同一事件、讨论同一问题，或由另一条推导而来。检索时可以沿符合条件的关系再走两跳。 |
| Z · 事实演化 | 区分现行事实与历史版本。同一事实键写入新的有效记录时，前一版会标为已取代。 |
| E · 体验 | 主智能体记录一段经历对今后回应的影响，并给出初始优先级。 |
| M · 代谢 | 检查记忆质量，找出值得复核、整理或归档的记录。 |

要保存一轮对话，用 `log-event`。要记住今后还会用到的决定，用 `add`。要查整理过的记忆，用 `recall`；连原始证据也要看，用 `surface`。[架构说明](docs/architecture.md) · [五层如何协作](docs/CONNECTING_XYZEM.md)

## 选哪种部署方式

| 你的目标 | 从哪里开始 |
|---|---|
| 给本地或单机智能体接记忆 | 安装后的 `lmc5` 命令和 Python 库使用 SQLite。[持久化和定时巡检](docs/DEPLOYMENT.md) |
| 接到已有的 PostgreSQL 应用 | 源码里的 pgvector 后端提供建表 SQL、召回组件和 hook。先验证关键词召回，再按需接向量和夜间任务。[PostgreSQL 部署步骤](extras/pgvector_backend/README.md) |
| 运行 AML 比赛的 Add/Search HTTP 接口 | 参赛适配器有独立的依赖、凭据配置和启动脚本。[主线中的 AML 适配器](extras/aml/README.md) · [参赛维护分支](https://github.com/wuxuyun0606-collab/lmc-5/tree/fix/aml-serving-recovery) |

## 按需阅读

- 接入自己的智能体。[Claude Code 接入示例](docs/claude_code.md) · [Hook 与召回流程](docs/HOOKS_AND_RECALL.md)
- 看能运行的代码。[Python 读写示例](examples/demo.py) · [两跳关系检索示例](examples/two_hop_graph.py)
- 把五层移植到已有项目。[实现顺序](docs/IMPLEMENTATION_ORDER.md) · [自动化边界](docs/AUTOMATION_BOUNDARIES.md) · [安全设计](docs/safety.md)
- 了解项目从哪里来。[克霖的原文](docs/KELIN.md) · [鸣谢与设计来源](docs/credits.md)

**状态与许可**　项目处于 Alpha 阶段。0.3.0 起采用 AGPL-3.0-or-later，v0.2.0 及更早的发布版继续适用 MIT 许可。另提供单独签署的商业许可。[许可说明](LICENSING.md) · [贡献指南](CONTRIBUTING.md)
