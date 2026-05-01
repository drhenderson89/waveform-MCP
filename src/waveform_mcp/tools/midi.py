"""MIDI authoring tools: clip create, note add/remove/quantize."""

from __future__ import annotations

from typing import Any

from ..model import Edit, MidiClip, Note, _id
from .common import op

TOOLS: list[dict[str, Any]] = [
    {
        "name": "midi_clip_add",
        "description": "Create an empty MIDI clip on a track.",
        "inputSchema": {
            "type": "object",
            "required": ["track_id", "start_beats", "length_beats"],
            "properties": {
                "track_id": {"type": "string"},
                "name": {"type": "string", "default": "Clip"},
                "start_beats": {"type": "number"},
                "length_beats": {"type": "number"},
            },
        },
    },
    {
        "name": "midi_notes_add",
        "description": (
            "Add notes to an existing MIDI clip. Pass an array of {pitch, start_beats, "
            "length_beats, velocity?, colour?}. Pitch is MIDI 0-127. Channel is per-clip, "
            "set when the clip is created (not per-note)."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["clip_id", "notes"],
            "properties": {
                "clip_id": {"type": "string"},
                "notes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["pitch", "start_beats", "length_beats"],
                        "properties": {
                            "pitch": {"type": "integer", "minimum": 0, "maximum": 127},
                            "start_beats": {"type": "number"},
                            "length_beats": {"type": "number"},
                            "velocity": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 127,
                                "default": 100,
                            },
                            "colour": {
                                "type": "integer",
                                "minimum": 0,
                                "default": 0,
                                "description": "Display tint (NOT channel)",
                            },
                        },
                    },
                },
            },
        },
    },
    {
        "name": "midi_notes_clear",
        "description": "Remove all notes from a MIDI clip.",
        "inputSchema": {
            "type": "object",
            "required": ["clip_id"],
            "properties": {"clip_id": {"type": "string"}},
        },
    },
    {
        "name": "midi_clip_quantize",
        "description": (
            "Snap note start times in a clip to the nearest grid division. "
            "Grid is in beats: 0.25 = 16th notes, 0.5 = 8ths, etc. Strength 0..1."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["clip_id", "grid_beats"],
            "properties": {
                "clip_id": {"type": "string"},
                "grid_beats": {"type": "number", "default": 0.25},
                "strength": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "default": 1.0,
                },
            },
        },
    },
]


# ---------- impls ----------


def _find_midi_clip(edit: Edit, clip_id: str) -> MidiClip:
    c = edit.clip(clip_id)
    if not isinstance(c, MidiClip):
        raise TypeError(f"clip {clip_id} is not a MIDI clip")
    return c


@op("midi_clip_add", intent_template="MIDI clip on {track_id}")
def midi_clip_add(
    edit: Edit,
    track_id: str,
    start_beats: float,
    length_beats: float,
    name: str = "Clip",
) -> dict:
    t = edit.track(track_id)
    c = MidiClip(id=_id("clip"), name=name, start_beats=start_beats, length_beats=length_beats)
    t.clips.append(c)
    return {"clip_id": c.id}


@op("midi_notes_add", intent_template="Add notes to {clip_id}")
def midi_notes_add(edit: Edit, clip_id: str, notes: list[dict]) -> dict:
    c = _find_midi_clip(edit, clip_id)
    added = 0
    for n in notes:
        c.notes.append(
            Note(
                pitch=int(n["pitch"]),
                start_beats=float(n["start_beats"]),
                length_beats=float(n["length_beats"]),
                velocity=int(n.get("velocity", 100)),
                colour=int(n.get("colour", 0)),
            )
        )
        added += 1
    return {"clip_id": clip_id, "added": added}


@op("midi_notes_clear", intent_template="Clear notes in {clip_id}")
def midi_notes_clear(edit: Edit, clip_id: str) -> dict:
    c = _find_midi_clip(edit, clip_id)
    c.notes.clear()
    return {"clip_id": clip_id}


@op("midi_clip_quantize", intent_template="Quantize {clip_id} @ {grid_beats}")
def midi_clip_quantize(
    edit: Edit, clip_id: str, grid_beats: float = 0.25, strength: float = 1.0
) -> dict:
    c = _find_midi_clip(edit, clip_id)
    if grid_beats <= 0:
        raise ValueError("grid_beats must be > 0")
    strength = max(0.0, min(1.0, strength))
    for n in c.notes:
        snapped = round(n.start_beats / grid_beats) * grid_beats
        n.start_beats = n.start_beats + (snapped - n.start_beats) * strength
    return {"clip_id": clip_id, "notes": len(c.notes)}


HANDLERS = {
    "midi_clip_add": midi_clip_add,
    "midi_notes_add": midi_notes_add,
    "midi_notes_clear": midi_notes_clear,
    "midi_clip_quantize": midi_clip_quantize,
}
