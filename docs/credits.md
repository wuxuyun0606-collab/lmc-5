# Credits and Prior Art

LMC-5's raw event journal was inspired by public design ideas in
[`Qizhan7/imprint-memory`](https://github.com/Qizhan7/imprint-memory),
especially the separation between automatic conversation capture and curated
long-term memory.

This repository does not copy `imprint-memory` source code. It implements a
smaller, original offline-first event journal inside the LMC-5 coordinate model.

## Differences

- LMC-5 uses `event journal` terminology instead of `imprint`.
- LMC-5 keeps raw events separate from curated X/Y/Z/E/M memories.
- LMC-5 does not include MCP or Claude Code hook installers in the core; it is
  intentionally compatible through CLI, Python API, wrapper scripts, hooks, or
  sidecar adapters.
- LMC-5 keeps the default package network-free and provider-free.
- LMC-5 uses read-only patrol checks; lifecycle mutation remains explicit.

## Why Attribution Is Explicit

Renaming files to hide influence is not engineering. It is plagiarism wearing a
fake moustache. Prior art should be credited, and the new implementation should
stand on its own design boundaries.
