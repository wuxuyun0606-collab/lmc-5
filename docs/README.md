# LMC-5 documentation

## Start here

- [SQLite quickstart](../README.md) and [persistent deployment](DEPLOYMENT.md) cover installation, a first recall and scheduled patrol.
- [中文导览与安装自检](LMC-5可分享导览与安装自检_20260702.md) · [中文架构思维导图](LMC-5专业思维导图_AI版_20260702.md)
- [Architecture](architecture.md), [implementation order](IMPLEMENTATION_ORDER.md) and [automation boundaries](AUTOMATION_BOUNDARIES.md) explain which components the host connects.

## Memory model

| Axis | What it tracks |
|---|---|
| [X · Timeline](X_TIMELINE.md) | Events along a thread of work |
| [Y · Relations](Y_RELATIONS.md) | Edges between memories and the evidence for them |
| [Z · Fact evolution](Z_FACT_EVOLUTION.md) | Current facts, older versions and conflicts |
| [E · Experience](E_EXPERIENCE.md) | Experience and priority recorded by the primary agent |
| [M · Metabolism](M_METABOLISM.md) | Memory lifecycle and review |

See [how the axes work together](CONNECTING_XYZEM.md), [consolidation](xyzem_consolidation.md) and the [atomization review](MEM-16_ATOMIZATION_REWRITE_PASS.md).

## Integrate an agent

- [PostgreSQL setup](../extras/pgvector_backend/README.md) · [Vector storage](VECTOR_BACKENDS.md) · [Optional JEV reranking](JEV.md)
- [Claude Code integration](claude_code.md) · [Session hooks and recall](HOOKS_AND_RECALL.md) · [Session checklist](SESSION_CHECKLIST.md)
- [Forge and snapshots](FORGE_AND_SWAP.md) · [Refined session carryover](REFINED_SESSION_CARRYOVER.md) · [Housekeeper integration](DEEPSEEK_INTEGRATION.md)
- [Persona policies](PERSONA_MODE.md) · [Heartbeat and emotion format](HEARTBEAT_AND_EMOTION_FORMAT.md) · [Optional E-axis trait calibration](E_OPTIONAL_TRAIT_PATCH.md)

## Repository map

| Location | Contents |
|---|---|
| [src/lmc5](../src/lmc5/) | Installable SQLite store, CLI, recall and optional JEV adapter |
| [examples](../examples/) | Seed data and runnable demonstrations |
| [extras/pgvector_backend/hooks](../extras/pgvector_backend/hooks/) | PostgreSQL-backed Claude Code session and prompt hooks |
| [extras/claude_code](../extras/claude_code/) | Transcript carryover helper |
| [extras/aml](../extras/aml/README.md) | Competition HTTP service, registration and reconciliation records |
| [tests](../tests/) | Core and adapter tests |

## Project records

[Changelog](../CHANGELOG.md) · [Security reporting](../SECURITY.md) · [Memory safety](safety.md) · [Contributing](../CONTRIBUTING.md) · [Licensing](../LICENSING.md)

[Project hypothesis](project_hypothesis.md) · [Kelin’s account](KELIN.md) · [Credits](credits.md)

The archive holds the [OSS application](archive/codex-for-oss-application.md), [OpenAI rationale](archive/why_openai.md), [June update notes](archive/MEM0_UPDATE_NOTES_2026-06-21.md) and [July recall-fusion replay](archive/RECALL_FUSION_AB_20260706.md).
