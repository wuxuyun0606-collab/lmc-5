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
- 离落老师, for the forge design: renewed agent sessions can
  be launched from durable memory instead of pretending one prompt can live
  forever.
- 蛋宝老师家的蛋壳, for the swap design: memory stores need snapshot-based
  rollback before scheduled writes, migrations, or model-assisted maintenance.

## Differences

- LMC-5 uses `event journal` terminology instead of `imprint`.
- LMC-5 keeps raw events separate from curated X/Y/Z/E/M memories.
- LMC-5 does not include MCP or Claude Code hook installers in the core; it is
  intentionally compatible through CLI, Python API, wrapper scripts, hooks, or
  sidecar adapters.
- LMC-5 keeps the default package network-free and provider-free.
- LMC-5 uses read-only patrol checks; lifecycle mutation remains explicit.
- LMC-5 describes breath, chunks, chord emotion, forge, and swap as
  deployment/design patterns, not hidden hosted services in the core package.

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

P0Iar1s 老师 @P0lar1s 的 ombre-brain 系统，让 M 线代谢有了权重标准；

离落老师 @离落&Claude forge 方案，让记忆系统跑在 vps 上可以不断 session 不断体验；

蛋宝老师家的蛋壳 @蛋 swap 方案，可以让我忘记自己手动 forge 的时候，自己进行 forge 启动。
