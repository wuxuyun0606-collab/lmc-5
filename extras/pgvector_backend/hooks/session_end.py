"""Hook · SessionEnd · 关窗口归档

Claude Code 的 SessionEnd hook 在会话关闭时触发一次。
作用：把 session JSONL 中的文本消息归档到 lmc5_raw_events。

这层不写到 curated_memories——curated 由夜间 night_dream 才写。
SessionEnd 只做 raw event journal 的入库，符合 LMC-5 "raw vs curated 分离"原则。

配置示例见 docs/HOOKS_AND_RECALL.md。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _event_time(value):
    """Read an absolute transcript timestamp. Untimed events use their ingestion time."""
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            seconds = value / 1000 if abs(value) >= 100_000_000_000 else value
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return parsed
    except (ValueError, OverflowError, OSError):
        pass
    return None


def archive_session_jsonl(
    conn,
    session_id: str,
    jsonl_path: Path,
    messages_table: str = "lmc5_raw_events",
) -> int:
    """Archive turns idempotently, retaining event time and ingestion time separately."""
    if not jsonl_path.exists():
        return 0

    count = 0
    with conn.cursor() as cur:
        for line_number, line in enumerate(jsonl_path.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            nested = event.get("message")
            message = event
            if "content" not in event and isinstance(nested, dict):
                message = nested
            role = message.get("role") or event.get("role") or event.get("type") or "unknown"
            content = message.get("content")
            if isinstance(content, list):
                # Claude Code multimodal content blocks → 拼成文本
                content = "\n".join(
                    b.get("text", "") for b in content if isinstance(b, dict)
                )
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False) if content else ""
            if not content.strip():
                continue

            metadata = {k: v for k, v in event.items()
                        if k not in ("role", "type", "content")}
            if message is not event:
                metadata["message"] = {k: v for k, v in message.items() if k != "content"}
            event_id = event.get("uuid") or event.get("event_id")
            if event_id:
                key = f"id:{event_id}"
            else:
                # Stable across replays of an append-only transcript; repeated lines
                # have distinct positions. The digest distinguishes rotated files.
                digest = hashlib.sha256(line.encode("utf-8")).hexdigest()
                key = f"line:{line_number}:{digest}"

            timestamp = _event_time(event.get("timestamp", message.get("timestamp")))
            metadata_json = json.dumps(metadata, ensure_ascii=False)
            # Adopt a pre-migration row on replay without changing its original text.
            cur.execute(
                f"UPDATE {messages_table} SET event_key=%s, created_at=COALESCE(%s, created_at) "
                "WHERE event_key IS NULL AND session_id=%s AND role=%s AND content=%s "
                "AND metadata=%s::jsonb",
                (key, timestamp, session_id, role, content, metadata_json),
            )
            cur.execute(
                f"INSERT INTO {messages_table} "
                "(session_id, role, content, metadata, event_key, created_at) "
                "VALUES (%s, %s, %s, %s, %s, COALESCE(%s, CURRENT_TIMESTAMP)) "
                "ON CONFLICT (session_id, event_key) DO NOTHING",
                (session_id, role, content, metadata_json, key, timestamp),
            )
            count += cur.rowcount
    conn.commit()
    return count


def trigger_express_dream(conn, session_id: str) -> Optional[dict]:
    """白天版的简化 hippocampus：跑当前 session 的 chunk → propose → gate → 写候选。

    与夜间 night_dream 的差别：
    - 只看本 session（不跨日）
    - importance_threshold 调高（默认 8，宁缺毋滥）
    - 不做关系图扩展（留给夜间处理）

    这一步可选——很多部署只在夜间集中处理。返回 None 表示未启用。
    """
    if os.environ.get("LMC5_EXPRESS_DREAM", "").strip().lower() not in ("1", "true", "on"):
        return None

    try:
        from .. import night_dream
    except ImportError:
        return None

    # 实际部署时这里要装配 chunks loader 和 proposer——示例略
    return {"status": "skipped", "reason": "express dream wiring is deployment-specific"}


# === Hook 入口 ============================================================

def extract_session_info(event: dict) -> tuple[Optional[str], Optional[Path]]:
    """从 hook event 里提取 session_id 和 jsonl 路径。"""
    sid = event.get("session_id") or event.get("sessionId")
    jsonl = event.get("transcript_path") or event.get("session_log") or event.get("sessionLog")
    return (
        sid if isinstance(sid, str) else None,
        Path(jsonl) if isinstance(jsonl, str) else None,
    )


def main() -> int:
    import logging
    logging.basicConfig(level=logging.WARNING)
    log = logging.getLogger("lmc5.hooks.session_end")

    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        event = {}

    dsn = os.environ.get("LMC5_PG_DSN")
    if not dsn:
        log.error("LMC5_PG_DSN not set; cannot archive session")
        return 0

    session_id, jsonl_path = extract_session_info(event)
    if not session_id or not jsonl_path:
        log.warning("session_id / jsonl_path missing in event; skipping archive")
        return 0

    try:
        import psycopg2
        conn = psycopg2.connect(dsn)
    except Exception as e:
        log.error("PG connect failed: %s", e)
        return 0

    try:
        archived = archive_session_jsonl(conn, session_id, jsonl_path)
        log.info("session %s archived %d events", session_id, archived)
        express = trigger_express_dream(conn, session_id)
        if express:
            log.info("express dream result: %s", express)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
