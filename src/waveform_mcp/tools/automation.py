"""Automation lane tools — write parameter curves on tracks.

A `lane` is a list of (beat, value, curve) points attached to a target on
a track. Targets:
  - "volume"  → track volume in dB (implicit volume plugin)
  - "pan"     → track pan -1..1
  - "plugin/<plugin_id>/<param>" → any param on a track plugin

Curve shape: 0 = linear segment, ±1 = exponential bend.

`automation_envelope` is the convenience wrapper for the most common case
("ramp from A to B between beats X and Y"); use `automation_add` to drop
a list of arbitrary points (sidechain duck pattern, LFO-style sweeps).
"""

from __future__ import annotations

import math
from typing import Any

from ..model import AutomationLane, AutomationPoint, Edit
from .common import op


TOOLS: list[dict[str, Any]] = [
    {
        "name": "automation_add",
        "description": (
            "Add an automation lane to a track. target ∈ {'volume', 'pan', "
            "'plugin/<plugin_id>/<param>'}. points = [{beat, value, curve?}]. "
            "Replaces any existing lane on the same target."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["track_id", "target", "points"],
            "properties": {
                "track_id": {"type": "string"},
                "target": {"type": "string"},
                "points": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["beat", "value"],
                        "properties": {
                            "beat": {"type": "number"},
                            "value": {"type": "number"},
                            "curve": {"type": "number", "default": 0.0},
                        },
                    },
                },
            },
        },
    },
    {
        "name": "automation_envelope",
        "description": (
            "Convenience: ramp a target from start_value to end_value between "
            "start_beat and end_beat. shape ∈ {'linear', 'exp_in', 'exp_out', "
            "'sine'}. exp_in starts slow then speeds up; exp_out is the inverse; "
            "sine is a smooth S-curve. Appends to an existing lane on the same "
            "target if one exists, otherwise creates a new lane."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["track_id", "target", "start_beat", "end_beat", "start_value", "end_value"],
            "properties": {
                "track_id": {"type": "string"},
                "target": {"type": "string"},
                "start_beat": {"type": "number"},
                "end_beat": {"type": "number"},
                "start_value": {"type": "number"},
                "end_value": {"type": "number"},
                "shape": {"type": "string", "enum": ["linear", "exp_in", "exp_out", "sine"], "default": "linear"},
                "steps": {"type": "integer", "default": 16, "description": "Number of intermediate points to render the curve"},
            },
        },
    },
    {
        "name": "automation_clear",
        "description": "Remove all automation lanes from a track, or just the lane on a given target.",
        "inputSchema": {
            "type": "object",
            "required": ["track_id"],
            "properties": {
                "track_id": {"type": "string"},
                "target": {"type": "string", "description": "Optional — if omitted, clears all lanes"},
            },
        },
    },
    {
        "name": "automation_list",
        "description": "List automation lanes on a track (or all tracks if track_id omitted).",
        "inputSchema": {
            "type": "object",
            "properties": {"track_id": {"type": "string"}},
        },
    },
]


# ---------- impls ----------


def _find_lane(track, target: str) -> AutomationLane | None:
    for lane in track.automation:
        if lane.target == target:
            return lane
    return None


def _convert_value(target: str, db_or_pan: float) -> float:
    """Map user-friendly units to Tracktion's stored value.

    For target='volume', users pass dB; we convert to the normalized 0..1
    that the volume plugin actually stores (matching xml_writer._db_to_norm).
    For 'pan', the -1..1 range is already what's stored.
    For plugin params, pass-through — params are stored as-is.
    """
    if target == "volume":
        if db_or_pan == 0.0:
            return 0.6376281380653381
        return 0.6376281380653381 * (10.0 ** (db_or_pan / 40.0))
    return db_or_pan


# Volume AUTOMATIONCURVE silently breaks Waveform's volume plugin — the
# track goes silent whenever a curve is attached, even if every point
# matches the static value. Real fix needs a hand-edited fixture so we
# can copy Tracktion's exact paramID + curve schema. Until then we hard-
# refuse the target so composers don't silently produce broken edits.
_VOLUME_TARGET_ERROR = (
    "automation target='volume' is disabled — Waveform's volume plugin does "
    "not honor our current AUTOMATIONCURVE schema and the track goes silent. "
    "Use one of: mix_set / mix_apply_reference for static levels, "
    "clip_set(fade_in_beats|fade_out_beats) for fades, or split the song "
    "into multiple clips with per-clip gain_db. Pan + plugin/<id>/<param> "
    "targets are still supported."
)


@op("automation_add", intent_template="Add automation on {track_id}/{target}")
def automation_add(edit: Edit, track_id: str, target: str, points: list[dict]) -> dict:
    if target == "volume":
        raise ValueError(_VOLUME_TARGET_ERROR)
    t = edit.track(track_id)
    pts = [
        AutomationPoint(
            beat=float(p["beat"]),
            value=_convert_value(target, float(p["value"])),
            curve=float(p.get("curve", 0.0)),
        )
        for p in points
    ]
    pts.sort(key=lambda p: p.beat)
    # Replace any existing lane on same target
    t.automation = [l for l in t.automation if l.target != target]
    t.automation.append(AutomationLane(target=target, points=pts))
    return {"track_id": track_id, "target": target, "points": len(pts)}


@op("automation_envelope", intent_template="Envelope on {track_id}/{target}")
def automation_envelope(
    edit: Edit,
    track_id: str,
    target: str,
    start_beat: float,
    end_beat: float,
    start_value: float,
    end_value: float,
    shape: str = "linear",
    steps: int = 16,
) -> dict:
    if target == "volume":
        raise ValueError(_VOLUME_TARGET_ERROR)
    if end_beat <= start_beat:
        raise ValueError("end_beat must be > start_beat")
    if steps < 2:
        steps = 2

    def shape_fn(t: float) -> float:
        # t in 0..1 → 0..1 according to shape
        if shape == "linear":
            return t
        if shape == "exp_in":
            return t * t
        if shape == "exp_out":
            return 1.0 - (1.0 - t) ** 2
        if shape == "sine":
            return 0.5 - 0.5 * math.cos(math.pi * t)
        raise ValueError(f"unknown shape: {shape}")

    new_points: list[AutomationPoint] = []
    for i in range(steps + 1):
        t = i / steps
        beat = start_beat + (end_beat - start_beat) * t
        raw_value = start_value + (end_value - start_value) * shape_fn(t)
        new_points.append(AutomationPoint(
            beat=beat,
            value=_convert_value(target, raw_value),
            curve=0.0,
        ))

    track = edit.track(track_id)
    lane = _find_lane(track, target)
    if lane is None:
        lane = AutomationLane(target=target, points=[])
        track.automation.append(lane)
    # Drop any existing points inside the envelope range, then append new
    lane.points = [p for p in lane.points if p.beat < start_beat or p.beat > end_beat]
    lane.points.extend(new_points)
    lane.points.sort(key=lambda p: p.beat)
    # Anchor: if the lane's first point is later than beat 0, prepend an
    # anchor at beat 0 with the same value as that first point. Without
    # this Tracktion silences (or zero-defaults) the param before the curve
    # starts — which made the pad and lead inaudible until their fade-out.
    if lane.points and lane.points[0].beat > 0.0:
        lane.points.insert(0, AutomationPoint(beat=0.0, value=lane.points[0].value, curve=0.0))
    return {"track_id": track_id, "target": target, "points": len(lane.points)}


@op("automation_clear", intent_template="Clear automation on {track_id}")
def automation_clear(edit: Edit, track_id: str, target: str | None = None) -> dict:
    t = edit.track(track_id)
    if target is None:
        n = len(t.automation)
        t.automation = []
        return {"track_id": track_id, "removed": n}
    before = len(t.automation)
    t.automation = [l for l in t.automation if l.target != target]
    return {"track_id": track_id, "removed": before - len(t.automation)}


def automation_list(args: dict) -> dict:
    """Read-only — direct STORE access."""
    from ..model import STORE
    edit = STORE.edit
    track_id = args.get("track_id")
    out = []
    for t in edit.tracks:
        if track_id and t.id != track_id:
            continue
        for lane in t.automation:
            out.append({
                "track_id": t.id,
                "target": lane.target,
                "point_count": len(lane.points),
                "first_beat": lane.points[0].beat if lane.points else None,
                "last_beat": lane.points[-1].beat if lane.points else None,
            })
    return {"ok": True, "lanes": out}


HANDLERS = {
    "automation_add": automation_add,
    "automation_envelope": automation_envelope,
    "automation_clear": automation_clear,
    "automation_list": automation_list,
}
