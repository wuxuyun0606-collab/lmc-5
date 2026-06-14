# 给 LLM Agent 的五维活体记忆坐标

**Living Memory Coordinate-5，简称 LMC-5。**

[English](README.md) | [简体中文](README.zh-CN.md)

> 给 Claude Code、Codex 和其他 coding agent 用的可恢复记忆层：
> raw events、curated memory、事实演化、关系网、向量检索和脱敏。
> 不是又一个普通向量库。

**要可恢复的连续性，不要幻想无限上下文。**

![LMC-5 封面插画：手绘翻开的笔记本，围绕五个坐标——关系网、事实演化、和弦情绪、叙事时间线、记忆代谢——LMC-5 吉祥物在旁。可恢复的连续性，开源记忆方案。](docs/assets/cover.png)

任何模型的上下文窗口都有上限。也许是 100k tokens，也许是 1M，也许未来会更大。
但它仍然不是无限的；上下文越长，成本越高，噪声越多，也越脆弱。

人也不是把一生听过的每句话都塞在脑子里随时激活。我们会留下重要的事，修正旧事实，
把相似经验连起来。反复被纠正的地方会改变下一次反应；压力、风险和没解决完的冲突，
也会慢慢变成做事的手感。

LMC-5 就是围绕这个想法做的小型、离线优先 **LLM agent memory** 架构：不要追一个听起来很神的
“无限 prompt”，而是做一个在关键时刻能恢复连续性的记忆系统。

它适合 Claude Code、Codex 风格 coding agent、个人助理 agent、本地 CLI 工作流，
以及其他需要长期记忆但不想绑定单一模型厂商的 LLM 工具。

## 模型

**Living Memory Coordinate-5**，简称 **LMC-5**。它把记忆看成五个协作层，而不是一堆被召回的文本碎片：

| 坐标 | 名称 | 回答的问题 |
|---|---|---|
| **X** | 时间线 | 这条记忆属于 agent 哪条工作历史？ |
| **Y** | 关系网 | 它支持、冲突、解释或连接了哪些其他记忆？ |
| **Z** | 事实演化 | 这条事实现在有效、只是历史、已被覆盖，还是待确认？ |
| **E** | 体验信号 | 它带来了什么风险、紧急度、张力和回应姿态？ |
| **M** | 记忆代谢 | 它应该升权、降权、复核、归档，还是沉淀成长期规则？ |

参考实现还在这些坐标下面加了一层 raw event journal：

```text
raw events       -> 可搜索的黑匣子
curated memories -> 持久的 LMC-5 坐标记忆
surface()        -> 从两层里取出脱敏后的上下文
```

这个分层很重要。Raw logs 负责保留“发生过什么”；curated memories 负责决定“以后什么应该影响行为”。
把它们混成一张表，agent 就很容易把昨天的工具报错当成宪法修正案。小小架构犯罪，大大后患。

## 功能

这个仓库提供一个紧凑的 Python 参考实现：

- **SQLite 存储**：保存精选记忆、关系和原始事件。
- **FTS5 检索 + LIKE fallback**：离线关键词检索。
- **SQLite 向量索引**：便携的余弦相似度检索。
- **二跳带类型加权关系扩展**：让相关记忆按关系类型和距离衰减一起浮现。
- **Raw event journal**：保存会话黑匣子。
- **事件 chunk consolidation**：从原始会话里生成可复核的 observation。
- **夜间海马体 pass**：从 chunk 中筛选候选记忆，默认 dry-run。
- **适合 VPS 的 7*24 小时生命周期**：常驻记录事件，定时 consolidation、
  hippocampus 和 patrol。
- **Mixed surfacing**：同时召回精选记忆和原始事件。
- **fact-key supersession**：保留旧事实，但不让旧事实继续冒充当前事实。
- **Z 轴冲突审计**：把 contradiction 候选先放进 pending review，不自动 supersede。
- **体验信号**：风险、紧急度、张力和回应姿态。
- **只读代谢巡检**：检查重复 current facts、review 堆积和拆线候选。
- **脱敏工具**：用于 recall 输出和 embedding 输入。
- **JSONL 导入/导出**：方便迁移。
- **CLI 和 Python API**：核心不需要联网。
- **`doctor` 检查**：确认本地 SQLite / FTS 能力。

## 给谁用？

LMC-5 面向想给长期运行 LLM agents 加一层小型记忆系统的开发者：

- Claude Code 和 Codex 风格 coding agents：需要恢复项目上下文。
- 本地助理工作流：需要 raw event logs 和 curated memory 同时存在。
- 多模型 agent 系统：不希望记忆层绑定某一个 provider。
- 跑在 VPS 上的个人 agent：需要一个 7*24 小时存活的小型记忆服务，而不是只靠桌面窗口活着。
- 研究原型：想比较普通 RAG、向量召回和结构化记忆。
- 开发者工具：需要在把记忆注入 prompt 前先做脱敏和事实演化判断。

核心是 provider-free。你可以把它接到 OpenAI models、Gemini、Voyage embeddings、
Claude Code hooks、MCP sidecar、shell wrapper，或者完全本地的 stack。LMC-5 负责保存和浮现记忆；
你的 agent 决定如何使用这些上下文。

## Claude Code / Codex 兼容性

LMC-5 故意做成本地 CLI 和 Python library，所以它可以放在 Claude Code、Codex
或其他 coding agent 旁边，而不是绑死进某一个运行时。常见接法：

- **Shell wrapper**：启动 agent 前先跑 `lmc5 surface`，把脱敏后的上下文拼进项目指令。
- **Claude Code hooks**：用 `lmc5 log-event` 记录 prompt/tool 事件，用 `lmc5 surface` 做 SessionStart 或 UserPromptSubmit 注入。
- **MCP sidecar**：把 `recall`、`surface`、`log-event`、`consolidate` 暴露成工具，同时 SQLite 仍留在本地。
- **Codex 或其他 CLI agent**：在 pre/post task scripts 里调用同样的命令。

核心包暂时不内置 Claude Code hook installer。这是有意为之：存储、脱敏和生命周期规则保持
provider-free，适配器可以后续添加，不把记忆层锁死到某一个 agent。

具体 Claude Code 接入方式见 [docs/claude_code.md](docs/claude_code.md)。

## VPS / 7*24 小时部署

LMC-5 很适合放在小 VPS 上跑。核心只是本地 CLI + SQLite，所以就算 agent
窗口睡了，记忆层也可以继续活着：

```text
agent hooks / sidecar
  -> lmc5 log-event
  -> 定时 lmc5 consolidate
  -> 定时 lmc5 hippocampus
  -> 定时 lmc5 z-audit
  -> 定时 lmc5 patrol
  -> 下次会话前 lmc5 surface
```

这是一套更适合 VPS 上 7*24 小时生存的方案：raw events 可以持续落库，
夜间任务可以准备可复核记忆，patrol 可以提示 review backlog 或记忆漂移。
但它不是“无限上下文魔法”，也不应该变成无人审计的自动改记忆机器。
建议先让 `hippocampus` 长期 dry-run，确认输出稳定后，再把 `--apply` 放进受控
定时任务；同时限制文件权限，并定期备份 SQLite 数据库。活下来，比装神重要。

### Forge 方案

forge 方案负责“无限 session”的连续性。不要幻想一个 agent 进程永远不死，
而是在 VPS 上把下一次 session 锻造出来：

```text
上一轮 session events
  -> consolidate / hippocampus / z-audit / patrol
  -> lmc5 surface 当前项目
  -> 下一轮 agent session 带着恢复上下文启动
```

这样每个窗口都可以结束、compact、崩掉或重启；VPS 继续维护记忆时钟，
再用已复核记忆和近期证据 forge 出新的启动上下文。无限 session 不是无限 prompt，
是可重复恢复的工程流程。

### Swap 方案

swap 方案负责耐久和回滚。建议保持一个 active memory store、一个 warm backup，
以及定期 cold snapshots：

```text
active SQLite store
  -> 高频 snapshot
  -> warm standby copy
  -> scheduled writes 前 cold backup
```

如果夜间任务出噪声、provider 给了坏候选、迁移脚本跑歪，就 swap 回上一份好快照，
检查 pending Z audits 和 hippocampus 输出，再只重放通过审核的变更。记忆系统要有备胎，
不然第一个坏掉的夜间任务就会变成考古现场。

## 项目猜想

这个项目的 thesis 是：长期运行的 agents 需要一套记忆生命周期，而不只是更大的 prompt
或又一个向量库。面向审核和贡献者的论证、可证伪问题和 demo 形态见
[docs/project_hypothesis.md](docs/project_hypothesis.md)。

如果要补申请材料，见 [docs/why_openai.md](docs/why_openai.md)：为什么 Claude Code
是重要使用场景，但 OpenAI / GPT-class 评测仍然有价值。

## 快速开始

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
lmc5 consolidate --db demo.sqlite --window-size 20
lmc5 hippocampus --db demo.sqlite --channel demo
lmc5 z-audit --db demo.sqlite
lmc5 surface --db demo.sqlite "production rollback"
lmc5 patrol --db demo.sqlite
lmc5 stats --db demo.sqlite
lmc5 doctor --db demo.sqlite
```

运行 Python demo：

```bash
PYTHONPATH=src python examples/demo.py
```

示例输出：

```text
4.50 #1 Production safety boundary (fts)
2.15 #2 Post-change verification (related:1)
surface: 2 memories, 1 events
```

## Chunk Consolidation / 意识可用层

Raw events 是证据，不是长期信念。LMC-5 可以把原始事件分组成有边界的
chunks，再把 chunk 提升成待复核的 `observation` 记忆：

```bash
lmc5 consolidate --db demo.sqlite --window-size 20
```

这会形成一个中间层：

```text
raw events -> event chunks -> observations/current models -> agent response
```

默认 consolidator 是确定性、离线的，不调用外部 LLM，方便测试和本地 demo。
生产系统可以替换 summarizer，但保留同一套 LMC-5 坐标和审计表。

设计说明见 [docs/xyzem_consolidation.md](docs/xyzem_consolidation.md)。

## 夜间海马体

`consolidate` 负责把 raw events 切成证据 chunk。`hippocampus` 负责判断哪些
chunk 值得进入可复核记忆层：

```bash
lmc5 hippocampus --db demo.sqlite --channel demo
lmc5 hippocampus --db demo.sqlite --channel demo --apply
```

默认是 dry-run，只列出候选，不写 memory、不建关系。加 `--apply` 后才会把通过闸门
的候选写成 `review` 记忆，并且只自动应用安全关系，例如 `same_topic`、`same_event`、
`temporal_sequence`、`derived_from`。`contradicts`、`cause_effect`、`supports`
这类更容易误伤的关系只进入 review plan，不能自动改事实演化。

核心仍然 provider-free。DeepSeek 或其他便宜模型可以作为“记忆管家”提出候选，
但脱敏、重要度闸门、写入决定和关系安全都由本地 LMC-5 控制。模型可以建议记什么，
不能拿记忆系统的 root 权限。听起来不浪漫，但很能活。

## Z 轴冲突审计

Z 是事实演化线，负责保护“什么仍然为真”，不是兴冲冲拿着橡皮擦乱改历史。
LMC-5 把冲突发现和事实改写拆开：

```bash
lmc5 z-audit --db demo.sqlite
lmc5 z-audit --db demo.sqlite --apply
```

默认是 dry-run：只列出待判冲突对，不需要任何 API key，不调用模型，不写审计表，
也不 supersede。候选来源包括同一个 `fact_key` 下内容不同的 current/review 记忆，
以及显式 `contradicts` 关系。加 `--apply` 也只是写入 `z_conflict_audits`
的 pending 记录，memory 本身不变。

以后可以接 DeepSeek 之类便宜模型做裁判辅助，但边界不变：模型最多给 pending audit
打标签，本地策略才决定是否 supersede、archive 或保留 historical。

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

## Embedding / 向量层

LMC-5 当前离线核心依赖 SQLite FTS5、关系扩展和显式评分。同时项目里已经有一个轻量
SQLite 向量索引，可以存向量、做余弦相似度检索、关联 memory/event。

它是便携 reference store，不是生产级 ANN 数据库。大规模部署时可以替换成 pgvector、
LanceDB、FAISS、Milvus 或其他向量后端，但 LMC-5 的元数据规则不变。

推荐实现方式：

- 保留关键词检索作为底线：embedding provider 不可用时，FTS5/BM25 仍然必须能工作。
- 向量单独放在派生索引里，用 `memory_id` 或 `event_id` 关联原始记录。
- 每条向量记录 `provider`、`model`、`dimension`、`input_type` 和 `content_hash`。
- 同一个向量索引里不要混用不同模型族或不同维度；换 provider 或维度时重建索引。
- 精选记忆和原始事件分开 embed：raw events 是证据，curated memories 才是会影响行为的记忆。
- provider 支持时，用户问题用 `input_type=query`，已存记忆/事件用 `input_type=document`。
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

- **Gemini Embedding 2**：适合多模态、Google 生态或需要统一文本/图像/音视频表征的场景。
  当前 Google 文本 embedding 文档里的稳定 API model code 是 `gemini-embedding-001`，
  支持最高 3072 维；如果你的 API 账号已经暴露 `gemini-embedding-2` model ID，就优先用它。
- **Voyage AI**：如果你说的 `vogeya` 是 Voyage，那推荐它做高质量文本/代码检索。
  `voyage-4-large` 适合质量优先的通用多语言检索，`voyage-4` 适合均衡默认，
  `voyage-4-lite` 适合低延迟/低成本，`voyage-code-3` 适合代码记忆。

一句话：embedding 负责“找得到”，Z 轴负责“还算不算当前事实”。别让向量相似度替事实判断背锅，
它没那个脑子，别给它升职。

## 设计目标

LMC-5 不是聊天人格系统，也不是一个向量数据库穿了件实验室白大褂。它是给 agent 用的记忆协调层，
用于长期协作、可验证事实、低噪声召回和清晰安全边界。

参考实现偏向无聊但可靠的工程属性：

- 核心不联网。
- 没有隐藏模型 provider。
- 示例里没有凭据。
- 不自动删除。
- 巡检不自动改库。
- recall 输出不泄露 secret。

## 目录结构

```text
.github/workflows/
  ci.yml          # test matrix
src/lmc5/
  cli.py          # 命令行入口
  models.py       # dataclasses 和枚举
  redact.py       # 输出和 embedding 输入脱敏
  scoring.py      # 可解释 priority scoring
  store.py        # SQLite 持久化
  metabolism.py   # 只读生命周期建议
  consolidation.py # raw event chunking，生成可复核 observation
  vector.py       # 轻量向量工具
docs/
  architecture.md
  credits.md
  safety.md
examples/
  seed.jsonl
  demo.py
tests/
```

## LMC-5 相比普通 RAG 多了什么

普通 RAG 通常只问：“哪些文本块最相似？”

LMC-5 会问 agent 真正行动前需要知道的问题：

- 这条事实现在还有效吗？
- 它和其他记忆有没有冲突？
- 它属于哪条稳定工作线？
- 即使它很旧，是否仍然高风险？
- 它应该被召回、复核、蒸馏还是归档？
- 它应该影响下一次什么回应姿态？

相似度有用，但不够。一个分不清“历史上为真”和“现在仍为真”的记忆系统，不是在记忆，是在囤积。

## Event Journal

LMC-5 分成两层：

- Curated memories：带 X/Y/Z/E/M 坐标的精选记忆。
- Raw events：可恢复的 append-only 会话黑匣子。

用 `log-event` 记录原始对话轮次、工具观察或环境备注。用 `add` 写入真正会影响未来行为的精选记忆。
当 agent 需要“整理过的记忆 + 原始证据”时，用 `surface`。

这一层受盏老师的 `imprint-memory` chunk 设计启发，但这里是原创实现，并且使用不同命名和边界。
详见 `docs/credits.md`。

## 为什么做这个

目标不是让 AI 假装自己有一套人类传记。目标是让长时间运行的 agent 更安全、更连贯：

- 它们应该记住项目决策，而不是每次重新读完整项目。
- 它们应该保留旧事实，但不继续服从过期事实。
- 它们应该在碰生产、账号、密钥或费用前，先浮现相关风险。
- 它们应该从反复纠正里真的改变，而不是道歉得很漂亮然后什么都不变。
- 它们应该能在 compact、重启或切工具后恢复任务线索。

这就是可恢复的连续性。不是魔法，不是玄学，只是少一点金鱼脑，多一点 schema。

## 状态

Alpha。API 还很小，之后可能变化。目前目标是让这套坐标模型可测试、可迁移、可审计，
而不是立刻变成完整记忆平台。

## Roadmap

- 增加可选 embedding adapters，但不把联网调用放进核心。
- 增加关系扩展召回的图解释。
- 增加 Markdown / JSONL 记忆日志迁移工具。
- 增加长时间 coding-agent 任务的 benchmark fixtures。
- 增加可选模型辅助抽取：fact keys 和 relation candidates。

## 鸣谢

鸣谢鹤见老师的 `ombre-brain` breath 设计，鸣谢盏老师的 `imprint-memory`
chunk 设计，鸣谢电脑眠眠豹的和弦情绪设计，鸣谢离落老师的 forge 设计，
也鸣谢蛋宝老师的 swap 设计。LMC-5 吸收这些设计对话，但保持自己的
provider-free、可审计实现边界。
