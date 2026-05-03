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
from . import instruments as instr_tools
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

    # --- 3b. Section-aware progression variants ---
    # Verse uses the base progression. Chorus substitutes the last chord
    # with V7 (dominant pull). Bridge transposes up a minor 3rd
    # (modulation). Intro/outro use simpler pedal tones. See
    # _make_section_progressions for full mapping.
    section_progressions = _make_section_progressions(progression_chords, rng)

    def chord_at(bar: int) -> dict:
        return _chord_for_bar(section_progressions, sections_plan, bar,
                              progression_chords)

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

    # --- 6. Bass — pick a real bass preset (Smack/Diode/Dub/Pick/...) ---
    bass_id = track_tools.track_add({"name": "Bass", "kind": "midi"})["track_id"]
    chosen_bass = instr_tools.equip(bass_id, "bass", rng)
    # Bump output gain so factory bass presets sit forward under drums.
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
    # Build per-bar progression for bass-locked-to-kick (chord-aware)
    bar_chord_list = [chord_at(b) for b in range(total_bars)]
    bass_notes_locked = _bass_locked_to_kick(
        drum_notes=drum_notes,
        progression_chords=bar_chord_list,
        total_bars=total_bars,
        bar_beats=4,
        bass_octave=0,
        velocity=100,
    )
    # Section-aware bass enrichment + layer-entry filter
    if bass_notes_locked:
        enriched = []
        for n in bass_notes_locked:
            bar = int(n["start_beats"] // 4)
            sec = _section_at_bar(sections_plan, bar)
            pos = _bar_position_in_section(sections_plan, bar)
            if not sec or not pos:
                enriched.append(n)
                continue
            role = sec["role"]
            bar_off, sec_n = pos
            # Layer-entry: bass enters mid-intro, drops mid-outro
            if not _track_active_at_bar("bass", role, bar_off, sec_n):
                continue
            if role.startswith("chorus"):
                beat_in_bar = n["start_beats"] % 4
                if beat_in_bar > 1.99:
                    n = dict(n)
                    n["pitch"] = int(n["pitch"]) + 12
                    n["velocity"] = min(127, int(n["velocity"]) + 5)
            elif role == "bridge":
                beat_in_bar = n["start_beats"] % 4
                if 2.4 < beat_in_bar < 3.1:
                    n = dict(n)
                    n["pitch"] = int(n["pitch"]) + 7
            enriched.append(n)
        midi_tools.midi_notes_add({"clip_id": bass_clip, "notes": enriched})

    # --- 7. Pad — pick a real pad preset (Bell/Bright/Dark/Hollow/...) ---
    pad_id = track_tools.track_add({"name": "Pad", "kind": "midi"})["track_id"]
    chosen_pad = instr_tools.equip(pad_id, "pad", rng, fallback="strings")
    pad_clip = midi_tools.midi_clip_add({
        "track_id": pad_id, "name": "Pad",
        "start_beats": 0.0, "length_beats": total_bars * 4,
    })["clip_id"]
    pad_notes = []
    for bar in range(total_bars):
        sec = _section_at_bar(sections_plan, bar)
        pos = _bar_position_in_section(sections_plan, bar)
        if sec and pos and not _track_active_at_bar("pad", sec["role"], pos[0], pos[1]):
            continue
        chord = chord_at(bar)
        for p in chord["voicing"]:
            pad_notes.append({
                "pitch": int(p),
                "start_beats": float(bar * 4),
                "length_beats": 4.05,
                "velocity": 60,
            })
    midi_tools.midi_notes_add({"clip_id": pad_clip, "notes": pad_notes})

    # --- 8. (Arp removed — formulaic ascending arps were the same every
    # song. The Piano now carries the melody role; if anything fills the
    # mid-rhythm space later, it should be a section-aware decoration,
    # not a constant 8th/16th machine.)
    arp_id = None
    chosen_arp = None

    # --- 9. Lead — PIANO. Acoustic piano carries the melody, replacing
    # the synth lead that everything else used to compete with. Falls
    # back to e-piano / keys, then to a synth lead only as last resort.
    lead_id = track_tools.track_add({"name": "Lead Piano", "kind": "midi"})["track_id"]
    lead_category = "piano"
    chosen_lead = instr_tools.equip(lead_id, lead_category, rng,
                                     fallback="keys")
    lead_clip = midi_tools.midi_clip_add({
        "track_id": lead_id, "name": "Lead",
        "start_beats": 0.0, "length_beats": total_bars * 4,
    })["clip_id"]
    # Chord-aware lead — pulls notes from the current chord's tones so
    # borrowed/non-diatonic chords don't clash. Uses chord_lookup so the
    # section-aware progressions (chorus V7 substitution, bridge
    # transposition) drive the lead too.
    #
    # In VERSES, lead plays only on EVEN-indexed 4-bar phrases (bars
    # 1-4, 9-12) so the Counter can answer on the odd phrases. In
    # chorus / bridge, lead plays the full section.
    lead_notes_all = _chord_aware_lead(
        progression_chords=progression_chords,
        sections_plan=sections_plan,
        total_bars=total_bars,
        base_octave_midi=72,
        seed=seed * 59 + 1,
        chord_lookup=chord_at,
        # Verses get phrase_filter; choruses/bridge ignore it
    )
    # Apply the phrase filter only to verse-sectioned notes
    lead_notes_filtered = []
    for n in lead_notes_all:
        bar = int(n["start_beats"] // 4)
        sec = _section_at_bar(sections_plan, bar)
        pos = _bar_position_in_section(sections_plan, bar)
        if sec and pos and sec["role"] == "verse":
            phrase_idx = pos[0] // 4
            if phrase_idx % 2 == 1:
                continue   # leave odd phrases for the counter
        # Layer-entry: lead silent in intro/outro/buildup/breakdown
        if sec and pos and not _track_active_at_bar(
                "lead", sec["role"], pos[0], pos[1]):
            # _track_active_at_bar returns True for verse/chorus/bridge by
            # default; the explicit no-go cases (intro/outro/buildup/
            # breakdown) are already handled in _chord_aware_lead's
            # sec_role_filter. So this is a defensive guard.
            continue
        lead_notes_filtered.append(n)
    if lead_notes_filtered:
        midi_tools.midi_notes_add({"clip_id": lead_clip,
                                    "notes": lead_notes_filtered})

    # --- 9b. Sub Bass — sustained octave-low chord roots (60% chance) ---
    sub_id = None
    if rng.random() < 0.6:
        sub_id = track_tools.track_add({"name": "Sub Bass", "kind": "midi"})["track_id"]
        instr_tools.equip(sub_id, "bass", rng)
        sub_clip = midi_tools.midi_clip_add({
            "track_id": sub_id, "name": "Sub",
            "start_beats": 0.0, "length_beats": total_bars * 4,
        })["clip_id"]
        sub_notes = []
        for bar in range(total_bars):
            sec = _section_at_bar(sections_plan, bar)
            pos = _bar_position_in_section(sections_plan, bar)
            if sec and pos and not _track_active_at_bar(
                    "sub_bass", sec["role"], pos[0], pos[1]):
                continue
            chord = chord_at(bar)
            sub_notes.append({
                "pitch": int(chord["bass_root"]) - 12,
                "start_beats": float(bar * 4),
                "length_beats": 4.05,
                "velocity": 85,
            })
        if sub_notes:
            midi_tools.midi_notes_add({"clip_id": sub_clip, "notes": sub_notes})

    # --- 9c. Pluck — short percussive chord stabs on offbeats (50% chance) ---
    pluck_id = None
    if rng.random() < 0.5:
        pluck_id = track_tools.track_add({"name": "Pluck", "kind": "midi"})["track_id"]
        instr_tools.equip(pluck_id, "pluck", rng, fallback="lead")
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
        # Counter often plays a contrasting timbre — strings or vibes
        instr_tools.equip(counter_id, rng.choice(["strings", "vibes", "lead"]),
                          rng, fallback="lead")
        counter_clip = midi_tools.midi_clip_add({
            "track_id": counter_id, "name": "Counter",
            "start_beats": 0.0, "length_beats": total_bars * 4,
        })["clip_id"]
        # Counter: chord-aware, lower register, ANSWERS the lead.
        # Lead plays even-indexed verse phrases (bars 1-4, 9-12) so
        # counter plays the odd phrases (5-8, 13-16). Result is a
        # call-and-response: lead states a phrase, counter answers,
        # lead returns, counter answers, etc.
        counter_notes_all = _chord_aware_lead(
            progression_chords=progression_chords,
            sections_plan=sections_plan,
            total_bars=total_bars,
            base_octave_midi=60,
            seed=seed * 67 + 7,
            sec_role_filter=("intro", "outro", "buildup", "breakdown",
                             "chorus", "chorus_a", "chorus_b", "bridge"),
            # Aggressive: counter only plays on odd-indexed verse phrases
            # AND each bar is then dice-rolled for rest. Combined effect
            # was killing the layer (4 notes across whole song). Pull
            # rest_chance way down so phrase-filtered bars actually play.
            rest_chance=0.15,
            phrase_filter="odd",
            chord_lookup=chord_at,
        )
        for n in counter_notes_all:
            n["velocity"] = max(40, int(n["velocity"]) - 20)
        if counter_notes_all:
            midi_tools.midi_notes_add({"clip_id": counter_clip,
                                        "notes": counter_notes_all})

    # --- 9e. Pad B — second pad layer with different voicing (40% chance) ---
    pad_b_id = None
    if rng.random() < 0.4:
        pad_b_id = track_tools.track_add({"name": "Pad B", "kind": "midi"})["track_id"]
        instr_tools.equip(pad_b_id, "pad", rng, fallback="strings")
        pad_b_clip = midi_tools.midi_clip_add({
            "track_id": pad_b_id, "name": "Pad B",
            "start_beats": 0.0, "length_beats": total_bars * 4,
        })["clip_id"]
        pad_b_notes = []
        for bar in range(total_bars):
            sec = _section_at_bar(sections_plan, bar)
            pos = _bar_position_in_section(sections_plan, bar)
            if sec and pos and not _track_active_at_bar(
                    "pad_b", sec["role"], pos[0], pos[1]):
                continue
            chord = chord_at(bar)
            pad_b_notes.append({
                "pitch": int(chord["bass_root"]) + 24,
                "start_beats": float(bar * 4),
                "length_beats": 4.05,
                "velocity": 50,
            })
            pad_b_notes.append({
                "pitch": int(chord["bass_root"]) + 24 + 7,
                "start_beats": float(bar * 4),
                "length_beats": 4.05,
                "velocity": 45,
            })
        if pad_b_notes:
            midi_tools.midi_notes_add({"clip_id": pad_b_clip, "notes": pad_b_notes})

    # --- 9f. Harmony — parallel-3rd doubling of the Lead (35% chance)
    # Doubles every lead note a major or minor 3rd up (whichever is in
    # the current chord). Instant fullness without competing for space.
    harmony_id = None
    if rng.random() < 0.35 and lead_notes_filtered:
        harmony_id = track_tools.track_add({"name": "Harmony", "kind": "midi"})["track_id"]
        # Harmony usually gets a contrasting voice from the lead — brass
        # or strings or a softer lead. Distinct timbre from main lead.
        instr_tools.equip(harmony_id,
                          rng.choice(["brass", "strings", "lead", "violin"]),
                          rng, fallback="lead")
        harmony_clip = midi_tools.midi_clip_add({
            "track_id": harmony_id, "name": "Harmony",
            "start_beats": 0.0, "length_beats": total_bars * 4,
        })["clip_id"]
        harmony_notes = []
        for n in lead_notes_filtered:
            bar = int(n["start_beats"] // 4)
            chord = chord_at(bar)
            chord_pcs = {int(p) % 12 for p in [chord["bass_root"]] + list(chord["voicing"])}
            # Try +4 (major 3rd) and +3 (minor 3rd); pick whichever
            # lands in the chord's pitch-class set
            harm_pitch = None
            for interval in (4, 3, 7):    # 3rd up, then 5th up as fallback
                cand = int(n["pitch"]) + interval
                if cand % 12 in chord_pcs:
                    harm_pitch = cand
                    break
            if harm_pitch is None:
                continue
            harmony_notes.append({
                "pitch": int(harm_pitch),
                "start_beats": float(n["start_beats"]),
                "length_beats": float(n["length_beats"]),
                "velocity": max(50, int(n["velocity"]) - 15),
            })
        if harmony_notes:
            midi_tools.midi_notes_add({"clip_id": harmony_clip,
                                        "notes": harmony_notes})

    # --- 9g. Chimes — high-register chord-tone sparkle (50% chance)
    # Adds the high-frequency content that's been missing from every
    # render audit (-25 dB air band). Plays one 8th-note chord-tone ping
    # per bar in MIDI 84-96 register.
    chimes_id = None
    if rng.random() < 0.5:
        chimes_id = track_tools.track_add({"name": "Chimes", "kind": "midi"})["track_id"]
        instr_tools.equip(chimes_id,
                          rng.choice(["bells", "vibes", "pluck"]),
                          rng, fallback="pluck")
        chimes_clip = midi_tools.midi_clip_add({
            "track_id": chimes_id, "name": "Chimes",
            "start_beats": 0.0, "length_beats": total_bars * 4,
        })["clip_id"]
        chimes_notes = []
        for bar in range(total_bars):
            sec = _section_at_bar(sections_plan, bar)
            pos = _bar_position_in_section(sections_plan, bar)
            # Chimes ride through chorus + bridge + verse-second-half;
            # silent in intro/outro/buildup/breakdown
            if not sec or sec["role"] in ("intro", "outro", "buildup", "breakdown"):
                continue
            if not pos: continue
            chord = chord_at(bar)
            # Pick chord 5th in MIDI 84-95 range; if 5th sits low, use root
            target_pc = (int(chord["bass_root"]) + 7) % 12
            for cand in range(84, 96):
                if cand % 12 == target_pc:
                    pitch = cand
                    break
            else:
                pitch = 84 + (int(chord["bass_root"]) % 12)
            # One ping per bar on beat 1
            chimes_notes.append({
                "pitch": int(pitch),
                "start_beats": float(bar * 4),
                "length_beats": 1.5,
                "velocity": 65,
            })
            # Sparkle on the "&" of beat 3 in chorus only
            if sec["role"].startswith("chorus"):
                chimes_notes.append({
                    "pitch": int(pitch) + 5,    # add a 4th interval up
                    "start_beats": float(bar * 4 + 2.5),
                    "length_beats": 1.0,
                    "velocity": 60,
                })
        if chimes_notes:
            midi_tools.midi_notes_add({"clip_id": chimes_clip,
                                        "notes": chimes_notes})

    # --- 9h. Risers — synth FX sweep at section boundaries (40% chance)
    # Long ascending pitch sweep on the bar BEFORE each chorus / bridge
    # entry. Anticipation tool. Implemented as an 8-note ascending
    # chromatic run on a separate track so it's distinct from the arp.
    riser_id = None
    if rng.random() < 0.4:
        riser_id = track_tools.track_add({"name": "Riser", "kind": "midi"})["track_id"]
        instr_tools.equip(riser_id, "lead", rng)
        riser_clip = midi_tools.midi_clip_add({
            "track_id": riser_id, "name": "Riser",
            "start_beats": 0.0, "length_beats": total_bars * 4,
        })["clip_id"]
        riser_notes = []
        # Find section boundaries leading into chorus/bridge
        cursor_bar = 0
        for sec_idx, sec in enumerate(sections_plan):
            cursor_bar += sec["bars"]
            # cursor_bar is now the start of the NEXT section
            if sec_idx + 1 >= len(sections_plan):
                break
            next_sec = sections_plan[sec_idx + 1]
            if next_sec["role"] not in ("chorus", "chorus_a", "chorus_b",
                                         "bridge", "buildup"):
                continue
            # Place riser on the LAST bar of current section
            riser_bar = cursor_bar - 1
            if riser_bar < 0:
                continue
            # 8-note ascending chromatic from MIDI 60 → 67
            for i in range(8):
                riser_notes.append({
                    "pitch": 60 + i,
                    "start_beats": float(riser_bar * 4 + i * 0.5),
                    "length_beats": 0.45,
                    "velocity": 55 + i * 5,    # crescendo
                })
        if riser_notes:
            midi_tools.midi_notes_add({"clip_id": riser_clip,
                                        "notes": riser_notes})

    # --- 9i. Tom fills — replace some snare-roll fills with tom rolls
    # (always present). Adds drum-kit color beyond just kick/snare/hat
    # already on the Drums track via direct note injection.
    tom_fill_notes = []
    cursor_bar = 0
    for sec_idx, sec in enumerate(sections_plan):
        sec_bars = sec["bars"]
        if sec_idx + 1 < len(sections_plan) and sec_bars >= 4:
            next_sec = sections_plan[sec_idx + 1]
            # Half the time, replace the fill with a tom roll
            if (sec["role"] not in ("intro", "outro")
                    and next_sec["role"] in ("chorus", "chorus_a", "chorus_b")
                    and rng.random() < 0.5):
                fill_bar = cursor_bar + sec_bars - 1
                # 16th-note tom roll on beats 3-4: 4 hits low → mid → high → low
                tom_seq = [45, 48, 50, 48, 45, 50, 48, 45]    # low/mid/hi toms
                for i, pitch in enumerate(tom_seq):
                    tom_fill_notes.append({
                        "pitch": pitch,
                        "start_beats": float(fill_bar * 4 + 2 + i * 0.25),
                        "length_beats": 0.2,
                        "velocity": 80 + i * 4,
                    })
        cursor_bar += sec_bars
    if tom_fill_notes:
        midi_tools.midi_notes_add({"clip_id": drum_clip, "notes": tom_fill_notes})

    # --- 9j. Keys — chord comping (e-piano / wurli, NOT acoustic piano).
    # The acoustic piano is now the LEAD (carries the melody), so this
    # comping track uses a contrasting electric keys voice. Plays chord
    # voicing on beats 1 and 3 every bar in MIDI 55-72.
    piano_id = track_tools.track_add({"name": "Keys", "kind": "midi"})["track_id"]
    instr_tools.equip(piano_id, "keys", rng, fallback="strings")
    piano_clip = midi_tools.midi_clip_add({
        "track_id": piano_id, "name": "Piano",
        "start_beats": 0.0, "length_beats": total_bars * 4,
    })["clip_id"]
    piano_notes = []
    for bar in range(total_bars):
        sec = _section_at_bar(sections_plan, bar)
        if not sec or sec["role"] in ("intro", "outro", "buildup", "breakdown"):
            continue
        chord = chord_at(bar)
        # Build a 3-note voicing in MIDI 55-72 range
        root = int(chord["bass_root"])
        # Drop voicing into mid register
        voicing_pcs = [(root) % 12, (root + 4 if "voicing" not in chord else (chord["voicing"][0]) % 12),
                       (chord["voicing"][1] if len(chord.get("voicing", [])) > 1 else root + 7) % 12]
        voiced = []
        for pc in voicing_pcs:
            for cand in range(55, 73):
                if cand % 12 == pc:
                    voiced.append(cand); break
        # Beat 1 and beat 3 hits
        for beat_offset, vel in ((0.0, 75), (2.0, 65)):
            for p in voiced:
                piano_notes.append({
                    "pitch": int(p),
                    "start_beats": float(bar * 4 + beat_offset),
                    "length_beats": 1.9,
                    "velocity": vel,
                })
    if piano_notes:
        midi_tools.midi_notes_add({"clip_id": piano_clip, "notes": piano_notes})

    # --- 9k. Violin — always-on sustained line (chord 5th)
    # Acoustic strings color over the synth pad. Arp is back to a synth
    # pluck, so the violin can return to playing real violin.
    violin_id = track_tools.track_add({"name": "Violin", "kind": "midi"})["track_id"]
    instr_tools.equip(violin_id, "violin", rng, fallback="strings")
    violin_clip = midi_tools.midi_clip_add({
        "track_id": violin_id, "name": "Violin",
        "start_beats": 0.0, "length_beats": total_bars * 4,
    })["clip_id"]
    violin_notes = []
    for bar in range(total_bars):
        sec = _section_at_bar(sections_plan, bar)
        if not sec:
            continue
        # Violin rides through verse-back-half + chorus + bridge
        if sec["role"] in ("intro", "outro", "buildup", "breakdown"):
            continue
        pos = _bar_position_in_section(sections_plan, bar)
        if pos and sec["role"].startswith("verse") and pos[0] < pos[1] // 2:
            # Skip first half of verses to leave space
            continue
        chord = chord_at(bar)
        target_pc = (int(chord["bass_root"]) + 7) % 12     # 5th
        for cand in range(67, 80):
            if cand % 12 == target_pc:
                pitch_5 = cand; break
        else:
            pitch_5 = 67 + (int(chord["bass_root"]) % 12)
        # Sustained whole note + a swelled half-note on beat 3 in chorus
        violin_notes.append({
            "pitch": int(pitch_5),
            "start_beats": float(bar * 4),
            "length_beats": 4.0,
            "velocity": 60,
        })
        if sec["role"].startswith("chorus"):
            # Add a melodic 3rd above to thicken in chorus
            target_pc3 = (int(chord["bass_root"]) + 11) % 12
            for cand in range(72, 84):
                if cand % 12 == target_pc3:
                    pitch_3 = cand; break
            else:
                pitch_3 = pitch_5 + 4
            violin_notes.append({
                "pitch": int(pitch_3),
                "start_beats": float(bar * 4 + 2.0),
                "length_beats": 2.0,
                "velocity": 55,
            })
    if violin_notes:
        midi_tools.midi_notes_add({"clip_id": violin_clip, "notes": violin_notes})

    # --- 10. Mix balance ---
    # Calibrated against analyzer of seed-16151542:
    # 80% of energy was below 250Hz (Sub Bass 57%, Bass 23%); Mid 3.5%,
    # Upper Mid 0.34%, Presence 0.09%, Brilliance/Air ~0%. Spectral
    # centroid 2088 Hz vs synthwave norm ~3200 Hz. Drums only 7.7%
    # percussive. Lead was buried — arp's harmonics dominated the
    # midrange so the ear treated arp as the melody.
    #
    # Strategy: slash Sub Bass + Bass, push Drums forward, boost Lead
    # so it owns the mid, lift everything that adds presence/air
    # (piano, violin, chimes).
    track_tools.mix_apply_reference({"track_id": drums_id,
                                      "genre": "synthwave", "role": "drums",
                                      "offset_db": +6})    # +2 → +6 (was 7.7% energy)
    track_tools.mix_apply_reference({"track_id": bass_id,
                                      "genre": "synthwave", "role": "bass",
                                      "offset_db": -2})    # +6 → -2 (Bass band was 23%)
    track_tools.mix_apply_reference({"track_id": pad_id,
                                      "genre": "synthwave", "role": "pad",
                                      "offset_db": 0})     # +2 → 0
    # (Arp track removed — no mix line for it)
    track_tools.mix_apply_reference({"track_id": lead_id,
                                      "genre": "synthwave", "role": "lead",
                                      "offset_db": +6})    # piano lead — slightly less hot than synth
    if sub_id:
        track_tools.mix_apply_reference({"track_id": sub_id,
                                          "genre": "synthwave", "role": "sub_bass",
                                          "offset_db": -8})   # +6 → -8 (Sub Bass was 57%)
    if pluck_id:
        track_tools.mix_apply_reference({"track_id": pluck_id,
                                          "genre": "synthwave", "role": "arp",
                                          "offset_db": -10})
    if counter_id:
        track_tools.mix_apply_reference({"track_id": counter_id,
                                          "genre": "synthwave", "role": "counter",
                                          "offset_db": -2})   # +2 → -2 (sit under lead)
    if pad_b_id:
        track_tools.mix_apply_reference({"track_id": pad_b_id,
                                          "genre": "synthwave", "role": "pad",
                                          "offset_db": -12})  # -8 → -12 (less mud)
    if harmony_id:
        track_tools.mix_apply_reference({"track_id": harmony_id,
                                          "genre": "synthwave", "role": "lead",
                                          "offset_db": -2})
    if chimes_id:
        # Brilliance/Air bands were 0% — chimes lift the top octave
        track_tools.mix_apply_reference({"track_id": chimes_id,
                                          "genre": "synthwave", "role": "arp",
                                          "offset_db": +5})   # -3 → +5
    if riser_id:
        track_tools.mix_apply_reference({"track_id": riser_id,
                                          "genre": "synthwave", "role": "fx",
                                          "offset_db": +2})
    # Keys (e-piano comping) — sits well under the piano lead
    track_tools.mix_apply_reference({"track_id": piano_id,
                                      "genre": "synthwave", "role": "keys",
                                      "offset_db": -1})       # comp under lead
    track_tools.mix_apply_reference({"track_id": violin_id,
                                      "genre": "synthwave", "role": "strings",
                                      "offset_db": +3})

    # Master pulled back so transients have headroom and the song can
    # breathe. Earlier -2 was clipping with this many layers.
    STORE.edit.master_volume_db = -5.0

    # --- 11. Save ---
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    edit_tools.edit_save({"path": out_path})

    optional_layers = {
        "sub_bass": sub_id is not None,
        "pluck":    pluck_id is not None,
        "counter":  counter_id is not None,
        "pad_b":    pad_b_id is not None,
        "harmony":  harmony_id is not None,
        "chimes":   chimes_id is not None,
        "riser":    riser_id is not None,
        "tom_fills": bool(tom_fill_notes),
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
        "track_count": 5 + sum(1 for k, v in optional_layers.items()
                                if v and k != "tom_fills"),
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


def _bar_position_in_section(sections_plan: list[dict], bar: int) -> tuple[int, int] | None:
    """Return (bar_offset_within_section, section_total_bars) or None
    if bar isn't inside any section."""
    cursor = 0
    for sec in sections_plan:
        n = sec["bars"]
        if cursor <= bar < cursor + n:
            return (bar - cursor, n)
        cursor += n
    return None


def _make_section_progressions(
    base_progression: list[dict],
    rng: random.Random,
) -> dict[str, list[dict]]:
    """Build per-section-role progression variants from a single base
    4-chord progression.

    Returns a dict keyed by section role:
      "verse"   → base progression as-is
      "chorus"  → substitute the LAST chord with the V dominant of the
                  first chord (creates pull back to top), and bump the
                  pad up an octave for energy
      "bridge"  → transpose every chord up a minor 3rd (modulation
                  away from key center) — that's the classic synthwave
                  bridge move
      "intro"   → pedal tone: just the tonic chord on every bar
      "outro"   → same as verse but with the LAST chord swapped to a
                  resolved tonic
      "buildup" → chord 4 sustained throughout (creates anticipation)
      "breakdown" → chord 1 sustained throughout (creates relief)

    All variant voicings preserve the seventh_interval of the source
    chord so the chord-aware lead helper still gets correct chord-tone
    sets.
    """
    out: dict[str, list[dict]] = {"verse": list(base_progression)}

    # Chorus: replace last chord with V of first chord (dominant pull)
    first_root = base_progression[0]["bass_root"]
    v_root = first_root + 7  # perfect 5th up = V
    chorus = list(base_progression[:-1])
    chorus.append({
        "bass_root": v_root,
        # Voicing: dom7 chord (V7) — 3rd, 5th, 7th up an octave
        "voicing": [v_root + 4 + 12, v_root + 7 + 12, v_root + 10 + 12],
        "seventh_interval": 10,    # dominant 7
    })
    out["chorus"] = chorus
    out["chorus_a"] = chorus
    out["chorus_b"] = chorus

    # Bridge: transpose up a minor 3rd (3 semitones) — modulation
    bridge = []
    for c in base_progression:
        bridge.append({
            "bass_root": int(c["bass_root"]) + 3,
            "voicing": [int(p) + 3 for p in c["voicing"]],
            "seventh_interval": int(c.get("seventh_interval", 11)),
        })
    out["bridge"] = bridge

    # Intro: pedal tonic
    tonic = base_progression[0]
    out["intro"] = [tonic, tonic, tonic, tonic]

    # Outro: like verse but resolve to tonic on last chord
    outro = list(base_progression[:-1])
    outro.append(tonic)
    out["outro"] = outro

    # Buildup: sustain on V7 (or last chord) — anticipation
    buildup_chord = chorus[-1]
    out["buildup"] = [buildup_chord, buildup_chord, buildup_chord, buildup_chord]

    # Breakdown: tonic pedal — relief
    out["breakdown"] = [tonic, tonic, tonic, tonic]

    return out


def _chord_for_bar(
    progressions: dict[str, list[dict]],
    sections_plan: list[dict],
    bar: int,
    base_progression: list[dict],
) -> dict:
    """Look up the chord for `bar`, picking the per-section-role
    progression and indexing into it by the bar's offset within the
    section. Falls back to base_progression if section role is unknown."""
    sec = _section_at_bar(sections_plan, bar)
    if sec is None:
        return base_progression[bar % len(base_progression)]
    pos = _bar_position_in_section(sections_plan, bar)
    if pos is None:
        return base_progression[bar % len(base_progression)]
    bar_off, _ = pos
    role = sec["role"]
    prog = progressions.get(role, base_progression)
    return prog[bar_off % len(prog)]


def _track_active_at_bar(
    track_role: str, sec_role: str, bar_offset_in_section: int,
    section_bars: int,
) -> bool:
    """Decide whether a track plays during `bar` based on its role and
    the section it's in.

    Implements layer-by-layer entry through the intro and drop-out
    through the outro so the song builds dynamically instead of
    everything starting at full.

    Order of entry through INTRO (relative bar offset within intro):
        bar 0+:           pad
        25%+ in:          sub bass
        50%+ in:          bass
        66%+ in:          arp
        last bar:         drum kick teaser (handled separately)
        next section:     drums + lead come in
    Order of drop-out through OUTRO (mirror):
        first bar:        lose drums + lead
        25%+ in:          lose arp
        50%+ in:          lose bass + sub
        last bar:         pad fade only

    Other sections: track plays normally except when explicitly muted
    (e.g., counter is verse-only, lead is chorus-or-bridge-only).
    """
    progress = (bar_offset_in_section / max(1, section_bars))    # 0..1
    if sec_role == "intro":
        if track_role == "pad":               return True
        if track_role == "sub_bass":          return progress >= 0.25
        if track_role == "bass":              return progress >= 0.50
        if track_role == "arp":               return progress >= 0.66
        if track_role == "drums":             return False    # drums in at verse start
        if track_role in ("lead", "counter"): return False
        if track_role == "pad_b":             return progress >= 0.50
        return progress >= 0.50
    if sec_role == "outro":
        if track_role == "pad":               return True   # pad rides to end
        if track_role in ("drums", "lead",
                            "counter"):       return False   # drop on entry
        if track_role == "arp":               return progress < 0.25
        if track_role in ("bass", "sub_bass",
                            "pad_b"):         return progress < 0.50
        return progress < 0.50
    if sec_role == "buildup":
        # Buildup keeps everything except lead (lead enters in chorus)
        if track_role in ("lead", "counter"): return False
        return True
    if sec_role == "breakdown":
        # Breakdown drops drums + arp; pad + bass + lead carry
        if track_role in ("drums", "arp"):    return False
        return True
    # Verse / chorus / bridge — handled by track-specific logic below
    return True


def _chord_aware_lead(
    *,
    progression_chords: list[dict],
    sections_plan: list[dict],
    total_bars: int,
    base_octave_midi: int,
    seed: int,
    sec_role_filter: tuple[str, ...] = ("intro", "outro", "buildup", "breakdown"),
    rest_chance: float = 0.35,
    phrase_filter: str | None = None,
    chord_lookup: "callable[[int], dict] | None" = None,
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
        # Phrase filter for call-and-response. phrase_filter="even"
        # plays only on 4-bar phrases 0, 2, 4, ... within a section
        # (lead bars 1-4, rest 5-8, lead 9-12, ...). "odd" inverts.
        if phrase_filter is not None:
            pos = _bar_position_in_section(sections_plan, bar)
            if pos:
                bar_off, _ = pos
                phrase_idx = bar_off // 4
                if phrase_filter == "even" and phrase_idx % 2 == 1:
                    continue
                if phrase_filter == "odd" and phrase_idx % 2 == 0:
                    continue
        # Section types that get DENSER lead vs sparser
        is_chorus = bool(sec and sec["role"].startswith("chorus"))
        if rng.random() < (rest_chance if is_chorus else rest_chance * 1.4):
            continue

        # Use chord_lookup if provided (section-aware), else cycle base
        if chord_lookup is not None:
            chord = chord_lookup(bar)
        else:
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


# --------------------------------------------------------------------------- #
# Arp profile generator                                                       #
# --------------------------------------------------------------------------- #


def _build_arp_notes(profile: str, total_bars: int, sections_plan,
                     chord_at, _sec_at, _bar_pos, _track_active, rng,
                     mel_tools) -> list[dict]:
    """One of six rhythmically distinct arp patterns, picked per song."""
    notes: list[dict] = []
    for bar in range(total_bars):
        sec = _sec_at(sections_plan, bar)
        pos = _bar_pos(sections_plan, bar)
        if sec and pos and not _track_active("arp", sec["role"], pos[0], pos[1]):
            continue
        chord = chord_at(bar)
        chord_pitches = [chord["bass_root"] + 12] + list(chord["voicing"])
        bar_start = float(bar * 4)

        if profile == "classic_8th":
            try:
                r = mel_tools.arp_pattern({
                    "chord_pitches": chord_pitches, "length_beats": 4.0,
                    "rate": "eighth", "direction": "up", "octaves": 1,
                    "start_beats": bar_start, "velocity_base": 75,
                })
                notes.extend(r.get("notes", []))
            except Exception:
                pass

        elif profile == "broken_16th":
            # 16th rhythm mask: 1.011.011.011.0.1 (X = hit, . = rest)
            mask = [1, 0, 1, 1, 0, 1, 1, 0, 1, 1, 0, 1, 0, 1, 0, 1]
            cp = chord_pitches
            for i, hit in enumerate(mask):
                if not hit:
                    continue
                pitch = cp[i % len(cp)]
                notes.append({
                    "pitch": int(pitch),
                    "start_beats": bar_start + i * 0.25,
                    "length_beats": 0.22,
                    "velocity": 75 if i % 4 == 0 else 65,
                })

        elif profile == "off_beat":
            # Only the "&" of each beat — sparse, syncopated
            cp = chord_pitches
            for i in range(4):
                pitch = cp[i % len(cp)]
                notes.append({
                    "pitch": int(pitch),
                    "start_beats": bar_start + i + 0.5,
                    "length_beats": 0.45,
                    "velocity": 70,
                })

        elif profile == "two_note_pulse":
            # Ping-pong between root and fifth, 8th notes, octave varies
            root = chord["bass_root"] + 12
            fifth = chord["bass_root"] + 19
            pattern = [root, fifth, root + 12, fifth, root, fifth + 12, root, fifth]
            for i, p in enumerate(pattern):
                notes.append({
                    "pitch": int(p),
                    "start_beats": bar_start + i * 0.5,
                    "length_beats": 0.45,
                    "velocity": 70 if i % 2 == 0 else 60,
                })

        elif profile == "triplet_swirl":
            # 8th-note triplets — 12 hits per bar, up_down shape
            cp = chord_pitches + chord_pitches[::-1][1:-1]
            for i in range(12):
                pitch = cp[i % len(cp)]
                notes.append({
                    "pitch": int(pitch),
                    "start_beats": bar_start + i * (4.0 / 12.0),
                    "length_beats": 4.0 / 12.0 - 0.02,
                    "velocity": 70,
                })

        elif profile == "chord_tone_sparse":
            # 4 hits per bar at irregular positions: 1, 1.75, 2.5, 3.25
            offsets = [0.0, 1.75, 2.5, 3.25]
            cp = chord_pitches
            for i, off in enumerate(offsets):
                pitch = cp[i % len(cp)]
                notes.append({
                    "pitch": int(pitch),
                    "start_beats": bar_start + off,
                    "length_beats": 0.5,
                    "velocity": 75,
                })
        else:
            # Fallback: classic 8th
            try:
                r = mel_tools.arp_pattern({
                    "chord_pitches": chord_pitches, "length_beats": 4.0,
                    "rate": "eighth", "direction": "up", "octaves": 1,
                    "start_beats": bar_start, "velocity_base": 75,
                })
                notes.extend(r.get("notes", []))
            except Exception:
                pass
    return notes
