"""Edit lifecycle tools: create, open, save, flush, undo, summary."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..events import flush as flush_event
from ..events import narrate, publish
from ..model import STORE, Edit
from ..xml_reader import read as xml_read
from ..xml_writer import write as xml_write
from .common import op

# Path of the .tracktionedit currently being mirrored to disk on `flush`.
# Single string at module scope — the server is single-session.
_open_path: str | None = None


TOOLS: list[dict[str, Any]] = [
    {
        "name": "edit_create",
        "description": (
            "Create a new empty Edit (song). Sets tempo, time signature, and key. "
            "Replaces the in-memory Edit; does not write to disk until edit_save or flush."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "default": "Untitled"},
                "tempo": {"type": "number", "default": 120.0},
                "time_sig_num": {"type": "integer", "default": 4},
                "time_sig_denom": {"type": "integer", "default": 4},
                "key_root": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 11,
                    "default": 0,
                    "description": "0=C, 1=C#, ... 11=B",
                },
                "key_mode": {"type": "string", "enum": ["major", "minor"], "default": "major"},
                "length_beats": {"type": "number", "default": 64.0},
            },
        },
    },
    {
        "name": "edit_open",
        "description": "Load an existing .tracktionedit file from disk into the model.",
        "inputSchema": {
            "type": "object",
            "required": ["path"],
            "properties": {"path": {"type": "string"}},
        },
    },
    {
        "name": "edit_save",
        "description": "Serialize the current Edit to a .tracktionedit file. Sets the flush target.",
        "inputSchema": {
            "type": "object",
            "required": ["path"],
            "properties": {"path": {"type": "string"}},
        },
    },
    {
        "name": "flush",
        "description": (
            "Write the current Edit to the previously-opened/saved .tracktionedit path. "
            "Use this between batches of mutations so Waveform (when open) sees a single, "
            "coherent update instead of mid-edit flicker."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "edit_summary",
        "description": "Return a JSON summary of the current Edit (tracks, clips, markers, tempo).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "edit_save_stem",
        "description": (
            "Save a stem-mix copy of the current Edit: only named tracks "
            "audible, all others muted. Pass `track_ids` or `track_names` "
            "(list). Use in a loop to produce per-track stems for rendering."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["out_path"],
            "properties": {
                "out_path":    {"type": "string"},
                "track_ids":   {"type": "array", "items": {"type": "string"}},
                "track_names": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    {
        "name": "edit_inspect",
        "description": (
            "Detailed structural dump of the current Edit. Heavier than edit_summary — "
            "includes every plugin's params, clip gain/fade/offset, automation lane "
            "counts, master chain. Use after edit_open to understand a song before "
            "mutating it."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "undo",
        "description": "Roll back the most recent mutation.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "narrate",
        "description": (
            "Tell the user (via the event stream / preview UI) what the agent is about to do, "
            "in plain English. Call this before a batch of mutations so the user can follow along."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["text"],
            "properties": {"text": {"type": "string"}},
        },
    },
]


# ---------- impls ----------


@op("edit_create", intent_template="Create new edit '{name}'")
def edit_create(
    edit: Edit,
    name: str = "Untitled",
    tempo: float = 120.0,
    time_sig_num: int = 4,
    time_sig_denom: int = 4,
    key_root: int = 0,
    key_mode: str = "major",
    length_beats: float = 64.0,
) -> dict:
    fresh = Edit(
        name=name,
        tempo=tempo,
        time_sig_num=time_sig_num,
        time_sig_denom=time_sig_denom,
        key_root=key_root,
        key_mode=key_mode,  # type: ignore[arg-type]
        length_beats=length_beats,
    )
    # Replace in-place because the @op decorator passed `edit` by reference;
    # easiest way to "create" is to overwrite the store after the fact.
    STORE.replace(fresh)
    return {"edit_id": fresh.id, "name": fresh.name}


def edit_open(args: dict) -> dict:
    global _open_path
    path = args["path"]
    try:
        loaded = xml_read(path)
    except Exception as e:
        return {"ok": False, "error": str(e), "type": type(e).__name__}
    STORE.replace(loaded)
    _open_path = path
    publish({"kind": "edit_opened", "path": path, "summary": loaded.summary()})
    return {"ok": True, "edit_id": loaded.id, "tracks": len(loaded.tracks)}


def edit_save(args: dict) -> dict:
    global _open_path
    path = args["path"]
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        xml_write(STORE.edit, path)
    except Exception as e:
        return {"ok": False, "error": str(e), "type": type(e).__name__}
    _open_path = path
    flush_event(target=path, ok=True)
    return {"ok": True, "path": path}


def flush(args: dict) -> dict:
    if _open_path is None:
        return {"ok": False, "error": "no flush target — call edit_save(path) first"}
    try:
        xml_write(STORE.edit, _open_path)
    except Exception as e:
        flush_event(target=_open_path, ok=False, error=str(e))
        return {"ok": False, "error": str(e)}
    flush_event(target=_open_path, ok=True)
    return {"ok": True, "path": _open_path}


def edit_summary(args: dict) -> dict:
    return {"ok": True, "summary": STORE.edit.summary()}


def edit_save_stem(args: dict) -> dict:
    """Save a copy of the current Edit as a 'stem' file: muted/soloed so only
    the named tracks are audible. Use to render individual stems by:
      1. edit_save_stem(track_names=['Drums'], out_path=...)
      2. waveform_revert_to_saved (or open the new file in Waveform)
      3. waveform_render_to_mp3
      4. Repeat for each stem.

    Pass `track_ids` OR `track_names` (not both). If neither, returns the
    full edit unchanged.
    """
    import copy as _copy
    from pathlib import Path

    out_path = args["out_path"]
    track_ids = set(args.get("track_ids", []))
    track_names = set(args.get("track_names", []))

    edit = _copy.deepcopy(STORE.edit)
    if track_ids or track_names:
        for t in edit.tracks:
            keep = (t.id in track_ids) or (t.name in track_names)
            t.mute = not keep
            t.solo = False  # rely on mute-others rather than solo
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    xml_write(edit, out_path)
    return {"ok": True, "path": out_path,
            "kept": [t.name for t in edit.tracks if not t.mute]}


def edit_inspect(args: dict) -> dict:
    """Detailed structural dump. See tool description for shape."""
    from ..model import AudioClip, MidiClip
    e = STORE.edit
    tracks_out = []
    for t in e.tracks:
        clips_out = []
        for c in t.clips:
            entry: dict[str, Any] = {
                "id": c.id, "kind": c.kind, "name": c.name,
                "start_beats": c.start_beats, "length_beats": c.length_beats,
                "gain_db": getattr(c, "gain_db", 0.0),
            }
            if isinstance(c, AudioClip):
                entry.update({
                    "file_path": c.file_path, "warp_mode": c.warp_mode,
                    "fade_in_beats": c.fade_in_beats,
                    "fade_out_beats": c.fade_out_beats,
                    "offset_in_source_beats": c.offset_in_source_beats,
                })
            elif isinstance(c, MidiClip):
                entry["note_count"] = len(c.notes)
            clips_out.append(entry)
        plugins_out = [
            {"id": p.id, "type": p.type, "name": p.name, "enabled": p.enabled,
             "param_count": len(p.params), "params": p.params,
             "sounds": len(p.sounds), "modmatrix_items": len(p.modmatrix)}
            for p in t.plugins
        ]
        automation_out = [
            {"target": l.target, "point_count": len(l.points)}
            for l in t.automation
        ]
        tracks_out.append({
            "id": t.id, "name": t.name, "kind": t.kind,
            "volume_db": t.volume_db, "pan": t.pan,
            "mute": t.mute, "solo": t.solo,
            "clip_count": len(t.clips), "clips": clips_out,
            "plugins": plugins_out, "automation": automation_out,
            "send_count": len(t.sends),
        })
    return {
        "ok": True,
        "edit": {
            "id": e.id, "name": e.name, "tempo": e.tempo,
            "time_signature": [e.time_sig_num, e.time_sig_denom],
            "key": [e.key_root, e.key_mode],
            "length_beats": e.length_beats,
            "master_volume_db": e.master_volume_db,
            "master_chain": [
                {"id": p.id, "type": p.type, "name": p.name, "params": p.params}
                for p in e.master_chain
            ],
            "tracks": tracks_out,
            "markers": [
                {"name": m.name, "start_beats": m.start_beats, "length_beats": m.length_beats}
                for m in e.markers
            ],
        },
    }


def undo(args: dict) -> dict:
    ok = STORE.undo()
    publish({"kind": "undo", "ok": ok})
    return {"ok": ok}


def narrate_tool(args: dict) -> dict:
    text = args.get("text", "")
    narrate(text)
    return {"ok": True}


HANDLERS = {
    "edit_create": edit_create,
    "edit_open": edit_open,
    "edit_save": edit_save,
    "edit_save_stem": edit_save_stem,
    "flush": flush,
    "edit_summary": edit_summary,
    "edit_inspect": edit_inspect,
    "undo": undo,
    "narrate": narrate_tool,
}
