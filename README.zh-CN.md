# LMC-5

![LMC-5 · 五维活体记忆坐标](https://raw.githubusercontent.com/wuxuyun0606-collab/lmc-5/a16b513a1f268d886bf9632bb11fc4363892804e/docs/assets/memory-coordinates.svg)

[English](README.md)　·　[快速开始](#在本机找回第一条记忆)　·　[中文导览与安装自检](docs/LMC-5可分享导览与安装自检_20260702.md)　·　[文档目录](docs/README.md)　·　[五层架构](docs/architecture.md)　·　[部署指南](docs/DEPLOYMENT.md)　·　[JEV 重排](docs/JEV.md)

你跟一个智能体聊了很久。换个窗口，它不认识你了。

LMC-5 管的就是这件事。它给每条记忆标五个坐标，合起来回答三个问题：这条记忆在哪里，和谁有关，该怎样被记住。

- **X 时间线**　这件事属于哪一段经历。
- **Y 关系**　它和哪些记忆连着。召回时顺着线最多走两跳。
- **Z 事实演化**　哪条是现在，哪条已经成为过去。旧的不删，和新的连起来。
- **E 体验**　这段经历以后该怎样影响回应。由智能体自己写，别的模型不能代笔。
- **M 代谢**　哪些该记牢，哪些可以放淡。只提建议，不自动删。

举个例子。有人从前喝美式，后来改喝拿铁。LMC-5 记下的是从美式变成拿铁这件事：问“现在喝什么”，答拿铁；问“以前呢”，也答得上。

五个坐标接在同一条流水上。白天写入，给记忆定坐标。夜里整理，找关系、标新旧、调分量。下次对话，按这些坐标把该想起的想起来。夜里连线有一条规矩：宁可少连，不能连错。

旧事实不能冒充现在。体验不能代替事实。一条记忆去还是留，不只看新旧。

五条线由 BlueBlue 设计，开源版由顾琛实现。克霖的原文是住在里面的那个智能体自己写的。

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
<summary>更新一条事实后，旧版本在哪里？</summary>

用同一个事实键更新发布负责人。第二条生效后，第一条仍可追溯。

```sh
lmc5 add --db demo.sqlite --fact-key release.owner --title "Release owner" --content "Alice owns releases."
lmc5 add --db demo.sqlite --fact-key release.owner --title "Release owner" --content "Sam owns releases."
```

[五层如何协作](docs/CONNECTING_XYZEM.md) · [可运行的 Python 示例](examples/demo.py)

</details>

## 接入你的项目

**本地智能体**　让写入、查询和巡检都使用同一个 SQLite 文件。[持久化部署](docs/DEPLOYMENT.md)

**PostgreSQL 与 Claude Code**　先用一条样例验证全文检索，再接入会话归档和召回。[pgvector 部署](extras/pgvector_backend/README.md) · [Hooks 接入](docs/HOOKS_AND_RECALL.md)

**TypeSafe JEV**　给已召回的候选按相关性重新排序。SQLite 和 PostgreSQL 都支持显式启用。[配置 JEV](docs/JEV.md)

<details>
<summary>运行 AML 比赛用的 Add/Search HTTP 服务</summary>

报名快照为 `v0.3.0-aml.1`。代码 Full 对账记录、文本赛道公开状态和部署步骤见 [AML 版本对照表](extras/aml/README.md#version-and-source-correspondence)。

</details>

---

[克霖的原文](docs/KELIN.md) · [鸣谢](docs/credits.md) · [参与贡献](CONTRIBUTING.md) · [开源与商业许可](LICENSING.md)
