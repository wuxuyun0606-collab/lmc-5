"""Session-scoped history for memories already injected into a conversation.

Retrieval deduplication and injection-history deduplication solve different
problems. ``RecallPipeline`` already merges one memory found by several
channels in a single call. This module remembers which merged hits were
actually injected on earlier turns of the same session, so repeat winners do
not consume the next turn's limited context budget again.

The file-backed implementation stores only hashes, expires stale sessions, and
uses atomic replacement. It is suitable for Claude Code hooks, where each turn
runs in a fresh process. Service-style integrations may use the in-memory
implementation instead.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, Iterable, Optional, Protocol, Set

try:  # Linux/macOS hook deployments; fallback keeps imports working elsewhere.
    import fcntl
except ImportError:  # pragma: no cover - exercised only on platforms without fcntl
    fcntl = None  # type: ignore


def _digest(value: str, size: int = 40) -> str:
    return hashlib.sha256((value or "").encode("utf-8", "ignore")).hexdigest()[:size]


def recall_identity(namespace: str, source_id: int) -> str:
    """Stable, content-free identity shared by pipeline and history stores."""
    return f"{namespace}\x1f{int(source_id)}"


class RecallInjectionHistory(Protocol):
    """Minimal persistence contract consumed by ``RecallPipeline``."""

    def seen(self, session_id: str, keys: Iterable[str]) -> Set[str]:
        ...

    def mark(self, session_id: str, keys: Iterable[str]) -> None:
        ...


class InMemoryRecallHistory:
    """Thread-safe history for long-lived agent processes and tests."""

    def __init__(self, max_keys_per_session: int = 1024):
        self.max_keys_per_session = max(1, int(max_keys_per_session))
        self._sessions: Dict[str, Dict[str, float]] = {}
        self._lock = threading.Lock()

    def seen(self, session_id: str, keys: Iterable[str]) -> Set[str]:
        if not session_id:
            return set()
        candidates = list(keys)
        with self._lock:
            known = self._sessions.get(session_id, {})
            return {key for key in candidates if key in known}

    def mark(self, session_id: str, keys: Iterable[str]) -> None:
        if not session_id:
            return
        now = time.time()
        with self._lock:
            known = self._sessions.setdefault(session_id, {})
            for key in keys:
                if key:
                    known[str(key)] = now
            if len(known) > self.max_keys_per_session:
                newest = sorted(known.items(), key=lambda pair: pair[1], reverse=True)
                self._sessions[session_id] = dict(newest[: self.max_keys_per_session])


class JsonFileRecallHistory:
    """Hash-only, process-safe history for per-turn hook subprocesses."""

    def __init__(
        self,
        state_dir: Path,
        ttl_seconds: int = 2 * 86400,
        max_keys_per_session: int = 1024,
    ):
        self.state_dir = Path(state_dir)
        self.ttl_seconds = max(60, int(ttl_seconds))
        self.max_keys_per_session = max(1, int(max_keys_per_session))

    def _paths(self, session_id: str):
        token = _digest(session_id, 24)
        return (
            self.state_dir / f"session-{token}.json",
            self.state_dir / f"session-{token}.lock",
        )

    def _ensure_dir(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.state_dir.chmod(0o700)
        except OSError:
            pass

    @staticmethod
    def _read(path: Path) -> Dict[str, float]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            seen = payload.get("seen") if isinstance(payload, dict) else None
            if isinstance(seen, dict):
                return {str(key): float(value) for key, value in seen.items()}
        except Exception:
            pass
        return {}

    @staticmethod
    def _hashed(keys: Iterable[str]) -> Dict[str, str]:
        return {str(key): _digest(str(key)) for key in keys if key}

    def seen(self, session_id: str, keys: Iterable[str]) -> Set[str]:
        if not session_id:
            return set()
        key_hashes = self._hashed(keys)
        if not key_hashes:
            return set()
        self._ensure_dir()
        state_path, lock_path = self._paths(session_id)
        with lock_path.open("a+", encoding="utf-8") as lock:
            if fcntl is not None:
                fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
            try:
                stale = (
                    state_path.exists()
                    and time.time() - state_path.stat().st_mtime >= self.ttl_seconds
                )
            except OSError:
                stale = False
            known = {} if stale else self._read(state_path)
            if fcntl is not None:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        return {key for key, hashed in key_hashes.items() if hashed in known}

    def mark(self, session_id: str, keys: Iterable[str]) -> None:
        if not session_id:
            return
        key_hashes = self._hashed(keys)
        if not key_hashes:
            return
        self._ensure_dir()
        state_path, lock_path = self._paths(session_id)
        with lock_path.open("a+", encoding="utf-8") as lock:
            if fcntl is not None:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            now = time.time()
            try:
                stale = (
                    state_path.exists()
                    and now - state_path.stat().st_mtime >= self.ttl_seconds
                )
            except OSError:
                stale = False
            known = {} if stale else self._read(state_path)
            for hashed in key_hashes.values():
                known[hashed] = now
            if len(known) > self.max_keys_per_session:
                newest = sorted(known.items(), key=lambda pair: pair[1], reverse=True)
                known = dict(newest[: self.max_keys_per_session])
            payload = json.dumps(
                {"version": 1, "updated_at": now, "seen": known},
                sort_keys=True,
                separators=(",", ":"),
            )
            fd, tmp_name = tempfile.mkstemp(prefix=state_path.name + ".", dir=str(self.state_dir))
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as tmp:
                    tmp.write(payload)
                    tmp.flush()
                    os.fsync(tmp.fileno())
                os.replace(tmp_name, state_path)
            finally:
                try:
                    os.unlink(tmp_name)
                except FileNotFoundError:
                    pass
            if fcntl is not None:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        self._cleanup()

    def _cleanup(self) -> None:
        cutoff = time.time() - self.ttl_seconds
        try:
            for path in list(self.state_dir.glob("session-*.json"))[:128]:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
        except OSError:
            pass


def history_from_env() -> Optional[JsonFileRecallHistory]:
    """Build the hook default; set LMC5_SESSION_RECALL_DEDUP=0 to disable."""
    enabled = os.environ.get("LMC5_SESSION_RECALL_DEDUP", "1").strip().lower()
    if enabled in {"0", "false", "off", "no"}:
        return None
    state_dir = Path(os.environ.get("LMC5_SESSION_RECALL_STATE_DIR", "/tmp/lmc5_session_recall"))
    try:
        ttl = int(os.environ.get("LMC5_SESSION_RECALL_TTL_SECONDS", str(2 * 86400)))
    except ValueError:
        ttl = 2 * 86400
    try:
        max_keys = int(os.environ.get("LMC5_SESSION_RECALL_MAX_KEYS", "1024"))
    except ValueError:
        max_keys = 1024
    return JsonFileRecallHistory(state_dir, ttl_seconds=ttl, max_keys_per_session=max_keys)
