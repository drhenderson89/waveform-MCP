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
            # Special case: an empty Sampler on a drum track is silent —
            # MIDI hits nothing because no SOUND entries are loaded. Auto-
            # populate with the bundled factory drum kit so drums actually
            # play out of the box.
            if (plug_name.lower() == "sampler"
                    and tspec.role in ("drums",)):
                _populate_default_drum_kit(tid, res["plugin_id"], warnings)
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
        # Tracks with explicit notes (bass, lead, keys post-transcription)
        # use them directly.
        _lay_notes(tid, tspec.notes, total_beats, warnings)
    elif tspec.role == "pad" and spec.chord_track:
        # Pads sustain chord tones — only role that auto-fills from
        # the chord track.
        _lay_chord_sustain(tid, spec.chord_track, octave=4,
                           warnings=warnings, rhythm="sustain")
    return tid


def _lay_drum_pattern(track_id: str, pat: DrumPatternSpec,
                      sections: list[SectionSpec], total_beats: float,
                      warnings: list[str]) -> None:
    """Lay drums as ONE continuous clip from beat 0 → end of song.

    Per-section drum clips were producing "off-tempo" feel because each
    section's snapped start_beat was a small fraction off from the
    song's actual musical bar lines. Phase errors compound across
    sections. A single continuous clip from beat 0 keeps drums locked
    to a single tempo grid throughout — same musical alignment as a
    real drummer playing through the whole song.

    Section variants are still honored: where a section index has
    overrides, those notes replace the default for the section's beat
    range within the single clip.
    """
    if not pat.notes and not pat.section_variants:
        return

    try:
        clip = midi_tools.midi_clip_add({
            "track_id": track_id,
            "start_beats": 0.0,
            "length_beats": float(total_beats),
            "name": "Drums",
        })["clip_id"]
    except Exception as e:
        warnings.append(f"midi_clip_add on drums (continuous): {e}")
        return

    # Build a sorted section list for variant lookup
    sorted_sections = sorted(sections, key=lambda s: s.start_beat) if sections else []

    def section_for(beat: float) -> int | None:
        for idx, s in enumerate(sorted_sections):
            if s.start_beat <= beat < s.start_beat + s.length_beats:
                return idx
        return None

    midi_notes: list[dict] = []
    n_iters = int(_ceil_div(total_beats, pat.loop_length_beats))
    for k in range(n_iters):
        offset = k * pat.loop_length_beats
        if offset >= total_beats:
            break

        # Variant lookup: which section is this iteration's start in?
        sec_idx = section_for(offset)
        notes_for_iter = (pat.section_variants.get(sec_idx, pat.notes)
                          if sec_idx is not None else pat.notes)
        if not notes_for_iter:
            continue

        # Section density modulates velocity — quieter intros, louder choruses
        vel_scale = 1.0
        if sec_idx is not None and sec_idx < len(sorted_sections):
            vel_scale = 0.6 + 0.4 * float(sorted_sections[sec_idx].density)

        for n in notes_for_iter:
            start = offset + n.start_beat
            if start >= total_beats:
                continue
            length = min(n.length_beats, total_beats - start)
            if length <= 0:
                continue
            midi_notes.append({
                "pitch": int(n.pitch),
                "start_beats": float(start),
                "length_beats": float(length),
                "velocity": _clip(int(n.velocity * vel_scale), 1, 127),
            })

    if midi_notes:
        try:
            midi_tools.midi_notes_add({"clip_id": clip, "notes": midi_notes})
        except Exception as e:
            warnings.append(f"midi_notes_add on drums (continuous): {e}")


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
                       *, octave: int, warnings: list[str],
                       rhythm: str = "sustain") -> None:
    """Auto-fill a track with chord tones from the chord_track.

    `rhythm` controls how each chord is articulated:
      * "sustain"  — single long note over the full chord duration
                     (best for pads and ambience)
      * "quarter"  — re-articulate every quarter note (basic comping;
                     keys / piano feel)
      * "eighth"   — re-articulate every 8th (busier comping)
    """
    if not chord_track:
        return
    last_end = max(c.start_beat + c.length_beats for c in chord_track)
    clip_name = {"sustain": "Chord Pad", "quarter": "Chord Comp",
                 "eighth": "Chord Arp"}.get(rhythm, "Chords")
    try:
        clip = midi_tools.midi_clip_add({
            "track_id": track_id,
            "start_beats": 0.0,
            "length_beats": float(last_end),
            "name": clip_name,
        })["clip_id"]
    except Exception as e:
        warnings.append(f"midi_clip_add for chord {rhythm}: {e}")
        return

    notes: list[dict] = []
    base = 12 * octave  # C4=60 at octave=4
    pulse_beats = {"quarter": 1.0, "eighth": 0.5}.get(rhythm)

    for c in chord_track:
        # Triad: root, third, fifth
        third = 4 if c.quality in ("maj", "maj7", "7", "aug", "add9") else 3
        fifth = 7 if c.quality not in ("dim", "dim7", "m7b5") else 6
        intervals = [0, fifth] if c.quality == "5" else [0, third, fifth]

        if pulse_beats is None:
            # Single sustained note across the chord
            for semis in intervals:
                notes.append({
                    "pitch": int(base + c.root_pc + semis),
                    "start_beats": float(c.start_beat),
                    "length_beats": float(c.length_beats),
                    "velocity": 80,
                })
        else:
            # Pulsed re-articulation — e.g. one hit per quarter note
            t = c.start_beat
            end = c.start_beat + c.length_beats
            while t < end:
                hit_len = min(pulse_beats, end - t)
                if hit_len <= 0:
                    break
                for semis in intervals:
                    notes.append({
                        "pitch": int(base + c.root_pc + semis),
                        "start_beats": float(t),
                        # Slightly shorter than the pulse so successive
                        # voicings don't blur together
                        "length_beats": float(hit_len * 0.85),
                        "velocity": 75,
                    })
                t += pulse_beats

    if notes:
        try:
            midi_tools.midi_notes_add({"clip_id": clip, "notes": notes})
        except Exception as e:
            warnings.append(f"midi_notes_add for chord {rhythm}: {e}")


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


def _populate_default_drum_kit(track_id: str, plugin_id: str,
                                warnings: list[str]) -> None:
    """Load the bundled Tracktion 808 factory kit into a Sampler plugin.

    Called for any drum-role track whose chain contains a bare 'sampler'
    plugin — without SOUND entries the sampler is silent and the user
    hears nothing despite the MIDI notes firing.
    """
    try:
        from .composer import _drum_kit_sounds
        sounds = _drum_kit_sounds()
    except Exception as e:
        warnings.append(f"could not load default drum kit: {e}")
        return
    if not sounds:
        warnings.append("default drum kit returned no sounds — "
                        "factory presets dir may be missing")
        return
    # Find the Sampler plugin we just added and attach the sounds
    track = STORE.edit.track(track_id)
    target = next((p for p in track.plugins if p.id == plugin_id), None)
    if target is None:
        warnings.append(f"sampler plugin {plugin_id} not found")
        return
    target.sounds = sounds


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
