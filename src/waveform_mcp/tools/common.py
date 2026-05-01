"""Shared helpers for tool modules: a decorator that wires every tool into
the EditStore + event bus uniformly.

Pattern:
    @op("track_add", intent="Add a track")
    def track_add(edit, name, kind="midi"):
        t = Track(id=_id("trk"), name=name, kind=kind)
        edit.tracks.append(t)
        return {"track_id": t.id}

The decorator handles:
  - timing
  - calling EditStore.apply (snapshot + undo)
  - computing diff
  - publishing the tool_call event
  - converting exceptions into structured error events instead of crashes
"""

from __future__ import annotations

import time
from functools import wraps

from ..diff import diff_edits
from ..events import tool_call
from ..model import STORE


def op(name: str, intent_template: str | None = None):
    """Decorator. The wrapped function takes `(edit, **kwargs)` and mutates
    `edit` in place. We expose `(args_dict)` to the tool dispatcher.
    """

    def deco(fn):
        @wraps(fn)
        def wrapper(args: dict | None = None) -> dict:
            args = args or {}
            t0 = time.perf_counter()
            try:
                result = STORE.apply(name, args, fn)
                duration_ms = (time.perf_counter() - t0) * 1000.0
                tool_call(
                    tool=name,
                    args=args,
                    intent=_format_intent(intent_template, args),
                    duration_ms=duration_ms,
                    result="ok",
                )
                _record_history(name, args, True)
                return {"ok": True, **(result or {})}
            except Exception as e:
                duration_ms = (time.perf_counter() - t0) * 1000.0
                tool_call(
                    tool=name,
                    args=args,
                    intent=_format_intent(intent_template, args),
                    duration_ms=duration_ms,
                    result="error",
                    error=str(e),
                )
                _record_history(name, args, False, str(e))
                return {"ok": False, "error": str(e), "type": type(e).__name__}

        wrapper._op_name = name  # type: ignore[attr-defined]
        return wrapper

    return deco


def _record_history(name: str, args: dict, ok: bool, error: str | None = None) -> None:
    """Push a record into workflow.tool_history if the module is loaded."""
    try:
        from . import workflow as _wf  # type: ignore
        _wf._TOOL_HISTORY.append({
            "ts": time.time(),
            "tool": name,
            "args_keys": sorted(args.keys()) if isinstance(args, dict) else None,
            "ok": ok,
            "error": error,
        })
    except Exception:
        pass


def _format_intent(template: str | None, args: dict) -> str | None:
    if template is None:
        return None
    try:
        return template.format(**args)
    except KeyError:
        return template


# wire the EditStore subscriber that publishes diff events
def _diff_subscriber(op_name: str, args: dict, before, after) -> None:
    from ..events import model_change

    d = diff_edits(before, after)
    if d:
        model_change({"op": op_name, **d})


STORE.subscribe(_diff_subscriber)
