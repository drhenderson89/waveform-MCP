"""Step clip tools — drum-machine-style step sequencing.

A step clip has CHANNELS (drum rows) and PATTERNS (programmed step
sequences). Each pattern has rows of '0'/'1' on/off + a velocity per step.
Schema captured from a real Waveform "Convert to Step clip" fixture.

This is the LLM-friendly path to drum programming:
    pads = [{midi:36,name:"Kick"}, {midi:38,name:"Snare"}, {midi:42,name:"Hat"}]
    pattern = ["1000100010001000",  # kick
               "0000100000001000",  # snare on 2 and 4
               "1010101010101010"]  # hat 8ths
    step_clip_add(track_id, pads, [pattern])

Way cleaner than building 80 MidiClip notes by hand.
"""

from __future__ import annotations

from typing import Any

from ..model import Edit, StepClip, StepClipChannel, StepClipPattern, _id
from .common import op


TOOLS: list[dict[str, Any]] = [
    {
        "name": "step_clip_add",
        "description": (
            "Add a step clip (drum-machine grid) to a track. `channels` is "
            "a list of {midi_note, name?, velocity?} dicts — one per drum "
            "row. `patterns` is a list of pattern dicts: each pattern has "
            "`rows` parallel to `channels`, where each row has a "
            "`pattern` string of '0'/'1' steps and optional `velocities` "
            "list. step_length is duration of each step in beats (0.25 = "
            "16th notes). Way cleaner than 80 individual MidiClip notes."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["track_id", "channels", "patterns",
                         "start_beats", "length_beats"],
            "properties": {
                "track_id":     {"type": "string"},
                "name":         {"type": "string", "default": "Step Clip"},
                "start_beats":  {"type": "number"},
                "length_beats": {"type": "number"},
                "step_length":  {"type": "number", "default": 0.25,
                                  "description": "Beats per step (0.25=16th, 0.5=8th, 1.0=quarter)"},
                "channels": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["midi_note"],
                        "properties": {
                            "midi_note": {"type": "integer"},
                            "name":      {"type": "string"},
                            "velocity":  {"type": "integer", "default": 127},
                            "channel":   {"type": "integer", "default": 1},
                        },
                    },
                },
                "patterns": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["rows"],
                        "properties": {
                            "rows": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "required": ["pattern"],
                                    "properties": {
                                        "pattern":    {"type": "string"},
                                        "velocities": {"type": "array",
                                                        "items": {"type": "integer"}},
                                    },
                                },
                            },
                        },
                    },
                },
                "repeat_sequence": {"type": "boolean", "default": True},
            },
        },
    },
]


_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
def _midi_to_name(midi: int) -> str:
    return f"{_NOTE_NAMES[midi % 12]}{midi // 12 - 1}"


@op("step_clip_add", intent_template="Add step clip to {track_id}")
def step_clip_add(
    edit: Edit,
    track_id: str,
    channels: list[dict],
    patterns: list[dict],
    start_beats: float,
    length_beats: float,
    name: str = "Step Clip",
    step_length: float = 0.25,
    repeat_sequence: bool = True,
) -> dict:
    t = edit.track(track_id)

    chan_objs: list[StepClipChannel] = []
    for ch in channels:
        midi = int(ch["midi_note"])
        chan_objs.append(StepClipChannel(
            channel=int(ch.get("channel", 1)),
            note=midi,
            velocity=int(ch.get("velocity", 127)),
            name=ch.get("name") or _midi_to_name(midi),
        ))

    pat_objs: list[StepClipPattern] = []
    for pat in patterns:
        rows: list[dict] = []
        for row in pat["rows"]:
            pat_str = row["pattern"]
            vels = row.get("velocities") or [127] * len(pat_str)
            # Ensure velocities length matches pattern length
            if len(vels) < len(pat_str):
                vels = list(vels) + [127] * (len(pat_str) - len(vels))
            rows.append({"pattern": pat_str, "velocities": vels[:len(pat_str)]})
        pat_objs.append(StepClipPattern(
            note_length=float(step_length),
            rows=rows,
        ))

    clip = StepClip(
        id=_id("clip"),
        name=name,
        start_beats=float(start_beats),
        length_beats=float(length_beats),
        channels=chan_objs,
        patterns=pat_objs,
        repeat_sequence=bool(repeat_sequence),
    )
    t.clips.append(clip)
    return {"clip_id": clip.id, "channel_count": len(chan_objs),
            "pattern_count": len(pat_objs)}


HANDLERS = {"step_clip_add": step_clip_add}
