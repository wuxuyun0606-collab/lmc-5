# Credits and Prior Art

LMC-5's event chunking layer was inspired by 盏老师's `imprint-memory`
chunk design, especially the separation between automatic conversation capture,
bounded chunks, and curated long-term memory.

This repository does not copy `imprint-memory` source code. It implements a
smaller, original offline-first event journal inside the LMC-5 coordinate model.

LMC-5 also credits these design influences:

- 鹤见老师's `ombre-brain`, for the breath design: scheduled maintenance
  should have rhythm instead of becoming an always-on mutation daemon.
- 盏老师's `imprint-memory`, for the chunk design: raw session material
  needs bounded evidence units before it becomes curated memory.
- 电脑眠眠豹, for the chord emotion design: affect and salience
  can be represented as composed signals rather than a single flat label.
- 离落老师, for the forge design: renewed agent sessions can
  be launched from durable memory instead of pretending one prompt can live
  forever.
- 蛋宝老师, for the swap design: memory stores need snapshot-based
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
