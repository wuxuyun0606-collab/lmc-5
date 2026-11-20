![LMC-5 · 五维活体记忆坐标](docs/assets/memory-coordinates.svg)

**给智能体的跨会话记忆库。** 把发布决定存进 SQLite，下次打开会话就能查回来。流程变了，用同一个事实键写入新版，旧决定依然可以追溯。

[English](README.md)　·　[五层架构](docs/architecture.md)　·　[部署指南](docs/DEPLOYMENT.md)　·　[JEV 重排](docs/JEV.md)

## 在本机找回第一条记忆

准备 Git 和带 SQLite FTS5 的 Python 3.9 或更高版本。在 macOS 或 Linux 的终端执行：

```sh
git clone https://github.com/wuxuyun0606-collab/lmc-5.git
cd lmc-5
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .

lmc5 import-jsonl --db demo.sqlite examples/seed.jsonl
lmc5 recall --db demo.sqlite rollback
```

**你会看到什么？** 首次导入显示 `"created": 2`。搜索结果中的 **Confirm blast radius before production work** 就是样例里那条回滚决定。以后指向同一个数据库文件，就能再次查到它。

<details>
<summary>这条记忆在五层坐标里怎么用？</summary>

- **X · 时间线** 将决定归入一条工作线，方便回看经过。
- **Y · 关系** 把同一事件里的决定和后续验证连起来，召回时最多可以走两跳。
- **Z · 事实演化** 在同一事实键下保留历史版本，指出哪条仍然有效。
- **E · 体验** 由主智能体记录这次经历怎样影响以后的回应，并给出初始优先级。
- **M · 代谢** 检查积累的记忆，标出值得复核、整理或归档的记录。

[五层如何协作](docs/CONNECTING_XYZEM.md) · [可运行的 Python 示例](examples/demo.py)

</details>

## 接入你的项目

**本地智能体**　让写入、查询和巡检都使用同一个 SQLite 文件。[持久化部署](docs/DEPLOYMENT.md)

**PostgreSQL 与 Claude Code**　先用一条样例验证全文检索，再接入会话归档和召回。[pgvector 部署](extras/pgvector_backend/README.md) · [Hooks 接入](docs/HOOKS_AND_RECALL.md)

**TypeSafe JEV**　给已召回的候选按相关性重新排序。SQLite 和 PostgreSQL 都支持显式启用。[配置 JEV](docs/JEV.md)

<details>
<summary>运行 AML 比赛用的 Add/Search HTTP 服务</summary>

比赛适配器的依赖、凭据配置和启动方式见 [AML 部署说明](extras/aml/README.md)。

</details>

---

[克霖的原文](docs/KELIN.md) · [鸣谢](docs/credits.md) · [参与贡献](CONTRIBUTING.md) · [开源与商业许可](LICENSING.md)
