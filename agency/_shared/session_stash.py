"""Per-session scratch state for the warehouse mesh's bounded loops.

Two jobs, both keyed by session_id:

  * IDEMPOTENCY — a humanoid actuator must not double-dispatch the same
    pick/pack/load if the chain re-enters (e.g. a retried event). The
    dispatcher records the task key here and no-ops on a repeat.
  * COUNTERS — bump()/peek() for a bounded back-edge. The outbound chain
    has none today (a failed inspection goes to the exception handler,
    never back to the picker); use them if you add a loop.

In-process dict keyed by session_id — single-replica dev default. For a
multi-replica deployment, swap the primitives for Redis hashes (the
SDK's Redis client is on the mesh instance); the interface stays.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any

_LOCK = threading.Lock()
_MAX_SESSIONS = 500
_STASH: "OrderedDict[str, dict[str, Any]]" = OrderedDict()


def resolve_session_id(input_data: Any, context: Any) -> str:
    for src in (input_data, context):
        if not isinstance(src, dict):
            continue
        for key in ("session_id", "sessionId"):
            sid = src.get(key)
            if sid:
                return str(sid)
        owd = src.get("original_webhook_data")
        if isinstance(owd, dict) and owd.get("session_id"):
            return str(owd["session_id"])
    return ""


def stash_get(session_id: str) -> dict[str, Any]:
    if not session_id:
        return {}
    with _LOCK:
        return dict(_STASH.get(session_id) or {})


def stash_put(session_id: str, data: dict[str, Any]) -> None:
    if not session_id:
        return
    with _LOCK:
        _STASH[session_id] = dict(data)
        _STASH.move_to_end(session_id)
        while len(_STASH) > _MAX_SESSIONS:
            _STASH.popitem(last=False)


def bump(session_id: str, key: str, by: int = 1) -> int:
    """Increment a counter and return the NEW value."""
    sid = session_id or "_nosession"
    with _LOCK:
        bucket = _STASH.setdefault(sid, {})
        _STASH.move_to_end(sid)
        bucket[key] = int(bucket.get(key, 0)) + by
        return bucket[key]


def peek(session_id: str, key: str, default: int = 0) -> int:
    sid = session_id or "_nosession"
    with _LOCK:
        return int(_STASH.get(sid, {}).get(key, default))


def mark_once(session_id: str, task_key: str) -> bool:
    """Idempotency latch. Returns True the FIRST time this (session,
    task) is seen, False on every repeat — the actuator side-effect runs
    only on True."""
    sid = session_id or "_nosession"
    with _LOCK:
        bucket = _STASH.setdefault(sid, {})
        _STASH.move_to_end(sid)
        done = set(bucket.get("_done_tasks") or [])
        if task_key in done:
            return False
        done.add(task_key)
        bucket["_done_tasks"] = sorted(done)
        return True
