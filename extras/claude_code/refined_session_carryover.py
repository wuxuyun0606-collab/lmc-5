"""Refined Session Carryover for Claude Code transcript resume.

This module implements the deterministic version of "精炼续窗":

- keep a short natural dialogue tail for conversational continuity
- keep high-signal relationship/preference/state messages
- drop tool output, stack traces, hook injections, long JSON, paths, and logs
- fail closed when recent context looks policy/AUP poisoned

It is deliberately not a memory writer. Durable memory should still flow
through LMC-5's event journal, consolidation, hippocampus, Z, and M paths.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


KEEP_TYPES = {"user", "assistant"}

MEMORY_RE = re.compile(
    r"(remember|don't forget|preference|likes?|dislikes?|afraid|tired|crying|sad|"
    r"relationship|boundary|nickname|identity|promise|next time|continuity|memory|"
    r"记得|别忘|偏好|喜欢|讨厌|害怕|难过|委屈|开心|累|哭|关系|边界|称呼|身份|"
    r"承诺|以后|连续性|记忆)",
    re.I,
)

STATE_RE = re.compile(
    r"(current task|next step|risk|done|todo|checkpoint|blocked|assumption|"
    r"当前任务|下一步|风险|已完成|待办|检查点|阻塞|假设)",
    re.I,
)

NOISE_RE = re.compile(
    r"(Traceback|Exception|Exit code|Chunk ID|Wall time|stdout|stderr|apply_patch|"
    r"pytest|npm |pnpm |yarn |curl |ssh |tmux |systemctl|journalctl|"
    r"SELECT |INSERT |UPDATE |DELETE |CREATE TABLE|"
    r"/Users/|/root/|/opt/|\.py\b|\.sh\b|\.jsonl\b|\.sqlite\b|\.db\b|"
    r"tool_result|tool_use|<function_calls>|```|^\s*\{)",
    re.I | re.M,
)

HOOK_RE = re.compile(
    r"(hook injection|UserPromptSubmit|SessionStart|additional context|recall result|"
    r"memory recall|召回结果|记忆召回|注入块)",
    re.I,
)

INJECTION_BLOCK_RE = re.compile(
    r"</?(?:task-notification|system-reminder)\b",
    re.I,
)

POISON_RE = re.compile(
    r"(AUP|Acceptable Use|policy violation|policy blocked|unsafe content|refusal loop|"
    r"I can't assist|I'm sorry, I can't|风控|安全策略|毒上下文|中毒|拒绝循环|我不能帮助)",
    re.I,
)


@dataclass
class Candidate:
    index: int
    event: dict
    reason: str
    priority: int
    token_estimate: int


@dataclass
class CarryoverStats:
    source_dialogue_events: int
    clean_candidates: int
    selected_gold: int
    selected_state: int
    selected_tail: int
    dropped_for_budget: int
    poison_score: int


def estimate_tokens(value: object) -> int:
    return max(1, len(json.dumps(value, ensure_ascii=False)) // 3)


def load_jsonl(path: Path) -> List[dict]:
    events: List[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def write_jsonl(path: Path, events: Sequence[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")


def content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    return ""


def event_text(event: dict) -> str:
    message = event.get("message")
    if not isinstance(message, dict):
        return ""
    return content_text(message.get("content", ""))


def is_injection_block(event: dict) -> bool:
    """Return True for exact runtime-injection wrappers that must never carry over."""
    return bool(INJECTION_BLOCK_RE.search(event_text(event)))


def synthetic_user_prefix(template: dict) -> dict:
    """Build a minimal user event without discarding assistant-first carryover."""
    prefix = copy.deepcopy(template)
    prefix["type"] = "user"
    prefix["userType"] = "external"
    prefix["isMeta"] = False
    prefix["isSidechain"] = False
    prefix["message"] = {
        "role": "user",
        "content": "[refined-carryover: preserved high-signal context follows]",
    }
    for key in ("requestId", "isApiErrorMessage", "error", "durationMs", "usage", "costUSD"):
        prefix.pop(key, None)
    return prefix


def ensure_user_first(events: Sequence[dict]) -> List[dict]:
    """Prefix a format sentinel when selected history starts with an assistant."""
    selected = list(events)
    if not selected or selected[0].get("type") == "user":
        return selected
    return [synthetic_user_prefix(selected[0]), *selected]


def compact_text(text: str, max_chars: int) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    if len(text) <= max_chars:
        return text
    head = text[: max_chars // 2].rstrip()
    tail = text[-max_chars // 2 :].lstrip()
    return head + "\n\n[refined-carryover: long low-value middle omitted]\n\n" + tail


def sanitize_event(event: dict, max_chars: int) -> Optional[dict]:
    text = compact_text(event_text(event), max_chars=max_chars)
    if not text:
        return None
    clean = copy.deepcopy(event)
    message = clean.get("message")
    if not isinstance(message, dict):
        return None
    if isinstance(message.get("content"), list):
        message["content"] = [{"type": "text", "text": text}]
    else:
        message["content"] = text
    clean["message"] = message
    return clean


def is_dialogue_event(event: dict) -> bool:
    if event.get("type") not in KEEP_TYPES:
        return False
    if event.get("isMeta") or event.get("isSidechain"):
        return False
    message = event.get("message")
    if not isinstance(message, dict):
        return False
    content = message.get("content", "")
    if isinstance(content, list):
        block_types = {item.get("type") for item in content if isinstance(item, dict)}
        if block_types and block_types <= {"tool_result", "tool_use"}:
            return False
    return bool(event_text(event).strip())


def classify_event(index: int, event: dict, max_chars: int) -> Optional[Candidate]:
    if not is_dialogue_event(event):
        return None
    if is_injection_block(event):
        return None
    text = event_text(event)
    memory_hits = len(MEMORY_RE.findall(text))
    state_hits = len(STATE_RE.findall(text))
    noise_hits = len(NOISE_RE.findall(text))
    hook_hits = len(HOOK_RE.findall(text))

    if hook_hits >= 2 and memory_hits == 0:
        return None
    if len(text) > 9000 and memory_hits < 2:
        return None
    if noise_hits >= 6 and memory_hits == 0:
        return None
    if noise_hits >= 10 and memory_hits < 3:
        return None
    if "```" in text and len(text) > 1800 and memory_hits < 2:
        return None

    clean = sanitize_event(event, max_chars=max_chars)
    if clean is None:
        return None

    if memory_hits >= 2 and noise_hits <= max(4, memory_hits + 2):
        return Candidate(index, clean, "gold-memory", 90 + memory_hits * 3 - noise_hits, estimate_tokens(clean))
    if memory_hits >= 1 and state_hits >= 1 and len(text) <= 2800 and noise_hits <= 4:
        return Candidate(index, clean, "state-note", 70 + memory_hits + state_hits, estimate_tokens(clean))
    if state_hits >= 2 and len(text) <= 1600 and noise_hits <= 2:
        return Candidate(index, clean, "task-checkpoint", 55 + state_hits, estimate_tokens(clean))
    if len(text) <= 1800 and noise_hits <= 2 and hook_hits == 0:
        return Candidate(index, clean, "natural-tail", 35 + memory_hits + state_hits, estimate_tokens(clean))
    return None


def recent_poison_score(events: Sequence[dict], window: int = 30) -> int:
    recent = [event_text(event) for event in events if event.get("type") in KEEP_TYPES][-window:]
    return len(POISON_RE.findall("\n".join(recent)))


def select_refined_events(
    events: Sequence[dict],
    target_tokens: int = 50_000,
    tail_events: int = 14,
    max_event_chars: int = 3600,
) -> Tuple[List[dict], CarryoverStats]:
    poison = recent_poison_score(events)
    candidates = [
        candidate
        for index, event in enumerate(events)
        for candidate in [classify_event(index, event, max_chars=max_event_chars)]
        if candidate is not None
    ]

    selected: Dict[int, Candidate] = {}
    gold = [candidate for candidate in candidates if candidate.reason == "gold-memory"]
    state = [candidate for candidate in candidates if candidate.reason in {"state-note", "task-checkpoint"}]
    tail = candidates[-tail_events:]

    for candidate in sorted(gold, key=lambda c: c.priority, reverse=True)[:80]:
        selected[candidate.index] = candidate
    for candidate in sorted(state, key=lambda c: c.priority, reverse=True)[:20]:
        selected[candidate.index] = candidate
    for candidate in tail:
        selected[candidate.index] = candidate

    selected_list = sorted(selected.values(), key=lambda c: c.index)
    total = sum(candidate.token_estimate for candidate in selected_list)
    dropped = 0
    if total > target_tokens:
        tail_ids = {candidate.index for candidate in tail}
        removable = sorted(
            [candidate for candidate in selected_list if candidate.index not in tail_ids],
            key=lambda c: (c.priority, -c.index),
        )
        remove_ids = set()
        for candidate in removable:
            if total <= target_tokens:
                break
            remove_ids.add(candidate.index)
            total -= candidate.token_estimate
        dropped = len(remove_ids)
        selected_list = [candidate for candidate in selected_list if candidate.index not in remove_ids]

    stats = CarryoverStats(
        source_dialogue_events=sum(1 for event in events if event.get("type") in KEEP_TYPES),
        clean_candidates=len(candidates),
        selected_gold=sum(1 for candidate in selected_list if candidate.reason == "gold-memory"),
        selected_state=sum(1 for candidate in selected_list if candidate.reason in {"state-note", "task-checkpoint"}),
        selected_tail=sum(1 for candidate in tail if candidate.index in {item.index for item in selected_list}),
        dropped_for_budget=dropped,
        poison_score=poison,
    )
    return ensure_user_first([candidate.event for candidate in selected_list]), stats


def rewrite_session(events: Sequence[dict], new_session_id: str) -> List[dict]:
    rewritten: List[dict] = []
    previous_uuid: Optional[str] = None
    for event in events:
        clean = copy.deepcopy(event)
        event_uuid = str(uuid.uuid4())
        clean["sessionId"] = new_session_id
        clean["uuid"] = event_uuid
        clean["parentUuid"] = previous_uuid
        previous_uuid = event_uuid
        rewritten.append(clean)
    return rewritten


def latest_transcript(project_dir: Path) -> Optional[Path]:
    jsonls = sorted(project_dir.glob("*.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)
    return jsonls[0] if jsonls else None


def create_refined_session(
    source_path: Path,
    target_dir: Optional[Path] = None,
    fail_on_poison: bool = True,
    target_tokens: int = 50_000,
    tail_events: int = 14,
    max_event_chars: int = 3600,
) -> Tuple[Optional[str], Optional[Path], CarryoverStats]:
    events = load_jsonl(source_path)
    selected, stats = select_refined_events(
        events,
        target_tokens=target_tokens,
        tail_events=tail_events,
        max_event_chars=max_event_chars,
    )
    if fail_on_poison and stats.poison_score >= 2:
        return None, None, stats
    if not selected:
        return None, None, stats
    new_session_id = str(uuid.uuid4())
    out_dir = target_dir or source_path.parent
    out_path = out_dir / (new_session_id + ".jsonl")
    write_jsonl(out_path, rewrite_session(selected, new_session_id))
    return new_session_id, out_path, stats


def _print_stats(stats: CarryoverStats) -> None:
    print(
        "refined carryover stats: "
        f"source={stats.source_dialogue_events} "
        f"clean={stats.clean_candidates} "
        f"gold={stats.selected_gold} "
        f"state={stats.selected_state} "
        f"tail={stats.selected_tail} "
        f"drop={stats.dropped_for_budget} "
        f"poison={stats.poison_score}"
    )


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a refined Claude Code resume transcript.")
    parser.add_argument("--project-dir", type=Path, help="Claude Code project transcript directory")
    parser.add_argument("--source", type=Path, help="explicit source JSONL transcript")
    parser.add_argument("--target-dir", type=Path, help="where to write the new JSONL; defaults to source dir")
    parser.add_argument("--target-tokens", type=int, default=int(os.getenv("LMC5_REFINED_TARGET_TOKENS", "50000")))
    parser.add_argument("--tail-events", type=int, default=int(os.getenv("LMC5_REFINED_TAIL_EVENTS", "14")))
    parser.add_argument("--max-event-chars", type=int, default=int(os.getenv("LMC5_REFINED_MAX_EVENT_CHARS", "3600")))
    parser.add_argument("--allow-poison", action="store_true", help="do not fail closed on recent AUP/policy poison")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    source = args.source
    if source is None:
        if args.project_dir is None:
            print("provide --source or --project-dir", file=sys.stderr)
            return 2
        source = latest_transcript(args.project_dir)
    if source is None:
        print("no transcript found", file=sys.stderr)
        return 1

    events = load_jsonl(source)
    selected, stats = select_refined_events(
        events,
        target_tokens=args.target_tokens,
        tail_events=args.tail_events,
        max_event_chars=args.max_event_chars,
    )
    _print_stats(stats)

    if stats.poison_score >= 2 and not args.allow_poison:
        print("refused: recent context looks policy/AUP poisoned; start a fresh window instead", file=sys.stderr)
        return 1
    if not selected:
        print("refused: no clean carryover events selected", file=sys.stderr)
        return 1
    if args.dry_run:
        print(f"dry-run: would keep {len(selected)} events, approx {estimate_tokens(selected)} tokens")
        return 0

    new_session_id = str(uuid.uuid4())
    out_dir = args.target_dir or source.parent
    out_path = out_dir / (new_session_id + ".jsonl")
    write_jsonl(out_path, rewrite_session(selected, new_session_id))
    print(f"new session: {new_session_id}")
    print(f"new file: {out_path}")
    print(f"resume: claude --resume {new_session_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
