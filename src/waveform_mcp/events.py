"""Event bus.

Three subscribers wired up by default:
  - JSONL log on disk (`./events.log`)
  - In-memory ring buffer for the preview UI's initial sync
  - Asyncio queues registered by the preview UI's websocket handler

Producers call `publish(event)`. Subscribers are decoupled — adding a Slack
notifier or a TUI is `bus.subscribe(fn)` away.

Events are flat JSON-serializable dicts so they round-trip through the
websocket without ceremony.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------- ring buffer ----------

_BUFFER_SIZE = 500
_buffer: deque[dict[str, Any]] = deque(maxlen=_BUFFER_SIZE)
_seq = 0
_log_path: Path | None = None
_subscribers: list = []  # synchronous fns
_async_queues: list[asyncio.Queue] = []  # for websocket clients


def configure(log_path: str | Path = "events.log") -> None:
    global _log_path
    _log_path = Path(log_path)


def reset() -> None:
    """Test helper."""
    global _seq
    _seq = 0
    _buffer.clear()
    _subscribers.clear()
    _async_queues.clear()


def buffer() -> list[dict[str, Any]]:
    return list(_buffer)


def subscribe(fn) -> None:
    _subscribers.append(fn)


def register_queue(q: asyncio.Queue) -> None:
    _async_queues.append(q)


def unregister_queue(q: asyncio.Queue) -> None:
    if q in _async_queues:
        _async_queues.remove(q)


# ---------- publishing ----------


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="milliseconds")


def publish(event: dict[str, Any]) -> dict[str, Any]:
    """Stamp `seq`, `ts`, `id` if missing, fan out to subscribers, return the
    fully-formed event for the caller's records.
    """
    global _seq
    _seq += 1
    event = dict(event)  # don't mutate caller's dict
    event.setdefault("seq", _seq)
    event.setdefault("ts", _now_iso())
    event.setdefault("id", f"evt_{int(time.time()*1000):x}_{_seq:04d}")

    _buffer.append(event)

    if _log_path is not None:
        try:
            with _log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event) + "\n")
        except OSError:
            pass

    for fn in _subscribers:
        try:
            fn(event)
        except Exception:
            pass

    for q in list(_async_queues):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass

    return event


# ---------- structured helpers (so call-sites stay terse) ----------


def tool_call(
    tool: str,
    args: dict | None = None,
    intent: str | None = None,
    target: dict | None = None,
    diff: dict | None = None,
    result: str = "ok",
    duration_ms: float | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    return publish(
        {
            "kind": "tool_call",
            "tool": tool,
            "intent": intent,
            "target": target,
            "args": args,
            "diff": diff,
            "result": result,
            "duration_ms": duration_ms,
            "error": error,
        }
    )


def model_change(diff: dict) -> dict[str, Any]:
    return publish({"kind": "model_change", "diff": diff})


def narrate(text: str) -> dict[str, Any]:
    return publish({"kind": "narrate", "text": text})


def flush(target: str, ok: bool = True, error: str | None = None) -> dict[str, Any]:
    return publish(
        {"kind": "flush", "target": target, "result": "ok" if ok else "error", "error": error}
    )
