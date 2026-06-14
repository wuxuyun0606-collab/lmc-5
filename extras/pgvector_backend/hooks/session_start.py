"""Hook · SessionStart · 开机注入 startup pack

Claude Code 的 SessionStart hook 在新会话开窗时触发一次。
作用：把"这个 agent 是谁、最近在做什么、当前事实有哪些、自发浮现什么"
打包成 additionalContext 注入。

参考实现 — 接 Claude Code hook 协议（stdin JSON event / stdout =
additionalContext）。其他 agent runtime 改 main() 里的 IO 即可，
build_startup_pack() 的逻辑是通用的。

集成（settings.json 节选）:
    {
      "hooks": {
        "SessionStart": [{
          "type": "command",
          "command": "python -m extras.pgvector_backend.hooks.session_start"
        }]
      }
    }
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


# === 数据装载（调用方替换为自己的实现）=====================================

def load_identity_records(conn) -> list[dict]:
    """所有 protected=true 的身份记忆 — 永远先到位"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, title, content, fact_key FROM lmc5_curated_memories "
            "WHERE protected = true AND category = 'identity' "
            "  AND version_status = 'current' "
            "ORDER BY weight DESC LIMIT 20"
        )
        return [
            {"id": r[0], "title": r[1], "content": r[2], "fact_key": r[3]}
            for r in cur.fetchall()
        ]


def load_current_facts(conn) -> list[dict]:
    """active_fact=true 的事实快照 — 当前是真的"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, title, content, fact_key, category FROM lmc5_curated_memories "
            "WHERE active_fact = true AND version_status = 'current' "
            "ORDER BY weight DESC LIMIT 30"
        )
        return [
            {"id": r[0], "title": r[1], "content": r[2],
             "fact_key": r[3], "category": r[4]}
            for r in cur.fetchall()
        ]


def load_recent_narrative(conn, days: int = 30) -> list[dict]:
    """最近的 narrative timeline — agent 知道 '上周发生了什么'"""
    cutoff = datetime.now() - timedelta(days=days)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT period, title, summary, start_date, end_date "
            "FROM lmc5_narrative_index "
            "WHERE start_date >= %s "
            "ORDER BY start_date DESC LIMIT 5",
            (cutoff.date(),),
        )
        return [
            {"period": r[0], "title": r[1], "summary": r[2],
             "start_date": str(r[3]), "end_date": str(r[4])}
            for r in cur.fetchall()
        ]


def load_open_threads(conn) -> list[dict]:
    """未完成的事——resolved=false 且 weight 高的"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, title, content FROM lmc5_curated_memories "
            "WHERE resolved = false AND weight >= 2.0 "
            "  AND version_status = 'current' "
            "  AND category IN ('worklog', 'tasks', 'fragments') "
            "ORDER BY weight DESC LIMIT 5"
        )
        return [
            {"id": r[0], "title": r[1], "content": r[2]}
            for r in cur.fetchall()
        ]


def load_perception_surface(cache_path: Path) -> list[dict]:
    """从 perception cache 读出本次该浮现什么"""
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


# === 打包 =================================================================

def build_startup_pack(
    identity: list[dict],
    facts: list[dict],
    narrative: list[dict],
    threads: list[dict],
    perception: list[dict],
    budget_chars: int = 6000,
) -> str:
    """合并成可注入 system prompt 的字符串。控制总长度。"""
    sections: list[str] = []

    if identity:
        lines = ["## Identity (永远生效)"]
        for r in identity:
            lines.append(f"- {r['title']}: {r['content'][:160]}")
        sections.append("\n".join(lines))

    if facts:
        lines = ["## 当前事实"]
        for r in facts:
            lines.append(f"- [{r.get('category','?')}] {r['title']}: {r['content'][:160]}")
        sections.append("\n".join(lines))

    if narrative:
        lines = ["## 最近叙事"]
        for r in narrative:
            lines.append(f"- [{r['period']} {r['start_date']}–{r['end_date']}] {r['title']}")
            lines.append(f"  {r['summary'][:300]}")
        sections.append("\n".join(lines))

    if threads:
        lines = ["## 未完成的事"]
        for r in threads:
            lines.append(f"- {r['title']}: {r['content'][:200]}")
        sections.append("\n".join(lines))

    if perception:
        lines = ["## 自发浮现（不是被问，是想到了）"]
        for r in perception:
            via = r.get("selected_via", "")
            lines.append(f"- [{via}] {r['title']}: {r['content'][:200]}")
        sections.append("\n".join(lines))

    text = "\n\n".join(sections)
    if len(text) > budget_chars:
        text = text[:budget_chars] + "\n... (truncated by budget_chars)"
    return text


# === Hook 入口（Claude Code 协议）==========================================

def main() -> int:
    """读 stdin 拿 event，写 stdout 拿到 additionalContext。

    需要的环境变量：
      LMC5_PG_DSN          数据库连接
      LMC5_PERCEPTION_CACHE 自发浮现 cache 路径
    """
    import os
    import logging
    logging.basicConfig(level=logging.WARNING)
    log = logging.getLogger("lmc5.hooks.session_start")

    try:
        # 不强依赖 stdin event 内容，但读掉避免 broken pipe
        _ = sys.stdin.read()
    except Exception:
        pass

    dsn = os.environ.get("LMC5_PG_DSN")
    if not dsn:
        log.error("LMC5_PG_DSN not set; emitting empty startup pack")
        sys.stdout.write("")
        return 0

    cache_path = Path(os.environ.get("LMC5_PERCEPTION_CACHE",
                                     "/tmp/lmc5_perception.json"))

    try:
        import psycopg2
        conn = psycopg2.connect(dsn)
    except Exception as e:
        log.error("PG connect failed: %s", e)
        sys.stdout.write("")
        return 0

    try:
        identity = load_identity_records(conn)
        facts = load_current_facts(conn)
        narrative = load_recent_narrative(conn)
        threads = load_open_threads(conn)
    except Exception as e:
        log.warning("startup data load partial-fail: %s", e)
        identity = facts = narrative = threads = []
    finally:
        conn.close()

    perception = load_perception_surface(cache_path)

    pack = build_startup_pack(identity, facts, narrative, threads, perception)
    sys.stdout.write(pack)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
