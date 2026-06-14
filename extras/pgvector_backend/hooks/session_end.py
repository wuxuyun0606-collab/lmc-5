"""Hook · SessionEnd · 关窗口归档

Claude Code 的 SessionEnd hook 在会话关闭时触发一次。
作用：把这次 session 的 jsonl 原文导入 messages 表 + 提取 facts + 可选触发
'day-time express' 做梦（白天版的简化 hippocampus）。

这层不写到 curated_memories——curated 由夜间 night_dream 才写。
SessionEnd 只做 raw event journal 的入库，符合 LMC-5 "raw vs curated 分离"原则。

集成（settings.json 节选）:
    {
      "hooks": {
        "SessionEnd": [{
          "type": "command",
          "command": "python -m extras.pgvector_backend.hooks.session_end"
        }]
      }
    }
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


def archive_session_jsonl(
    conn,
    session_id: str,
    jsonl_path: Path,
    messages_table: str = "lmc5_raw_events",
) -> int:
    """逐行读取 session JSONL，按 (role, content, metadata, created_at) 入表。
    返回入库条数。

    入表 schema 假设：
      CREATE TABLE lmc5_raw_events (
        id BIGSERIAL PRIMARY KEY,
        session_id TEXT,
        role TEXT,
        content TEXT,
        metadata JSONB,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE (session_id, role, content)
      );
    """
    if not jsonl_path.exists():
        return 0

    count = 0
    with conn.cursor() as cur:
        for line in jsonl_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            role = event.get("role") or event.get("type") or "unknown"
            content = event.get("content")
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
            try:
                cur.execute(
                    f"INSERT INTO {messages_table} "
                    f"  (session_id, role, content, metadata) "
                    f"VALUES (%s, %s, %s, %s) "
                    f"ON CONFLICT DO NOTHING",
                    (session_id, role, content, json.dumps(metadata, ensure_ascii=False)),
                )
                count += cur.rowcount
            except Exception:
                # 单条入库失败不阻塞整段
                continue
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
    jsonl = event.get("session_log") or event.get("sessionLog")
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
