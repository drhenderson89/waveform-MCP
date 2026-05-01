"""Clip-level tools — list, set, move, resize, duplicate, remove.

These complement audio_clip_import / midi_clip_add so that a session that
opened an existing edit can rearrange its content without rerunning a
composer from scratch. Both AudioClip and MidiClip are addressed by id.
"""

from __future__ import annotations

import copy
from typing import Any

from ..model import AudioClip, Edit, MidiClip, _id
from .common import op


TOOLS: list[dict[str, Any]] = [
    {
        "name": "clip_list",
        "description": (
            "List clips on a track (or all tracks). Returns id, kind, name, "
            "start, length, plus per-clip gain/fade/offset for audio clips."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "track_id": {"type": "string", "description": "Optional — if omitted, lists clips across all tracks"},
            },
        },
    },
    {
        "name": "clip_set",
        "description": (
            "Modify any field on an existing clip. Pass only fields you want "
            "changed. For AudioClip: name, start_beats, length_beats, gain_db, "
            "fade_in_beats, fade_out_beats, offset_in_source_beats, warp_mode. "
            "For MidiClip: name, start_beats, length_beats, gain_db."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["clip_id"],
            "properties": {
                "clip_id": {"type": "string"},
                "name": {"type": "string"},
                "start_beats": {"type": "number"},
                "length_beats": {"type": "number"},
                "gain_db": {"type": "number"},
                "fade_in_beats": {"type": "number"},
                "fade_out_beats": {"type": "number"},
                "offset_in_source_beats": {"type": "number"},
                "warp_mode": {"type": "string", "enum": ["none", "stretch", "warp"]},
            },
        },
    },
    {
        "name": "clip_move",
        "description": "Shorthand: change a clip's start_beats. Length unchanged.",
        "inputSchema": {
            "type": "object",
            "required": ["clip_id", "new_start_beats"],
            "properties": {
                "clip_id": {"type": "string"},
                "new_start_beats": {"type": "number"},
            },
        },
    },
    {
        "name": "clip_resize",
        "description": (
            "Change a clip's length. anchor='start' (default) keeps start_beats "
            "fixed and moves the right edge. anchor='end' keeps the right edge "
            "fixed and moves start_beats so total duration changes from the left."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["clip_id", "new_length_beats"],
            "properties": {
                "clip_id": {"type": "string"},
                "new_length_beats": {"type": "number"},
                "anchor": {"type": "string", "enum": ["start", "end"], "default": "start"},
            },
        },
    },
    {
        "name": "clip_duplicate",
        "description": (
            "Clone a clip onto the same track at start_beats + offset_beats. "
            "Returns the new clip_id. Notes are deep-copied for MIDI clips."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["clip_id", "offset_beats"],
            "properties": {
                "clip_id": {"type": "string"},
                "offset_beats": {"type": "number"},
            },
        },
    },
    {
        "name": "clip_remove",
        "description": "Delete a clip by id.",
        "inputSchema": {
            "type": "object",
            "required": ["clip_id"],
            "properties": {"clip_id": {"type": "string"}},
        },
    },
]


# ---------- helpers ----------


def _find_clip(edit: Edit, clip_id: str):
    """Return (track, clip_index, clip) or raise KeyError."""
    for t in edit.tracks:
        for i, c in enumerate(t.clips):
            if c.id == clip_id:
                return t, i, c
    raise KeyError(f"clip not found: {clip_id}")


# ---------- impls ----------


def clip_list(args: dict) -> dict:
    """Read-only — bypasses STORE.apply to avoid pointless undo entries."""
    from ..model import STORE
    edit = STORE.edit
    track_id = args.get("track_id")
    out = []
    for t in edit.tracks:
        if track_id and t.id != track_id:
            continue
        for c in t.clips:
            entry: dict[str, Any] = {
                "id": c.id,
                "kind": c.kind,
                "name": c.name,
                "track_id": t.id,
                "start_beats": c.start_beats,
                "length_beats": c.length_beats,
                "gain_db": c.gain_db,
            }
            if isinstance(c, AudioClip):
                entry.update({
                    "file_path": c.file_path,
                    "warp_mode": c.warp_mode,
                    "fade_in_beats": c.fade_in_beats,
                    "fade_out_beats": c.fade_out_beats,
                    "offset_in_source_beats": c.offset_in_source_beats,
                })
            elif isinstance(c, MidiClip):
                entry["note_count"] = len(c.notes)
            out.append(entry)
    return {"ok": True, "clips": out}


_AUDIO_FIELDS = {
    "name", "start_beats", "length_beats", "gain_db",
    "fade_in_beats", "fade_out_beats", "offset_in_source_beats", "warp_mode",
}
_MIDI_FIELDS = {
    "name", "start_beats", "length_beats", "gain_db",
    "fade_in_beats", "fade_out_beats",
}


@op("clip_set", intent_template="Set fields on {clip_id}")
def clip_set(edit: Edit, clip_id: str, **fields) -> dict:
    _t, _i, c = _find_clip(edit, clip_id)
    allowed = _AUDIO_FIELDS if isinstance(c, AudioClip) else _MIDI_FIELDS
    rejected = [k for k in fields if k not in allowed]
    if rejected:
        raise ValueError(f"fields not valid for {c.kind} clip: {rejected}")
    for k, v in fields.items():
        setattr(c, k, v)
    return {"clip_id": clip_id, "updated": list(fields.keys())}


@op("clip_move", intent_template="Move {clip_id} to {new_start_beats}")
def clip_move(edit: Edit, clip_id: str, new_start_beats: float) -> dict:
    _t, _i, c = _find_clip(edit, clip_id)
    c.start_beats = float(new_start_beats)
    return {"clip_id": clip_id, "start_beats": c.start_beats}


@op("clip_resize", intent_template="Resize {clip_id} to {new_length_beats}")
def clip_resize(edit: Edit, clip_id: str, new_length_beats: float, anchor: str = "start") -> dict:
    _t, _i, c = _find_clip(edit, clip_id)
    new_len = float(new_length_beats)
    if anchor == "end":
        # Keep the right edge fixed: start moves to end - new_len.
        right_edge = c.start_beats + c.length_beats
        c.start_beats = right_edge - new_len
    c.length_beats = new_len
    return {
        "clip_id": clip_id,
        "start_beats": c.start_beats,
        "length_beats": c.length_beats,
    }


@op("clip_duplicate", intent_template="Duplicate {clip_id} +{offset_beats}")
def clip_duplicate(edit: Edit, clip_id: str, offset_beats: float) -> dict:
    track, _i, c = _find_clip(edit, clip_id)
    new = copy.deepcopy(c)
    new.id = _id("clip")
    new.start_beats = c.start_beats + float(offset_beats)
    track.clips.append(new)
    return {"clip_id": new.id, "source_clip_id": clip_id, "start_beats": new.start_beats}


@op("clip_remove", intent_template="Remove {clip_id}")
def clip_remove(edit: Edit, clip_id: str) -> dict:
    track, _i, c = _find_clip(edit, clip_id)
    track.clips = [x for x in track.clips if x.id != clip_id]
    return {"clip_id": clip_id, "removed": True}


HANDLERS = {
    "clip_list": clip_list,
    "clip_set": clip_set,
    "clip_move": clip_move,
    "clip_resize": clip_resize,
    "clip_duplicate": clip_duplicate,
    "clip_remove": clip_remove,
}
