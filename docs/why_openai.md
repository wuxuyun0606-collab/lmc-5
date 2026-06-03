# Why OpenAI / GPT-Class Evaluation Matters for LMC-5

LMC-5 is intentionally compatible with Claude Code, Codex-style agents, MCP
sidecars, shell wrappers, and local scripts. That compatibility is important:
Claude Code is one of the clearest real-world places where a persistent coding
agent memory layer can be useful.

But the project should not be evaluated only as a Claude Code add-on. LMC-5 is
a provider-free memory lifecycle. The right evaluation target is broader:

> Can a strong coding model use structured memory better than a larger prompt
> or plain retrieval, across many sessions, tools, and changing facts?

That is why OpenAI / GPT-class evaluation is valuable for this project.

## Claude Code Is the Workflow, Not the Boundary

Claude Code is a strong developer workflow target. It gives LMC-5 an immediate
integration story:

- record prompt and tool events through hooks,
- surface redacted project context before a new task,
- consolidate session chunks into reviewable memories,
- keep memory local and provider-free.

That makes Claude Code a good adoption path. It should be supported clearly.

But if LMC-5 is only framed as a Claude Code helper, the project looks narrower
than it is. The actual contribution is not a hook. The contribution is the
memory model beneath the hook: raw events, chunk consolidation, curated
coordinates, fact evolution, experience signals, metabolism, and redaction.

## Why GPT-Class Models Are Useful Here

LMC-5 needs evaluation on tasks that require more than one good answer. It needs
models that can reason over changing project state, inspect evidence, revise old
facts, and explain why a memory should or should not be injected.

OpenAI / GPT-class models are useful for this because they can be used in a
controlled API setting:

- **Extraction experiments**: turn raw session logs into candidate fact keys,
  relation edges, risk labels, and reviewable memories.
- **Recall comparisons**: compare plain FTS, vector recall, long-context
  prompting, and LMC-5 coordinate-aware surfacing on the same tasks.
- **Agent benchmarks**: replay multi-session coding workflows and measure stale
  fact injection, contradiction handling, and recovery of project context.
- **Redaction testing**: evaluate whether sensitive strings stay out of
  embeddings, recall output, and exported memory surfaces.
- **Provider-neutral adapters**: test that the same memory lifecycle works
  across Codex-style agents, Claude Code, local scripts, and MCP tools.

In other words: Claude Code proves the workflow is useful. OpenAI / GPT-class
evaluation helps prove the memory architecture is real.

## Why Not Just Long Context

Long context is powerful, but it does not solve memory governance by itself.
A very large context window can still contain:

- obsolete facts that look current,
- old user preferences that were later corrected,
- unsafe instructions that should have been demoted,
- sensitive raw logs that should not be injected,
- repeated low-value details that drown out the one important constraint.

LMC-5 is designed to test a different hypothesis: the agent should not remember
more by default; it should remember with lifecycle control.

The question is not whether GPT-class models can read a huge prompt. The
question is whether they perform better when memory has already been organized
into evidence, current facts, relation edges, risk signals, and review states.

## A Strong Application Framing

If this needs to be sent as an update, the clean version is:

> We are building LMC-5 as a provider-free memory lifecycle for coding agents,
> compatible with Claude Code, Codex-style agents, MCP sidecars, and local
> scripts. Claude Code is an important integration target, but OpenAI credits
> would let us evaluate the core hypothesis under controlled GPT-class API
> experiments: whether structured memory coordinates reduce stale-context
> injection, improve multi-session project recovery, and make agent memory more
> auditable than plain long-context prompting or vector retrieval. The core
> remains offline-first and network-free; model calls are used only for optional
> extraction, evaluation, and benchmark generation.

That framing keeps the project honest:

- not anti-Claude,
- not locked to OpenAI,
- not just asking for compute,
- and not pretending bigger context alone is memory.

## One-Sentence Version

Claude Code shows where LMC-5 can be used; OpenAI / GPT-class evaluation helps
prove whether the memory lifecycle itself is better than plain retrieval or a
bigger prompt.
