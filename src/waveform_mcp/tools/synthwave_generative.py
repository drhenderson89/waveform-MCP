"""Generative synthwave composer — every call yields a different song.

Mirrors lofi_generative.py: drives the existing MCP primitives
(progression_generate, arrangement_plan, drum_pattern, melody_generate,
bassline_generate) with seed-driven variation across tempo, key, form,
chord progression, drum pattern, melody contour, and bass feel.

The old `compose_synthwave_track` hardcoded everything — same chord
progression and same arrangement on every call. This generative version
genuinely produces different songs per seed.

Synthwave genre conventions baked in:
  * Tempo: 100–128 BPM range (driving but not frantic)
  * 4-on-the-floor kick patterns from drum_pattern("synthwave", role)
  * Bass locked to kick (4-on-floor pump feel)
  * Bright keys + pad + arp instrumentation
  * Lead synth on top
  * Plate reverb on lead and arp for that wide retro feel

Public API:
  compose_synthwave_generative(args) -> dict
    args = {
      "out_path": str,                     # required
      "seed": int = 0,
      "tempo": float = random in [100, 128],
      "key_root": str = random from synthwave-friendly palette,
      "key_mode": "major"|"minor" (random — minor weighted heavier),
      "target_seconds": float = random in [150, 240],
      "energy_curve": str = random,
      "chord_progression": list[dict] = optional override,
    }
"""
from __future__ import annotations

import random
from pathlib import Path
from typing import Any

from . import composition as comp_tools
from . import drum_patterns as drum_tools
from . import edit as edit_tools
from . import melody as mel_tools
from . import midi as midi_tools
from . import plugins as plugin_tools
from . import tracks as track_tools
from ..model import STORE
from .lofi_generative import (
    _bass_locked_to_kick,
    _quantize_melody_to_grid,
    _root_to_pc,
    _section_to_drum_role,
)


# Synthwave key roots: minor keys dominate but a few majors give variety.
_SYNTHWAVE_ROOTS = ["A", "D", "E", "F", "G", "C", "B", "Bb"]
_SYNTHWAVE_MODES = ["minor", "major"]
_CONTOURS = ["arch", "wave", "ascending", "descending", "question_answer"]
_DENSITIES = ["medium", "busy"]
_BASS_FEELS = ["pump", "sub", "root_5"]   # 4-on-floor + sustained variants


def compose_synthwave_generative(args: dict[str, Any]) -> dict[str, Any]:
    out_path = args.get("out_path")
    if not out_path:
        return {"ok": False, "error": "missing out_path"}
    seed = int(args.get("seed", 0))
    rng = random.Random(seed)

    # --- 1. Random choices, all seed-driven ---
    tempo = float(args.get("tempo") or rng.uniform(100.0, 128.0))
    target_seconds = float(args.get("target_seconds") or rng.uniform(150.0, 240.0))
    key_root = args.get("key_root") or rng.choice(_SYNTHWAVE_ROOTS)
    # Synthwave leans minor (~70% of the time)
    key_mode = args.get("key_mode") or rng.choices(
        _SYNTHWAVE_MODES, weights=[0.7, 0.3])[0]
    bass_feel = args.get("bass_feel") or rng.choice(_BASS_FEELS)
    energy_curve = args.get("energy_curve") or rng.choice(
        ["slow_burn", "steady", "climax"])

    # --- 2. Chord progression ---
    user_progression = args.get("chord_progression")
    if user_progression:
        progression_chords = list(user_progression)
    else:
        prog = comp_tools.progression_generate({
            "root": key_root, "mode": key_mode,
            "length_chords": 4, "style": "pop",
            "allow_borrowed": True,
            "seed": seed * 19 + 3,
            "octave": 3,
        })
        # progression_generate returns each chord as a dict with shape
        # {roman, degree, root_midi, chord_pitches: [MIDI notes], quality}.
        progression_chords = []
        for chord in prog["chords"]:
            pitches = chord["chord_pitches"]
            root = int(chord.get("root_midi", pitches[0]))
            if len(pitches) >= 4:
                third, fifth, seventh = pitches[1], pitches[2], pitches[3]
            else:
                third, fifth = pitches[1], pitches[2]
                seventh = root + (11 if chord.get("quality") in ("maj", "aug") else 10)
            # Rich voicing for synthwave pad/keys: full triad + 7th up an octave
            voicing = [third + 12, fifth + 12, seventh + 12]
            progression_chords.append({
                "bass_root": root,
                "voicing": voicing,
                "seventh_interval": seventh - root,
            })

    # --- 3. Arrangement (synthwave usually has a buildup before chorus) ---
    plan = comp_tools.arrangement_plan({
        "genre": "synthwave", "tempo": tempo,
        "target_length_seconds": target_seconds,
        "energy_curve": energy_curve,
        "include_bridge": rng.random() < 0.5,
        "include_buildup": rng.random() < 0.7,   # buildups are signature
    })
    sections_plan = plan["sections"]
    total_bars = sum(s["bars"] for s in sections_plan)

    # --- 4. Build the edit ---
    key_root_pc = _root_to_pc(key_root)
    edit_tools.edit_create({
        "name": f"Synthwave-{seed}",
        "tempo": tempo,
        "time_sig_num": 4, "time_sig_denom": 4,
        "key_root": key_root_pc, "key_mode": key_mode,
        "length_beats": total_bars * 4 + 4,
    })

    # --- 5. Drums (4-on-floor synthwave patterns from library) ---
    drums_id = track_tools.track_add({"name": "Drums", "kind": "midi"})["track_id"]
    plugin_tools.plugin_add({"track_id": drums_id, "type": "Sampler",
                              "name": "808 Kit"})
    drum_clip = midi_tools.midi_clip_add({
        "track_id": drums_id, "name": "Drums",
        "start_beats": 0.0, "length_beats": total_bars * 4,
    })["clip_id"]
    drum_notes: list[dict] = []
    cursor_bar = 0
    for sec in sections_plan:
        sec_bars = sec["bars"]
        sec_role = _section_to_drum_role(sec["role"], rng)
        density = float(sec.get("vel_peak", 1.0))
        # Intro starts drumless ~70% of the time; outro 60%
        intro_drums_out = sec["role"] == "intro" and rng.random() < 0.7
        outro_drums_out = sec["role"] == "outro" and rng.random() < 0.6
        if intro_drums_out or outro_drums_out or sec_bars <= 0:
            cursor_bar += sec_bars
            continue
        try:
            r = drum_tools.drum_pattern({
                "genre": "synthwave", "role": sec_role, "length_bars": sec_bars,
                "density": density,
                "start_beats": float(cursor_bar * 4),
                "swing": 0.0,    # synthwave is straight time
                "humanize": True, "seed": seed * 37 + cursor_bar,
            })
            drum_notes.extend(r.get("notes", []))
        except Exception:
            pass
        cursor_bar += sec_bars
    if drum_notes:
        midi_tools.midi_notes_add({"clip_id": drum_clip, "notes": drum_notes})

    # --- 6. Bass — 4OSC + clean-gain comp; locked to kick ---
    bass_id = track_tools.track_add({"name": "Bass", "kind": "midi"})["track_id"]
    plugin_tools.plugin_add({"track_id": bass_id, "type": "4OSC"})
    # Synthwave bass is supposed to be a synth — bump output gain so
    # 4OSC default doesn't sit too low under the drums.
    from ..model import Plugin as _Plugin
    from ..model import _id as _gen_id
    STORE.edit.track(bass_id).plugins.append(_Plugin(
        id=_gen_id("plg"), type="comp", name="Bass Gain",
        params={"threshold": 0.0, "ratio": 1.0, "attack": 1.0,
                "release": 50.0, "knee": 0.0, "outputDb": 8.0, "rms": 0},
    ))
    bass_clip = midi_tools.midi_clip_add({
        "track_id": bass_id, "name": "Bass",
        "start_beats": 0.0, "length_beats": total_bars * 4,
    })["clip_id"]
    bass_notes_locked = _bass_locked_to_kick(
        drum_notes=drum_notes,
        progression_chords=progression_chords,
        total_bars=total_bars,
        bar_beats=4,
        bass_octave=0,
        velocity=100,    # synthwave bass should hit
    )
    if bass_notes_locked:
        midi_tools.midi_notes_add({"clip_id": bass_clip,
                                    "notes": bass_notes_locked})

    # --- 7. Pad — sustained chord tones, soft attack ---
    pad_id = track_tools.track_add({"name": "Pad", "kind": "midi"})["track_id"]
    plugin_tools.plugin_add({"track_id": pad_id, "type": "4OSC"})
    pad_clip = midi_tools.midi_clip_add({
        "track_id": pad_id, "name": "Pad",
        "start_beats": 0.0, "length_beats": total_bars * 4,
    })["clip_id"]
    pad_notes = []
    for bar in range(total_bars):
        chord = progression_chords[bar % len(progression_chords)]
        for p in chord["voicing"]:
            pad_notes.append({
                "pitch": int(p),
                "start_beats": float(bar * 4),
                "length_beats": 4.05,
                "velocity": 60,
            })
    midi_tools.midi_notes_add({"clip_id": pad_clip, "notes": pad_notes})

    # --- 8. Arp — eighth-note arpeggio of chord tones ---
    arp_id = track_tools.track_add({"name": "Arp", "kind": "midi"})["track_id"]
    plugin_tools.plugin_add({"track_id": arp_id, "type": "4OSC"})
    arp_clip = midi_tools.midi_clip_add({
        "track_id": arp_id, "name": "Arp",
        "start_beats": 0.0, "length_beats": total_bars * 4,
    })["clip_id"]
    arp_notes = []
    arp_direction = rng.choice(["up", "down", "up_down"])
    for bar in range(total_bars):
        # Mute arp during intro/outro
        sec = _section_at_bar(sections_plan, bar)
        if sec and sec["role"] in ("intro", "outro"):
            continue
        chord = progression_chords[bar % len(progression_chords)]
        try:
            r = mel_tools.arp_pattern({
                "chord_pitches": [chord["bass_root"] + 12] + list(chord["voicing"]),
                "length_beats": 4.0,
                "rate_beats": 0.5,    # 8th-note arp
                "direction": arp_direction,
                "octave_offset": 1,
                "start_beats": float(bar * 4),
            })
            arp_notes.extend(r.get("notes", []))
        except Exception:
            pass
    midi_tools.midi_notes_add({"clip_id": arp_clip, "notes": arp_notes})

    # --- 9. Lead — per-section melody with quantization ---
    lead_id = track_tools.track_add({"name": "Lead", "kind": "midi"})["track_id"]
    plugin_tools.plugin_add({"track_id": lead_id, "type": "4OSC"})
    lead_clip = midi_tools.midi_clip_add({
        "track_id": lead_id, "name": "Lead",
        "start_beats": 0.0, "length_beats": total_bars * 4,
    })["clip_id"]
    lead_notes_all = []
    cursor_bar = 0
    for sec_idx, sec in enumerate(sections_plan):
        sec_bars = sec["bars"]
        # Lead enters in chorus/verse; absent in intro/outro/buildup/breakdown
        if sec["role"] in ("intro", "outro", "buildup", "breakdown") or sec_bars < 4:
            cursor_bar += sec_bars
            continue
        try:
            mel = mel_tools.melody_generate({
                "root": key_root, "mode": key_mode,
                "length_beats": float(sec_bars * 4),
                "contour": rng.choice(_CONTOURS),
                "density": rng.choice(_DENSITIES),
                "register_octave": 6,    # synthwave lead sits high
                "seed": seed * 59 + sec_idx,
            })
            section_notes = _quantize_melody_to_grid(
                mel.get("notes", []),
                grid_beats=0.5,
                min_length_beats=0.25,
            )
            for n in section_notes:
                lead_notes_all.append({
                    "pitch": int(n["pitch"]),
                    "start_beats": float(n["start_beats"]) + cursor_bar * 4,
                    "length_beats": float(n["length_beats"]),
                    "velocity": int(n.get("velocity", 90)),
                })
        except Exception:
            pass
        cursor_bar += sec_bars
    if lead_notes_all:
        midi_tools.midi_notes_add({"clip_id": lead_clip, "notes": lead_notes_all})

    # --- 10. Mix balance ---
    track_tools.mix_apply_reference({"track_id": drums_id,
                                      "genre": "synthwave", "role": "drums",
                                      "offset_db": +2})
    track_tools.mix_apply_reference({"track_id": bass_id,
                                      "genre": "synthwave", "role": "bass",
                                      "offset_db": +6})
    track_tools.mix_apply_reference({"track_id": pad_id,
                                      "genre": "synthwave", "role": "pad",
                                      "offset_db": +2})
    track_tools.mix_apply_reference({"track_id": arp_id,
                                      "genre": "synthwave", "role": "arp",
                                      "offset_db": 0})
    track_tools.mix_apply_reference({"track_id": lead_id,
                                      "genre": "synthwave", "role": "lead",
                                      "offset_db": +3})

    # Master pulled back so transients have headroom
    STORE.edit.master_volume_db = -2.0

    # --- 11. Save ---
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    edit_tools.edit_save({"path": out_path})

    return {
        "ok": True,
        "path": out_path,
        "seed": seed,
        "tempo": tempo,
        "key": f"{key_root} {key_mode}",
        "bars": total_bars,
        "sections": [s["role"] for s in sections_plan],
        "bass_feel": bass_feel,
        "energy_curve": energy_curve,
        "arp_direction": arp_direction,
        "chord_roots": [int(c["bass_root"]) for c in progression_chords],
    }


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _section_at_bar(sections_plan: list[dict], bar: int) -> dict | None:
    cursor = 0
    for sec in sections_plan:
        if cursor <= bar < cursor + sec["bars"]:
            return sec
        cursor += sec["bars"]
    return None


HANDLERS = {"compose_synthwave_generative": compose_synthwave_generative}

TOOLS = [{
    "name": "compose_synthwave_generative",
    "description": (
        "Generate a UNIQUE synthwave track on every call. Drives "
        "progression_generate / arrangement_plan / drum_pattern / "
        "melody_generate primitives with seed-driven variation across "
        "tempo (100-128 BPM), key, form, chord progression, drum "
        "density, melody contour, and bass feel. Different seeds = "
        "meaningfully different songs."
    ),
    "input_schema": {
        "type": "object",
        "required": ["out_path"],
        "properties": {
            "out_path":     {"type": "string"},
            "seed":         {"type": "integer", "default": 0},
            "tempo":        {"type": "number"},
            "key_root":     {"type": "string"},
            "key_mode":     {"type": "string"},
            "target_seconds": {"type": "number"},
            "energy_curve": {"type": "string",
                              "enum": ["slow_burn", "steady", "climax"]},
            "chord_progression": {"type": "array"},
        },
    },
}]
