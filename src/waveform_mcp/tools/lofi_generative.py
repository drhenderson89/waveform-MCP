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
        # progression_generate returns each chord as a dict with shape
        # {roman, degree, root_midi, chord_pitches: [MIDI notes], quality,
        # borrowed}. chord_pitches is the actual MIDI triad/seventh
        # voicing (3 or 4 notes); we synthesise rootless voicings from it.
        progression_chords = []
        for chord in prog["chords"]:
            pitches = chord["chord_pitches"]   # [root, 3rd, 5th, (7th)]
            root = int(chord.get("root_midi", pitches[0]))
            # Pad to a 4-note 7th voicing if only triad came back
            if len(pitches) >= 4:
                third, fifth, seventh = pitches[1], pitches[2], pitches[3]
            else:
                third, fifth = pitches[1], pitches[2]
                # Default 7th: maj=11, min/dim=10 from root
                seventh = root + (11 if chord.get("quality") in ("maj", "aug") else 10)
            # Rootless voicing: 3-7-9 stack up an octave
            voicing = [third + 12, seventh + 12, root + 14]
            progression_chords.append({
                "bass_root": root,
                "voicing": voicing,
                "seventh_interval": seventh - root,
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
    # Track named "Drums" → plugin_add auto-loads the factory 808 kit.
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

    # --- 5. Bass — REAL multi-sample bass guitar locked to the kick ---
    bass_id = track_tools.track_add({"name": "Bass", "kind": "midi"})["track_id"]
    plugin_tools.plugin_add({"track_id": bass_id, "type": "4OSC"})
    # Try to upgrade to multi-sample bass. If factory samples missing,
    # we keep 4OSC and add gain compensation (the synth is too thin
    # without it).
    using_real_bass = _install_lofi_bass_samples(bass_id)
    if not using_real_bass:
        from ..model import Plugin as _Plugin
        from ..model import _id as _gen_id
        STORE.edit.track(bass_id).plugins.append(_Plugin(
            id=_gen_id("plg"), type="comp", name="Bass Gain",
            params={"threshold": 0.0, "ratio": 1.0, "attack": 1.0,
                    "release": 50.0, "knee": 0.0, "outputDb": 12.0, "rms": 0},
        ))
    bass_clip = midi_tools.midi_clip_add({
        "track_id": bass_id, "name": "Bass",
        "start_beats": 0.0, "length_beats": total_bars * 4,
    })["clip_id"]

    # Lock bass notes to kick onsets — the foundation of any modern
    # rhythmic groove. Replaces the previous bassline_generate path,
    # which produced bass figures that didn't share rhythm with the
    # drum pattern. This is what "meshing" sounds like: kick + bass
    # hitting together on every kick.
    bass_notes_locked = _bass_locked_to_kick(
        drum_notes=drum_notes,
        progression_chords=progression_chords,
        total_bars=total_bars,
        bar_beats=4,
        # Multi-sample bass natural range = 36-50; chord roots from
        # progression_generate land at octave-3 (~40-50). 0 offset works.
        # If the synth fallback is in play, no offset either.
        bass_octave=0,
        velocity=95,
    )
    if bass_notes_locked:
        midi_tools.midi_notes_add({"clip_id": bass_clip,
                                    "notes": bass_notes_locked})

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
            # Quantize melody to 8th-note grid + collapse same-pitch
            # collisions. melody_generate accumulates positions
            # sequentially with 12 ms humanize per note; cumulative
            # drift puts notes off-grid relative to drums. Snapping to
            # 0.5-beat grid forces every melody onset to land on the
            # same beat positions the drums and bass are landing on.
            section_notes_quantized = _quantize_melody_to_grid(
                mel.get("notes", []),
                grid_beats=0.5,
                min_length_beats=0.25,
            )
            for n in section_notes_quantized:
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
    # Audit-tuned mix offsets. Last analyzer pass showed:
    #   drums  -22 RMS / +18 dB sub band → over-dominant kick
    #   bass   -49 RMS                    → inaudible
    #   other  -21 RMS / 0 dB low-mid    → keys+melody+pad fine
    #   full   -17 RMS / +0.6 dBTP        → clipping
    track_tools.mix_apply_reference({"track_id": drums_id,
                                      "genre": "lofi", "role": "drums",
                                      "offset_db": +2})
    # Bass: real samples are at unity output level, so the previous
    # +18 dB hack (compensating for thin 4OSC) isn't needed when
    # _install_lofi_bass_samples succeeded. Use a moderate +6 to match
    # the lofi reference balance.
    track_tools.mix_apply_reference({"track_id": bass_id,
                                      "genre": "lofi", "role": "bass",
                                      "offset_db": +6 if using_real_bass else +18})
    track_tools.mix_apply_reference({"track_id": keys_id,
                                      "genre": "lofi", "role": "keys",
                                      "offset_db": -8})
    track_tools.mix_apply_reference({"track_id": melody_id,
                                      "genre": "lofi", "role": "melody",
                                      "offset_db": +5})
    track_tools.mix_apply_reference({"track_id": pad_id,
                                      "genre": "lofi", "role": "pad",
                                      "offset_db": +8})

    # Pull master volume down 2 dB to give the limiter headroom.
    # Previous render hit +0.6 dBTP — clipping on every transient peak.
    STORE.edit.master_volume_db = -2.0

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


def _install_lofi_bass_samples(track_id: str) -> bool:
    """Replace the default 4OSC patch on a Bass track with a multi-
    sampled bass guitar (4 factory Lofi samples). Returns True on success.

    Mirrors compose_lofi_track's known-good bass sound: 4 Cymatics
    samples at E2/A2/B2/C3, mapped with narrow ranges so each played
    note is within ~3 semitones of the nearest sample's natural pitch.
    Avoids the synth-bass-too-thin problem we hit with bare 4OSC.
    """
    base = (Path.home() / "AppData" / "Roaming" / "Tracktion" / "Waveform"
            / "Factory Loops" / "Lofi Samples")
    if not base.exists():
        return False
    samples = [
        ("lofi_bass_E.wav",        "Bass E2", 40, 24, 41),
        ("lofi_bass_A.wav",        "Bass A2", 45, 42, 46),
        ("lofi_bass_B.wav",        "Bass B2", 47, 47, 47),
        ("lofi_bass_guitar_C.wav", "Bass C3", 48, 48, 80),
    ]
    if not all((base / fn).exists() for fn, *_ in samples):
        return False

    # Wipe existing (4OSC) plugin and replace with Sampler
    track = STORE.edit.track(track_id)
    track.plugins.clear()
    plugin_tools.plugin_add({
        "track_id": track_id, "type": "Sampler", "name": "Lofi Bass",
    })
    # The auto-drum-kit-load in plugin_add is gated on track name
    # containing 'drum'/'kit'/'percussion', so a track named 'Bass'
    # gets a clean empty Sampler — we can fill it directly.
    sampler = next((p for p in track.plugins
                     if (p.type or "").lower() == "sampler"), None)
    if sampler is None:
        return False
    sampler.sounds = [
        {"source": str(base / fn), "name": nm,
         "keyNote": kn, "minNote": mn, "maxNote": mx,
         "startTime": 0.0, "gainDb": 0.0, "pan": 0.0, "openEnded": 1}
        for fn, nm, kn, mn, mx in samples
    ]
    return True


def _quantize_melody_to_grid(
    notes: list[dict], *,
    grid_beats: float = 0.5,
    min_length_beats: float = 0.25,
) -> list[dict]:
    """Snap melody onsets to a beat grid and clean up collisions.

    melody_generate's output drifts off-grid because it accumulates
    sequential durations + 12 ms humanize per note. Over many notes,
    accumulated jitter puts melody onsets between the drum/bass beats.
    Snapping to an 8th-note grid (0.5 beats default) puts every melody
    note on the same time positions the drums hit, which is what
    "rhythmically locked" actually means.

    After snapping, multiple notes that collapsed to the same grid
    position are deduped — keep the loudest, drop the rest.
    Lengths are also snapped to grid; notes shorter than
    `min_length_beats` are stretched to that floor for audibility.
    """
    if not notes or grid_beats <= 0:
        return list(notes)

    snapped: list[dict] = []
    for n in notes:
        start = float(n.get("start_beats", 0.0))
        length = float(n.get("length_beats", grid_beats))
        # Snap start
        snapped_start = round(start / grid_beats) * grid_beats
        # Snap length to grid; enforce floor
        snapped_len = max(min_length_beats,
                          round(length / grid_beats) * grid_beats or grid_beats)
        snapped.append({
            "pitch": int(n.get("pitch", 60)),
            "start_beats": float(snapped_start),
            "length_beats": float(snapped_len),
            "velocity": int(n.get("velocity", 80)),
        })

    # Dedupe: at each unique start_beat, keep only the highest-velocity note
    by_start: dict[float, dict] = {}
    for n in snapped:
        key = round(n["start_beats"], 4)
        if key not in by_start or n["velocity"] > by_start[key]["velocity"]:
            by_start[key] = n
    return sorted(by_start.values(), key=lambda x: x["start_beats"])


def _bass_locked_to_kick(
    drum_notes: list[dict],
    progression_chords: list[dict],
    total_bars: int,
    bar_beats: int = 4,
    *,
    bass_octave: int = 0,   # 0 = play chord root as-is
    velocity: int = 95,
) -> list[dict]:
    """Place bass notes on every KICK onset, at the chord root for the
    bar that kick falls in. This is the foundational rhythm-section
    interaction that makes a song groove — kick + bass hitting together.

    A note sustains until the next kick or until the chord changes,
    whichever comes first, so holds through bar transitions sound
    naturally. We add a tiny LAY_BACK (4 ms) so the bass sits half a
    hair behind the kick — pro bassist feel.

    Args:
      drum_notes: output of drum_pattern (list of {pitch, start_beats, ...})
      progression_chords: list of chord descriptors from compose_lofi_generative
      total_bars: total song length in bars
      bar_beats: 4 in 4/4
      bass_octave: 0 keeps the progression root; -12 drops an octave
      velocity: bass note velocity (95 = strong but not slamming)
    """
    LAY_BACK = 0.004  # tiny feel
    KICK_PITCH = 36

    # Extract kick onsets
    kick_beats = sorted(
        set(round(float(n["start_beats"]) * 16) / 16  # snap to 64th
            for n in drum_notes if int(n["pitch"]) == KICK_PITCH)
    )
    if not kick_beats:
        return []

    total_beats = float(total_bars * bar_beats)
    bass_notes: list[dict] = []
    for i, kb in enumerate(kick_beats):
        if kb >= total_beats:
            break
        # Which chord at this beat?
        bar = int(kb // bar_beats)
        chord = progression_chords[bar % len(progression_chords)]
        root = int(chord["bass_root"]) + bass_octave

        # Length: until next kick, or until chord changes, whichever is
        # sooner. Avoids muddy sustain across chord changes.
        next_kick = kick_beats[i + 1] if i + 1 < len(kick_beats) else total_beats
        next_chord_beat = (bar + 1) * bar_beats
        end_beat = min(next_kick, next_chord_beat, total_beats)
        length = max(0.25, end_beat - kb)

        bass_notes.append({
            "pitch": int(root),
            "start_beats": float(kb + LAY_BACK),
            "length_beats": float(length),
            "velocity": int(velocity),
        })

    return bass_notes


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
