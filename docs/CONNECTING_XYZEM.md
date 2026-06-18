# Connecting XYZEM

> 五轴不是五个插件。它们必须接成同一套记忆生命周期。

很多实现会做到这一步：

```text
X axis: done
Y axis: done
Z axis: done
E axis: done
M axis: done
```

然后系统还是漏召回、图不扩展、旧事实继续冒充当前事实、自发浮现乱飘。

这通常不是因为某一条轴没有写，而是因为五条轴被做成了五个报表。LMC-5 要的不是五个报表，而是三条闭环：

```text
write path      raw events -> chunks -> candidate -> XYZEM coordinates
night path      consolidate -> hippocampus -> Y/Z/E/M audits -> snapshot
recall path     query -> vector/FTS/literal -> graph/emotion/perception -> merge -> inject
```

硬规则：

```text
如果一个轴的输出没有进入写入链路、夜间链路或召回链路，它就还没有接入系统。
```

## The Most Common Wrong Shape

错误形状长这样：

```text
X: timeline preview
Y: graph preview
Z: current-fact preview
E: emotion score preview
M: patrol preview
```

这些都能跑，但 agent 对话时不会变聪明。因为它们没有改变写入、维护和召回。

正确形状长这样：

```text
raw event
  -> chunk
  -> hippocampus candidate
  -> curated memory with X/Y/Z/E/M coordinates
  -> nightly relation/fact/emotion/metabolism maintenance
  -> RecallPipeline uses those coordinates in the next user turn
```

一句话：五轴不是五个科室各写一份病历，而是一次会诊流程。

## Circuit 1: Write Path

写入链路回答：这条材料从哪里来，是否值得进入长期记忆层，进入后带什么坐标？

```text
SessionEnd / log-event
  -> lmc5_raw_events or events
  -> consolidate
  -> event_chunks
  -> hippocampus candidate
  -> curated memory
```

每条候选记忆至少要落下这些信息：

| Axis | Must Write |
|---|---|
| X | `thread` / timeline label / source chunk time range |
| Y | relation hints or relation rows to other memory ids |
| Z | `fact_key`, `version_status`, `active_fact` when it is factual |
| E | risk, urgency, tension, valence/arousal if enabled |
| M | lifecycle status: current/review/archive candidate, weight/heat signals |

Acceptance check:

```text
Given a new conversation session
When SessionEnd and consolidate run
Then raw events become event_chunks
And hippocampus candidates keep source_chunk_ids
And any promoted memory has enough X/Z/E/M metadata to be audited later
```

If your candidate has only `title/content`, it is not an LMC-5 memory yet. It is just a note with ambition.

## Circuit 2: Night Path

夜间链路回答：昨天留下的东西，哪些要提升、连接、复核、降权、归档？

Recommended order:

```text
take snapshot
  -> consolidate raw events into chunks
  -> hippocampus candidate pass
  -> safe Y relation write
  -> Z fact audit
  -> E axis backfill / shadow scoring
  -> X timeline / narrative sweep
  -> M patrol
  -> validation
  -> keep snapshot or rollback
```

The order matters. Do not run M cleanup before Z audit knows which facts are current. Do not let E scoring rewrite facts. Do not let Y graph cleanup delete evidence before hippocampus has source chunks.

Safe defaults:

| Step | Default Posture |
|---|---|
| hippocampus | dry-run first, then apply only gated candidates |
| Y relations | auto-write safe structural edges; review risky semantic edges |
| Z audit | queue contradictions, do not auto-supersede at first |
| E scoring | shadow mode before affecting ranking |
| M patrol | read-only report before destructive maintenance |
| swap | snapshot before any write-heavy housekeeper run |

Acceptance check:

```text
Given yesterday's raw events
When the 04:00 housekeeper runs
Then new chunks are created
And safe relations are written or review-queued
And current-fact conflicts are listed
And E/M reports are produced
And the run can be inspected or rolled back
```

If night tasks only print pretty summaries and never update/read the tables used by recall, the system is still disconnected.

## Circuit 3: Recall Path

召回链路回答：用户这一句话来了，五轴如何一起决定什么进入上下文？

Production shape:

```text
UserPromptSubmit
  -> RecallPipeline.recall(query)
      -> query expansion, optional
      -> vector search
      -> curated FTS fallback
      -> raw-events FTS fallback
      -> literal raw-events channel
      -> optional recent raw_chunk bridge
      -> Y graph 2-hop expansion
      -> E emotion resonance
      -> spontaneous perception cache
      -> merge / dedup / rerank
      -> additionalContext injection
```

The Y graph must receive real seed ids from query-triggered channels:

```text
vector / FTS / literal / raw_events hits
  -> seed memory ids
  -> graph_expand(seed_ids, hops=2)
  -> graph hits
  -> merge back into recall output
```

Do not use spontaneous perception as a graph seed. A random floaty memory should not drag half the graph into the prompt. That is how "presence" turns into soup.

Z must filter or label facts before injection:

```text
current facts      -> eligible
review facts       -> label as review, inject sparingly
superseded facts   -> do not present as current truth
historical facts   -> inject only when the historical context is relevant
```

E must adjust posture, not overwrite reality:

```text
emotion resonance can boost relevant memories
emotion texture can guide tone
emotion score cannot make a false fact true
```

M must affect lifecycle and weighting:

```text
high heat / repeated correction -> review or promote
stale current facts -> Z audit candidate
low-use drift memories -> cooldown or demote
dangerous cleanup -> snapshot first
```

Acceptance check:

```text
Given a query that matches a raw-only proper noun
Then literal/raw-events channel should fire even if vector search has a weak hit

Given a query that hits memory A
And memory A has a same_topic edge to memory B
Then graph channel should surface B within two hops

Given an old fact superseded by a current fact
Then recall should not inject the old fact as current truth

Given an emotional query at night
Then E resonance may add emotionally similar memories
But it must not suppress exact factual recall
```

## Minimal vs Production

The minimal SQLite implementation already includes the core lifecycle pieces:

```text
lmc5 log-event
lmc5 consolidate --window-size 20
lmc5 hippocampus --consolidate
lmc5 recall
lmc5 patrol
```

That is enough to understand the shape.

The production pgvector implementation adds remote/local embeddings, `RecallPipeline`, Claude Code hooks, `night_dream`, E scoring, perception, and operational patterns such as forge/swap.

Production users must wire the deployment-specific callables:

```text
load raw events for chunking
embedder
vector search
graph_expand
emotion_resonate
spontaneous cache loader
nightly housekeeper schedule
snapshot / rollback policy
```

If a production deployment only imports the modules but never schedules them, nothing is alive. Python files do not wake up at 04:00 out of civic responsibility.

## Axis Contracts

### X: Timeline

X is connected only if timeline data affects chunking, narrative sweep, or recall grouping.

Not enough:

```text
timeline preview works
```

Enough:

```text
memories have stable thread/timeline labels
chunks preserve time ranges
startup pack or recall can surface current/open timelines
nightly narrative can summarize a thread without reading the whole raw log
```

### Y: Relations

Y is connected only if relation rows are used by graph expansion during recall.

Not enough:

```text
graph preview can draw memory_edges
```

Enough:

```text
RecallPipeline takes seed ids
graph_expand reads typed relation rows
2-hop hits are merged into final recall
relation strength and type affect ranking
```

### Z: Fact Evolution

Z is connected only if current/review/superseded status changes what the agent treats as truth.

Not enough:

```text
current-fact candidates are marked somewhere
```

Enough:

```text
active facts are preferred
superseded facts are blocked or labeled historical
contradictions enter review
manual or gated supersession is auditable
```

### E: Experience

E is connected only if risk/urgency/tension/valence/arousal affect recall posture or response posture.

Not enough:

```text
valence column exists
```

Enough:

```text
emotion_resonate can add relevant memories
risk/urgency can raise priority
shadow scoring logs failures and confidence
low-confidence scores do not silently rewrite behavior
```

### M: Metabolism

M is connected only if it changes lifecycle decisions or produces actionable review queues.

Not enough:

```text
metabolism_patrol prints heat and old memories
```

Enough:

```text
patrol identifies duplicate current facts
stale memories get review/demotion candidates
drift reports affect perception cooldown
dangerous cleanup requires snapshot and review
```

## Integration Checklist

Use this when a user says "all five axes are done."

| Check | Pass Condition |
|---|---|
| Raw capture | A user turn lands in raw events with timestamp/session/channel. |
| Chunking | `consolidate` creates bounded chunks and source links. |
| Candidate memory | Hippocampus candidates keep source chunk ids and evidence. |
| X | Candidate has thread/timeline and can be surfaced by timeline. |
| Y write | Safe relation rows are written or queued. |
| Y read | Query seed ids trigger 2-hop graph hits in recall. |
| Z | Current/superseded/review status changes recall output. |
| E | Emotional resonance is a bounded independent channel, not a fact gate. |
| M | Patrol creates reviewable lifecycle actions, not silent deletion. |
| Hook | `UserPromptSubmit` calls `RecallPipeline`, not five separate previews. |
| Night job | The 04:00 job runs consolidate/hippocampus/Y/Z/E/M in order. |
| Rollback | Write-heavy housekeeper runs have a snapshot or backup. |

If any row fails, the system is partially implemented. That is not shameful. It is just not connected yet.

## Anti-Patterns

Avoid these:

```text
Running graph preview but never calling graph_expand in RecallPipeline.
Marking current facts but never filtering superseded facts at recall time.
Scoring emotion but letting low-confidence E values affect ranking immediately.
Letting spontaneous perception become graph seeds.
Calling remote embedding synchronously with no hard timeout.
Auto-deleting memories from M patrol without snapshot and review.
Letting raw_events and curated memories share one identity namespace.
Treating "code exists" as "cron/systemd runs it every night".
```

## The One-Sentence Rule

When in doubt, ask:

```text
Does this axis change what gets written, maintained, or recalled?
```

If the answer is no, it is not connected. It is just a dashboard.
