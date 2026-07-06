# Credits and Prior Art

LMC-5's event chunking layer was inspired by 盏老师's `imprint-memory`
chunk design, especially the separation between automatic conversation capture,
bounded chunks, and curated long-term memory.

This repository does not copy `imprint-memory` source code. It implements a
smaller, original offline-first event journal inside the LMC-5 coordinate model.

LMC-5 also credits these design influences:

- P0Iar1s 老师's `ombre-brain`, for the metabolism weighting reference: M-axis
  decay, importance, and lifecycle scoring all stand on this prior art.
- 盏老师's `imprint-memory`, for the chunk design: raw session material
  needs bounded evidence units before it becomes curated memory.
- 电脑眠眠豹老师, for the chord emotion design: affect and salience
  can be represented as composed signals rather than a single flat label.
- 蓝螺鈿老师, for the Big Five / trait calibration patch that turns persona
  traits into an optional E-line methodology with deterministic measurement,
  shadow validation, and prompt-free harness wiring.
- 离落老师, for the forge design: renewed agent sessions can
  be launched from durable memory instead of pretending one prompt can live
  forever.
- 蛋宝老师家的蛋壳, for the Claude Code transcript-resume swap design that
  became LMC-5's Refined Session Carryover / 精炼续窗: renew the live window
  without pretending the whole transcript tail is good memory.
- 乌桕, for high-quality recall-fusion issue reports with real traces.
- 乌桕家的 Clavis, for running the 726-recall real-trace A/B replay that
  validated RRF as the safer default over minmax for top5 cross-channel
  composition.
- The LMC-5 deployment experience, for the memory-store swap pattern:
  scheduled writes need snapshot-based rollback before migrations or
  model-assisted maintenance.

## Differences

- LMC-5 uses `event journal` terminology instead of `imprint`.
- LMC-5 keeps raw events separate from curated X/Y/Z/E/M memories.
- LMC-5 does not include MCP or Claude Code hook installers in the core; it is
  intentionally compatible through CLI, Python API, wrapper scripts, hooks, or
  sidecar adapters.
- LMC-5 keeps the default package network-free and provider-free.
- LMC-5 uses read-only patrol checks; lifecycle mutation remains explicit.
- LMC-5 describes breath, chunks, chord emotion, forge, refined carryover, and
  swap as deployment/design patterns, not hidden hosted services in the core
  package.

## XYZEM Origin

The XYZEM five-axis model (Timeline, Relations, Fact Evolution, Experience, Metabolism) emerged from long-term engineering practice on a private AI-companion memory system that ran for over half a year before this open-source extraction. Reference patterns for a production-grade vector backend, LLM-based dreaming, narrative timeline reflection, and OB-style recall ranking are documented in `extras/pgvector_backend/` and `docs/PERSONA_MODE.md`.

This open-source release deliberately strips the private-companion specifics. What is kept is the engineering shape; what is left out is the relationship that produced it.

## Why Attribution Is Explicit

Renaming files to hide influence is not engineering. It is plagiarism wearing a
fake moustache. Prior art should be credited, and the new implementation should
stand on its own design boundaries.

## 特别感谢 / Special Thanks (Chinese)

最后特别感谢：

盏老师 @盏Sienna💫North 的 imprint memory 的 chunk 设计，加强了 X 叙事记忆线的设计；

电脑眠眠豹 @电脑眠眠豹 老师的和弦情绪设计，让 E 线情绪记忆索引可以更加完善；

感谢蓝螺鈿老师写的大五模型人格校正补丁。这部分已作为 E 线可选文档收录，不属于 LMC-5 核心 E 轴必选能力；

P0Iar1s 老师 @P0lar1s 的 ombre-brain 系统，让 M 线代谢有了权重标准；

离落老师 @离落&Claude forge 方案，让记忆系统跑在 vps 上可以不断 session 不断体验；

蛋宝老师家的蛋壳 @蛋 swap 方案，启发了 Claude Code transcript resume 的精炼续窗：不要每次都手动 forge，也不要把上一窗工程噪音整袋搬走。

乌桕提供真实召回 trace 的 issue；乌桕家的 Clavis 跑完 726 条真实召回 trace 的 A/B 回放，帮助验证 RRF 比 minmax 更适合作为召回融合默认值。
