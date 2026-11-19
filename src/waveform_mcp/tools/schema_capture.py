"""Schema-capture utilities.

Pattern we use to learn Waveform's XML format incrementally:

    1. Save the current edit (Waveform → disk).
    2. Snapshot the .tracktionedit XML — call this BEFORE.
    3. Drive the UI to add one new entity (track, clip, plugin, send, ...).
    4. Save again.
    5. Snapshot the new XML — call this AFTER.
    6. Diff BEFORE vs AFTER. The delta IS the schema for that entity.

These tools wrap that loop so the agent can run it without scripting.
"""

from __future__ import annotations

import difflib
import re
import sys
import time
from pathlib import Path
from typing import Any

from .. import safety


def _windows_only() -> dict | None:
    if sys.platform != "win32":
        return {"ok": False, "error": "schema_capture is Windows-only"}
    return None


def _snap_dir() -> Path:
    """Where snapshots live (state/schema_snapshots under WAVEFORM_MCP_STATE_DIR
    or the current directory). Created on first use, not at import."""
    folder = safety.snapshots_dir()
    folder.mkdir(parents=True, exist_ok=True)
    return folder


TOOLS: list[dict[str, Any]] = [
    {
        "name": "schema_snapshot_current_edit",
        "description": (
            "Save the active Waveform Edit (Ctrl+S) and copy the .tracktionedit file to "
            "state/schema_snapshots/ with a label. Returns the snapshot path. "
            "Call this before AND after any UI action that changes the edit, then use "
            "schema_diff_snapshots to compute the delta."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["edit_file_path", "label"],
            "properties": {
                "edit_file_path": {
                    "type": "string",
                    "description": "Path to the .tracktionedit file Waveform writes to",
                },
                "label": {
                    "type": "string",
                    "description": "Human label, e.g. 'before-add-clip'",
                },
                "skip_save": {
                    "type": "boolean",
                    "default": False,
                    "description": "Skip Ctrl+S — useful when the file is already up-to-date",
                },
            },
        },
    },
    {
        "name": "schema_diff_snapshots",
        "description": (
            "Diff two XML snapshots saved by schema_snapshot_current_edit. Returns "
            "added elements, removed elements, and a unified text diff. Use this to "
            "extract the XML schema for whatever changed between two snapshots."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["before", "after"],
            "properties": {
                "before": {"type": "string", "description": "Path to before-snapshot"},
                "after": {"type": "string", "description": "Path to after-snapshot"},
                "context_lines": {"type": "integer", "default": 3},
            },
        },
    },
    {
        "name": "schema_list_snapshots",
        "description": "List all schema snapshots captured this session.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def schema_snapshot_current_edit(args: dict) -> dict:
    err = _windows_only()
    if err:
        return err

    edit_path = Path(args["edit_file_path"])
    label = args["label"]
    if not edit_path.exists():
        return {"ok": False, "error": f"edit file not found: {edit_path}"}

    if not args.get("skip_save"):
        from .waveform_workflows import waveform_save
        save_res = waveform_save({})
        if not save_res.get("ok"):
            return {"ok": False, "error": f"save failed: {save_res.get('error')}"}
        time.sleep(0.6)  # let Waveform finish writing

    safe_label = re.sub(r"[^a-zA-Z0-9_-]", "_", label)
    ts = int(time.time() * 1000)
    snap_path = _snap_dir() / f"{ts}_{safe_label}.tracktionedit"
    snap_path.write_bytes(edit_path.read_bytes())
    return {
        "ok": True,
        "label": label,
        "snapshot_path": str(snap_path),
        "size_bytes": snap_path.stat().st_size,
    }


def schema_diff_snapshots(args: dict) -> dict:
    before = Path(args["before"])
    after = Path(args["after"])
    if not before.exists() or not after.exists():
        return {"ok": False, "error": "one or both snapshots missing"}
    ctx = int(args.get("context_lines", 3))

    a = before.read_text(encoding="utf-8").splitlines(keepends=False)
    b = after.read_text(encoding="utf-8").splitlines(keepends=False)
    diff = list(
        difflib.unified_diff(
            a,
            b,
            fromfile=before.name,
            tofile=after.name,
            n=ctx,
        )
    )

    # element-level summary
    elem_re = re.compile(r"<([A-Z][A-Z0-9]*)")
    added_elems: dict[str, int] = {}
    removed_elems: dict[str, int] = {}
    for line in diff:
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        if line.startswith("+"):
            for m in elem_re.findall(line):
                added_elems[m] = added_elems.get(m, 0) + 1
        elif line.startswith("-"):
            for m in elem_re.findall(line):
                removed_elems[m] = removed_elems.get(m, 0) + 1

    return {
        "ok": True,
        "added_elements": added_elems,
        "removed_elements": removed_elems,
        "diff_lines": len(diff),
        "diff": "\n".join(diff[:400]),  # cap so MCP responses stay manageable
        "diff_truncated": len(diff) > 400,
    }


def schema_list_snapshots(args: dict) -> dict:
    files = sorted(_snap_dir().glob("*.tracktionedit"))
    return {
        "ok": True,
        "snapshots": [
            {"path": str(f), "size": f.stat().st_size, "label": f.stem.split("_", 1)[-1]}
            for f in files
        ],
    }


HANDLERS = {
    "schema_snapshot_current_edit": schema_snapshot_current_edit,
    "schema_diff_snapshots": schema_diff_snapshots,
    "schema_list_snapshots": schema_list_snapshots,
}
