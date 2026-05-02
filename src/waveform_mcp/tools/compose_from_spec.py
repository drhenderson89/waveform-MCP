"""Spec-driven composition builder.

Takes a CompositionSpec (a pure data object) and constructs a
.tracktionedit on disk. Drives the existing MCP primitives — track_add,
plugin_add, midi_clip_add, midi_notes_add, mix_apply_reference (now
calibrated), edit_save — so behaviour is consistent with hand-authored
composer functions.

The builder is the single back-end for *any* spec source, whether that
spec came from `reference_adapter.spec_from_analyzer_json()` (a real
song's analysis) or from a hand-built spec or from a different composer
that emits a CompositionSpec.

Public API:
  compose_from_spec(args: dict) -> dict
    args = {
      "spec": dict | CompositionSpec   # required
      "out_path": str                  # required
      "calibrate": bool = True         # use mix_calibration
    }
    returns {ok, path, tempo, bars, tracks, warnings}
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from ..composition_spec import (
    ChordEvent,
    CompositionSpec,
    DrumPatternSpec,
    NoteEvent,
    SectionSpec,
    TrackSpec,
)
from . import edit as edit_tools
from . import midi as midi_tools
from . import plugins as plugin_tools
from . import tracks as track_tools
from ..model import STORE, _id

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Public entry                                                                #
# --------------------------------------------------------------------------- #


def compose_from_spec(args: dict[str, Any]) -> dict[str, Any]:
    """Construct a .tracktionedit from a CompositionSpec.

    Tolerant: spec validation problems are logged + returned in
    `warnings` but don't abort. Hard errors (no spec, no out_path,
    unwritable target) raise / return ok=False.
    """
    raw_spec = args.get("spec")
    out_path = args.get("out_path")
    calibrate = bool(args.get("calibrate", True))

    if raw_spec is None:
        return {"ok": False, "error": "missing 'spec' argument"}
    if not out_path:
        return {"ok": False, "error": "missing 'out_path' argument"}

    # Coerce to CompositionSpec
    try:
        if isinstance(raw_spec, CompositionSpec):
            spec = raw_spec
        elif isinstance(raw_spec, dict):
            spec = CompositionSpec.from_dict(raw_spec)
        else:
            return {"ok": False, "error": f"spec must be dict or CompositionSpec, got {type(raw_spec).__name__}"}
    except Exception as e:
        return {"ok": False, "error": f"failed to parse spec: {e}"}

    problems = spec.validate()
    if problems:
        logger.warning("compose_from_spec: %d validation issues; proceeding anyway", len(problems))
        for p in problems:
            logger.warning("  - %s", p)

    out_path = str(out_path)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)

    return _build(spec, out_path, calibrate=calibrate, validation_problems=problems)


# --------------------------------------------------------------------------- #
# Build                                                                       #
# --------------------------------------------------------------------------- #


def _build(spec: CompositionSpec, out_path: str, *,
           calibrate: bool, validation_problems: list[str]) -> dict[str, Any]:
    warnings: list[str] = list(validation_problems)

    # Length: round up to whole bars; minimum 4 bars
    total_beats = max(spec.total_length_beats, float(spec.time_sig_num) * 4)
    total_bars = int(_ceil_div(total_beats, spec.time_sig_num))
    total_beats = float(total_bars * spec.time_sig_num)

    # 1. Create the edit
    edit_tools.edit_create({
        "name": spec.name,
        "tempo": float(spec.tempo_bpm),
        "time_sig_num": int(spec.time_sig_num),
        "time_sig_denom": int(spec.time_sig_denom),
        "key_root": int(spec.key_root_pc),
        "key_mode": spec.key_mode,
        "length_beats": float(total_beats),
    })

    track_id_by_role: dict[str, str] = {}

    # 2. Build each track
    for tspec in spec.tracks:
        try:
            tid = _build_track(tspec, spec, total_beats, warnings)
            if tid:
                track_id_by_role[tspec.role] = tid
        except Exception as e:
            logger.exception("compose_from_spec: failed to build track %r", tspec.name)
            warnings.append(f"track '{tspec.name}' failed: {e}")

    # 3. Apply mix levels (genre/role-based, plugin-aware via calibration)
    for tspec in spec.tracks:
        tid = track_id_by_role.get(tspec.role)
        if not tid:
            continue
        try:
            track_tools.mix_apply_reference({
                "track_id": tid,
                "genre": spec.genre,
                "role": tspec.role,
                "pan": tspec.pan,
                "offset_db": float(tspec.mix_offset_db),
                "calibrate": calibrate,
            })
        except Exception as e:
            warnings.append(f"mix_apply_reference failed on {tspec.role}: {e}")

    # 4. Master chain
    if spec.master_chain.plugins:
        try:
            _apply_master_chain(spec.master_chain.plugins, spec.master_chain.params)
        except Exception as e:
            warnings.append(f"master chain build failed: {e}")

    # 5. Save
    try:
        edit_tools.edit_save({"path": out_path})
    except Exception as e:
        return {
            "ok": False, "error": f"edit_save failed: {e}",
            "warnings": warnings,
        }

    summary = STORE.edit.summary()
    return {
        "ok": True,
        "path": out_path,
        "tempo": spec.tempo_bpm,
        "bars": total_bars,
        "genre": spec.genre,
        "tracks": [t["name"] for t in summary["tracks"]],
        "section_count": len(spec.sections),
        "chord_count": len(spec.chord_track),
        "warnings": warnings,
        "fidelity_caveats": spec.provenance.get("fidelity_caveats", []),
    }


# --------------------------------------------------------------------------- #
# Track construction                                                          #
# --------------------------------------------------------------------------- #


def _build_track(tspec: TrackSpec, spec: CompositionSpec, total_beats: float,
                 warnings: list[str]) -> str | None:
    """Add a track + its plugins + its content. Returns the track_id."""
    # Add the track
    add_res = track_tools.track_add({
        "name": tspec.name or tspec.role,
        "kind": tspec.kind,
    })
    tid = add_res["track_id"]

    # Add plugins in chain order
    for plug_name, plug_params in zip(tspec.chain.plugins, tspec.chain.params):
        try:
            res = plugin_tools.plugin_add({
                "track_id": tid,
                "type": plug_name,
            })
            # Pass through any params via plugin_set_param
            for k, v in (plug_params or {}).items():
                plugin_tools.plugin_set_param({
                    "track_id": tid,
                    "plugin_id": res["plugin_id"],
                    "param": k, "value": v,
                })
        except Exception as e:
            warnings.append(f"plugin_add({plug_name}) on {tspec.name}: {e}")

    # B2: append plate reverb if track has a reverb estimate
    if tspec.reverb_rt60_s is not None:
        try:
            from ..production_analysis import ReverbEstimate
            est = ReverbEstimate(
                rt60_seconds=float(tspec.reverb_rt60_s),
                wet_ratio=float(tspec.reverb_wet or 0.2),
                confidence=1.0,
            )
            params = est.to_plate_reverb_params()
            plugin_tools.plugin_add_reverb({
                "track_id": tid, "type": "plate",
                "size": params["size"], "decay": params["decay"],
                "mix": params["mix"], "high_damp": 0.5,
            })
        except Exception as e:
            warnings.append(f"plate reverb add on {tspec.name}: {e}")

    # B3: pan automation breakpoints
    if tspec.pan_automation:
        try:
            from . import automation as auto_tools
            points = [{"beat": float(b), "value": float(v)}
                      for b, v in tspec.pan_automation]
            auto_tools.automation_add({
                "track_id": tid, "target": "pan", "points": points,
            })
        except Exception as e:
            warnings.append(f"pan automation on {tspec.name}: {e}")

    # Note: volume_automation on TrackSpec is currently a no-op at the
    # builder level — Waveform's volume plugin doesn't honor our
    # AUTOMATIONCURVE schema and the track goes silent. The spec field
    # is preserved for future work (multi-clip ride approach) and so
    # callers can hand-edit it. We surface a one-line warning so
    # downstream users know the field was seen but not applied.
    if tspec.volume_automation:
        warnings.append(
            f"volume_automation on {tspec.name} ignored — Waveform's volume "
            "plugin breaks under our AUTOMATIONCURVE schema. Use mix_offset_db "
            "for static rides or split into multiple sections."
        )

    # B2: append a ducking compressor to approximate sidechain pumping.
    # Tracktion doesn't expose true sidechaining at the model layer, so we
    # use a fast-attack compressor on the pumped track. It's a heuristic —
    # produces a pumping *feel* triggered by the track's own loud transients,
    # which on bass/pad tracks coincide with kick onsets enough to read as
    # "pumpy."
    if tspec.sidechain_to_drums:
        try:
            from ..model import Plugin
            STORE.edit.track(tid).plugins.append(Plugin(
                id=_id("plg"), type="comp", name="Pump",
                params={"threshold": -18.0, "ratio": 6.0, "attack": 1.0,
                        "release": 120.0, "knee": 1.0, "outputDb": 0.0, "rms": 0},
            ))
        except Exception as e:
            warnings.append(f"sidechain comp add on {tspec.name}: {e}")

    # Content
    if tspec.kind == "audio":
        if tspec.audio_path:
            try:
                from . import audio as audio_tools
                audio_tools.audio_clip_import({
                    "track_id": tid,
                    "file_path": tspec.audio_path,
                    "start_beats": float(tspec.audio_offset_beats),
                })
            except Exception as e:
                warnings.append(f"audio_clip_import on {tspec.name}: {e}")
        return tid

    # MIDI track: drums vs melodic
    if tspec.drum_pattern is not None:
        _lay_drum_pattern(tid, tspec.drum_pattern, spec.sections, total_beats, warnings)
    elif tspec.notes:
        _lay_notes(tid, tspec.notes, total_beats, warnings)
    elif tspec.role in ("pad", "keys") and spec.chord_track:
        # Auto-fill pad/keys with chord-track sustain
        _lay_chord_sustain(tid, spec.chord_track, octave=4, warnings=warnings)
    return tid


def _lay_drum_pattern(track_id: str, pat: DrumPatternSpec,
                      sections: list[SectionSpec], total_beats: float,
                      warnings: list[str]) -> None:
    """Lay one MIDI clip per section, looping the drum pattern through.
    Section variants override the loop notes for that section."""
    if not sections:
        # Single clip covering the whole edit
        sections = [SectionSpec(type="verse", start_beat=0,
                                length_beats=total_beats, density=0.7)]

    sorted_sections = sorted(sections, key=lambda s: s.start_beat)
    for idx, sec in enumerate(sorted_sections):
        notes_for_section = pat.section_variants.get(idx, pat.notes)
        if not notes_for_section:
            continue
        try:
            clip = midi_tools.midi_clip_add({
                "track_id": track_id,
                "start_beats": float(sec.start_beat),
                "length_beats": float(sec.length_beats),
                "name": f"Drums {sec.type}",
            })["clip_id"]
        except Exception as e:
            warnings.append(f"midi_clip_add on drums for section {idx}: {e}")
            continue

        looped = _loop_pattern_across(notes_for_section, pat.loop_length_beats,
                                      sec.length_beats)
        # Density-based velocity scaling: dim drums in low-density sections
        vel_scale = 0.6 + 0.4 * float(sec.density)
        midi_notes = [
            {"pitch": int(n.pitch),
             "start_beats": float(n.start_beat),
             "length_beats": float(n.length_beats),
             "velocity": _clip(int(n.velocity * vel_scale), 1, 127)}
            for n in looped
        ]
        try:
            midi_tools.midi_notes_add({"clip_id": clip, "notes": midi_notes})
        except Exception as e:
            warnings.append(f"midi_notes_add on drums for section {idx}: {e}")


def _lay_notes(track_id: str, notes: list[NoteEvent], total_beats: float,
               warnings: list[str]) -> None:
    """Lay all notes into a single MIDI clip covering the full edit."""
    try:
        clip = midi_tools.midi_clip_add({
            "track_id": track_id,
            "start_beats": 0.0,
            "length_beats": float(total_beats),
            "name": "Clip",
        })["clip_id"]
    except Exception as e:
        warnings.append(f"midi_clip_add for {track_id}: {e}")
        return

    midi_notes = [
        {"pitch": int(n.pitch),
         "start_beats": float(n.start_beat),
         "length_beats": float(max(0.05, n.length_beats)),
         "velocity": _clip(int(n.velocity), 1, 127)}
        for n in notes
        if 0 <= n.start_beat < total_beats
    ]
    if midi_notes:
        try:
            midi_tools.midi_notes_add({"clip_id": clip, "notes": midi_notes})
        except Exception as e:
            warnings.append(f"midi_notes_add for {track_id}: {e}")


def _lay_chord_sustain(track_id: str, chord_track: list[ChordEvent],
                       *, octave: int, warnings: list[str]) -> None:
    """Auto-fill a pad/keys track with chord-tone sustain on the chord_track."""
    if not chord_track:
        return
    last_end = max(c.start_beat + c.length_beats for c in chord_track)
    try:
        clip = midi_tools.midi_clip_add({
            "track_id": track_id,
            "start_beats": 0.0,
            "length_beats": float(last_end),
            "name": "Chord Pad",
        })["clip_id"]
    except Exception as e:
        warnings.append(f"midi_clip_add for chord sustain: {e}")
        return

    notes: list[dict] = []
    base = 12 * octave  # C4=60 at octave=4
    for c in chord_track:
        # Triad: root, third, fifth
        third = 4 if c.quality in ("maj", "maj7", "7", "aug", "add9") else 3
        fifth = 7 if c.quality not in ("dim", "dim7", "m7b5") else 6
        # Power-chord: just root + 5th
        intervals = [0, fifth] if c.quality == "5" else [0, third, fifth]
        for semis in intervals:
            notes.append({
                "pitch": int(base + c.root_pc + semis),
                "start_beats": float(c.start_beat),
                "length_beats": float(c.length_beats),
                "velocity": 80,
            })
    if notes:
        try:
            midi_tools.midi_notes_add({"clip_id": clip, "notes": notes})
        except Exception as e:
            warnings.append(f"midi_notes_add for chord sustain: {e}")


# --------------------------------------------------------------------------- #
# Master chain                                                                #
# --------------------------------------------------------------------------- #


def _apply_master_chain(plugins: list[str], params_list: list[dict]) -> None:
    """Append plugins to the master chain via the model directly."""
    from ..model import Plugin

    # Defaults for built-in master plugins
    defaults: dict[str, dict] = {
        "3bandeq":     {"loFreq": 80.0, "loGain": 2.0,
                        "midFreq": 500.0, "midGain": -2.0, "midQ": 0.7,
                        "hiFreq": 9000.0, "hiGain": 3.0},
        "comp":        {"threshold": -12.0, "ratio": 3.0, "attack": 15.0,
                        "release": 120.0, "knee": 6.0, "outputDb": 2.0, "rms": 1},
        "limiter":     {"threshold": -1.0, "release": 50.0},
    }

    for i, name in enumerate(plugins):
        params = (params_list[i] if i < len(params_list) else {}) or {}
        ptype = name
        merged = {**defaults.get(name.lower(), {}), **params}
        STORE.edit.master_chain.append(Plugin(
            id=_id("plg"), type=ptype, name=ptype, params=merged,
        ))


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _loop_pattern_across(notes: list[NoteEvent], loop_len: float,
                         section_len: float) -> list[NoteEvent]:
    """Repeat `notes` (a 0-based loop of length loop_len) across
    `section_len` beats. Returns absolute-positioned NoteEvents.
    Section start offset is added by the caller (the clip itself is
    placed at the section's start_beat)."""
    if loop_len <= 0 or section_len <= 0 or not notes:
        return []
    out: list[NoteEvent] = []
    n_iters = int(_ceil_div(section_len, loop_len))
    for k in range(n_iters):
        offset = k * loop_len
        if offset >= section_len:
            break
        for n in notes:
            start = n.start_beat + offset
            if start >= section_len:
                break
            length = min(n.length_beats, section_len - start)
            if length <= 0:
                continue
            out.append(NoteEvent(
                start_beat=start, length_beats=length,
                pitch=n.pitch, velocity=n.velocity, channel=n.channel,
            ))
    return out


def _ceil_div(a: float, b: float) -> float:
    if b <= 0:
        return 0.0
    return -(-a // b)


def _clip(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


# --------------------------------------------------------------------------- #
# MCP tool registration                                                       #
# --------------------------------------------------------------------------- #


HANDLERS = {"compose_from_spec": compose_from_spec}

TOOLS = [{
    "name": "compose_from_spec",
    "description": (
        "Build a .tracktionedit from a CompositionSpec. The spec carries "
        "tempo, key, sections, chord progression, per-track instrumentation "
        "with plugin chains, drum patterns, and per-track notes. The builder "
        "drives the existing MCP primitives (track_add, plugin_add, "
        "midi_clip_add, mix_apply_reference) so output is calibration-aware "
        "out of the box."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "spec": {
                "type": "object",
                "description": "CompositionSpec as a dict (see composition_spec.py)",
            },
            "out_path": {"type": "string"},
            "calibrate": {"type": "boolean", "default": True},
        },
        "required": ["spec", "out_path"],
    },
}]
