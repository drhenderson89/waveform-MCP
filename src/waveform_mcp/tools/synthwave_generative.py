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
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("layer generation failed: %s", e)
        cursor_bar += sec_bars
    if drum_notes:
        midi_tools.midi_notes_add({"clip_id": drum_clip, "notes": drum_notes})

    # --- 5b. Drum fills on the last bar of each section (transitions) ---
    fill_notes: list[dict] = []
    cursor_bar = 0
    for sec_idx, sec in enumerate(sections_plan):
        sec_bars = sec["bars"]
        # Place a fill at the end of any section that's followed by a
        # higher-energy or different section. Skip outro.
        is_last = sec_idx == len(sections_plan) - 1
        if is_last or sec_bars < 4:
            cursor_bar += sec_bars
            continue
        if sec["role"] in ("intro", "outro"):
            cursor_bar += sec_bars
            continue
        # Last bar of this section → snare roll + crash on next downbeat
        fill_bar = cursor_bar + sec_bars - 1
        # 16th-note snare roll across the bar
        for i in range(16):
            fill_notes.append({
                "pitch": 38,  # snare
                "start_beats": float(fill_bar * 4 + i * 0.25),
                "length_beats": 0.2,
                "velocity": 70 + int(i * 2.5),  # crescendo
            })
        # Crash on the downbeat of the next section
        fill_notes.append({
            "pitch": 49,  # crash
            "start_beats": float((fill_bar + 1) * 4),
            "length_beats": 1.0,
            "velocity": 110,
        })
        cursor_bar += sec_bars
    if fill_notes:
        midi_tools.midi_notes_add({"clip_id": drum_clip, "notes": fill_notes})

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
    # Section-aware bass enrichment: chorus sections get octave-jump
    # accents (every other note up an octave for energy), bridge gets
    # a chord-tone walk (+3rd or +5th on weak beats).
    if bass_notes_locked:
        enriched = []
        for n in bass_notes_locked:
            sec = _section_at_bar(sections_plan, int(n["start_beats"] // 4))
            if not sec:
                enriched.append(n)
                continue
            role = sec["role"]
            if role.startswith("chorus"):
                # Alternate root and root+12
                beat_in_bar = n["start_beats"] % 4
                if beat_in_bar > 1.99:    # back half of bar gets the octave jump
                    n = dict(n)
                    n["pitch"] = int(n["pitch"]) + 12
                    n["velocity"] = min(127, int(n["velocity"]) + 5)
            elif role == "bridge":
                # Walk to the chord 5th on beat 3
                beat_in_bar = n["start_beats"] % 4
                if 2.4 < beat_in_bar < 3.1:
                    n = dict(n)
                    n["pitch"] = int(n["pitch"]) + 7
            enriched.append(n)
        midi_tools.midi_notes_add({"clip_id": bass_clip, "notes": enriched})

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
    # Arp variety. Earlier version had two bugs and zero per-bar
    # variation:
    #   * rate_beats / octave_offset args silently ignored (arp_pattern
    #     uses `rate` (string) and `octaves` (int)) — every bar fell
    #     through to default sixteenth/up/octaves=2.
    #   * Direction was section-fixed; whole sections played identical
    #     patterns.
    # Now: rate per section (eighth in verse, sixteenth in chorus,
    # triplet in bridge), direction CHANGES every 2-bar phrase within
    # a section, octave count flips between 1 and 2 per phrase, and
    # chorus rolls "octave_alternate" or "random" for extra motion.
    arp_notes = []
    section_rate = {
        "intro": "eighth",
        "verse": "eighth",
        "chorus": "sixteenth",
        "chorus_a": "sixteenth",
        "chorus_b": "sixteenth",
        "bridge": "triplet",
        "buildup": "sixteenth",
        "breakdown": "eighth",
        "outro": "eighth",
    }
    # Per-section direction pool — picked from each 2-bar phrase
    section_dir_pool = {
        "intro":     ["up"],
        "verse":     ["up", "up_down"],
        "chorus":    ["up_down", "octave_alternate", "random"],
        "chorus_a":  ["up_down", "octave_alternate"],
        "chorus_b":  ["random", "up_down", "octave_alternate"],
        "bridge":    ["down", "down_up"],
        "buildup":   ["up", "up"],     # straight ascent for tension
        "breakdown": ["down"],
        "outro":     ["down", "down"],
    }
    for bar in range(total_bars):
        sec = _section_at_bar(sections_plan, bar)
        if sec and sec["role"] in ("intro", "outro"):
            continue
        chord = progression_chords[bar % len(progression_chords)]
        section_role = sec["role"] if sec else "verse"
        rate = section_rate.get(section_role, "eighth")
        # Phrase index = bar // 2, picks a direction from the pool.
        phrase_idx = bar // 2
        pool = section_dir_pool.get(section_role, ["up"])
        direction = pool[phrase_idx % len(pool)]
        # Octave count alternates per phrase (1 octave for tighter feel,
        # 2 for sweeping feel)
        octaves = 1 + (phrase_idx % 2)
        try:
            r = mel_tools.arp_pattern({
                "chord_pitches": [chord["bass_root"] + 12] + list(chord["voicing"]),
                "length_beats": 4.0,
                "rate": rate,
                "direction": direction,
                "octaves": octaves,
                "start_beats": float(bar * 4),
                "velocity_base": 80,
            })
            arp_notes.extend(r.get("notes", []))
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                "arp_pattern failed at bar %d: %s", bar, e)
    midi_tools.midi_notes_add({"clip_id": arp_clip, "notes": arp_notes})

    # --- 9. Lead — per-section melody with quantization ---
    lead_id = track_tools.track_add({"name": "Lead", "kind": "midi"})["track_id"]
    plugin_tools.plugin_add({"track_id": lead_id, "type": "4OSC"})
    lead_clip = midi_tools.midi_clip_add({
        "track_id": lead_id, "name": "Lead",
        "start_beats": 0.0, "length_beats": total_bars * 4,
    })["clip_id"]
    # Chord-aware lead — pulls notes from the current chord's tones so
    # borrowed/non-diatonic chords don't clash with a key-bound scale.
    # Centered in octave 5 (MIDI 72-84) which is the standard synthwave
    # lead register. Previous implementation called melody_generate with
    # the song's key only, ignoring chord changes, which produced
    # out-of-tune notes against borrowed chords.
    lead_notes_all = _chord_aware_lead(
        progression_chords=progression_chords,
        sections_plan=sections_plan,
        total_bars=total_bars,
        base_octave_midi=72,    # C5 — sits above pad/keys, below scream
        seed=seed * 59 + 1,
    )
    if lead_notes_all:
        midi_tools.midi_notes_add({"clip_id": lead_clip, "notes": lead_notes_all})

    # --- 9b. Sub Bass — sustained octave-low chord roots (60% chance) ---
    sub_id = None
    if rng.random() < 0.6:
        sub_id = track_tools.track_add({"name": "Sub Bass", "kind": "midi"})["track_id"]
        plugin_tools.plugin_add({"track_id": sub_id, "type": "4OSC"})
        sub_clip = midi_tools.midi_clip_add({
            "track_id": sub_id, "name": "Sub",
            "start_beats": 0.0, "length_beats": total_bars * 4,
        })["clip_id"]
        sub_notes = []
        for bar in range(total_bars):
            sec = _section_at_bar(sections_plan, bar)
            # Sub mutes during intro/outro to leave space for entry
            if sec and sec["role"] in ("intro", "outro"):
                continue
            chord = progression_chords[bar % len(progression_chords)]
            # Octave below the main bass root for real low end
            sub_notes.append({
                "pitch": int(chord["bass_root"]) - 12,
                "start_beats": float(bar * 4),
                "length_beats": 4.05,    # sustain across the bar
                "velocity": 85,
            })
        if sub_notes:
            midi_tools.midi_notes_add({"clip_id": sub_clip, "notes": sub_notes})

    # --- 9c. Pluck — short percussive chord stabs on offbeats (50% chance) ---
    pluck_id = None
    if rng.random() < 0.5:
        pluck_id = track_tools.track_add({"name": "Pluck", "kind": "midi"})["track_id"]
        plugin_tools.plugin_add({"track_id": pluck_id, "type": "4OSC"})
        pluck_clip = midi_tools.midi_clip_add({
            "track_id": pluck_id, "name": "Pluck",
            "start_beats": 0.0, "length_beats": total_bars * 4,
        })["clip_id"]
        pluck_notes = []
        # Stabs on the "&" of beats 2 and 4 (synthwave offbeat pluck pattern)
        # in 4/4: positions 1.5 and 3.5 of each bar
        for bar in range(total_bars):
            sec = _section_at_bar(sections_plan, bar)
            if sec and sec["role"] in ("intro", "outro", "breakdown"):
                continue
            chord = progression_chords[bar % len(progression_chords)]
            for offset in (1.5, 3.5):
                # Play the upper voicing notes, very short
                for p in chord["voicing"][:2]:
                    pluck_notes.append({
                        "pitch": int(p) + 12,    # extra octave up for brightness
                        "start_beats": float(bar * 4 + offset),
                        "length_beats": 0.2,    # staccato
                        "velocity": 85,
                    })
        if pluck_notes:
            midi_tools.midi_notes_add({"clip_id": pluck_clip, "notes": pluck_notes})

    # --- 9d. Counter melody — rhythmic counterpoint during verses (50%) ---
    counter_id = None
    if rng.random() < 0.5:
        counter_id = track_tools.track_add({"name": "Counter", "kind": "midi"})["track_id"]
        plugin_tools.plugin_add({"track_id": counter_id, "type": "4OSC"})
        counter_clip = midi_tools.midi_clip_add({
            "track_id": counter_id, "name": "Counter",
            "start_beats": 0.0, "length_beats": total_bars * 4,
        })["clip_id"]
        # Chord-aware counter: same approach as the lead but in a lower
        # register (octave 4 = MIDI 60, below the C5 lead). Plays only
        # during verses (skips chorus/bridge/intro/outro/buildup/breakdown)
        # so it fills space the lead leaves.
        counter_notes_all = _chord_aware_lead(
            progression_chords=progression_chords,
            sections_plan=sections_plan,
            total_bars=total_bars,
            base_octave_midi=60,   # C4 — under the C5 lead
            seed=seed * 67 + 7,
            sec_role_filter=("intro", "outro", "buildup", "breakdown",
                             "chorus", "chorus_a", "chorus_b", "bridge"),
            rest_chance=0.55,    # counter is sparse
        )
        # Counter is supposed to be quieter than the lead
        for n in counter_notes_all:
            n["velocity"] = max(40, int(n["velocity"]) - 20)
        if counter_notes_all:
            midi_tools.midi_notes_add({"clip_id": counter_clip,
                                        "notes": counter_notes_all})

    # --- 9e. Pad B — second pad layer with different voicing (40% chance) ---
    pad_b_id = None
    if rng.random() < 0.4:
        pad_b_id = track_tools.track_add({"name": "Pad B", "kind": "midi"})["track_id"]
        plugin_tools.plugin_add({"track_id": pad_b_id, "type": "4OSC"})
        pad_b_clip = midi_tools.midi_clip_add({
            "track_id": pad_b_id, "name": "Pad B",
            "start_beats": 0.0, "length_beats": total_bars * 4,
        })["clip_id"]
        pad_b_notes = []
        for bar in range(total_bars):
            sec = _section_at_bar(sections_plan, bar)
            if sec and sec["role"] in ("intro", "outro"):
                continue
            chord = progression_chords[bar % len(progression_chords)]
            # Pad B sits an octave above main pad with just root + 5th for openness
            pad_b_notes.append({
                "pitch": int(chord["bass_root"]) + 24,   # 2 octaves up
                "start_beats": float(bar * 4),
                "length_beats": 4.05,
                "velocity": 50,
            })
            pad_b_notes.append({
                "pitch": int(chord["bass_root"]) + 24 + 7,  # +5th
                "start_beats": float(bar * 4),
                "length_beats": 4.05,
                "velocity": 45,
            })
        if pad_b_notes:
            midi_tools.midi_notes_add({"clip_id": pad_b_clip, "notes": pad_b_notes})

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
    if sub_id:
        track_tools.mix_apply_reference({"track_id": sub_id,
                                          "genre": "synthwave", "role": "sub_bass",
                                          "offset_db": +6})
    if pluck_id:
        # Pluck is decorative — sit it under the arp
        track_tools.mix_apply_reference({"track_id": pluck_id,
                                          "genre": "synthwave", "role": "arp",
                                          "offset_db": -4})
    if counter_id:
        track_tools.mix_apply_reference({"track_id": counter_id,
                                          "genre": "synthwave", "role": "counter",
                                          "offset_db": +2})
    if pad_b_id:
        # Pad B is a glue layer — barely-there
        track_tools.mix_apply_reference({"track_id": pad_b_id,
                                          "genre": "synthwave", "role": "pad",
                                          "offset_db": -8})

    # Master pulled back so transients have headroom
    STORE.edit.master_volume_db = -2.0

    # --- 11. Save ---
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    edit_tools.edit_save({"path": out_path})

    optional_layers = {
        "sub_bass": sub_id is not None,
        "pluck":    pluck_id is not None,
        "counter":  counter_id is not None,
        "pad_b":    pad_b_id is not None,
    }
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
        "arp_directions": "varies per 2-bar phrase",
        "chord_roots": [int(c["bass_root"]) for c in progression_chords],
        "optional_layers": optional_layers,
        "track_count": 5 + sum(optional_layers.values()),
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


def _chord_aware_lead(
    *,
    progression_chords: list[dict],
    sections_plan: list[dict],
    total_bars: int,
    base_octave_midi: int,
    seed: int,
    sec_role_filter: tuple[str, ...] = ("intro", "outro", "buildup", "breakdown"),
    rest_chance: float = 0.35,
) -> list[dict]:
    """Generate a lead melody whose notes follow the underlying chord
    instead of the key. For each bar, pick notes from the current chord's
    tones (root, 3, 5, 7) with occasional scale-step passing tones,
    keeping everything consonant against borrowed chords.

    base_octave_midi: where the melody centers (60=C4, 72=C5, 84=C6).
                     Notes will land within ±5 semitones of this.
    rest_chance: per-bar chance the lead rests (creates space).
    """
    rng = random.Random(seed)
    notes: list[dict] = []

    for bar in range(total_bars):
        sec = _section_at_bar(sections_plan, bar)
        if sec and sec["role"] in sec_role_filter:
            continue
        # Section types that get DENSER lead vs sparser
        is_chorus = bool(sec and sec["role"].startswith("chorus"))
        # Per-bar rest probability — choruses get more lead, verses sparser
        if rng.random() < (rest_chance if is_chorus else rest_chance * 1.4):
            continue

        chord = progression_chords[bar % len(progression_chords)]
        # Build the available pitch pool: chord tones + neighbour notes.
        # voicing is [3rd+12, 5th+12, 7th+12]; bass_root is the chord root.
        chord_tones_pc = set()
        for pitch in [chord["bass_root"]] + list(chord["voicing"]):
            chord_tones_pc.add(int(pitch) % 12)

        # Pick pitches in our target octave that match chord-tone PCs,
        # plus scale-step neighbours (±1 or ±2 semitones) for melodic motion
        candidate_pitches: list[int] = []
        for pc in chord_tones_pc:
            for octave_off in (-12, 0, 12):
                target_class = pc
                # Find the pitch in [base-7, base+7] matching this PC
                for offset in range(-7, 8):
                    candidate = base_octave_midi + offset + octave_off
                    if candidate % 12 == target_class:
                        candidate_pitches.append(candidate)
        # Dedup and constrain
        candidate_pitches = sorted(set(p for p in candidate_pitches
                                        if base_octave_midi - 7 <= p <= base_octave_midi + 9))
        if not candidate_pitches:
            continue

        # Lay 1-3 notes per bar depending on section
        n_notes = rng.randint(2, 4) if is_chorus else rng.randint(1, 2)
        # Beat positions on the 8th-note grid (0, 0.5, 1.0, ..., 3.5)
        beat_choices = [0.0, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5]
        chosen_beats = sorted(rng.sample(beat_choices,
                                          min(n_notes, len(beat_choices))))
        for i, b in enumerate(chosen_beats):
            # First note of the bar should be a strong chord tone
            if i == 0:
                pitch = rng.choice([p for p in candidate_pitches
                                    if p % 12 in chord_tones_pc])
            else:
                pitch = rng.choice(candidate_pitches)
            # Length: mostly 8th to dotted-quarter, rarely longer
            length = rng.choice([0.5, 0.75, 1.0, 1.5])
            velocity = 95 if is_chorus else 80
            notes.append({
                "pitch": int(pitch),
                "start_beats": float(bar * 4 + b),
                "length_beats": float(length),
                "velocity": velocity,
            })
    return notes


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
