"""Command-line interface for LMC-5."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from .consolidation import consolidate_events
from .fact_evolution import run_z_audit
from .hippocampus import run_hippocampus
from .metabolism import patrol
from .models import RELATION_TYPES
from .redact import redact_obj
from .store import MemoryStore
from .vector import toy_embed


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def cmd_init(args: argparse.Namespace) -> None:
    with MemoryStore(args.db) as store:
        store.init()
    print(f"initialized {args.db}")


def cmd_add(args: argparse.Namespace) -> None:
    with MemoryStore(args.db) as store:
        store.init()
        record, created = store.add_memory(
            title=args.title,
            content=args.content,
            thread=args.thread,
            category=args.category,
            tags=args.tag,
            fact_key=args.fact_key,
            active_fact=not args.inactive_fact,
            status=args.status,
            risk_level=args.risk,
            urgency=args.urgency,
            response_tendency=args.response_tendency,
            valence=args.valence,
            arousal=args.arousal,
            tension=args.tension,
            confidence=args.confidence,
            growth_delta=args.growth_delta,
            source=args.source,
        )
    result = record.to_public_dict()
    result["created"] = created
    _print_json(redact_obj(result))


def cmd_relate(args: argparse.Namespace) -> None:
    with MemoryStore(args.db) as store:
        store.init()
        relation = store.add_relation(
            args.source_id,
            args.target_id,
            args.type,
            strength=args.strength,
            reason=args.reason,
        )
    _print_json(redact_obj(relation.__dict__))


def cmd_relations(args: argparse.Namespace) -> None:
    with MemoryStore(args.db) as store:
        store.init()
        rows = [relation.__dict__ for relation in store.list_relations(args.memory_id)]
    _print_json(redact_obj(rows))


def cmd_entities(args: argparse.Namespace) -> None:
    with MemoryStore(args.db) as store:
        store.init()
        rows = store.list_entities(memory_id=args.memory_id, limit=args.limit)
    _print_json(redact_obj(rows))


def _parse_json_arg(value: str, *, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


def cmd_log_event(args: argparse.Namespace) -> None:
    with MemoryStore(args.db) as store:
        store.init()
        event, created = store.log_event(
            role=args.role,
            content=args.content,
            channel=args.channel,
            metadata=_parse_json_arg(args.metadata, default={}),
            attachments=_parse_json_arg(args.attachments, default=[]),
        )
    result = event.to_public_dict()
    result["created"] = created
    _print_json(redact_obj(result))


def cmd_events(args: argparse.Namespace) -> None:
    with MemoryStore(args.db) as store:
        store.init()
        rows = [event.to_public_dict() for event in store.list_events(args.limit, channel=args.channel)]
    _print_json(redact_obj(rows))


def cmd_search_events(args: argparse.Namespace) -> None:
    with MemoryStore(args.db) as store:
        store.init()
        rows = store.search_events(
            args.query,
            limit=args.limit,
            channel=args.channel,
            redact=True,
        )
    _print_json(rows)


def cmd_surface(args: argparse.Namespace) -> None:
    with MemoryStore(args.db) as store:
        store.init()
        result = store.surface(
            args.query,
            limit=args.limit,
            event_limit=args.event_limit,
            memory_limit=args.memory_limit,
            state_limit=args.state_limit,
            include_state=not args.no_state,
            redact=True,
        )
    _print_json(result)


def cmd_state_refresh(args: argparse.Namespace) -> None:
    with MemoryStore(args.db) as store:
        store.init()
        result = store.refresh_current_state(
            ttl_hours=args.ttl_hours,
            fact_limit=args.fact_limit,
            thread_limit=args.thread_limit,
            event_limit=args.event_limit,
            audit_limit=args.audit_limit,
            source=args.source,
        )
    _print_json(result)


def cmd_state(args: argparse.Namespace) -> None:
    with MemoryStore(args.db) as store:
        store.init()
        rows = store.list_current_state(
            limit=args.limit,
            category=args.category,
            include_expired=args.include_expired,
            redact=True,
        )
    _print_json(rows)


def cmd_stats(args: argparse.Namespace) -> None:
    with MemoryStore(args.db) as store:
        store.init()
        result = store.stats()
    _print_json(result)


def _parse_vector_arg(value: str) -> list[float]:
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        raise ValueError("vector must be a JSON list")
    return [float(item) for item in parsed]


def cmd_vector_upsert(args: argparse.Namespace) -> None:
    with MemoryStore(args.db) as store:
        store.init()
        if args.toy_text:
            vector = toy_embed(args.toy_text, dimensions=args.dimensions)
            provider = args.provider or "local"
            model = args.model or f"toy-hash-{args.dimensions}"
        else:
            vector = _parse_vector_arg(args.vector)
            provider = args.provider
            model = args.model
            if not model:
                raise ValueError("--model is required when --vector is used")
        record = store.upsert_vector(
            owner_type=args.owner_type,
            owner_id=args.owner_id,
            vector=vector,
            provider=provider,
            model=model,
            input_type=args.input_type,
            content_hash=args.content_hash,
        )
    _print_json(record.to_public_dict())


def cmd_vector_search(args: argparse.Namespace) -> None:
    with MemoryStore(args.db) as store:
        store.init()
        if args.toy_text:
            vector = toy_embed(args.toy_text, dimensions=args.dimensions)
            provider = args.provider or "local"
            model = args.model or f"toy-hash-{args.dimensions}"
        else:
            vector = _parse_vector_arg(args.vector)
            provider = args.provider
            model = args.model
            if not model:
                raise ValueError("--model is required when --vector is used")
        rows = store.search_vectors(
            query_vector=vector,
            provider=provider,
            model=model,
            owner_type=args.owner_type,
            input_type=args.input_type,
            limit=args.limit,
            redact=True,
        )
    _print_json(rows)


def cmd_vectors(args: argparse.Namespace) -> None:
    with MemoryStore(args.db) as store:
        store.init()
        rows = [
            row.to_public_dict()
            for row in store.list_vectors(owner_type=args.owner_type, limit=args.limit)
        ]
    _print_json(rows)


def cmd_recall(args: argparse.Namespace) -> None:
    with MemoryStore(args.db) as store:
        store.init()
        rows = store.recall(
            args.query,
            limit=args.limit,
            redact=True,
            expand_relations=not args.no_relations,
            entity_boost=not args.no_entity_boost,
            temporal_boost=not args.no_temporal_boost,
            trace=not args.no_trace,
        )
    _print_json(rows)


def cmd_recall_traces(args: argparse.Namespace) -> None:
    with MemoryStore(args.db) as store:
        store.init()
        rows = store.list_recall_traces(
            limit=args.limit,
            memory_id=args.memory_id,
        )
    _print_json(redact_obj(rows))


def cmd_list(args: argparse.Namespace) -> None:
    with MemoryStore(args.db) as store:
        store.init()
        rows = [redact_obj(row.to_public_dict()) for row in store.list_recent(limit=args.limit)]
    _print_json(rows)


def cmd_patrol(args: argparse.Namespace) -> None:
    with MemoryStore(args.db) as store:
        store.init()
        suggestions = [suggestion.to_dict() for suggestion in patrol(store.conn)]
    _print_json(suggestions)


def cmd_consolidate(args: argparse.Namespace) -> None:
    with MemoryStore(args.db) as store:
        store.init()
        result = consolidate_events(
            store,
            window_size=args.window_size,
            channel=args.channel,
            max_events=args.max_events,
            create_observations=not args.no_observations,
        )
    _print_json(result.to_dict())


def cmd_hippocampus(args: argparse.Namespace) -> None:
    with MemoryStore(args.db) as store:
        store.init()
        if args.consolidate:
            consolidate_events(
                store,
                window_size=args.window_size,
                channel=args.channel,
                max_events=args.max_events,
                create_observations=False,
            )
        result = run_hippocampus(
            store,
            channel=args.channel,
            limit_chunks=args.limit_chunks,
            min_importance=args.min_importance,
            max_promote=args.max_promote,
            apply=args.apply,
            create_relations=not args.no_relations,
        )
    _print_json(result.to_dict())


def cmd_z_audit(args: argparse.Namespace) -> None:
    with MemoryStore(args.db) as store:
        store.init()
        result = run_z_audit(
            store,
            limit=args.limit,
            apply=args.apply,
            include_existing=args.include_existing,
        )
    _print_json(result.to_dict())


def cmd_doctor(args: argparse.Namespace) -> None:
    checks: list[dict[str, Any]] = []
    try:
        with MemoryStore(args.db) as store:
            store.init()
            store.conn.execute("SELECT count(*) FROM memories_fts").fetchone()
            memory_count = store.conn.execute("SELECT count(*) FROM memories").fetchone()[0]
            relation_count = store.conn.execute("SELECT count(*) FROM relations").fetchone()[0]
            event_count = store.conn.execute("SELECT count(*) FROM events").fetchone()[0]
            vector_count = store.conn.execute("SELECT count(*) FROM vectors").fetchone()[0]
        checks.append({"check": "sqlite", "ok": True, "version": sqlite3.sqlite_version})
        checks.append({"check": "fts5", "ok": True})
        checks.append({"check": "memory_count", "ok": True, "value": memory_count})
        checks.append({"check": "relation_count", "ok": True, "value": relation_count})
        checks.append({"check": "event_count", "ok": True, "value": event_count})
        checks.append({"check": "vector_count", "ok": True, "value": vector_count})
    except Exception as exc:
        checks.append({"check": "database", "ok": False, "error": str(exc)})
    _print_json(checks)


def cmd_import(args: argparse.Namespace) -> None:
    text = Path(args.file).read_text(encoding="utf-8")
    with MemoryStore(args.db) as store:
        store.init()
        created, reused = store.import_jsonl(text)
    _print_json({"created": created, "reused": reused})


def cmd_export(args: argparse.Namespace) -> None:
    with MemoryStore(args.db) as store:
        store.init()
        text = store.export_jsonl()
    print(text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LMC-5 reference memory CLI")
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--db", default="lmc5.sqlite", help="SQLite database path")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", parents=[parent], help="create database schema")
    p_init.set_defaults(func=cmd_init)

    p_add = sub.add_parser("add", parents=[parent], help="add a memory")
    p_add.add_argument("--title", required=True)
    p_add.add_argument("--content", required=True)
    p_add.add_argument("--thread", default="other")
    p_add.add_argument("--category", default="note")
    p_add.add_argument("--tag", action="append", default=[])
    p_add.add_argument("--fact-key")
    p_add.add_argument("--inactive-fact", action="store_true")
    p_add.add_argument("--status", default="current")
    p_add.add_argument("--risk", default="normal", choices=["normal", "medium", "high"])
    p_add.add_argument("--urgency", default="normal", choices=["low", "normal", "high"])
    p_add.add_argument("--response-tendency", default="")
    p_add.add_argument("--valence", type=float)
    p_add.add_argument("--arousal", type=float)
    p_add.add_argument("--tension", type=float)
    p_add.add_argument("--confidence", type=float)
    p_add.add_argument("--growth-delta", default="")
    p_add.add_argument("--source", default="")
    p_add.set_defaults(func=cmd_add)

    p_relate = sub.add_parser("relate", parents=[parent], help="connect two memories")
    p_relate.add_argument("source_id", type=int)
    p_relate.add_argument("target_id", type=int)
    p_relate.add_argument(
        "--type",
        required=True,
        choices=sorted(RELATION_TYPES | {"contradiction"}),
    )
    p_relate.add_argument("--strength", type=float, default=1.0)
    p_relate.add_argument("--reason", default="")
    p_relate.set_defaults(func=cmd_relate)

    p_relations = sub.add_parser("relations", parents=[parent], help="list memory relations")
    p_relations.add_argument("--memory-id", type=int)
    p_relations.set_defaults(func=cmd_relations)

    p_entities = sub.add_parser("entities", parents=[parent], help="list indexed memory entities")
    p_entities.add_argument("--memory-id", type=int)
    p_entities.add_argument("--limit", type=int, default=100)
    p_entities.set_defaults(func=cmd_entities)

    p_log_event = sub.add_parser("log-event", parents=[parent], help="append a raw event")
    p_log_event.add_argument("--role", required=True, choices=["system", "user", "assistant", "tool", "environment", "note"])
    p_log_event.add_argument("--content", required=True)
    p_log_event.add_argument("--channel", default="default")
    p_log_event.add_argument("--metadata", default="", help="JSON object")
    p_log_event.add_argument("--attachments", default="", help="JSON array")
    p_log_event.set_defaults(func=cmd_log_event)

    p_events = sub.add_parser("events", parents=[parent], help="list recent raw events")
    p_events.add_argument("--limit", type=int, default=20)
    p_events.add_argument("--channel")
    p_events.set_defaults(func=cmd_events)

    p_search_events = sub.add_parser("search-events", parents=[parent], help="search raw events")
    p_search_events.add_argument("query")
    p_search_events.add_argument("--limit", type=int, default=10)
    p_search_events.add_argument("--channel")
    p_search_events.set_defaults(func=cmd_search_events)

    p_surface = sub.add_parser("surface", parents=[parent], help="surface memories plus raw events")
    p_surface.add_argument("query")
    p_surface.add_argument("--limit", type=int, default=8)
    p_surface.add_argument("--event-limit", type=int)
    p_surface.add_argument("--memory-limit", type=int)
    p_surface.add_argument("--state-limit", type=int, default=4)
    p_surface.add_argument(
        "--no-state",
        action="store_true",
        help="omit the current-state pack from surface output",
    )
    p_surface.set_defaults(func=cmd_surface)

    p_state_refresh = sub.add_parser(
        "state-refresh",
        parents=[parent],
        help="rebuild the materialized current-state pack",
    )
    p_state_refresh.add_argument("--ttl-hours", type=int, default=24)
    p_state_refresh.add_argument("--fact-limit", type=int, default=20)
    p_state_refresh.add_argument("--thread-limit", type=int, default=8)
    p_state_refresh.add_argument("--event-limit", type=int, default=6)
    p_state_refresh.add_argument("--audit-limit", type=int, default=8)
    p_state_refresh.add_argument("--source", default="manual")
    p_state_refresh.set_defaults(func=cmd_state_refresh)

    p_state = sub.add_parser(
        "state",
        parents=[parent],
        help="list materialized current-state items",
    )
    p_state.add_argument("--limit", type=int, default=20)
    p_state.add_argument("--category")
    p_state.add_argument("--include-expired", action="store_true")
    p_state.set_defaults(func=cmd_state)

    p_stats = sub.add_parser(
        "stats",
        parents=[parent],
        help="show database counts and coverage",
    )
    p_stats.set_defaults(func=cmd_stats)

    p_vector_upsert = sub.add_parser("vector-upsert", parents=[parent], help="store a vector for a memory or event")
    p_vector_upsert.add_argument("--owner-type", required=True, choices=["memory", "event"])
    p_vector_upsert.add_argument("--owner-id", required=True, type=int)
    p_vector_upsert.add_argument("--provider", default="local")
    p_vector_upsert.add_argument("--model", default="")
    p_vector_upsert.add_argument("--input-type", default="document", choices=["query", "document", "unspecified"])
    p_vector_upsert.add_argument("--vector", default="", help="JSON list of floats")
    p_vector_upsert.add_argument("--toy-text", default="", help="offline demo embedding text")
    p_vector_upsert.add_argument("--dimensions", type=int, default=64)
    p_vector_upsert.add_argument("--content-hash")
    p_vector_upsert.set_defaults(func=cmd_vector_upsert)

    p_vector_search = sub.add_parser("vector-search", parents=[parent], help="search stored vectors")
    p_vector_search.add_argument("--provider", default="local")
    p_vector_search.add_argument("--model", default="")
    p_vector_search.add_argument("--owner-type", choices=["memory", "event"])
    p_vector_search.add_argument("--input-type", default="document", choices=["query", "document", "unspecified"])
    p_vector_search.add_argument("--vector", default="", help="JSON list of floats")
    p_vector_search.add_argument("--toy-text", default="", help="offline demo query text")
    p_vector_search.add_argument("--dimensions", type=int, default=64)
    p_vector_search.add_argument("--limit", type=int, default=10)
    p_vector_search.set_defaults(func=cmd_vector_search)

    p_vectors = sub.add_parser("vectors", parents=[parent], help="list stored vector metadata")
    p_vectors.add_argument("--owner-type", choices=["memory", "event"])
    p_vectors.add_argument("--limit", type=int, default=50)
    p_vectors.set_defaults(func=cmd_vectors)

    p_recall = sub.add_parser("recall", parents=[parent], help="recall memories by text query")
    p_recall.add_argument("query")
    p_recall.add_argument("--limit", type=int, default=5)
    p_recall.add_argument(
        "--no-relations",
        action="store_true",
        help="disable two-hop typed relation expansion",
    )
    p_recall.add_argument(
        "--no-entity-boost",
        action="store_true",
        help="disable entity-index boost and entity-only candidates",
    )
    p_recall.add_argument(
        "--no-temporal-boost",
        action="store_true",
        help="disable recent/current temporal ranking boost",
    )
    p_recall.add_argument(
        "--no-trace",
        action="store_true",
        help="skip writing recall explain trace rows",
    )
    p_recall.set_defaults(func=cmd_recall)

    p_recall_traces = sub.add_parser(
        "recall-traces",
        parents=[parent],
        help="list recent recall explain trace rows",
    )
    p_recall_traces.add_argument("--limit", type=int, default=20)
    p_recall_traces.add_argument("--memory-id", type=int)
    p_recall_traces.set_defaults(func=cmd_recall_traces)

    p_list = sub.add_parser("list", parents=[parent], help="list recent memories")
    p_list.add_argument("--limit", type=int, default=20)
    p_list.set_defaults(func=cmd_list)

    p_patrol = sub.add_parser("patrol", parents=[parent], help="run read-only metabolism checks")
    p_patrol.set_defaults(func=cmd_patrol)

    p_consolidate = sub.add_parser(
        "consolidate",
        parents=[parent],
        help="chunk raw events into reviewable awareness-layer observations",
    )
    p_consolidate.add_argument("--window-size", type=int, default=20)
    p_consolidate.add_argument("--channel")
    p_consolidate.add_argument("--max-events", type=int, default=500)
    p_consolidate.add_argument(
        "--no-observations",
        action="store_true",
        help="create event_chunks only, without candidate observation memories",
    )
    p_consolidate.set_defaults(func=cmd_consolidate)

    p_hippocampus = sub.add_parser(
        "hippocampus",
        parents=[parent],
        help="preview or apply a gated chunk-to-memory hippocampus pass",
    )
    p_hippocampus.add_argument("--channel")
    p_hippocampus.add_argument("--limit-chunks", type=int, default=50)
    p_hippocampus.add_argument("--min-importance", type=int, default=7)
    p_hippocampus.add_argument("--max-promote", type=int, default=10)
    p_hippocampus.add_argument(
        "--apply",
        action="store_true",
        help="write accepted candidates and safe relations; default is dry-run",
    )
    p_hippocampus.add_argument(
        "--no-relations",
        action="store_true",
        help="skip relation planning/application",
    )
    p_hippocampus.add_argument(
        "--consolidate",
        action="store_true",
        help="first create event chunks from unconsolidated raw events",
    )
    p_hippocampus.add_argument("--window-size", type=int, default=20)
    p_hippocampus.add_argument("--max-events", type=int, default=500)
    p_hippocampus.set_defaults(func=cmd_hippocampus)

    p_z_audit = sub.add_parser(
        "z-audit",
        parents=[parent],
        help="preview or record pending Z-axis conflict audits",
    )
    p_z_audit.add_argument("--limit", type=int, default=100)
    p_z_audit.add_argument(
        "--apply",
        action="store_true",
        help="write pending audit rows; default is dry-run",
    )
    p_z_audit.add_argument(
        "--include-existing",
        action="store_true",
        help="include pairs that already have an audit row",
    )
    p_z_audit.set_defaults(func=cmd_z_audit)

    p_doctor = sub.add_parser("doctor", parents=[parent], help="check local database capabilities")
    p_doctor.set_defaults(func=cmd_doctor)

    p_import = sub.add_parser("import-jsonl", parents=[parent], help="import memories from JSONL")
    p_import.add_argument("file")
    p_import.set_defaults(func=cmd_import)

    p_export = sub.add_parser("export-jsonl", parents=[parent], help="export memories as JSONL")
    p_export.set_defaults(func=cmd_export)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
