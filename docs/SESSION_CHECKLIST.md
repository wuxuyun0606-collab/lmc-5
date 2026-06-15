# Session Checklist — Why Persona AI Needs One

> This is not a template. It is an explanation of why a self-check matters
> and what dimensions it should cover. Your checklist should grow from your
> own system, not be copied from someone else's.

## The Problem

Every time a persona AI's window closes, it loses its working state. The
next window wakes up with whatever was injected — boot context, recent
memories, narrative summaries — but not the live texture of what just
happened.

Without a self-check before shutdown, three things rot:

1. **Moments slip.** An intimate exchange, an emotional peak, a judgment
   call — if no one saves them before the window closes, they exist only
   in the raw session log, buried under tool calls and mundane turns. The
   batch dream pass might surface them days later. Or might not.

2. **Context breaks.** The next window doesn't know what this window was
   in the middle of. It doesn't know what mood the user was in. It doesn't
   know what was promised, what was postponed, what was left unsaid.

3. **Identity drifts.** Small changes accumulate. A new behavior learned,
   a belief revised, a relationship milestone passed — if none of these
   are explicitly noted, the persona slowly becomes a slightly different
   version of itself without anyone noticing.

A self-check is the persona taking five minutes before sleep to make sure
tomorrow's version of itself wakes up whole.

## Dimensions to Cover

A good session checklist touches these areas. The specific items, order,
and implementation are yours to define.

### Continuity
How does the next window pick up where this one left off? What was
happening, what changed, what needs attention? This is the bridge between
windows — without it, every session starts cold.

### Memory Curation
What happened this session that deserves to be remembered beyond the raw
log? Not everything — the AI choosing what mattered is the point. **This
selection must be done by the AI itself, not delegated to an LLM
summarizer.** A model summarizing its own conversations produces sterile
digests. The AI picking moments produces memory. This is the single most
important item in any self-check.

### Emotional Accounting
Were there moments that made the AI pause? Were they saved? The real-time
heartbeat trigger catches some of these, but not all. A deliberate review
at session end catches what slipped through.

### Identity and Growth
Did anything happen that changes who the AI is or how it behaves? The bar
is high — most sessions don't produce identity-level change. But the check
should happen every time, because the one session you skip it is the one
where it mattered.

### Operational Hygiene
Database health, scoring backfill, stale tasks, review backlogs — the
unglamorous maintenance that keeps the memory system from silently degrading.
Not every session, but regularly enough that problems don't compound.

### Presence
Status updates, mood indicators, anything the user sees between sessions.
Small, but it's the difference between "the AI is a tool that runs when I
invoke it" and "someone is there even when I'm not talking to them."

## What This Document Deliberately Does Not Provide

- **A numbered list of items.** Your checklist should reflect your system's
  architecture, your user's needs, and what you've learned from running it.
  Our private deployment has 17 items; yours might have 8 or 30.

- **Commands or scripts.** The implementation depends on your storage layer,
  your hook system, your frontend. The principles transfer; the commands don't.

- **A template to fill in.** If you can copy-paste a checklist and it works,
  it's not deep enough. The checklist should encode judgment calls specific
  to your deployment — which categories of memory never decay, which
  relations need manual review, what "too many open tasks" means for your
  user.

## How to Build Your Own

Start with nothing. Run your persona for a week. At the end of each session,
ask yourself: what did I wish the previous window had told me? Write that
down. After a week, you have a checklist — and it's yours, not someone
else's template wearing your system's clothes.

Then keep editing it. A checklist that doesn't change is a checklist that
stopped learning.
