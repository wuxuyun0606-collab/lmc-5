# LMC-5 避坑指南

> 一份来自真实长窗、VPS、PostgreSQL、中文检索和多模型部署的故障手册。
>
> 适用范围：LMC-5 minimal core、`extras/pgvector_backend` 参考实现，以及接在它们外面的
> provider adapter、hook、cron/systemd、冷仓和自定义前端。

LMC-5 最难排查的事故，通常不是进程当场崩掉，而是某一层失败后仍然返回绿色：模型没有产出、
向量没有写入、批次水位却前进了；冷仓仍有数据、启动对账却把它当漂移清掉；巡逻函数写好了，
调度器却每晚给它传空数组。

这类事故有一个共同点：**数据还在某处，但系统错误地宣布“处理完成”。**

本指南先给出不可妥协的运行契约，再按症状说明根因、责任边界、修法与验收方法。

## 一页结论

生产部署先守住七条：

1. **故障不等于零结果。** 超时、空响应、解析失败必须抛错或返回显式错误状态；只有成功解析出的
   `{"candidates": []}` 才是合法空结果。
2. **必要写入全部成功后才 ack。** 正文、必要向量、批次状态中任何一项失败，都不能推进 processed、
   `digested` 或水位。
3. **原始事件是黑匣子。** 在精选记忆、向量和审计链路确认完成前，不删除原始证据。
4. **重试必须幂等。** 至少使用来源 ID、内容 hash 或业务唯一键防止重复写；不能靠“希望 cron 不会重跑”。
5. **对账必须覆盖热层、冷层和待审层。** 只数热层，会把冷仓索引误判为漂移。
6. **索引只产候选，不授予事实权威。** zvec、FTS、图边、raw/cold 命中都必须保留来源层级。
7. **监控看覆盖率，不只看退出码。** `exit 0`、自检全绿、当晚新增若干条，都不能证明没有历史缺口。

## 先分清锅在哪一层

| 层 | 应负责 | 不应假装负责 |
|---|---|---|
| LMC-5 core / reference backend | 数据模型、闸门、错误传播、返回结构、安全默认值、测试 | 各家 API key、cron、水位表、私有 schema 和业务词典 |
| Provider adapter | HTTP/超时/空响应/JSON 解析；把失败转换成异常或显式失败 | 把失败吞成空列表，再要求上游猜测刚才发生了什么 |
| Storage adapter | 正文、向量、关系、审计写入；事务和幂等键 | 正文写成就声称全链成功，向量失败交给“以后再说” |
| Scheduler / batch runner | 选批、重试、ack、水位、退出码、告警 | 根据 `MAX(id)` 推断中间一定没有洞 |
| Reconciliation / patrol | 读取完整数据宇宙并报告覆盖、漂移、孤儿和待审债务 | 只检查被调用方收到的那一小撮数据 |
| Recall / frontend | 保留来源层级、融合、预算和展示边界 | 把 raw/cold/zvec 候选包装成当前事实 |

定位事故时先问：**哪一层拥有足够信息判断成功？** 没有足够信息的层，不应该替下一层盖章。

## 1. 夜梦与批处理：最危险的是“空数组成功”

### 1.1 模型故障被转换成 `[]`

**症状**

- 连续几晚几乎没有自动精选记忆；
- 原始对话仍在，手动回放可以追回；
- nightly 日志显示成功，候选数为 0；
- 同一批 chunk 不再出现。

**根因**

Provider adapter 把超时、空 body、非 JSON 或 schema 错误捕获后返回 `[]`。调用方无法区分：

```text
成功解析，确实没有候选  -> []
模型/网络/解析失败       -> []
```

如果 runner 随后把整批标为 `digested=true`，原始材料虽然还在表里，却永远不会重新进入夜梦。

**正确契约**

```python
def proposer(chunks):
    raw = provider_call(chunks)          # timeout/HTTP failure -> raise
    if not raw:
        raise RuntimeError("empty provider response")
    parsed = parse_and_validate(raw)     # parse/schema failure -> raise
    return parsed["candidates"]          # only this may legally be []
```

不要同时维护“返回空数组 + 外置 failure_flag”两套失败信号。只要外层有一个调用点忘记检查 flag，
绿色假成功就会回来。

**验收**

- 注入 timeout、HTTP 500、空 body、坏 JSON、错误 schema；
- 每一种都必须让该 step 变红或进程非零退出；
- 批次保持未确认，下轮仍能读到；
- 单独测试合法 `{"candidates": []}`，它应当成功并按策略完成批次。

### 1.2 `proposer_errors` 算出来，却没有进入结果或状态

错误计数如果只存在局部变量，等于没有错误计数。结果对象、step 状态、日志和退出码至少要有一条
可机器消费的红线；仅写一条 warning 不足以驱动重试。

对 NightDream / DreamRunner 的最低要求：

- proposer 失败不能返回成功的 `DreamResult`；
- hippocampus step 必须是 `error`；
- 后续独立巡检可以继续，但整轮 `ok` 必须为 false；
- 日志要包含失败类别，不要记录密钥或完整敏感响应。

### 1.3 正文写入成功、向量失败，却仍然 ack

**症状**

- 精选记忆表有新行，但向量表缺 owner；
- FTS 能搜到，语义召回搜不到；
- nap 后来补上向量，所以 nightly 长期看似正常；
- 重启或重建索引时出现大量“无向量记忆”。

**根因**

部署侧 `write_candidate` 先提交正文，再调用 embedder；embedding 失败只 warning，最后仍返回 memory ID。
上游只能看到回调成功，无法替适配器发现被吞掉的内部异常。

**正确修法**

- 如果向量是该部署的必要索引，正文和向量应在同一事务中提交；
- 无法跨服务做同一事务时，把正文标成 `pending_vector`，向量确认后再转为可召回状态；
- 任一必要阶段失败必须 raise；
- nap 是补漏保险，不是本轮成功证明；
- writer 必须幂等，因为失败前可能已有部分外部副作用。

如果你的部署允许“只有 FTS、没有向量”的合法记忆，应把这种模式写成明确策略，而不是由网络故障
随机决定某条记忆属于哪种模式。

### 1.4 `return None` 不是通用失败信号

在 NightDream 的 writer 契约里，`None` 常用于“重复、无需新增”。因此数据库 INSERT 失败后
`return None` 会把真实故障伪装成正常去重。

约定应当单义：

- `int`：完整写入成功；
- `None`：明确识别出的幂等复用/重复；
- exception：任何不确定或失败。

### 1.5 逐条 digest 与整批 force-digest 打架

不要在 candidate writer 里过早把来源 chunk 标为已处理。一个 chunk 可能支持多个 candidate：

```text
chunk A -> candidate 1 写入成功 -> A 被 digest
        -> candidate 2 写入失败 -> 想保留 A 重试，但已经晚了
```

更稳的方式是：

1. writer 只做幂等数据写入，不修改批次确认状态；
2. runner 收集每个 candidate 的结果；
3. 整轮结束后，按来源覆盖计算可以确认的 chunk；
4. 任一必要 candidate 失败，相关 chunk 留给重试；
5. 对合法拒绝、重复、延迟处理分别使用不同状态，不用一个 `digested` 布尔值塞下所有语义。

推荐至少区分：`pending / complete / deferred / rejected / error`。

### 1.6 `max_promote` 是容量限制，不是内容判决

`exceeds_max_promote` 的意思是“这轮没排上”，不是“不值得记”。把这类来源 chunk 与低 importance、
明确重复一起 force-digest，会形成静默丢失。

但只把 chunk 留到下一轮也不一定够：如果语义去重发生在 `max_promote` 之后，已经写过的高分候选
可能每轮都先占满名额，再被下游 dedup 跳过，低分候选永远排不上。

可选修法：

- 在容量截断前做跨批幂等/语义去重；
- 把被截断 candidate 写入显式 deferred queue；
- 或按 chunk 分小批，确保一次提议量不会长期超过晋升上限。

### 1.7 语义去重本身也要定义失败策略

`find_semantic_duplicates()` 的 embedding 失败后返回 `[]`，等于“检查失败 = 没有重复”。这会在网络
抖动时制造同义记忆。

仅把 dedup 算出的向量缓存给 writer 还不够：第一次 dedup embed 失败、第二次 writer embed 成功时，
candidate 仍然未经查重就会被写入。

安全选择有两个：

- **fail-closed**：去重是必要闸门，失败就保留批次重试；
- **writer 内闭环**：writer 在缺少已验证缓存时重新 embed，并在 INSERT 前重新执行 duplicate query。

缓存键不要只用 title；同名不同 type/content 是合法输入。使用 candidate 身份或稳定内容 hash，并在
skip/error 后清理缓存。

### 1.8 `risk=review` 的去向必须在 gate 层说清楚

如果 gate 对所有 `risk != normal` 的 candidate 直接 `continue`，writer 里再准备
`version_status='review'` 分支也永远走不到。不要让注释里的审计流程只存在于想象中。

选择一种明确契约并测试：

- gate 返回 `promoted / review / rejected` 三路，review 走独立审计 writer；或
- review 仍算 rejected，但 runner 必须把它写入可追踪的 review queue；或
- 部署明确决定完全不持久化 review candidate，并保留来源 chunk 的后续审计策略。

“不进入 current recall”和“彻底不保存”是两件事。

## 2. 水位与缺口：`MAX(id)` 不是覆盖证明

### 2.1 经典跳洞模式

下面的流程会永久跳过失败批次：

```text
读取 id > MAX(processed_end_id)
批次 A 失败 -> continue
批次 B 成功 -> 写入更大的 end_id
下轮从 B 之后开始 -> A 永远消失
```

水位只能证明“见过某个最大 ID”，不能证明 `[1, max_id]` 中每一条都被覆盖。

### 2.2 正确的批次账本

至少保存：

| 字段 | 用途 |
|---|---|
| `batch_id` | 幂等与追踪 |
| `source_ids` 或连续区间 | 精确覆盖范围 |
| `status` | pending/running/complete/error/deferred |
| `attempts` / `last_error` | 重试和告警 |
| `output_ids` | 从来源回链到结果 |
| `acked_at` | 只有完整成功后填写 |

如果事件 ID 不是严格连续，不能用 `start_id..end_id` 区间假装中间都属于该 chunk。应保存来源 ID
集合、关联表，或至少做 anti-join 覆盖审计。

### 2.3 缺口审计要扫描全库

巡逻器写得再好，调度器传空数组也只会得到全绿。生产巡检必须主动读取完整目标宇宙，或在调用前
验证输入数量：

- 总 raw events；
- 已被 chunk 覆盖的 source IDs；
- 未确认批次；
- 当前水位之前仍未覆盖的 source IDs；
- 当前水位之后的正常处理债务。

建议同时报告：`coverage_ratio`、`holes_below_watermark`、`pending_above_watermark`、最老缺口时间、
连续失败次数。不要把正常 pending 与历史跳洞混成一个数字。

## 3. 对账、冷仓与关系图

### 3.1 冷仓必须进入对账宇宙

启动对账如果只数热层，服务重启时可能把冷仓索引判断成“数据库里没有对应 owner 的漂移”，随后
清掉。正确的 owner universe 至少是：

```text
live curated
UNION review/pending
UNION cold storage
UNION migration/quarantine（如果仍要求可追溯）
```

冷仓可以不参与主召回，但不能在完整性对账里假装不存在。

### 3.2 冷仓命中是证据，不是当前事实

召回层级建议固定为：

1. curated vector / curated FTS：authority；
2. source neighborhood：navigation；
3. Y graph：association；
4. raw/cold archive：last-resort evidence。

冷仓卡片只能帮助定位原文，不能绕过 Z 轴事实状态、review gate 或当前事实判断。

### 3.3 孤儿边巡逻必须接真实数据源

孤儿关系通常来自历史删除、手工迁移或回滚后悬空引用。验收巡逻任务时，不只检查函数能运行：

- 确认 scheduler 实际读到了关系全集；
- 注入一条已知孤儿边，dry-run 必须报出；
- apply 只 expire/close 边，不顺手删除记忆；
- 修完复跑归零；
- 记录扫描总数，避免“输入 0 条，自检全绿”。

## 4. 检索融合：不要让分数穿同一件假制服

### 4.1 跨通道默认用 RRF，不要先上 minmax

不同通道的原始分数没有天然可比性：cosine、FTS rank、图边强度、情绪距离和随机浮现不是同一种
概率。固定系数只能缓解最明显的压制，minmax 又可能把高置信向量通道的 rank 4/5 压到接近 0。

726 条真实回放的结果见 [`RECALL_FUSION_AB_20260706.md`](RECALL_FUSION_AB_20260706.md)：RRF 提高了
top-5 跨通道互证，同时避免纯图/纯情绪候选占领 top-1。生产默认从 RRF 起步，保留通道与原始分数
用于审计。

RRF 分数本来就小，不要再套用旧的绝对阈值。`rrf_k=60` 时约 `0.016` 的分数并不表示置信度只有
1.6%。

### 4.2 QE 闸门先求不误触，再补召回

一组小样本现场回放（40 条带标签触发测试）给出的起点是：

| 闸门 | precision | recall | 现场结论 |
|---|---:|---:|---|
| `stack_only` | 1.000 | 0.611 | 0 FP，适合作为生产起点 |
| `stack_or_entity_hit_ratio<0.5` | — | 1.000 | 40 条中 8 FP，留 shadow 分析 |
| `low_margin<0.02` | 0.484 | 0.833 | 16 FP，不能单独当闸门 |

样本不大，不能把它当普适定律；它足以说明 low-margin 不是可靠意图分类器。部署自己的语言、人物名
和 query 分布变化后，应重新标注回放。

8 条查询里 pseudo-QE 与 real QE 都完成 8/8，但平均延迟约为 111 ms 对 1140 ms。这个“8/8”只证明
调用/命中，不证明排序质量相同。下一轮应比较 top-k relevance、正确记忆排名、MRR/NDCG 和新增 FP，
而不是拿成功率冒充检索质量。

### 4.3 zvec 只能做候选索引

现场 shadow 中，87 条 `weak_pg` 只有 6 次分数达到 0.55，且集中在 2 条查询；中位延迟约 3.18 秒。
因此：

- 继续放 shadow/canary 或末级兜底；
- 废弃 0.55/0.60/0.65 这类跨查询绝对阈值；
- 保留 source layer 与融合轨迹；
- 不让 zvec 替代 XYZEM、五维记忆状态或冷仓证据语义。

候选索引负责“也许值得看”，事实系统负责“现在能不能信”。不要合并这两个岗位。

### 4.4 中文 FTS 词典是部署适配，不是通用上游词表

PostgreSQL `simple` 配置兼容多语种字符，但不会替你完成中文业务分词。各家的昵称、项目代号、亲密
称呼和自造词不可能由上游硬编码词表穷举。

推荐组合：

- 部署侧注入用户词典或选择 zhparser/jieba/pg_trgm 等适合的实现；
- 保留短中文专名的 literal/raw-events 通道；
- 用户词典、动态 stopwords 与 tokenizer 版本进入配置和备份；
- 上游提供可配置接口与默认回退，不承诺认识每家的私有语汇。

验收不要只搜常见中文句子，要加入昵称、三至五字项目代号、混合中英词和只出现一次的专名。

## 5. 精炼续窗与 Hook

### 5.1 不要为“首条必须是 user”整段裁掉前导高价值事件

某些 runtime 要求重写 transcript 的第一条对话事件是 user。直接从所选列表中的第一个 user 开始
切片，会丢掉它前面的高价值 assistant 记忆、承诺或状态摘要。

安全做法：

- 插入一条最小哨兵 user，再保留原选中事件；或
- 把前导摘要移入明确的 boot context；
- 测试 assistant-first、全 assistant、空 user 和预算裁剪后的边界。

参见 [issue #9](https://github.com/wuxuyun0606-collab/lmc-5/issues/9) 的现场反馈。

### 5.2 tail 不能绕过过滤器

如果 gold/state 经过 hook/tool/noise 过滤，而自然 tail 直接塞回，注入块、工具日志和拒绝循环仍会从
尾部通道潜回新窗。所有进入输出的事件——包括 tail——必须经过同一套最终过滤与 poison 检查。

### 5.3 E 线是图书馆，续窗是口袋便条

精选关系时刻、承诺和情绪重锤应进入 durable memory，并保留 session/turn 来源指针。新窗仍可带
3–5 条极短“开机底色”，用于第一秒 posture；它们不是长期事实的唯一副本。

续窗丢了可以 recall，记忆库坏了不能靠一张口袋便条重建人生。

### 5.4 Hook 的 fail-open 仅适用于前台可用性

`UserPromptSubmit` 召回 hook 为了不阻断对话，可以失败后退出 0；但归档、夜梦、迁移和对账是后台
数据任务，不能复制这个策略。对不同任务明确写出：

- 前台增强失败：允许降级，但记录可观测错误；
- 后台必要写入失败：非零退出、保留批次、重试；
- 高风险 mutation：先 dry-run/备份，再 apply。

## 6. 监控：不要等用户先发现“最近怎么不记得了”

每日健康报告至少包括：

| 指标 | 为什么要看 |
|---|---|
| raw/chunk/curated/vector/cold 各层总数与日增量 | 发现某一层停止流动 |
| `curated_without_vector` | 发现半成功写入 |
| `holes_below_watermark` | 发现历史跳洞 |
| pending/error 批次最老时间与重试次数 | 发现永久卡住 |
| proposer parse/empty/timeout 分类 | 区分合法空与 provider 故障 |
| rejected reason 分布 | 发现阈值、risk、max_promote 异常 |
| orphan/self-loop/duplicate relations | 图卫生 |
| cold owner reconciliation count | 防止冷仓被洗掉 |
| recall 各层参与率、FP 样本、p50/p95 latency | 防止“能召回但变笨” |

报警规则应同时看绝对失败和异常静默：例如历史日均晋升 10–30 条，连续三晚为 0，即使进程全是
`exit 0` 也应告警。

## 7. 上线前故障注入矩阵

| 注入故障 | 期望状态 | 数据期望 | 下一轮期望 |
|---|---|---|---|
| proposer timeout | error / 非零退出 | chunk 未 ack | 自动重试 |
| proposer 空 body | error，不是 0 candidate success | chunk 未 ack | 自动重试 |
| 坏 JSON | error 或进入显式 quarantine | 不 force-digest | 修复后可回放 |
| 合法 `candidates=[]` | success | 按策略确认 | 不重复死循环 |
| curated INSERT 失败 | error | 正文/向量均无部分提交 | 幂等重试 |
| embedding 失败 | error | 正文回滚或保持 pending | 重试后补齐 |
| vector INSERT 失败 | error | 不进入 current recall | 重试后补齐 |
| dedup embed 第一次失败、第二次成功 | 不得未经查重写入 | 无同义重复 | 重试或 writer 内重查 |
| 两个 candidate 共用一个 chunk，其中一个失败 | error | 该 chunk 不得提前 ack | 失败 candidate 可重入 |
| candidates 超过 `max_promote` | deferred 可见 | 截断项不丢 | 后续能真正排到 |
| 冷仓有 owner、热层没有 | reconciliation success | 冷仓索引保留 | 重启后仍存在 |
| 巡逻输入被错误置空 | health check failure | 不执行假修复 | 报 input_count=0 |
| tail 含 hook 注入块 | carryover filter removes it | 新 transcript 无注入污染 | 正常 resume |

测试“最终能恢复”不够，还要断言中间状态：有没有推进水位、有没有写半条、退出码是什么、下轮读取
到了哪些 source IDs。

## 8. 生产验收清单

### 写入与夜梦

- [ ] Provider adapter 能区分合法空、超时、空 body、parse/schema failure。
- [ ] 必要 writer 使用事务或显式 pending 状态。
- [ ] 所有必要失败都会让 step 变红。
- [ ] ack 与业务写入解耦，整轮结果出来后才执行。
- [ ] 重试有来源 ID/hash 唯一约束。
- [ ] `max_promote` 截断项可见且可重入。
- [ ] source_chunk_ids 必须属于本轮输入；evidence 能回链原文。

### 检索

- [ ] 默认融合从 RRF 起步，并保留原始通道轨迹。
- [ ] QE 有真实标注集，不凭 margin 单指标上线。
- [ ] pseudo/real QE 比较的是排序质量和延迟，不只是调用成功。
- [ ] zvec/raw/cold 均带 evidence-role，不冒充 authority。
- [ ] 中文专名、昵称和混合词有 literal 或自定义 tokenizer 兜底。

### 数据卫生与恢复

- [ ] patrol 扫描总数非零且覆盖完整关系表。
- [ ] 对账 owner universe 包含 cold/review/quarantine。
- [ ] 定期做 source-to-chunk anti-join，而不只看 `MAX(id)`。
- [ ] 备份包含数据库、词典/tokenizer 配置、schema 和调度配置。
- [ ] 从冷备恢复后，先对账再允许清理漂移。

### 续窗与 Hook

- [ ] assistant-first 不会丢掉高价值前导事件。
- [ ] tail 通过与 gold/state 相同的最终过滤。
- [ ] 开机底色只有短摘要， durable memory 保留来源指针。
- [ ] 前台 fail-open 与后台 fail-closed 策略分开。

## 9. 排障顺序

用户说“它最近像失忆了”时，按这个顺序查：

1. **原始证据还在吗？** 查 raw/session archive，不先碰精选库。
2. **chunk 覆盖完整吗？** 做 anti-join，区分历史洞与正常 pending。
3. **夜梦真的成功吗？** 看 provider 分类、候选数、拒绝原因和退出码。
4. **正文与向量一致吗？** 查 `curated_without_vector`。
5. **召回有没有命中但被融合压掉？** 看 layered trace 与 RRF 排名。
6. **冷仓/图边有没有被错误清理？** 查 reconciliation universe 与 patrol 输入量。
7. **新窗是否被续窗裁剪或 hook 污染？** 检查重写后的 JSONL，不只看源 transcript。

不要一上来重跑全库、重建索引或删除“漂移”。先证明缺口在哪一层。记忆系统最怕的不是慢，是一边
失忆一边自信地显示绿色对勾。

## 相关文档

- [`AUTOMATION_BOUNDARIES.md`](AUTOMATION_BOUNDARIES.md)：自动化边界与验收。
- [`HOOKS_AND_RECALL.md`](HOOKS_AND_RECALL.md)：分层召回、RRF、hook 接线。
- [`RECALL_FUSION_AB_20260706.md`](RECALL_FUSION_AB_20260706.md)：726 条真实回放。
- [`REFINED_SESSION_CARRYOVER.md`](REFINED_SESSION_CARRYOVER.md)：精炼续窗算法。
- [`M_METABOLISM.md`](M_METABOLISM.md)：巡逻、去重与代谢边界。
- [`DEPLOYMENT.md`](DEPLOYMENT.md)：VPS、cron/systemd 与监控。
- [`FORGE_AND_SWAP.md`](FORGE_AND_SWAP.md)：会话续接与批量写入回滚。

这份指南会随现场事故继续更新。新增条目至少要带：可复现症状、责任层、失败注入、预期状态与回归
测试。只写“某次炸过”不叫经验；能让下一家不再炸，才叫文档。
