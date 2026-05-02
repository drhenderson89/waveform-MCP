"""Generative lofi composer — every call yields a different song.

The old `compose_lofi_track` hardcoded everything: chord progression
(Fmaj7-Am7-Dm7-Cmaj7), section arrangement (LOFI_ARRANGEMENT), drum
pattern, bass figure, melody motif. Result was: every seed produced
the same song with minor noise. The user correctly called this out —
that's not a composer, that's a static template.

This module uses the existing MCP generative primitives:

  * progression_generate — yields a fresh chord progression from a
    style transition table. With allow_borrowed=True you get borrowed-
    mode flavor (♭III, ♭VI, ♭VII) that's central to lofi.
  * arrangement_plan     — picks a section layout from a small genre
    form bank, with energy curve choice.
  * drum_pattern         — selects from 50+ pre-authored drum grooves
    by (genre, role); density scales hat count, swing offsets the
    8ths.
  * melody_generate      — walks scale degrees following a chosen
    contour (arch / wave / ascending / descending) at a given density.
  * bassline_generate    — bass figures keyed by chord roots and
    "feel" (sub / pump / walk / root_5).

All RNG is seeded so the same seed yields the same song; different
seeds yield meaningfully different songs.

Public API:
  compose_lofi_generative(args) -> dict
    args = {
      "out_path": str,
      "seed": int,
      "tempo": float = random in [70, 90],
      "key_root": str = "F" (or random),
      "target_seconds": float = random in [120, 200],
      "chord_progression": list[dict] = optional override,
      "energy_curve": str = "slow_burn"|"steady"|"climax",
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


# Possible roots for a "lofi" feel. F / G / D minor lean melancholy.
# C major / A minor are bright. Mix gives variety.
_LOFI_ROOTS = ["F", "G", "D", "C", "A", "E", "Bb"]
_LOFI_MODES = ["major", "minor"]
_CONTOURS = ["arch", "wave", "ascending", "descending"]
_DENSITIES = ["sparse", "medium"]
_BASS_FEELS = ["root_5", "sub", "walk"]


def compose_lofi_generative(args: dict[str, Any]) -> dict[str, Any]:
    out_path = args.get("out_path")
    if not out_path:
        return {"ok": False, "error": "missing out_path"}
    seed = int(args.get("seed", 0))
    rng = random.Random(seed)

    # --- pick all the random choices up front, all driven by seed ---
    tempo = float(args.get("tempo") or rng.uniform(70.0, 90.0))
    target_seconds = float(args.get("target_seconds") or rng.uniform(120.0, 200.0))
    key_root = args.get("key_root") or rng.choice(_LOFI_ROOTS)
    key_mode = args.get("key_mode") or rng.choices(
        _LOFI_MODES, weights=[0.6, 0.4])[0]
    melody_contour = args.get("melody_contour") or rng.choice(_CONTOURS)
    melody_density = args.get("melody_density") or rng.choice(_DENSITIES)
    bass_feel = args.get("bass_feel") or rng.choice(_BASS_FEELS)
    energy_curve = args.get("energy_curve") or rng.choice(
        ["slow_burn", "steady", "climax"])

    # --- 1. Chord progression (allow_borrowed = jazzier lofi flavor) ---
    user_progression = args.get("chord_progression")
    if user_progression:
        # Caller-provided: list of {bass_root, voicing, seventh_interval}
        progression_chords = user_progression
        progression_root_notes = [c["bass_root"] for c in user_progression]
    else:
        prog = comp_tools.progression_generate({
            "root": key_root, "mode": key_mode,
            "length_chords": 4, "style": "pop",
            "allow_borrowed": True,
            "seed": seed * 17 + 1,
            "octave": 3,
        })
        # progression_generate returns chord_pitches (list of [root,3,5,7])
        # and chord_roots (root MIDI notes). We synthesise voicings from
        # the returned pitches.
        progression_chords = []
        for chord_pitches in prog["chords"]:
            # chord_pitches = [root, 3rd, 5th, 7th]
            root = chord_pitches[0]
            third, fifth, seventh = chord_pitches[1:]
            # Rootless voicing: 3-7-9 stack starting at C5 area
            voicing = [
                third + 12,                            # 3rd up an octave
                seventh + 12,                          # 7th up an octave
                root + 14,                             # 9th = root + 14
            ]
            seventh_interval = seventh - root
            progression_chords.append({
                "bass_root": root,
                "voicing": voicing,
                "seventh_interval": seventh_interval,
            })
        progression_root_notes = [c["bass_root"] for c in progression_chords]

    # --- 2. Arrangement / section structure ---
    plan = comp_tools.arrangement_plan({
        "genre": "lofi", "tempo": tempo,
        "target_length_seconds": target_seconds,
        "energy_curve": energy_curve,
        "include_bridge": rng.random() < 0.6,
        "include_buildup": rng.random() < 0.4,
    })
    sections_plan = plan["sections"]
    total_bars = sum(s["bars"] for s in sections_plan)

    # --- 3. Build the edit ---
    key_root_pc = _root_to_pc(key_root)
    edit_tools.edit_create({
        "name": f"Lofi-{seed}",
        "tempo": tempo,
        "time_sig_num": 4, "time_sig_denom": 4,
        "key_root": key_root_pc, "key_mode": key_mode,
        "length_beats": total_bars * 4 + 4,
    })

    # --- 4. Drums (one continuous clip from beat 0; per-section role
    #         drives density via drum_pattern's library) ---
    drums_id = track_tools.track_add({"name": "Drums", "kind": "midi"})["track_id"]
    plugin_tools.plugin_add({"track_id": drums_id, "type": "Sampler",
                              "name": "808 Kit"})
    _populate_drum_kit(drums_id)

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
        # Intro often drums-out; sometimes drums in
        if sec["role"] in ("intro", "outro") and rng.random() < 0.7:
            cursor_bar += sec_bars
            continue
        if sec_bars <= 0:
            cursor_bar += sec_bars
            continue
        try:
            r = drum_tools.drum_pattern({
                "genre": "lofi", "role": sec_role, "length_bars": sec_bars,
                "density": density,
                "start_beats": float(cursor_bar * 4),
                "swing": rng.uniform(0.02, 0.06),
                "humanize": True, "seed": seed * 31 + cursor_bar,
            })
            drum_notes.extend(r.get("notes", []))
        except Exception:
            pass
        cursor_bar += sec_bars
    if drum_notes:
        midi_tools.midi_notes_add({"clip_id": drum_clip, "notes": drum_notes})

    # --- 5. Bass ---
    # NOTE: plugin name is left unset so it falls back to type="4OSC",
    # which IS in the calibration table (key "4osc" → -8 dB offset).
    # Setting a custom name like "Bass" makes calibration miss the
    # lookup and use the conservative -10 dB unknown default.
    bass_id = track_tools.track_add({"name": "Bass", "kind": "midi"})["track_id"]
    plugin_tools.plugin_add({"track_id": bass_id, "type": "4OSC"})
    bass_clip = midi_tools.midi_clip_add({
        "track_id": bass_id, "name": "Bass",
        "start_beats": 0.0, "length_beats": total_bars * 4,
    })["clip_id"]
    # Tile the chord progression across bars
    chord_dur_bars = 1   # one chord per bar
    bass_roots_full = []
    for bar in range(total_bars):
        chord = progression_chords[bar % len(progression_chords)]
        bass_roots_full.append(chord["bass_root"])
    bass_r = mel_tools.bassline_generate({
        "roots": bass_roots_full,
        "chord_duration_beats": float(chord_dur_bars * 4),
        "feel": bass_feel,
        "start_beats": 0.0,
    })
    bass_notes_raw = bass_r.get("notes", [])
    if bass_notes_raw:
        midi_tools.midi_notes_add({"clip_id": bass_clip, "notes": bass_notes_raw})

    # --- 6. Keys (jazz piano comp from progression voicings) ---
    keys_id = track_tools.track_add({"name": "Keys", "kind": "midi"})["track_id"]
    plugin_tools.plugin_add({"track_id": keys_id, "type": "4OSC"})
    keys_clip = midi_tools.midi_clip_add({
        "track_id": keys_id, "name": "Keys",
        "start_beats": 0.0, "length_beats": total_bars * 4,
    })["clip_id"]
    keys_notes = []
    for bar in range(total_bars):
        chord = progression_chords[bar % len(progression_chords)]
        b = bar * 4
        # Stab on beat 1 + the "&" of 3 — classic jazz comping pattern
        for offset in (0.0, 2.5):
            for i, p in enumerate(chord["voicing"]):
                keys_notes.append({
                    "pitch": int(p),
                    "start_beats": float(b + offset + i * 0.005),
                    "length_beats": 1.4 if offset == 2.5 else 0.7,
                    "velocity": 70,
                })
    midi_tools.midi_notes_add({"clip_id": keys_clip, "notes": keys_notes})

    # --- 7. Melody (per-section with fresh seed → variety) ---
    melody_id = track_tools.track_add({"name": "Melody", "kind": "midi"})["track_id"]
    plugin_tools.plugin_add({"track_id": melody_id, "type": "4OSC"})
    mel_clip = midi_tools.midi_clip_add({
        "track_id": melody_id, "name": "Melody",
        "start_beats": 0.0, "length_beats": total_bars * 4,
    })["clip_id"]
    melody_notes_all = []
    cursor_bar = 0
    for sec_idx, sec in enumerate(sections_plan):
        sec_bars = sec["bars"]
        # Skip melody during intro / outro / quiet sections
        if sec["role"] in ("intro", "outro", "buildup") or sec_bars < 4:
            cursor_bar += sec_bars
            continue
        try:
            mel = mel_tools.melody_generate({
                "root": key_root, "mode": key_mode,
                "length_beats": float(sec_bars * 4),
                "contour": rng.choice(_CONTOURS),  # contour varies per section
                "density": rng.choice(_DENSITIES),
                "register_octave": 5,
                "seed": seed * 53 + sec_idx,
            })
            for n in mel.get("notes", []):
                melody_notes_all.append({
                    "pitch": int(n["pitch"]),
                    "start_beats": float(n["start_beats"]) + cursor_bar * 4,
                    "length_beats": float(n["length_beats"]),
                    "velocity": int(n.get("velocity", 80)),
                })
        except Exception:
            pass
        cursor_bar += sec_bars
    if melody_notes_all:
        midi_tools.midi_notes_add({"clip_id": mel_clip, "notes": melody_notes_all})

    # --- 8. Pad (chord-tone sustain) ---
    pad_id = track_tools.track_add({"name": "Pad", "kind": "midi"})["track_id"]
    plugin_tools.plugin_add({"track_id": pad_id, "type": "4OSC"})
    pad_clip = midi_tools.midi_clip_add({
        "track_id": pad_id, "name": "Pad",
        "start_beats": 0.0, "length_beats": total_bars * 4,
    })["clip_id"]
    pad_notes = []
    for bar in range(total_bars):
        chord = progression_chords[bar % len(progression_chords)]
        root_pad = int(chord["bass_root"]) + 12
        seventh = root_pad + int(chord.get("seventh_interval", 11))
        pad_notes.extend([
            {"pitch": root_pad, "start_beats": bar * 4,
             "length_beats": 4.1, "velocity": 55},
            {"pitch": seventh, "start_beats": bar * 4,
             "length_beats": 4.1, "velocity": 50},
        ])
    midi_tools.midi_notes_add({"clip_id": pad_clip, "notes": pad_notes})

    # --- 9. Mix balance — user-tuned offsets on top of MIX_BALANCE["lofi"]
    #
    # MIX_BALANCE defaults for lofi: drums -8, bass -18, keys -2,
    # melody -8, pad -18 dB.
    #
    # Empirical tuning from listening tests:
    #   * Keys at -2 dB are way too hot — drown the melody. Pull down 8 dB.
    #   * Melody at -8 dB sits underneath the keys — push up 5 dB.
    #   * Drums at -8 dB feel underpresent in lofi (kick should hit) —
    #     push up 5 dB.
    #   * Pad at -18 dB is almost inaudible after master limiter — push
    #     up 8 dB so it audibly cushions the harmony.
    #   * Bass at -18 dB is a touch low — nudge up 3 dB.
    track_tools.mix_apply_reference({"track_id": drums_id,
                                      "genre": "lofi", "role": "drums",
                                      "offset_db": +5})
    track_tools.mix_apply_reference({"track_id": bass_id,
                                      "genre": "lofi", "role": "bass",
                                      "offset_db": +3})
    track_tools.mix_apply_reference({"track_id": keys_id,
                                      "genre": "lofi", "role": "keys",
                                      "offset_db": -8})
    track_tools.mix_apply_reference({"track_id": melody_id,
                                      "genre": "lofi", "role": "melody",
                                      "offset_db": +5})
    track_tools.mix_apply_reference({"track_id": pad_id,
                                      "genre": "lofi", "role": "pad",
                                      "offset_db": +8})

    # --- 10. Save ---
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
        "melody_contour": melody_contour,
        "melody_density": melody_density,
        "bass_feel": bass_feel,
        "energy_curve": energy_curve,
        "chord_roots": [int(c["bass_root"]) for c in progression_chords],
    }


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _root_to_pc(name: str) -> int:
    table = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3,
             "E": 4, "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8,
             "A": 9, "A#": 10, "Bb": 10, "B": 11}
    return table.get(name, 0)


def _section_to_drum_role(role: str, rng: random.Random) -> str:
    """Map arrangement_plan's section role to drum_pattern's role names."""
    mapping = {
        "intro": "intro",
        "verse": "verse",
        "verse_a": "verse",
        "verse_b": "verse",
        "chorus": "chorus",
        "chorus_a": "chorus",
        "chorus_b": "chorus",
        "bridge": "verse",      # lofi has no bridge pattern; reuse verse
        "buildup": "buildup",
        "breakdown": "breakdown",
        "outro": "outro",
    }
    return mapping.get(role, "verse")


def _populate_drum_kit(track_id: str) -> None:
    """Load factory 808 samples on the drum sampler. Mirrors the
    existing _drum_kit_sounds helper but inlined to avoid importing
    composer.py and re-running its module-level init."""
    base = (Path.home() / "AppData" / "Roaming" / "Tracktion" / "Waveform"
            / "Factory Presets" / "Drum Sampler")
    if not base.exists():
        return
    mappings = [
        (36, "808 Bass Drum 115.flac",   "Kick"),
        (38, "808 Snare Drum 253.flac",  "Snare"),
        (39, "808 Hand Clap 7.flac",     "Clap"),
        (42, "808 Hihat Closed 1.flac",  "Closed Hat"),
        (46, "808 Hihat Open 2.flac",    "Open Hat"),
        (49, "808 Cymbal 27.flac",       "Crash"),
        (45, "808 Tom Low 44.flac",      "Low Tom"),
        (48, "808 Tom Mid 44.flac",      "Mid Tom"),
        (50, "808 Tom Hi 1.flac",        "Hi Tom"),
        (37, "808 Rim Shot 1.flac",      "Rimshot"),
    ]
    sounds = []
    for note, fname, name in mappings:
        path = base / fname
        if not path.exists():
            continue
        sounds.append({
            "source": str(path), "name": name,
            "keyNote": note, "minNote": note, "maxNote": note,
            "startTime": 0.0, "gainDb": 0.0, "pan": 0.0, "openEnded": 1,
        })
    track = STORE.edit.track(track_id)
    sampler = next((p for p in track.plugins if p.type == "sampler"), None)
    if sampler is not None:
        sampler.sounds = sounds


HANDLERS = {"compose_lofi_generative": compose_lofi_generative}

TOOLS = [{
    "name": "compose_lofi_generative",
    "description": (
        "Generate a UNIQUE lofi track on every call. Unlike the old "
        "compose_lofi_track which hardcoded everything, this one uses "
        "progression_generate, arrangement_plan, drum_pattern, "
        "melody_generate, and bassline_generate primitives. Each seed "
        "produces a meaningfully different song: different chords, "
        "section layout, drum density, melody contour, bass feel, and "
        "tempo (within lofi range)."
    ),
    "input_schema": {
        "type": "object",
        "required": ["out_path"],
        "properties": {
            "out_path": {"type": "string"},
            "seed": {"type": "integer", "default": 0},
            "tempo": {"type": "number"},
            "key_root": {"type": "string"},
            "key_mode": {"type": "string"},
            "target_seconds": {"type": "number"},
            "energy_curve": {"type": "string",
                              "enum": ["slow_burn", "steady", "climax"]},
            "chord_progression": {
                "type": "array",
                "description": ("Optional: list of {bass_root, voicing, "
                                 "seventh_interval} dicts to inject a "
                                 "specific harmonic palette."),
            },
        },
    },
}]
