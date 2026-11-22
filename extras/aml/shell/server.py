"""Minimal AML memory-api-v1.1 shell around the LMC-5 minimal SQLite store.

- Listens on 127.0.0.1 only.
- POST /v1/memories/add     -> returns 200 only after rows are committed and searchable.
- POST /v1/memories/search  -> returns {"data": [...]} with at most top_k items.
- GET  /health

Storage: one LMC-5 SQLite file per user_id (isolation by user_id).
Write path: lmc5.MemoryStore.add_memory (raw text, FTS + entity index)
            + optional upsert_vector (text-embedding-v4 via DashScope, if key present).
Search path: lmc5.MemoryStore.recall (FTS/LIKE + entity + relation channels)
             + optional search_vectors; channels fused with RRF k=60, the same
             default fusion LMC-5 uses in extras/pgvector_backend/recall_pipeline.py.
No LLM is called on Add or Search.

Secrets are read from environment only and never logged.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aml_paths import STATE_DIR, SECRETS_DIR, PAID_INSTANCE_LOCK

from lmc5 import MemoryStore

import rerank  # work order 2; AML_RERANK=off and no AML_RETURN_N -> untouched path

ROOT = Path(os.environ.get("AML_TRIAL_DATA", str(STATE_DIR / "data/instance")))
DB_DIR = ROOT / "db"
DB_DIR.mkdir(parents=True, exist_ok=True)
IDEM_PATH = ROOT / "idempotency.sqlite"
LOG_PATH = Path(os.environ.get("AML_TRIAL_LOG", str(STATE_DIR / "logs/server_timing.jsonl")))
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

# EMBED_PROVIDER=dashscope (compliant: text-embedding-v4) | gemini (NON-COMPLIANT reference only)
EMBED_PROVIDER = os.environ.get("EMBED_PROVIDER", "dashscope")
if EMBED_PROVIDER == "gemini":
    EMBED_KEY_ENV = "GEMINI_API_KEY1"
    EMBED_MODEL = "gemini-embedding-2"
else:
    EMBED_KEY_ENV = "DASHSCOPE_API_KEY"
    EMBED_MODEL = "text-embedding-v4"
EMBED_BASE = os.environ.get("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
EMBED_DIM = int(os.environ.get("EMBED_DIM", "1024"))
EMBED_ENABLED = bool(os.environ.get(EMBED_KEY_ENV)) and os.environ.get("AML_NO_EMBED") != "1"
RRF_K = 60

# Time-order presentation (temporal knife, 2026-10-14). Selection (RRF + rerank truncation)
# is unchanged; only the display order of the returned set changes: chronological old->new by
# default, newest-first when the query asks about the current/latest state ("as of" intent).
# AML_TIME_ORDER=off restores pure score order.
TIME_ORDER = os.environ.get("AML_TIME_ORDER", "on")

# Public deployment (2026-10-16): unset -> legacy behavior (127.0.0.1, no auth).
# AML_API_KEY set -> POST /v1/* requires Authorization: Bearer <key> or X-Api-Key: <key>;
# GET /health stays open (platform smoke requirement). AML_BIND=0.0.0.0 for public exposure.
API_KEY = os.environ.get("AML_API_KEY", "")

# Rerank pool width (2026-10-16, exam serving): the sims always fed the reranker the RRF top-100
# regardless of the requested top_k (J50 "pool 100 -> jev -> top 50"). AML_RERANK_POOL=100 makes the
# serving path do the same: the pool slice becomes max(top_k, POOL) when a reranker is active; the
# reranker still truncates its output to min(RETURN_N, top_k). Unset/0 -> legacy top_k slice.
RERANK_POOL = int(os.environ.get("AML_RERANK_POOL", "0") or "0")

# Max accepted POST body (bytes), FIXED — deliberately not env-tunable, so no inherited variable can
# widen it (exam profile). Official Add batches cap at 20 messages / 2000 words, far below
# this; the limit only exists so an authenticated client cannot exhaust memory with one request.
MAX_BODY_BYTES = 8_000_000
ASOF_RE = re.compile(
    r"(?i)\b(now|nowadays|current(?:ly)?|latest|most recent(?:ly)?|right now|as of|"
    r"these days|still|anymore|at the moment|at present|presently)\b"
    r"|现在|目前|最新|当前|如今|眼下")

# Neighbor turns (NT knife, 2026-10-15). After the final return set is fixed (RRF + rerank
# truncation) and BEFORE the time-order block, each hit is merged with its stored predecessor
# and successor row (LMC-5 inserts in Add order, so adjacent ids = adjacent turns) into ONE
# data[] item: previous turn line / hit / next turn line, each keeping its own "[time]" prefix.
# No extra items, so the return count and the selected hits are unchanged. A neighbor is skipped
# when it lies in another session (session_id from source "iso|session_id"), when it is itself a
# returned hit, or when an earlier hit (in score order) already carries it.
# NEIGHBOR_TRUNC: neighbor cut length (hit text is never cut); see the r3 note below for its threshold.
# (r1 history: default was 0, the 116-set estimate being 2.08x, under the work order's 2.5x cut line.)
# AML_NEIGHBOR_TURNS=off restores the pre-NT response byte for byte.
# History: r1 (no gate) and r2 (MAXLEN=500) were left default "off" (acceptance not met).
# r3 (MAXLEN=500 + TRUNC=200, i.e. neighbors > 500 chars cut to 200) met all three acceptance lines on the
# nt_r1 49-question regression (LoCoMo multi-hop +3/27, must-do 6 = C0, guards -0.38 vs C0), so the default
# is "on" since 2026-10-15 (nt_r1/NT_R1_REPORT.md section 11). AML_NEIGHBOR_TURNS=off rolls back byte for byte.
NEIGHBOR_TURNS = os.environ.get("AML_NEIGHBOR_TURNS", "on")
# r3 (2026-10-15): default 200 together with MAXLEN=500 = a neighbor longer than 500 chars is cut to 200
# (ending in "…", so the cut is visible); neighbors <= 500 are kept whole. 0 = no cut (= r2 behaviour).
NEIGHBOR_TRUNC = int(os.environ.get("AML_NEIGHBOR_TRUNC", "200"))
# NT r2 (2026-10-15): only SHORT hits get neighbors. A hit whose stored content is longer than
# NEIGHBOR_MAXLEN characters is returned untouched (r1 showed long BEAM/PM items diluted / instructions
# washed out). 0 = no limit (= r1 behaviour).
NEIGHBOR_MAXLEN = int(os.environ.get("AML_NEIGHBOR_MAXLEN", "500"))

# Abstain floor (H knife, 2026-10-15, data/round3/hknife_r1/HKNIFE_R1_REPORT.md). Fixed stacking order in do_search:
#   1) NEIGHBOR merge (content only)  ->  2) TIME_ORDER sort (order only)  ->  3) ABSTAIN trim (this, LAST).
# If the best returned score (top1) is below AML_ABSTAIN_FLOOR, only the AML_ABSTAIN_MIN_KEEP highest-scoring items
# are kept (0 = empty data[]), in their time-ordered positions, so the platform template's "ONLY based on the
# provided context" can take effect. The floor is on the rerank 0..3 scale (Clef-flash expected relevance), so the
# gate only fires when this search was actually reranked (rerank meta mode == "rerank"); on fallback / off paths the
# scores are RRF (~0.03) and the gate is skipped. Default AML_ABSTAIN_FLOOR=off: response byte-identical to pre-knife.
# Rollback: unset AML_ABSTAIN_FLOOR (or =off); full revert: cp server.py.bak-hknife server.py.
ABSTAIN_FLOOR = os.environ.get("AML_ABSTAIN_FLOOR", "off").strip().lower() or "off"
ABSTAIN_FLOOR_VAL = None if ABSTAIN_FLOOR == "off" else float(ABSTAIN_FLOOR)
ABSTAIN_MIN_KEEP = int(os.environ.get("AML_ABSTAIN_MIN_KEEP", "0"))
if ABSTAIN_FLOOR_VAL is not None and not (0 <= ABSTAIN_FLOOR_VAL <= 3 and ABSTAIN_MIN_KEEP >= 0):
    raise SystemExit("AML_ABSTAIN_FLOOR must be off or a number in 0..3; AML_ABSTAIN_MIN_KEEP must be >= 0")

# Multi-hop sub-query bypass (MH-1, 2026-10-16; pre-registration data/round3/mh1/PREREG.md, report MH1_REPORT.md).
# Runs right after the original path (RRF + rerank truncation) and BEFORE the neighbor / time-order / abstain steps.
# gpt-4o-mini (Cloudflare) splits the query into 2-4 sub-queries; each goes through the same RRF (lexical recall +
# vector, k=60, no rerank) and contributes its RRF top-MH_PER_SUB. Return set (same count n): original top-MH_KEEP in
# score order first, then sub-query hits round-robin by rank (deduped), padded with original #MH_KEEP+1.. if short.
# Any failure (no token, HTTP error, > AML_MH_TIMEOUT s, bad JSON, < 2 valid sub-queries, retrieval error) -> the
# whole bypass is dropped and the original path result is used unchanged. Default AML_MH=off: response byte-identical
# to pre-MH (backup server.py.bak-pre-mh1-20261016). Sub-query hits carry their RRF score (not the 0..3 rerank scale).
MH = os.environ.get("AML_MH", "off").strip().lower() or "off"
MH_TIMEOUT = float(os.environ.get("AML_MH_TIMEOUT", "20"))
MH_TOKEN_PATH = Path(os.environ.get("AML_MH_TOKEN_PATH", str(SECRETS_DIR / ".cloudflare_token")))
MH_URL = ("https://api.cloudflare.com/client/v4/accounts/" + os.environ.get("AML_CF_ACCOUNT_ID", "")
          + "/ai/v1/chat/completions")
MH_MODEL = "openai/gpt-4o-mini"
MH_MAX_TOKENS = 300
MH_KEEP = 15
MH_FILL = 15
MH_PER_SUB = 5
MH_PROMPT = (
    "Rewrite the memory-search question below as 2 to 4 shorter sub-queries. Each sub-query asks for ONE piece of "
    "information the question needs, so that searching for each sub-query separately finds all the pieces.\n\n"
    "Rules:\n"
    "- Every sub-query is a rewording of a part of the original question. Use only words, names and things that "
    "appear in the original question.\n"
    "- Do NOT add any date, time or time-period words (dates, years, months, weekdays, \"when\", \"recently\", "
    "\"last week\", \"before\", \"after\", ...) that are not in the original question.\n"
    "- Do NOT add any person, place, product, number or other entity that is not in the original question.\n"
    "- Do not answer the question.\n\n"
    "Return only JSON: {\"subqueries\": [\"...\", \"...\"]}\n\n"
    "Question: {query}")
MH_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'\-]*")
MH_TIME_RE = re.compile(
    r"(?i)^(?:(?:19|20)\d\d|january|february|march|april|may|june|july|august|september|october|november|december|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|yesterday|today|tomorrow|tonight|morning|afternoon|"
    r"evening|night|(?:day|week|weekend|month|year|hour|minute)s?|date|time|when|recent|recently|lately|ago|earlier|"
    r"later|before|after|since|until|during|latest|current|currently|now|last|next|first)$")
MH_TIME_ZH = ("年", "月", "日", "天", "周", "星期", "昨天", "今天", "明天", "最近", "之前", "之后", "以前", "以后",
              "时候", "时间", "现在", "目前", "最新")
MH_PRONOUN_I = {"i", "i'm", "i've", "i'd", "i'll"}


def mh_split_body(query: str) -> dict:
    return {"model": MH_MODEL, "messages": [{"role": "user", "content": MH_PROMPT.replace("{query}", query)}],
            "temperature": 0, "max_tokens": MH_MAX_TOKENS, "response_format": {"type": "json_object"}}


def mh_violations(sub: str, query: str) -> list:
    """Pre-registered filter: time words / entities in the sub-query that the original query does not have."""
    qwords = {w.lower() for w in MH_WORD_RE.findall(query)}
    bad = []
    for m in MH_WORD_RE.finditer(sub):
        w, lw = m.group(0), m.group(0).lower()
        if lw in qwords:
            continue
        if MH_TIME_RE.match(w):
            bad.append("time")
            continue
        before = sub[:m.start()].rstrip()
        sentence_start = not before or before[-1] in ".?!"
        if any(ch.isdigit() for ch in w) or (w[0].isupper() and not sentence_start and lw not in MH_PRONOUN_I):
            bad.append("entity")
    bad += ["time_zh" for t in MH_TIME_ZH if t in sub and t not in query]
    return bad


def mh_parse(text, query: str):
    """Model reply -> (sub-queries or None, info). None = invalid -> the whole bypass is dropped."""
    try:
        obj = json.loads(text)
    except Exception:
        return None, {"reason": "bad_json"}
    subs = obj.get("subqueries") if isinstance(obj, dict) else None
    if not isinstance(subs, list) or not all(isinstance(s, str) for s in subs):
        return None, {"reason": "bad_shape"}
    if len(subs) > 4:
        return None, {"reason": "too_many", "n_raw": len(subs)}
    seen, out, dropped = {query.strip().lower()}, [], {}
    for s in subs:
        s = s.strip()
        if s.lower() in seen:
            continue
        if not 1 <= len(s) <= 300:
            return None, {"reason": "bad_length", "n_raw": len(subs)}
        seen.add(s.lower())
        bad = mh_violations(s, query)
        if bad:
            for b in set(bad):
                dropped[b] = dropped.get(b, 0) + 1
            continue
        out.append(s)
    info = {"n_raw": len(subs), "n_valid": len(out), "dropped": dropped}
    if len(out) < 2:
        return None, dict(info, reason="lt2_valid")
    return out, info


def mh_rrf(store, query: str, qvec, top_k: int):
    """Same RRF fusion as the original do_search path (lexical recall + vector, k=RRF_K). -> (ranked, records)."""
    lexical = store.recall(query, limit=top_k, trace=False)
    vec_hits = []
    if qvec is not None:
        vec_hits = store.search_vectors(query_vector=qvec, provider=EMBED_PROVIDER, model=EMBED_MODEL,
                                        owner_type="memory", limit=top_k, redact=False)
    records, fused = {}, {}
    for rank, hit in enumerate(lexical, start=1):
        rec = hit["record"] if "record" in hit else hit
        mid = int(rec["id"])
        records[mid] = rec
        fused[mid] = fused.get(mid, 0.0) + 1.0 / (RRF_K + rank)
    for rank, hit in enumerate(vec_hits, start=1):
        rec = hit["record"]
        mid = int(rec["id"])
        records[mid] = rec
        fused[mid] = fused.get(mid, 0.0) + 1.0 / (RRF_K + rank)
    return sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:top_k], records


def mh_merge(base, sub_lists, keep, fill):
    """Pure quota merge. base: [(mid, score)] original path, score order. sub_lists: per sub-query [(mid, score)].
    -> same length as base: base[:keep] + round-robin sub hits (deduped) up to `fill`, padded with base[keep:]."""
    n = len(base)
    out = list(base[:keep])
    used = {m for m, _ in out}
    want = min(fill, n - len(out))
    added = 0
    for r in range(max((len(s) for s in sub_lists), default=0)):
        for s in sub_lists:
            if added >= want:
                break
            if r < len(s) and s[r][0] not in used:
                out.append(s[r])
                used.add(s[r][0])
                added += 1
    padded = 0
    for m, sc in base[keep:]:
        if len(out) >= n:
            break
        if m not in used:
            out.append((m, sc))
            used.add(m)
            padded += 1
    return out, {"added": added, "padded": padded}


def mh_call(query: str) -> tuple:
    """One Cloudflare gpt-4o-mini call. Token read from MH_TOKEN_PATH at call time, never logged.
    -> (reply text, usage dict). Raises on any failure or when the call exceeds MH_TIMEOUT seconds."""
    import httpx

    key = MH_TOKEN_PATH.read_text().strip()
    if not key:
        raise RuntimeError("no_token")
    t0 = time.perf_counter()
    with httpx.Client(timeout=MH_TIMEOUT, follow_redirects=False, trust_env=False,
                      transport=httpx.HTTPTransport(retries=0)) as client:
        r = client.post(MH_URL, headers={"Authorization": "Bearer " + key}, json=mh_split_body(query))
    if time.perf_counter() - t0 > MH_TIMEOUT:
        raise TimeoutError("mh_timeout")
    r.raise_for_status()
    raw = r.json()
    u = raw.get("usage") or {}
    return raw["choices"][0]["message"]["content"], {"in": u.get("prompt_tokens"), "out": u.get("completion_tokens")}


def mh_apply(query: str, uid: str, top_k: int, ordered, records):
    """All-or-nothing bypass. Returns (ordered, meta); on any failure `ordered` and `records` are left unchanged."""
    t0 = time.perf_counter()
    meta = {"mode": "fallback"}
    try:
        text, usage = mh_call(query)
        meta["usage"] = usage
        subs, info = mh_parse(text, query)
        meta.update(info)
        if subs is None:
            return ordered, meta
        qvecs = embed(subs) if EMBED_ENABLED else [None] * len(subs)
        sub_lists, new_recs = [], {}
        with user_lock(uid):
            store = open_store(uid)
            try:
                for s, v in zip(subs, qvecs):
                    ranked, recs = mh_rrf(store, s, v, top_k)
                    sub_lists.append(ranked[:MH_PER_SUB])
                    new_recs.update(recs)
            finally:
                store.close()
        merged, st = mh_merge(list(ordered), sub_lists, MH_KEEP, MH_FILL)
        add = {mid: new_recs[mid] for mid, _ in merged if mid not in records}
        records.update(add)
        meta.update(st, mode="mh", n_sub=len(subs))
        return merged, meta
    except Exception as exc:  # never echo the query, the reply or the token
        meta["reason"] = type(exc).__name__
        return ordered, meta
    finally:
        meta["sec"] = round(time.perf_counter() - t0, 4)


# Multi-hop MH-2 (2026-10-16; pre-registration data/round3/mh2/PREREG.md). Fixes the two MH-1 causes of death:
# (1) intent gate: ONE gpt-4o-mini call decides split yes/no AND writes the sub-queries; "no" -> original path,
#     byte-identical; (2) rerank normalisation: sub-query hits are NOT put into the return set directly. Their
#     RRF top-MH_PER_SUB hits that are NOT in the original candidate pool (deduped, at most MH2_MAX_NEW) are scored by
#     the SAME live rerank.apply (unchanged), merged with the original rerank output by score (ties: original pool
#     first) and cut to the same n. Clef-flash scores each candidate independently, so this equals one rerank pass
#     over "original pool + new candidates appended". MH2 only runs when the original rerank succeeded
#     (mode == "rerank"); any failure -> original path unchanged. Default AML_MH2=off: response byte-identical to
#     pre-MH2 (backup server.py.bak-pre-mh2-20261016). AML_MH (MH-1) and AML_MH2 are mutually exclusive.
MH2 = os.environ.get("AML_MH2", "off").strip().lower() or "off"
MH2_TIMEOUT = float(os.environ.get("AML_MH2_TIMEOUT", "20"))
MH2_TOKEN_PATH = Path(os.environ.get("AML_MH2_TOKEN_PATH", str(SECRETS_DIR / ".cloudflare_token")))
MH2_MAX_NEW = 30
if MH != "off" and MH2 != "off":
    raise SystemExit("AML_MH and AML_MH2 are mutually exclusive")
MH2_PROMPT = (
    "You help search a user's long-term memory of past conversations. Read the question below and do two things.\n\n"
    "1. Decide \"split\": does answering this question need SEVERAL separate pieces of information that were most "
    "likely mentioned in DIFFERENT places (different conversations or different moments), so that one search may not "
    "find them all? Typical \"yes\" cases: counting or listing several things that were mentioned separately, "
    "combining or comparing facts from different conversations, or linking one fact to another fact. Answer \"no\" "
    "when the question asks about ONE fact, ONE event, ONE preference, ONE recommendation or ONE piece of advice, even "
    "if the question is long or detailed.\n\n"
    "2. Only if \"split\" is \"yes\": rewrite the question as 2 to 4 shorter sub-queries. Each sub-query asks for ONE "
    "piece of information the question needs, so that searching for each sub-query separately finds all the "
    "pieces.\n\n"
    "Rules for sub-queries:\n"
    "- Every sub-query is a rewording of a part of the original question. Use only words, names and things that "
    "appear in the original question.\n"
    "- Do NOT add any date, time or time-period words (dates, years, months, weekdays, \"when\", \"recently\", "
    "\"last week\", \"before\", \"after\", ...) that are not in the original question.\n"
    "- Do NOT add any person, place, product, number or other entity that is not in the original question.\n"
    "- Do not answer the question.\n\n"
    "Return only JSON: {\"split\": \"yes\", \"subqueries\": [\"...\", \"...\"]} or "
    "{\"split\": \"no\", \"subqueries\": []}\n\n"
    "Question: {query}")


def mh2_gate_body(query: str) -> dict:
    return {"model": MH_MODEL, "messages": [{"role": "user", "content": MH2_PROMPT.replace("{query}", query)}],
            "temperature": 0, "max_tokens": MH_MAX_TOKENS, "response_format": {"type": "json_object"}}


def mh2_parse(text, query: str):
    """Gate reply -> (subs, info). subs None = invalid (fallback), [] = gate said no, list = valid sub-queries
    (validated by the unchanged MH-1 mh_parse rules)."""
    try:
        obj = json.loads(text)
    except Exception:
        return None, {"reason": "bad_json"}
    if not isinstance(obj, dict):
        return None, {"reason": "bad_shape"}
    split = obj.get("split")
    split = split.strip().lower() if isinstance(split, str) else None
    if split not in ("yes", "no"):
        return None, {"reason": "bad_split"}
    if split == "no":
        return [], {"split": "no"}
    subs, info = mh_parse(json.dumps({"subqueries": obj.get("subqueries")}), query)
    return subs, dict(info, split="yes")


def mh2_new_candidates(sub_lists, pool_mids, cap):
    """Round-robin by rank over the sub-query lists [(mid, rrf)]; keep only mids NOT in the original candidate pool,
    deduped, at most `cap`. -> [(mid, rrf)] in round-robin order."""
    seen, out = set(pool_mids), []
    for r in range(max((len(s) for s in sub_lists), default=0)):
        for s in sub_lists:
            if len(out) >= cap:
                return out
            if r < len(s) and s[r][0] not in seen:
                out.append(s[r])
                seen.add(s[r][0])
    return out


def mh2_merge(base, extra, n):
    """Pure. base: original rerank output [(mid, score)] in rerank order; extra: rerank output of the new candidates
    in its rerank order. Stable sort by score desc (ties: base first, then extra order), cut to n."""
    return sorted(list(base) + list(extra), key=lambda x: -x[1])[:n]


def mh2_call(query: str) -> tuple:
    """One Cloudflare gpt-4o-mini gate call. Token read at call time, never logged. -> (reply text, usage)."""
    import httpx

    key = MH2_TOKEN_PATH.read_text().strip()
    if not key:
        raise RuntimeError("no_token")
    t0 = time.perf_counter()
    with httpx.Client(timeout=MH2_TIMEOUT, follow_redirects=False, trust_env=False,
                      transport=httpx.HTTPTransport(retries=0)) as client:
        r = client.post(MH_URL, headers={"Authorization": "Bearer " + key}, json=mh2_gate_body(query))
    if time.perf_counter() - t0 > MH2_TIMEOUT:
        raise TimeoutError("mh2_timeout")
    r.raise_for_status()
    raw = r.json()
    u = raw.get("usage") or {}
    return raw["choices"][0]["message"]["content"], {"in": u.get("prompt_tokens"), "out": u.get("completion_tokens")}


def mh2_apply(query: str, uid: str, top_k: int, ordered, records, pool_mids):
    """All-or-nothing. ordered = original rerank output (already cut to n). pool_mids = original RRF candidates
    (the rerank input). Returns (ordered, meta); on gate "no" or any failure `ordered`/`records` are unchanged."""
    t0 = time.perf_counter()
    meta = {"mode": "fallback"}
    try:
        text, usage = mh2_call(query)
        meta["usage"] = usage
        subs, info = mh2_parse(text, query)
        meta.update(info)
        if subs is None:
            return ordered, meta
        if not subs:
            meta["mode"] = "gate_no"
            return ordered, meta
        qvecs = embed(subs) if EMBED_ENABLED else [None] * len(subs)
        sub_lists, new_recs = [], {}
        with user_lock(uid):
            store = open_store(uid)
            try:
                for s, v in zip(subs, qvecs):
                    ranked, recs = mh_rrf(store, s, v, top_k)
                    sub_lists.append(ranked[:MH_PER_SUB])
                    new_recs.update(recs)
            finally:
                store.close()
        new = mh2_new_candidates(sub_lists, pool_mids, MH2_MAX_NEW)
        meta.update(n_sub=len(subs), n_new=len(new))
        if not new:
            meta["mode"] = "no_new"
            return ordered, meta
        extra, rmeta = rerank.apply(query, [(mid, sc, new_recs[mid]["content"]) for mid, sc in new], len(new))
        meta["rerank_new"] = {k: rmeta.get(k) for k in ("mode", "reason", "n_cand", "returned", "n_sent",
                                                         "n_valid", "rerank_sec")}
        if rmeta.get("mode") != "rerank":
            meta["reason"] = "rerank_new_" + str(rmeta.get("reason") or rmeta.get("mode"))
            return ordered, meta
        n = min(rerank.RETURN_N, top_k) if rerank.RETURN_N is not None else top_k
        merged = mh2_merge(ordered, extra, min(n, rerank.MAX_CAND))
        new_set = {mid for mid, _ in new}
        for mid, _ in merged:
            if mid not in records:
                records[mid] = new_recs[mid]
        meta.update(mode="mh2", entered=sum(1 for mid, _ in merged if mid in new_set))
        return merged, meta
    except Exception as exc:  # never echo the query, the reply or the token
        meta["reason"] = type(exc).__name__
        return ordered, meta
    finally:
        meta["sec"] = round(time.perf_counter() - t0, 4)


def session_of(source) -> str:
    return (source or "").split("|", 1)[1] if "|" in (source or "") else ""


def neighbor_merge(data, hit_src, prev_rows, next_rows, trunc, maxlen=0):
    """Pure merge step (no I/O). data: items in score order, ids "mem_<mid>".
    hit_src: {mid: source}. prev_rows / next_rows: {mid: (nid, content, source) or None}, the stored
    row right before / after each hit. maxlen > 0: hits with len(content) > maxlen get no neighbors
    (and claim none). trunc > 0: a neighbor longer than (maxlen or trunc) chars is cut to trunc chars
    ending in "…". Returns (new_data, stats)."""
    mids = [int(x["id"].split("_", 1)[1]) for x in data]
    used = set(mids)
    out, n_prev, n_next, n_long = [], 0, 0, 0

    def take(row, sess):
        if not row or not sess or row[0] in used or session_of(row[2]) != sess:
            return None
        used.add(row[0])
        txt = row[1]
        # r3: with a hit gate (maxlen > 0) only neighbors longer than maxlen are cut, to trunc chars;
        # without a gate (maxlen == 0) any neighbor longer than trunc is cut (r1 NT200 semantics).
        if trunc and len(txt) > (maxlen or trunc):
            txt = txt[:trunc - 1] + "…"
        return txt

    for mid, item in zip(mids, data):
        if maxlen and len(item["content"]) > maxlen:
            n_long += 1
            out.append(item)
            continue
        sess = session_of(hit_src.get(mid))
        p = take(prev_rows.get(mid), sess)
        n = take(next_rows.get(mid), sess)
        if p is None and n is None:
            out.append(item)
            continue
        new = dict(item)
        new["content"] = "\n".join(x for x in (p, item["content"], n) if x is not None)
        n_prev += p is not None
        n_next += n is not None
        out.append(new)
    return out, {"prev": n_prev, "next": n_next, "long_skipped": n_long}


def abstain_gate(data, score_rank, floor, min_keep):
    """Pure trim step (no I/O), run LAST (after neighbor merge and time order). data: items in their final display
    order, each with "score". score_rank: {item id: position in score order} (ties among equal scores keep the
    rerank order). If max score >= floor nothing changes. Otherwise keep the min_keep best items by
    (score desc, score_rank asc), preserving their current display order. Returns (new_data, stats)."""
    if not data:
        return data, {"gated": False, "top1": None, "kept": 0}
    top1 = max(x["score"] for x in data)
    if top1 >= floor:
        return data, {"gated": False, "top1": top1, "kept": len(data)}
    best = sorted(data, key=lambda x: (-x["score"], score_rank[x["id"]]))[:min_keep]
    keep = {x["id"] for x in best}
    out = [x for x in data if x["id"] in keep]
    return out, {"gated": True, "top1": top1, "kept": len(out), "dropped": len(data) - len(out)}


def fetch_adjacent(conn, mids):
    """Stored predecessor / successor row of each mid: (id, content, source) or None. Read-only SELECTs
    (no recall, no hit_count side effects)."""
    prev_rows, next_rows = {}, {}
    for mid in mids:
        prev_rows[mid] = conn.execute(
            "SELECT id, content, source FROM memories WHERE id < ? ORDER BY id DESC LIMIT 1", (mid,)).fetchone()
        next_rows[mid] = conn.execute(
            "SELECT id, content, source FROM memories WHERE id > ? ORDER BY id ASC LIMIT 1", (mid,)).fetchone()
    prev_rows = {k: (tuple(v) if v is not None else None) for k, v in prev_rows.items()}
    next_rows = {k: (tuple(v) if v is not None else None) for k, v in next_rows.items()}
    return prev_rows, next_rows

_user_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_log_lock = threading.Lock()
_embed_usage = {"tokens": 0, "calls": 0}
_tls = threading.local()  # per-request rerank metadata for the X-AML-Rerank header


def user_lock(user_id: str) -> threading.Lock:
    with _locks_guard:
        return _user_locks.setdefault(user_id, threading.Lock())


def db_path(user_id: str) -> Path:
    return DB_DIR / (hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:32] + ".sqlite")


def open_store(user_id: str) -> MemoryStore:
    store = MemoryStore(db_path(user_id))
    store.init()
    return store


def idem_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(IDEM_PATH), timeout=30)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS add_requests (request_id TEXT PRIMARY KEY, payload_hash TEXT, response TEXT)"
    )
    return conn


def log(event: dict) -> None:
    with _log_lock, LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")


def text_of(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for part in value:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text", "")))
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(p for p in parts if p)
    return ""


def ts_label(ms) -> tuple[str, str]:
    if ms is None:
        return "", ""
    dt = datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M (%A)"), dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def embed(texts: list[str]) -> list[list[float]]:
    """text-embedding-v4 via DashScope OpenAI-compatible endpoint. Key from env only."""
    import httpx

    key = os.environ[EMBED_KEY_ENV]
    out: list[list[float]] = []
    if EMBED_PROVIDER == "gemini":
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{EMBED_MODEL}:batchEmbedContents"
        with httpx.Client(timeout=60) as client:
            for i in range(0, len(texts), 100):
                batch = texts[i:i + 100]
                reqs = [{"model": f"models/{EMBED_MODEL}", "content": {"parts": [{"text": t}]},
                         "outputDimensionality": EMBED_DIM} for t in batch]
                for attempt in range(5):
                    resp = client.post(url, headers={"x-goog-api-key": key}, json={"requests": reqs})
                    if resp.status_code in (429, 500, 502, 503, 504) and attempt < 4:
                        time.sleep(2 ** attempt)
                        continue
                    resp.raise_for_status()
                    break
                _embed_usage["calls"] += 1
                _embed_usage["tokens"] += sum(len(t) for t in batch) // 4  # rough, API returns no usage
                out.extend(e["values"] for e in resp.json()["embeddings"])
        return out
    with httpx.Client(timeout=60) as client:
        for i in range(0, len(texts), 10):  # DashScope batch limit is 10 inputs
            batch = texts[i:i + 10]
            for attempt in range(5):
                resp = client.post(
                    EMBED_BASE + "/embeddings",
                    headers={"Authorization": f"Bearer {key}"},
                    json={"model": EMBED_MODEL, "input": batch, "dimensions": EMBED_DIM, "encoding_format": "float"},
                )
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < 4:
                    time.sleep(2 ** attempt)
                    continue
                resp.raise_for_status()
                break
            body = resp.json()
            _embed_usage["tokens"] += int(body.get("usage", {}).get("total_tokens", 0))
            _embed_usage["calls"] += 1
            out.extend(item["embedding"] for item in sorted(body["data"], key=lambda d: d["index"]))
    return out


def lookup_add(conn, request_id, payload_hash):
    row = conn.execute(
        "SELECT payload_hash, response FROM add_requests WHERE request_id=?", (request_id,)
    ).fetchone()
    if row is None:
        return None
    if row[0] != payload_hash:
        return 409, {"detail": {"reason": "request_id reused with different payload"}}
    return 200, json.loads(row[1])


def do_add(body: dict) -> tuple[int, dict]:
    for field in ("request_id", "messages", "user_id", "session_id"):
        if field not in body:
            return 400, {"detail": {"reason": f"missing field {field}"}}
    rid, uid, sid = body["request_id"], body["user_id"], body["session_id"]
    msgs = body["messages"]
    if not isinstance(msgs, list) or not msgs:
        return 400, {"detail": {"reason": "messages must be a non-empty array"}}
    payload_hash = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    conn = idem_conn()
    try:
        cached = lookup_add(conn, rid, payload_hash)
        if cached is not None:
            return cached
    finally:
        conn.close()

    t0 = time.perf_counter()
    prepared = []
    for idx, msg in enumerate(msgs):
        text = text_of(msg.get("content")).strip()
        if not text:
            return 400, {"detail": {"reason": f"messages[{idx}].content empty"}}
        human, iso = ts_label(msg.get("timestamp"))
        content = f"[{human}] {text}" if human else text
        title = f"{human or 'undated'} | {msg.get('role', 'user')}"
        prepared.append((title, content, iso))

    vectors = None
    t_embed = 0.0
    if EMBED_ENABLED:
        te = time.perf_counter()
        vectors = embed([c for _, c, _ in prepared])
        t_embed = time.perf_counter() - te

    resp = {"success": True, "request_id": rid, "user_id": uid, "session_id": sid}
    with user_lock(uid), open_store(uid) as store:
        # Keep the existing global request-id contract and legacy receipts. SQLite's
        # rollback journal commits both attached files in the same transaction.
        store.conn.execute("ATTACH DATABASE ? AS receipts", (str(IDEM_PATH),))
        with store.write_transaction():
            cached = lookup_add(store.conn, rid, payload_hash)
            if cached is not None:
                return cached
            for i, (title, content, iso) in enumerate(prepared):
                record, _ = store.add_memory(
                    title=title, content=content, thread="aml", category="raw_message",
                    source=f"{iso}|{sid}", occurrence_key=f"aml:{payload_hash}:{i}",
                )
                if vectors is not None:
                    store.upsert_vector(
                        owner_type="memory", owner_id=int(record.id), vector=vectors[i],
                        provider=EMBED_PROVIDER, model=EMBED_MODEL,
                    )
            store.conn.execute(
                "INSERT INTO receipts.add_requests VALUES (?, ?, ?)",
                (rid, payload_hash, json.dumps(resp)),
            )
    log({"op": "add", "request_id": rid, "n": len(prepared), "sec": round(time.perf_counter() - t0, 4),
         "embed_sec": round(t_embed, 4)})
    return 200, resp


def do_search(body: dict) -> tuple[int, dict]:
    for field in ("query", "user_id", "top_k"):
        if field not in body:
            return 400, {"detail": {"reason": f"missing field {field}"}}
    query = text_of(body["query"]).strip()
    uid = body["user_id"]
    top_k = int(body["top_k"])
    if top_k < 1:
        return 422, {"detail": {"reason": "top_k must be >= 1"}}
    t0 = time.perf_counter()
    if not db_path(uid).exists():
        return 200, {"data": []}

    qvec = embed([query])[0] if EMBED_ENABLED else None
    with user_lock(uid):
        store = open_store(uid)
        try:
            lexical = store.recall(query, limit=top_k, trace=False)
            vec_hits = []
            if qvec is not None:
                vec_hits = store.search_vectors(
                    query_vector=qvec, provider=EMBED_PROVIDER, model=EMBED_MODEL,
                    owner_type="memory", limit=top_k, redact=False,
                )
            records = {}
            fused: dict[int, float] = {}
            for rank, hit in enumerate(lexical, start=1):
                rec = hit["record"] if "record" in hit else hit
                mid = int(rec["id"])
                records[mid] = rec
                fused[mid] = fused.get(mid, 0.0) + 1.0 / (RRF_K + rank)
            for rank, hit in enumerate(vec_hits, start=1):
                rec = hit["record"]
                mid = int(rec["id"])
                records[mid] = rec
                fused[mid] = fused.get(mid, 0.0) + 1.0 / (RRF_K + rank)
        finally:
            store.close()

    pool_n = max(top_k, RERANK_POOL) if (RERANK_POOL and rerank.ACTIVE) else top_k
    ordered = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:pool_n]
    mh2_pool = [mid for mid, _ in ordered] if MH2 != "off" else None  # MH-2: the original rerank input pool
    rr_meta = None
    if rerank.ACTIVE:
        # Hook (work order 2): RRF candidates -> [(2) forget: hard exclude, see rerank.apply]
        # -> rerank score -> sort desc, ties keep RRF order -> [(2) void: reorder/annotate]
        # -> truncate to min(AML_RETURN_N, top_k). All-or-nothing: any failure returns the RRF
        # order with RRF scores. Response field shape unchanged.
        ordered, rr_meta = rerank.apply(query, [(mid, sc, records[mid]["content"]) for mid, sc in ordered], top_k)
        _tls.rerank_meta = rr_meta
    if pool_n != top_k:
        # pool mode insurance: whatever the reranker returned (incl. any fallback), never exceed top_k
        ordered = ordered[:top_k]
    mh_meta = None
    if MH != "off" and len(ordered) > MH_KEEP:
        # MH-1 bypass: after the original return set is fixed, BEFORE neighbor merge / time order / abstain.
        ordered, mh_meta = mh_apply(query, uid, top_k, ordered, records)
    mh2_meta = None
    if MH2 != "off":
        # MH-2: after the original rerank cut, BEFORE neighbor merge / time order / abstain. Only on a real rerank.
        if rr_meta is not None and rr_meta.get("mode") == "rerank" and ordered:
            ordered, mh2_meta = mh2_apply(query, uid, top_k, ordered, records, mh2_pool)
        else:
            mh2_meta = {"mode": "skipped", "reason": "not_reranked"}
    data = []
    for mid, score in ordered:
        rec = records[mid]
        iso = (rec.get("source") or "").split("|", 1)[0]
        item = {"id": f"mem_{mid}", "content": rec["content"], "score": round(score, 6)}
        if iso:
            item["created_at"] = iso
        data.append(item)
    nt_stats = None
    if NEIGHBOR_TURNS != "off" and data:
        # NT knife: merge neighbors first, THEN the time-order block below sorts the merged items.
        hit_mids = [mid for mid, _ in ordered]
        with user_lock(uid):
            store = open_store(uid)
            try:
                prev_rows, next_rows = fetch_adjacent(store.conn, hit_mids)
            finally:
                store.close()
        data, nt_stats = neighbor_merge(data, {mid: records[mid].get("source") for mid in hit_mids},
                                        prev_rows, next_rows, NEIGHBOR_TRUNC, NEIGHBOR_MAXLEN)
    score_rank = {x["id"]: i for i, x in enumerate(data)}  # score order, captured before the time-order block
    order_mode = "score"
    if TIME_ORDER != "off" and len(data) > 1:
        dated = [x for x in data if x.get("created_at")]
        undated = [x for x in data if not x.get("created_at")]
        newest_first = bool(ASOF_RE.search(query))
        dated.sort(key=lambda x: x["created_at"].rstrip("Z"), reverse=newest_first)  # ISO strings sort correctly
        data = dated + undated
        order_mode = "asof_newest_first" if newest_first else "chrono"
    ab_stats = None
    if ABSTAIN_FLOOR_VAL is not None:
        # H knife, LAST step (after NEIGHBOR merge and TIME_ORDER): trim only on a real rerank (0..3 scores).
        if rr_meta is not None and rr_meta.get("mode") == "rerank":
            data, ab_stats = abstain_gate(data, score_rank, ABSTAIN_FLOOR_VAL, ABSTAIN_MIN_KEEP)
        else:
            ab_stats = {"gated": False, "skipped": "not_reranked"}
    ev = {"op": "search", "n_lex": len(lexical), "n_vec": len(vec_hits), "returned": len(data),
          "time_order": order_mode, "sec": round(time.perf_counter() - t0, 4)}
    if mh_meta is not None:
        ev["mh"] = mh_meta  # metadata only: mode, reason code, counts, token usage, seconds
    if mh2_meta is not None:
        ev["mh2"] = mh2_meta  # metadata only: mode, split, reason code, counts, token usage, seconds
    if nt_stats is not None:
        ev["neighbors"] = nt_stats  # counts only
    if ab_stats is not None:
        ev["abstain"] = ab_stats  # gate decision, top1 score and counts only
    if rr_meta is not None:
        ev["rerank"] = rr_meta  # metadata only: mode, reason code, counts, seconds, USD estimate
        if rerank.MODE != "off":
            st = rerank.stats_snapshot()
            ev["rerank_cum"] = {k: st[k] for k in ("searches", "reranked", "fallback", "fallback_reasons",
                                                   "calls_sent", "calls_unknown", "http_429", "valid_candidates",
                                                   "queue_wait_s", "est_usd_settled", "budget", "breaker_open")}
    log(ev)
    return 200, {"data": data}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # keep stdout quiet; bodies never logged
        pass

    def _send(self, code: int, payload: dict) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        meta = getattr(_tls, "rerank_meta", None)
        if meta is not None:
            _tls.rerank_meta = None
            hdr = meta.get("mode", "?") + ("; reason=" + meta["reason"] if meta.get("reason") else "")
            if rerank.MODE != "off" and rerank.stats_snapshot()["budget"].get("stopped"):
                hdr += "; budget=stopped"
            self.send_header("X-AML-Rerank", hdr)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/health":
            h = {"status": "ok", "embedding": EMBED_MODEL if EMBED_ENABLED else "none(lexical-only)",
                 "embed_usage": _embed_usage}
            if rerank.ACTIVE:
                h["rerank"] = rerank.stats_snapshot()  # metadata only
            self._send(200, h)
        else:
            self._send(404, {"detail": {"reason": "not found"}})

    def _authed(self) -> bool:
        if not API_KEY:
            return True
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer ") and auth[7:].strip() == API_KEY:
            return True
        return self.headers.get("X-Api-Key", "").strip() == API_KEY

    def do_POST(self):
        _tls.rerank_meta = None
        if not self._authed():
            # auth BEFORE reading the body: an unauthenticated request never costs a body read.
            # The unread body desyncs HTTP/1.1 keep-alive, so close this connection after replying.
            self.close_connection = True
            self._send(401, {"detail": {"reason": "unauthorized"}})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            # body length unknowable -> keep-alive would desync; close like the 413 branch
            self.close_connection = True
            self._send(400, {"detail": {"reason": "invalid Content-Length"}})
            return
        if length < 0 or length > MAX_BODY_BYTES:
            self.close_connection = True
            self._send(413, {"detail": {"reason": "body too large (max %d bytes)" % MAX_BODY_BYTES}})
            return
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._send(400, {"detail": {"reason": "invalid JSON"}})
            return
        try:
            if self.path == "/v1/memories/add":
                code, payload = do_add(body)
            elif self.path == "/v1/memories/search":
                code, payload = do_search(body)
            else:
                code, payload = 404, {"detail": {"reason": "not found"}}
        except Exception as exc:  # never echo request bodies or secrets
            log({"op": "error", "path": self.path, "error": type(exc).__name__})
            code, payload = 500, {"detail": {"reason": f"internal error: {type(exc).__name__}"}}
        self._send(code, payload)


def main() -> None:
    port = int(os.environ.get("AML_TRIAL_PORT", "18080"))
    bind = os.environ.get("AML_BIND", "127.0.0.1")
    server = ThreadingHTTPServer((bind, port), Handler)
    print(f"listening {bind}:{port} auth={'on' if API_KEY else 'off'} "
          f"embedding={'on' if EMBED_ENABLED else 'off'}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
