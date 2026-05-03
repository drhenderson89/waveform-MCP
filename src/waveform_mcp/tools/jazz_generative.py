"""Generative jazz/jaunty composer.

Built fresh from research + theory_genre('jazz_standard') and
theory_chord_progression. Drops every synthwave-specific element that
was destroying the mix (sub-bass, synth pad, sub-bass-doubling pad B,
arpeggios). Replaces them with the actual jazz palette:

  * PIANO LEAD — acoustic piano carries the melody. Treated like a
    vocal: rests/breaths between phrases, syncopation, register
    MIDI 65-86 (vocal range).
  * STRIDE BASS — left-hand "Oom Pah" pattern: low note on 1&3,
    chord stab on 2&4. Signature ragtime/jaunty feel.
  * WALKING BASS — quarter-note 1-3-5-7 walks for sections that swing
    rather than stride.
  * RHODES COMPING — Charleston-rhythm chord stabs (1, 2.5, 4) at
    quiet level, sits under the lead.
  * BRUSHED DRUMS — kick on 1&3, snare/rim on 2&4, ride cymbal "ding-
    da-da-ding-da" 8th-note pattern. Light and behind the music.
  * SAXOPHONE COUNTER — sparse, only fills the rests in the piano
    lead. Off in verses, sparse in chorus.

Progressions:
  * ii-V-I (the jazz cadence)
  * Rhythm Changes A (I-vi-ii-V — Gershwin "I Got Rhythm")
  * 12-bar blues with dominant 7s
  * Add secondary dominants (V/V, V/ii) for jaunty motion

Form:
  32-bar AABA (collapsed to A-A-B-A: 8+8+8+8)

Key takeaways from research:
  * Diatonic with occasional secondary dominants — NOT borrowed/modal
  * 7th + 9th chord extensions are standard
  * "Jaunty" leans ragtime: straight 8ths, NOT swing 8ths
  * Piano comping uses Charleston rhythm (offbeat stabs)
  * Walking bass connects chord roots via stepwise quarter-notes
"""
from __future__ import annotations

import random
from pathlib import Path
from typing import Any

from . import composition as comp_tools
from . import edit as edit_tools
from . import instruments as instr_tools


def _equip_specific(track_id: str, name_substr: str, fallback_category: str,
                    fallback_name: str = None,
                    rng: random.Random = None):
    """Pick a preset whose name contains `name_substr` (case-insensitive)
    from any category. Falls back to picking from `fallback_category`
    if no name match is found. This sidesteps the rng-rolls-the-wrong-
    instrument problem for tracks where we have a specific sound in mind.
    """
    target = name_substr.lower()
    matches = []
    seen = set()
    for category, presets in instr_tools._REGISTRY.items():
        for p in presets:
            if id(p) in seen:
                continue
            seen.add(id(p))
            if target in p.name.lower():
                matches.append(p)
    if matches:
        # Inject the picked preset back into the registry temporarily?
        # Simpler — use the equip path via a one-off category list.
        instr_tools._REGISTRY.setdefault("__pinned__", []).clear()
        instr_tools._REGISTRY["__pinned__"].append(matches[0])
        return instr_tools.equip(track_id, "__pinned__", rng,
                                  fallback=fallback_category)
    return instr_tools.equip(track_id, fallback_category, rng)
from . import melody as mel_tools
from . import midi as midi_tools
from . import plugins as plugin_tools
from . import tracks as track_tools
from ..model import STORE
from .lofi_generative import _root_to_pc


# Jazz-friendly major keys (sax/piano-friendly fingerings)
_JAZZ_ROOTS = ["C", "F", "Bb", "Eb", "G", "D", "A"]
# Modal palette — major 80%, minor 20% (jazz standards lean major)
_JAZZ_MODES = ["major", "minor"]
_JAZZ_PROGRESSIONS = [
    # name, roman numerals (relative), bars per chord
    ("rhythm_changes", ["I", "vi", "ii", "V"], 1),       # 1 bar each
    ("ii_V_I_vamp",    ["ii", "V", "I", "I"], 1),
    ("blues_basic",    ["I", "IV", "I", "V7"], 2),       # 2 bars each
    ("autumn_leaves",  ["ii", "V", "I", "IV", "vii", "iii", "vi", "vi"], 1),
]


def compose_jazz_generative(args: dict[str, Any]) -> dict[str, Any]:
    out_path = args.get("out_path")
    if not out_path:
        return {"ok": False, "error": "missing out_path"}
    seed = int(args.get("seed", 0))
    rng = random.Random(seed)

    # --- 1. Random choices, all seed-driven ---
    tempo = float(args.get("tempo") or rng.uniform(110.0, 140.0))
    target_seconds = float(args.get("target_seconds") or rng.uniform(150.0, 200.0))
    key_root = args.get("key_root") or rng.choice(_JAZZ_ROOTS)
    key_mode = args.get("key_mode") or rng.choices(
        _JAZZ_MODES, weights=[0.8, 0.2])[0]
    prog_name, prog_romans, bars_per_chord = rng.choice(_JAZZ_PROGRESSIONS)
    bass_style = rng.choice(["stride", "walking", "stride", "walking"])  # 50/50

    # --- 2. Chord progression with 7th extensions ---
    prog = comp_tools.progression_generate({
        "root": key_root, "mode": key_mode,
        "length_chords": len(prog_romans),
        "style": "jazz",
        "allow_borrowed": False,
        "seed": seed * 19 + 7,
        "octave": 3,
    })
    progression_chords = []
    for chord in prog["chords"]:
        pitches = chord["chord_pitches"]
        root = int(chord.get("root_midi", pitches[0]))
        degree = int(chord.get("degree", 1))
        quality = chord.get("quality", "maj")
        # 7th selection rules:
        #   * V chord (degree 5) is ALWAYS dominant 7 (b7) in jazz —
        #     never maj7. Even if quality reads "maj", treat as dominant.
        #   * Otherwise: maj/aug → maj7 (+11); else → b7 (+10).
        # Without this, V resolves with a #11/maj7 that clashes
        # against the I chord and reads as "out of tune."
        if len(pitches) >= 4:
            third, fifth, seventh = pitches[1], pitches[2], pitches[3]
        else:
            third, fifth = pitches[1], pitches[2]
            if degree == 5:
                seventh = root + 10              # dominant 7
            elif quality in ("maj", "aug"):
                seventh = root + 11              # major 7
            elif quality == "dim":
                seventh = root + 9               # diminished 7
            else:
                seventh = root + 10              # minor 7 / half-dim
        # Drop the 3rd or 5th down an octave for a "rootless" jazz voicing
        voicing = sorted([third, fifth, seventh, root + 12])
        progression_chords.append({
            "bass_root": root,
            "voicing": voicing,
            "third": third,
            "fifth": fifth,
            "seventh": seventh,
        })

    # --- 3. Form: 32-bar AABA (8+8+8+8) ---
    bars_a = 8
    bars_b = 8
    sections_plan = [
        {"role": "intro",  "bars": 4},
        {"role": "A1",     "bars": bars_a},
        {"role": "A2",     "bars": bars_a},
        {"role": "B",      "bars": bars_b},   # bridge — modulation territory
        {"role": "A3",     "bars": bars_a},
        {"role": "outro",  "bars": 4},
    ]
    total_bars = sum(s["bars"] for s in sections_plan)

    # If target_seconds wants more, repeat A1-A2-B-A3 once
    sec_per_bar = 60.0 * 4 / tempo
    if total_bars * sec_per_bar < target_seconds * 0.7:
        sections_plan = [
            {"role": "intro",  "bars": 4},
            {"role": "A1",     "bars": bars_a},
            {"role": "A2",     "bars": bars_a},
            {"role": "B",      "bars": bars_b},
            {"role": "A3",     "bars": bars_a},
            {"role": "A4",     "bars": bars_a},
            {"role": "B2",     "bars": bars_b},
            {"role": "A5",     "bars": bars_a},
            {"role": "outro",  "bars": 4},
        ]
        total_bars = sum(s["bars"] for s in sections_plan)

    def chord_at(bar: int) -> dict:
        # Each chord in progression_chords lasts `bars_per_chord`. The
        # bridge (B) transposes up a 4th for harmonic motion.
        sec = _section_at_bar(sections_plan, bar)
        local_bar = bar - _section_start_bar(sections_plan, sec["role"]) if sec else bar
        idx = (local_bar // bars_per_chord) % len(progression_chords)
        chord = dict(progression_chords[idx])
        if sec and sec["role"] in ("B", "B2"):
            # Bridge: transpose chord up a perfect 4th (5 semitones)
            chord = {
                "bass_root": chord["bass_root"] + 5,
                "voicing": [p + 5 for p in chord["voicing"]],
                "third": chord["third"] + 5,
                "fifth": chord["fifth"] + 5,
                "seventh": chord["seventh"] + 5,
            }
        return chord

    # --- 4. Build the edit ---
    key_root_pc = _root_to_pc(key_root)
    edit_tools.edit_create({
        "name": f"Jazz-{seed}",
        "tempo": tempo,
        "time_sig_num": 4, "time_sig_denom": 4,
        "key_root": key_root_pc, "key_mode": key_mode,
        "length_beats": total_bars * 4 + 4,
    })

    # --- 4b. Pre-compute piano lead so the drum generator can comp for it.
    # The drummer's job in jazz is to support the soloist — accent piano
    # syncopations on solo phrases, leave space, drop kick on beat 3.
    # This requires knowing piano onsets BEFORE building drums.
    piano_rng = random.Random(seed * 13 + 1)
    pre_piano_notes = _build_piano_lead_vocal(
        progression_chords, sections_plan, total_bars, chord_at, piano_rng,
    )
    # Index by bar -> list of (offset_in_bar, velocity)
    piano_onsets_by_bar: dict[int, list[tuple[float, int]]] = {}
    for n in pre_piano_notes:
        bar_i = int(n["start_beats"] // 4)
        off = float(n["start_beats"]) - bar_i * 4
        piano_onsets_by_bar.setdefault(bar_i, []).append((off, int(n["velocity"])))
    # ── Section dynamics curve — every section gets a velocity scale
    # so the whole ensemble breathes as a unit. Quiet intro/outro,
    # building As, climactic bridge.
    SECTION_DYNAMIC_SCALE = {
        "intro": 0.55, "A1": 0.85, "A2": 0.92, "B": 1.05,
        "A3": 0.95, "A4": 0.98, "B2": 1.05, "A5": 0.88, "outro": 0.55,
    }
    def _dyn(bar: int) -> float:
        sec = _section_at_bar(sections_plan, bar)
        return SECTION_DYNAMIC_SCALE.get(sec["role"], 0.9) if sec else 0.9
    def _scale_vel(vel: int, bar: int) -> int:
        return max(20, min(127, int(round(vel * _dyn(bar)))))

    # ── Phrase position within each non-silent 4-bar block. Returns
    # 0, 1, 2, or 3. Bar 3 is the "fill bar" — drums fill, bass walks
    # chromatic, rhodes lays out.
    def _bar_in_phrase(bar: int) -> int:
        sec = _section_at_bar(sections_plan, bar)
        if not sec or sec["role"] in ("intro", "outro"):
            return 0
        # Walk forward from section start counting non-silent bars
        sec_start = _section_start_bar(sections_plan, sec["role"])
        return (bar - sec_start) % 4

    # Identify "solo" phrases: every 4th phrase is octave-lifted (the
    # peak/climax phrase) and every 3rd is contour-inverted with ornaments.
    # Both deserve special drum treatment. Phrase = 4 bars.
    def _is_solo_bar(bar: int) -> bool:
        sec = _section_at_bar(sections_plan, bar)
        if not sec or sec["role"] in ("intro", "outro"):
            return False
        # Walk 4-bar phrases the same way _build_piano_lead_vocal did:
        # skip intro bars, then each non-silent 4-bar block is one phrase.
        phrase_idx = 0
        cursor = 0
        while cursor < bar:
            sec_at = _section_at_bar(sections_plan, cursor)
            if sec_at and sec_at["role"] not in ("intro", "outro"):
                if cursor + 4 <= bar + 1:
                    phrase_idx += 1
                cursor += 4
            else:
                cursor += 1
        # phrase_idx % 4 in (2, 3) are the inverted/octave-lifted variants
        return phrase_idx % 4 in (2, 3)

    # --- 5. Drums — light brushed jazz kit, comping for the piano
    # Kick on 1&3, snare/rim on 2&4, ride cymbal 8th-note skip pattern
    # ("ding-da-da-ding-da" = 1, 2.33, 2.66, 3, 4.33). All velocities
    # low so drums sit BEHIND the piano.
    # Track named "Drums" + Sampler "808 Kit" → factory drum samples
    # auto-load (same trick lofi_generative + synthwave_generative use).
    drums_id = track_tools.track_add({"name": "Drums", "kind": "midi"})["track_id"]
    plugin_tools.plugin_add({"track_id": drums_id, "type": "Sampler",
                              "name": "808 Kit"})
    drum_clip = midi_tools.midi_clip_add({
        "track_id": drums_id, "name": "Drums",
        "start_beats": 0.0, "length_beats": total_bars * 4,
    })["clip_id"]
    drum_notes = []
    # 808 Kit notes available: 36, 37, 38, 39, 42, 45, 46, 48, 49, 50.
    KICK, SNARE, RIM, HAT, OPEN_HAT, CRASH = 36, 38, 37, 42, 46, 49
    RIDE = HAT     # no ride cymbal in 808 kit; closed hat doubles
    for bar in range(total_bars):
        sec = _section_at_bar(sections_plan, bar)
        if not sec or sec["role"] == "intro":
            for beat in range(4):
                drum_notes.append({"pitch": RIDE, "start_beats": float(bar*4+beat),
                                    "length_beats": 0.4, "velocity": 55})
            continue
        outro = sec["role"] == "outro"
        is_solo = _is_solo_bar(bar) and not outro
        bp = _bar_in_phrase(bar)
        is_fill_bar = (bp == 3) and not outro      # last bar of every phrase
        piano_hits = piano_onsets_by_bar.get(bar, [])
        # Find piano onsets that fall on syncopated positions (NOT on
        # the strong beats 0, 1, 2, 3). Drums will accent these.
        piano_off_beats = [(off, vel) for off, vel in piano_hits
                           if abs(off - round(off)) > 0.1]

        # ── KICK ──
        # Solo phrases: drop the beat-3 kick — give the piano space.
        # Standard: kick on 1 & 3 driving the swing.
        kick_beats = (0,) if is_solo else (0, 2)
        for beat in kick_beats:
            drum_notes.append({"pitch": KICK,
                                "start_beats": float(bar*4+beat),
                                "length_beats": 0.4,
                                "velocity": 100 if not outro else 75})
        # Ghost kick on "&" of 4 every other bar in non-solo (drive)
        if not outro and not is_solo and bar % 2 == 1:
            drum_notes.append({"pitch": KICK,
                                "start_beats": float(bar*4 + 3.5),
                                "length_beats": 0.3,
                                "velocity": 65})

        # ── SNARE ──
        # Standard 2 & 4 always; solo phrases ALSO accent piano
        # off-beats with rim-shot ghosts.
        for beat in (1, 3):
            drum_notes.append({"pitch": SNARE,
                                "start_beats": float(bar*4+beat),
                                "length_beats": 0.4,
                                "velocity": (80 if is_solo else 95) if not outro else 70})
        # SOLO COMP: rim-shot ghost on each piano off-beat onset
        if is_solo:
            for off, p_vel in piano_off_beats:
                # Don't double up if already a 2/4 hit nearby
                if any(abs(off - b) < 0.15 for b in (1, 3)):
                    continue
                drum_notes.append({"pitch": RIM,
                                    "start_beats": float(bar*4 + off),
                                    "length_beats": 0.18,
                                    "velocity": min(60, max(35, p_vel - 50))})
        elif not outro and bar % 4 == 3:
            # Standard: occasional rim ghost on "&" of 3
            drum_notes.append({"pitch": RIM,
                                "start_beats": float(bar*4 + 2.5),
                                "length_beats": 0.2,
                                "velocity": 55})

        # ── RIDE / HI-HAT ──
        # Solo phrases: thin out the ride to leave room for piano.
        # Standard: full "ding-DA-da" pattern.
        if is_solo:
            # Just beats 1 & 3 + one skip — sparse ride
            for beat in (0, 2):
                drum_notes.append({"pitch": RIDE,
                                    "start_beats": float(bar*4+beat),
                                    "length_beats": 0.45,
                                    "velocity": 60})
        else:
            for beat in range(4):
                drum_notes.append({"pitch": RIDE,
                                    "start_beats": float(bar*4+beat),
                                    "length_beats": 0.45,
                                    "velocity": 78 if beat in (0, 2) else 70})
                if beat in (1, 3):
                    drum_notes.append({"pitch": RIDE,
                                        "start_beats": float(bar*4+beat+0.66),
                                        "length_beats": 0.3,
                                        "velocity": 62})

        # ── CRASH ── on first bar of every solo phrase (punctuates entry)
        if is_solo:
            if bar == 0 or not _is_solo_bar(bar - 1):
                drum_notes.append({"pitch": CRASH,
                                    "start_beats": float(bar*4),
                                    "length_beats": 1.0,
                                    "velocity": 85})

        # ── PHRASE-END FILL ── bar 3 of every 4-bar phrase
        # Adds tom roll on beats 3-4 — punctuates the phrase boundary
        # so the listener feels each 4-bar group close. Without this,
        # the song "drones forward" with no shape.
        if is_fill_bar:
            tom_seq = [(50, 0.0), (48, 0.25), (45, 0.5)]    # hi-mid-low tom
            for pitch, off in tom_seq:
                drum_notes.append({"pitch": pitch,
                                    "start_beats": float(bar*4 + 3 + off),
                                    "length_beats": 0.22,
                                    "velocity": 75})

    # Apply section dynamics — scale every drum hit by its bar's
    # velocity multiplier. Ensemble breathes as one.
    for n in drum_notes:
        n["velocity"] = _scale_vel(n["velocity"], int(n["start_beats"] // 4))
    midi_tools.midi_notes_add({"clip_id": drum_clip, "notes": drum_notes})

    # --- 6. Bass — STRIDE or WALKING (seed choice) ---
    bass_id = track_tools.track_add({"name": "Upright Bass", "kind": "midi"})["track_id"]
    # Pin VSCO Contrabass PIZZICATO — plucked attack with short decay.
    # The "Bowed" sustained variant droned like a synth pad. The pizz
    # version is the actual jazz upright bass voice (Ron Carter feel).
    _equip_specific(bass_id, "contrabass pizz", "bass", rng=rng)
    bass_clip = midi_tools.midi_clip_add({
        "track_id": bass_id, "name": "Bass",
        "start_beats": 0.0, "length_beats": total_bars * 4,
    })["clip_id"]
    bass_notes = []
    for bar in range(total_bars):
        sec = _section_at_bar(sections_plan, bar)
        if not sec or sec["role"] == "intro":
            # Intro: half-note bass roots
            chord = chord_at(bar)
            bass_notes.append({"pitch": int(chord["bass_root"]) - 12,
                                "start_beats": float(bar*4),
                                "length_beats": 3.5,
                                "velocity": 75})
            continue
        chord = chord_at(bar)
        next_chord = chord_at(bar + 1) if bar + 1 < total_bars else chord
        # Was `root - 12` → MIDI ~24-36 (sub-bass territory). Real
        # upright bass is E1 (28) - G3 (55). Use chord_root directly
        # which gives ~36-48 range (proper upright).
        root = int(chord["bass_root"])

        if bass_style == "stride":
            # Beats 1 & 3: root.   Beats 2 & 4: fifth up an octave
            for beat in (0, 2):
                bass_notes.append({"pitch": root,
                                    "start_beats": float(bar*4+beat),
                                    "length_beats": 0.95,
                                    "velocity": 90})
            fifth_up = int(chord["fifth"])
            for beat in (1, 3):
                bass_notes.append({"pitch": fifth_up,
                                    "start_beats": float(bar*4+beat),
                                    "length_beats": 0.5,
                                    "velocity": 70})
        else:  # walking — but per-section feel varies
            third = int(chord["third"])
            fifth = int(chord["fifth"])
            next_root = int(next_chord["bass_root"])
            bp = _bar_in_phrase(bar)
            is_solo = _is_solo_bar(bar)

            # Per-section bass FEEL — real bassists change their
            # rhythmic approach throughout a tune:
            #   "two_feel" : half notes on 1 & 3 only — heads/intros
            #   "walk"     : quarter-note walk — bread-and-butter
            #   "walk_skip": walk with rest on beat 3 every other bar
            #   "anticipate": walk + 8th-note anticipation on "& of 4"
            #   "chromatic": chromatic step approach (used for solo/fill)
            section_role = sec["role"]
            if section_role in ("intro", "outro"):
                feel = "two_feel"
            elif section_role in ("A1", "A5"):
                feel = "two_feel"          # head statements
            elif section_role in ("A2",):
                feel = "walk"              # opens up
            elif section_role in ("A3",):
                feel = "walk_skip"         # syncopated holes
            elif section_role in ("A4",):
                feel = "anticipate"        # pushing the time
            elif section_role in ("B", "B2"):
                feel = "walk"              # bridge climbs
            else:
                feel = "walk"

            # ONLY override to chromatic on the very last bar before
            # a section CHANGE (one bar per section, not one per
            # phrase). Previously bp==3 hit every 4 bars + every solo
            # bar, which clobbered the section feels with constant
            # quarter walks. Now each section's character holds.
            next_sec = _section_at_bar(sections_plan, bar + 1)
            section_changing = (next_sec is not None
                                and next_sec["role"] != section_role)
            if section_changing:
                feel = "chromatic"

            if feel == "two_feel":
                # Half notes on 1 & 3: ROOT then FIFTH
                bass_notes.append({"pitch": root,
                                    "start_beats": float(bar*4),
                                    "length_beats": 1.85,
                                    "velocity": 92})
                bass_notes.append({"pitch": int(chord["fifth"]) - 12 if chord["fifth"] >= root + 12 else int(chord["fifth"]),
                                    "start_beats": float(bar*4 + 2),
                                    "length_beats": 1.85,
                                    "velocity": 80})
                # Optional pickup eighth into next bar — every 2nd bar
                if bar % 2 == 1 and bar + 1 < total_bars:
                    bass_notes.append({"pitch": next_root,
                                        "start_beats": float(bar*4 + 3.5),
                                        "length_beats": 0.4,
                                        "velocity": 70})
            elif feel == "walk_skip":
                # Walk but skip beat 3 on even bars — leaves a rhythmic hole
                walk = [root, third, fifth, third]
                positions = (0, 1, 3) if bar % 2 == 0 else (0, 1, 2, 3)
                pitches = [walk[i] for i in positions]
                for i, off in enumerate(positions):
                    base_vel = 90 if off == 0 else (76 if off % 2 == 0 else 72)
                    bass_notes.append({"pitch": int(pitches[i]),
                                        "start_beats": float(bar*4 + off),
                                        "length_beats": 0.85,
                                        "velocity": base_vel})
            elif feel == "anticipate":
                # Walk 1-2-3, then anticipate next root on "& of 4"
                walk = [root, third, fifth]
                for i, p in enumerate(walk):
                    base_vel = 90 if i == 0 else (76 if i % 2 == 0 else 72)
                    bass_notes.append({"pitch": int(p),
                                        "start_beats": float(bar*4 + i),
                                        "length_beats": 0.85,
                                        "velocity": base_vel})
                # The anticipation: next chord's root, 8th early
                if bar + 1 < total_bars:
                    bass_notes.append({"pitch": next_root,
                                        "start_beats": float(bar*4 + 3.5),
                                        "length_beats": 0.6,
                                        "velocity": 84})
            elif feel == "chromatic":
                # 4-step chromatic walk toward next_root
                steps = []
                cur = root
                for i in range(4):
                    steps.append(cur)
                    diff = next_root - cur
                    if diff > 0: cur += min(2, diff)
                    elif diff < 0: cur -= min(2, -diff)
                for i, p in enumerate(steps):
                    base_vel = 90 if i == 0 else (78 if i % 2 == 0 else 72)
                    if is_solo: base_vel += 4
                    bass_notes.append({"pitch": int(p),
                                        "start_beats": float(bar*4 + i),
                                        "length_beats": 0.85,
                                        "velocity": base_vel})
            else:  # plain "walk" — but pick a real jazz pattern per bar
                bar_notes = _jazz_walk_bar(
                    root=root, third=third, fifth=fifth, next_root=next_root,
                    chord=chord, bar=bar, seed=seed,
                )
                for off, pitch, vel, length in bar_notes:
                    bass_notes.append({"pitch": int(pitch),
                                        "start_beats": float(bar*4 + off),
                                        "length_beats": float(length),
                                        "velocity": int(vel)})
    # Apply section dynamics so bass swells/recedes with the ensemble.
    # Boost velocities +20 (VSCO pizzicato samples recorded quietly).
    # ALSO normalize every pitch into the upright-bass sample range
    # (MIDI 36-55) — the progression generator returns chord pitches
    # in piano-comping register, so bass would otherwise play above
    # its sample range and resample out of tune.
    for n in bass_notes:
        scaled = _scale_vel(n["velocity"], int(n["start_beats"] // 4))
        n["velocity"] = max(40, min(127, scaled + 20))
        n["pitch"] = _bass_register(int(n["pitch"]))
    midi_tools.midi_notes_add({"clip_id": bass_clip, "notes": bass_notes})

    # --- 7. Rhodes / Wurli COMPING — Charleston rhythm
    # Hits on beat 1, "&" of 2 (=2.5), and beat 4 — the ragtime/jazz
    # "Charleston" rhythm. Sits behind the piano lead.
    rhodes_id = track_tools.track_add({"name": "Rhodes Comp", "kind": "midi"})["track_id"]
    # Pin E-Piano R — the actual electric piano 4OSC preset. Avoids the
    # rng routing this to House Organ (too dark) or Strings WMF (a
    # sustained-string wall that dominated previous mixes).
    _equip_specific(rhodes_id, "e-piano", "vibes", rng=rng)
    rhodes_clip = midi_tools.midi_clip_add({
        "track_id": rhodes_id, "name": "Rhodes",
        "start_beats": 0.0, "length_beats": total_bars * 4,
    })["clip_id"]
    rhodes_notes = []
    for bar in range(total_bars):
        sec = _section_at_bar(sections_plan, bar)
        if not sec or sec["role"] == "intro":
            continue
        # PHRASE-END LAYOUT: rhodes goes silent on the fill bar so the
        # drums + bass + piano have the moment to themselves. Real jazz
        # comping leaves space — the chord is implied by what just played.
        if _bar_in_phrase(bar) == 3:
            continue
        # On SOLO bars (piano peak), comp on beat 1 only (sparser) so
        # the piano has more room.
        if _is_solo_bar(bar):
            chord = chord_at(bar)
            top_voicing = chord["voicing"][-2:]
            for p in top_voicing:
                rhodes_notes.append({
                    "pitch": int(p),
                    "start_beats": float(bar*4),
                    "length_beats": 0.5,
                    "velocity": 50,
                })
            continue
        chord = chord_at(bar)
        top_voicing = chord["voicing"][-2:]    # top two of [3rd, 5th, 7th, oct]
        for off, vel in ((0.0, 55), (2.5, 42)):
            for p in top_voicing:
                rhodes_notes.append({
                    "pitch": int(p),
                    "start_beats": float(bar*4 + off),
                    "length_beats": 0.6 if off == 0.0 else 1.0,
                    "velocity": vel,
                })
    for n in rhodes_notes:
        n["velocity"] = _scale_vel(n["velocity"], int(n["start_beats"] // 4))
    midi_tools.midi_notes_add({"clip_id": rhodes_clip, "notes": rhodes_notes})

    # --- 8. PIANO LEAD — vocal-treated melody
    # Built from a chord-aware melody generator with REST patterns
    # mimicking a vocal phrase ("breath" every 4 beats). Register
    # MIDI 65-86 (mezzo-soprano vocal range).
    piano_id = track_tools.track_add({"name": "Piano Lead", "kind": "midi"})["track_id"]
    # Pin Salamander Grand — the proper acoustic piano. RNG could pick
    # E-Piano R (which we want for the comp) or VSCO Upright (less rich)
    # so we fix Salamander as the lead.
    _equip_specific(piano_id, "salamander grand piano v3", "piano", rng=rng)
    piano_clip = midi_tools.midi_clip_add({
        "track_id": piano_id, "name": "Piano",
        "start_beats": 0.0, "length_beats": total_bars * 4,
    })["clip_id"]
    # Reuse the piano lead pre-computed in step 4b (drum generator
    # needs piano onsets to comp for solo phrases). Don't regenerate
    # or rng state diverges and the drums won't match the piano.
    piano_notes = pre_piano_notes
    if piano_notes:
        # Piano breathes with the section dynamics too — but less than
        # the rhythm section (it's the lead, can't get too quiet).
        # Compress the dynamic range: scale = 0.7 + 0.3 * dyn_factor.
        for n in piano_notes:
            bar = int(n["start_beats"] // 4)
            scale = 0.7 + 0.3 * _dyn(bar)
            n["velocity"] = max(40, min(127, int(round(n["velocity"] * scale))))
        midi_tools.midi_notes_add({"clip_id": piano_clip, "notes": piano_notes})

    # --- 9. Saxophone counter — VERY sparse, fills the gaps in piano
    # Active in section A2 and A3 only, plays one held note per bar
    # at the chord 7th (smoky color).
    sax_id = track_tools.track_add({"name": "Saxophone", "kind": "midi"})["track_id"]
    instr_tools.equip(sax_id, "brass", rng, fallback="strings")
    sax_clip = midi_tools.midi_clip_add({
        "track_id": sax_id, "name": "Sax",
        "start_beats": 0.0, "length_beats": total_bars * 4,
    })["clip_id"]
    sax_notes = []
    for bar in range(total_bars):
        sec = _section_at_bar(sections_plan, bar)
        if not sec or sec["role"] not in ("A2", "A3", "A4", "A5"):
            continue
        # VERY sparse — one note every 4 bars, not every 2.
        # Bassoon SFZ samples are openEnded=1 so they ring out for
        # several seconds regardless of MIDI note length. Spacing them
        # 4 bars apart prevents tail overlap clashes.
        if bar % 4 != 0:
            continue
        chord = chord_at(bar)
        # Play the chord 3rd (warmer than 7th, never clashes) in vocal
        # tenor-sax range MIDI 62-72.
        third = int(chord["third"])
        while third < 62: third += 12
        while third > 72: third -= 12
        sax_notes.append({
            "pitch": int(third),
            "start_beats": float(bar*4 + 2.5),
            "length_beats": 0.6,    # short — sample tail does the rest
            "velocity": 50,
        })
    if sax_notes:
        midi_tools.midi_notes_add({"clip_id": sax_clip, "notes": sax_notes})

    # --- 10. Mix balance + per-track processing ─ professional jazz
    # principles applied:
    #   * Frequency separation via complementary EQ (piano cuts mud,
    #     Rhodes cuts brightness — they don't fight in the same band)
    #   * Stereo "doughnut": piano + bass + kick center; Rhodes panned
    #     left, sax panned right (opposing supporting elements)
    #   * Reverb tiering: lead = small/dry, comp = larger/wetter
    #   * Compression: light on lead (preserve dynamics — jazz!),
    #     heavier on comp (glued in the background)
    # Sources: mixonline.com piano mixing, recordmixandmaster.com
    # Rhodes mixing, sageaudio.com piano mixing, musicguymixing.com
    # panning guide.

    # ── PIANO LEAD ── center, bright, dry, dynamic ──
    plugin_tools.eq_high_pass({"track_id": piano_id, "freq": 200.0})
    plugin_tools.eq_tilt({"track_id": piano_id, "brightness": 0.3})
    plugin_tools.compress_glue({"track_id": piano_id, "amount": 0.25})
    plugin_tools.plugin_add_reverb({
        "track_id": piano_id, "type": "plate",
        "size": 0.35, "decay": 0.4, "mix": 0.12,
    })
    track_tools.mix_apply_reference({"track_id": piano_id,
                                      "genre": "lofi", "role": "lead",
                                      "offset_db": +20})
    # Explicitly set fader to max (+8 dB) AFTER mix_apply_reference so
    # any internal clamp doesn't pull us back. mix_set takes precedence.
    track_tools.mix_set({"track_id": piano_id, "volume_db": 8.0, "pan": 0.0})

    # ── RHODES COMP ── panned LEFT, darker, more reverb, more compression ──
    plugin_tools.eq_high_pass({"track_id": rhodes_id, "freq": 150.0})
    plugin_tools.eq_tilt({"track_id": rhodes_id, "brightness": -0.2})
    plugin_tools.compress_glue({"track_id": rhodes_id, "amount": 0.55})
    plugin_tools.plugin_add_reverb({
        "track_id": rhodes_id, "type": "plate",
        "size": 0.55, "decay": 0.6, "mix": 0.22,
    })
    track_tools.mix_apply_reference({"track_id": rhodes_id,
                                      "genre": "lofi", "role": "keys",
                                      "offset_db": -10})
    # Hard cap rhodes at -10 dB regardless of calibrator output. Comp
    # absolutely should not compete with the piano lead.
    track_tools.mix_set({"track_id": rhodes_id, "volume_db": -10.0, "pan": -0.35})

    # ── BASS ── full upright-bass treatment per jazz mixing research:
    # HPF at 50 Hz (cleans rumble), boost 80-150 Hz (body), gentle LP
    # at 7 kHz (lets pluck attack through), compression with +8 dB
    # makeup gain (adds REAL post-fader gain — the +8 fader cap alone
    # wasn't enough since Pizz samples are recorded ~6 dB quieter than
    # Salamander), tilt to favor low-mids over highs.
    plugin_tools.eq_high_pass({"track_id": bass_id, "freq": 50.0})
    plugin_tools.eq_low_pass({"track_id": bass_id, "freq": 7000.0})
    plugin_tools.eq_tilt({"track_id": bass_id, "brightness": -0.4})
    bass_comp = plugin_tools.compress_glue({"track_id": bass_id, "amount": 0.7})
    # Override the compressor's makeup gain to +8 dB. Default is amount*1.5
    # (= 1.05 dB at 0.7) — way too low to lift Pizz to lead level.
    plugin_tools.plugin_set_param({
        "track_id": bass_id,
        "plugin_id": bass_comp["plugin_id"],
        "param": "outputDb", "value": 8.0,
    })
    track_tools.mix_apply_reference({"track_id": bass_id,
                                      "genre": "lofi", "role": "bass",
                                      "offset_db": +20})
    track_tools.mix_set({"track_id": bass_id, "volume_db": 8.0, "pan": 0.0})

    # ── DRUMS ── center, light glue ──
    plugin_tools.compress_glue({"track_id": drums_id, "amount": 0.3})
    track_tools.mix_apply_reference({"track_id": drums_id,
                                      "genre": "lofi", "role": "drums",
                                      "offset_db": -1})
    track_tools.mix_set({"track_id": drums_id, "pan": 0.0})

    # ── SAX ── panned RIGHT (opposite Rhodes), wet plate ──
    plugin_tools.plugin_add_reverb({
        "track_id": sax_id, "type": "plate",
        "size": 0.5, "decay": 0.7, "mix": 0.25,
    })
    track_tools.mix_apply_reference({"track_id": sax_id,
                                      "genre": "lofi", "role": "counter",
                                      "offset_db": -8})
    track_tools.mix_set({"track_id": sax_id, "pan": 0.30})

    STORE.edit.master_volume_db = -3.0

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
        "progression": prog_name,
        "bass_style": bass_style,
        "sections": [s["role"] for s in sections_plan],
        "track_count": 5,
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


def _section_start_bar(sections_plan: list[dict], role: str) -> int:
    cursor = 0
    for sec in sections_plan:
        if sec["role"] == role:
            return cursor
        cursor += sec["bars"]
    return 0


def _build_piano_lead_vocal(progression_chords: list[dict],
                             sections_plan: list[dict],
                             total_bars: int,
                             chord_at,
                             rng: random.Random) -> list[dict]:
    """Phrase-based piano lead.

    Generates a 2-bar MOTIF (fixed rhythm) and applies it across every
    4-bar phrase using a CALL-AND-RESPONSE structure:

      Bars 0-1: CALL  — full motif (5-6 notes), arch contour, beat 1 anchored
      Bars 2-3: RESPONSE — sparse answer (2-3 notes), descending, ends low

    The motif rhythm STAYS THE SAME across the song so the listener
    can lock on. Pitches change with each chord change. Beat 1 of every
    bar with a chord change always has an anchor note (chord root or 3rd).

    This replaces the previous bar-by-bar random-position generator
    that had no rhythmic continuity.
    """
    LO, HI = 67, 84      # G4 to C6 — vocal range
    SILENT = {"intro", "outro"}

    # ── Motif library — multiple rhythmic figures rotated through the
    # song so it doesn't get stale. A real jazz pianist plays AABA where
    # each A is a slight variation, and the B has a contrasting figure.
    call_motifs_a = [
        # AABA "A" call motifs — ragtime-leaning with anchored downbeats
        [(0.0,"anchor"), (1.5,"step"), (2.5,"step"),
         (4.0,"anchor"), (4.75,"step"), (6.5,"step")],
        [(0.0,"anchor"), (0.75,"step"), (2.0,"step"), (3.0,"step"),
         (4.0,"anchor"), (5.5,"step"), (7.0,"step")],
        [(0.0,"anchor"), (1.0,"step"), (2.5,"step"),
         (4.0,"anchor"), (5.0,"step"), (6.0,"step"), (7.5,"step")],
    ]
    call_motifs_b = [
        # AABA "B" (bridge) — busier, more ornamented
        [(0.0,"anchor"), (1.0,"skip"), (1.5,"step"), (2.5,"step"), (3.5,"skip"),
         (4.0,"anchor"), (5.0,"skip"), (5.5,"step"), (6.5,"step"), (7.5,"step")],
        [(0.5,"step"), (1.0,"skip"), (1.5,"step"), (2.5,"step"),
         (4.5,"step"), (5.0,"skip"), (5.5,"step"), (7.0,"step")],
    ]
    response_motifs_a = [
        # "A" responses — sparse, descending, resolves
        [(0.0,"anchor"), (2.0,"step"), (5.0,"step")],
        [(0.0,"anchor"), (3.5,"step"), (4.5,"step")],
        [(0.0,"anchor"), (1.5,"step"), (4.0,"anchor"), (6.0,"step")],
    ]
    response_motifs_b = [
        # "B" responses — held tones, more space
        [(0.0,"anchor"), (4.0,"anchor")],
        [(0.0,"anchor"), (2.5,"step"), (4.0,"anchor"), (6.5,"step")],
    ]

    # Pre-pick a fixed motif index for "A" sections (consistent identity)
    # and a fixed motif for the bridge. Variations come from PHRASE-LEVEL
    # transformations applied below (octave shift, ornament, contour
    # invert) — NOT from changing the rhythm wholesale.
    a_call = rng.choice(call_motifs_a)
    a_resp = rng.choice(response_motifs_a)
    b_call = rng.choice(call_motifs_b)
    b_resp = rng.choice(response_motifs_b)

    def chord_tones_in_range(chord) -> list[int]:
        """Return chord tones (root, 3rd, 5th, 7th) in vocal range, sorted."""
        out = []
        for pc in [chord["bass_root"], chord["third"],
                    chord["fifth"], chord["seventh"]]:
            for cand in range(LO, HI + 1):
                if cand % 12 == pc % 12:
                    out.append(cand)
        return sorted(set(out)) or [72]

    def note_for_role(chord_tones: list[int], role: str, idx: int) -> tuple[int, int]:
        """Pick a pitch + velocity given the role and a stepping index."""
        if role == "anchor":
            # Anchor on root (lowest chord tone in range) or 3rd — strong
            target = chord_tones[0]
            return target, 110
        if role == "step":
            # Walk up/down chord tones based on idx
            return chord_tones[idx % len(chord_tones)], 92
        # skip
        return chord_tones[(idx * 2) % len(chord_tones)], 88

    notes: list[dict] = []
    # Walk the song in 4-bar phrases. Track the phrase index so we can
    # apply variations: every other phrase gets contour-inverted or
    # octave-shifted; bridge sections get the B motif library; ornaments
    # appear on phrase indices that are multiples of 3.
    bar = 0
    phrase_idx = 0
    while bar < total_bars:
        sec = _section_at_bar(sections_plan, bar)
        if not sec or sec["role"] in SILENT:
            bar += 1
            continue
        is_bridge = sec["role"] in ("B", "B2")
        call = b_call if is_bridge else a_call
        response = b_resp if is_bridge else a_resp

        # Phrase variations — pick deterministically from phrase_idx so
        # the same seed yields the same song:
        invert_contour = (phrase_idx % 4 == 2)        # every 4th phrase inverts
        octave_lift    = (phrase_idx % 4 == 3)        # every 4th phrase up an oct
        add_ornament   = (phrase_idx % 3 == 1)        # every 3rd phrase ornaments

        # ── 2-bar CALL across bars [bar, bar+1] ─────────────────────────
        for i, (off, role) in enumerate(call):
            sub_bar = bar + (1 if off >= 4.0 else 0)
            if sub_bar >= total_bars:
                break
            chord = chord_at(sub_bar)
            tones = chord_tones_in_range(chord)
            local_off = off - (4.0 if off >= 4.0 else 0.0)
            # Index walks chord tones — invert contour reverses the walk
            walk_idx = (len(call) - 1 - i) if invert_contour else i
            pitch, vel = note_for_role(tones, role, walk_idx)
            if octave_lift and pitch + 12 <= HI:
                pitch += 12
                vel = max(60, vel - 8)     # quieter when up an octave
            notes.append({
                "pitch": int(pitch),
                "start_beats": float(sub_bar * 4 + local_off),
                "length_beats": _length_until_next(call, off, max_bar_end=8.0),
                "velocity": vel,
            })
            # Ornament: drop a quick passing tone a 16th after this note
            # (only on "step" notes — not anchors) on ornamented phrases
            if add_ornament and role == "step" and i + 1 < len(call):
                next_off = call[i + 1][0]
                gap = next_off - off
                if gap >= 0.5:
                    orn_off = off + 0.25
                    sub_bar2 = bar + (1 if orn_off >= 4.0 else 0)
                    local_orn = orn_off - (4.0 if orn_off >= 4.0 else 0.0)
                    chord2 = chord_at(sub_bar2)
                    orn_tones = chord_tones_in_range(chord2)
                    orn_pitch = orn_tones[(walk_idx + 1) % len(orn_tones)]
                    if octave_lift and orn_pitch + 12 <= HI:
                        orn_pitch += 12
                    notes.append({
                        "pitch": int(orn_pitch),
                        "start_beats": float(sub_bar2 * 4 + local_orn),
                        "length_beats": 0.2,
                        "velocity": 75,
                    })

        # ── 2-bar RESPONSE across bars [bar+2, bar+3] ──────────────────
        for i, (off, role) in enumerate(response):
            sub_bar = bar + 2 + (1 if off >= 4.0 else 0)
            if sub_bar >= total_bars:
                break
            chord = chord_at(sub_bar)
            tones = chord_tones_in_range(chord)
            local_off = off - (4.0 if off >= 4.0 else 0.0)
            walk_idx = (len(response) - 1 - i) if invert_contour else i
            pitch, vel = note_for_role(tones, role, walk_idx)
            vel = max(60, vel - 10)         # response always softer
            # Response defaults to LOWER octave (answer feel) UNLESS
            # this phrase is the octave-lifted variant.
            if octave_lift and pitch + 12 <= HI:
                pitch += 12
            elif pitch > 76:
                pitch -= 12
            notes.append({
                "pitch": int(pitch),
                "start_beats": float(sub_bar * 4 + local_off),
                "length_beats": _length_until_next(response, off, max_bar_end=8.0),
                "velocity": vel,
            })
        bar += 4
        phrase_idx += 1
    return notes


def _bass_register(pitch: int) -> int:
    """Pull `pitch` into the upright-bass register (MIDI 36-55, E2-G3).
    The progression generator returns chord pitches centered around
    MIDI 48-65 (piano comping register). VSCO Contrabass Pizz samples
    only cover MIDI 24-60 — anything above 55 sounds resampled-thin
    and out of tune. This drops pitches an octave when too high,
    raises them when too low.
    """
    while pitch > 55:
        pitch -= 12
    while pitch < 36:
        pitch += 12
    return pitch


def _jazz_walk_bar(*, root: int, third: int, fifth: int, next_root: int,
                    chord: dict, bar: int, seed: int) -> list[tuple[float, int, int, float]]:
    """Build ONE bar of authentic walking jazz bass.

    Draws from a 7-pattern library based on real bass-line research
    (Paul Chambers, Ron Carter, "101 Walking Jazz Bass Lines"):

      pattern 0: 1-3-5-approach (textbook) — anchor pattern
      pattern 1: 1-3-6-5         (Chambers move — adds the 6th color)
      pattern 2: 1-3-6-b6        (chromatic descent on beat 4)
      pattern 3: 1-3-5-triplet   (root-3rd-5th + chromatic triplet → next)
      pattern 4: 1-3-5-octave_drop (octave displacement on beat 4)
      pattern 5: 1-3-5-+8th_pickup (3 quarters + 8th-note ghost pickup)
      pattern 6: 1-5-3-approach  (Ron Carter inversion)

    Pattern is picked per-bar from a deterministic hash so the same
    seed yields the same line, but different bars roll different
    patterns. Velocities follow standard jazz voicing: beat 1 strong,
    beat 3 medium-strong, beats 2 & 4 medium.
    """
    # Per-bar pattern hash — deterministic but varied. Use bar*13 (not
    # *7) so bar actually contributes; *7 % 7 collapses to zero.
    h = (seed * 31 + bar * 13 + 5) % 7
    seventh = int(chord.get("seventh", root + 10))

    # Sixth = scale 6th = 9 semitones above root for major, 8 for minor.
    # Approximate via 9 unless quality is min.
    sixth = root + (8 if (chord.get("seventh", root + 10) - root) == 10
                    and (third - root) == 3 else 9)
    flat_sixth = root + 8

    # Approach tone — half-step toward next_root
    if next_root > root:
        approach = next_root - 1
    else:
        approach = next_root + 1

    notes: list[tuple[float, int, int, float]] = []   # (off, pitch, vel, len)

    if h == 0:
        # 1-3-5-approach (textbook)
        for i, p in enumerate([root, third, fifth, approach]):
            v = 92 if i == 0 else (76 if i % 2 == 0 else 72)
            notes.append((float(i), p, v, 0.85))
    elif h == 1:
        # 1-3-6-5 (Paul Chambers)
        for i, p in enumerate([root, third, sixth, fifth]):
            v = 92 if i == 0 else (76 if i % 2 == 0 else 72)
            notes.append((float(i), p, v, 0.85))
    elif h == 2:
        # 1-3-6-b6 (chromatic descent — landing point feels resolved)
        for i, p in enumerate([root, third, sixth, flat_sixth]):
            v = 92 if i == 0 else (76 if i % 2 == 0 else 72)
            notes.append((float(i), p, v, 0.85))
    elif h == 3:
        # 1-3-5 + chromatic triplet on beat 4 (the "So What" lick).
        # Beat 4 splits into 3 eighth-note-triplets at offsets
        # 3.0, 3.333, 3.667 — chromatic rush into next_root.
        for i, p in enumerate([root, third, fifth]):
            v = 92 if i == 0 else (76 if i % 2 == 0 else 72)
            notes.append((float(i), p, v, 0.85))
        # Triplet: approach-2, approach-1, next_root (pulls into beat 1)
        a = approach - (1 if next_root > root else -1)
        triplet = [a, approach, next_root]
        for i, p in enumerate(triplet):
            notes.append((3.0 + i * (1.0/3.0), p, 78, 0.30))
    elif h == 4:
        # 1-3-5 + octave drop on beat 4 (Ron-Carter-ish surprise)
        for i, p in enumerate([root, third, fifth]):
            v = 92 if i == 0 else (76 if i % 2 == 0 else 72)
            notes.append((float(i), p, v, 0.85))
        notes.append((3.0, root - 12, 80, 0.85))
    elif h == 5:
        # 1-3-5 + 8th-note GHOST pickup on "& of 4"
        # The pickup is quiet (ghost) and a chromatic step from approach
        for i, p in enumerate([root, third, fifth, approach]):
            v = 92 if i == 0 else (76 if i % 2 == 0 else 72)
            notes.append((float(i), p, v, 0.85))
        # Ghost 8th between beat 4 and beat 1 of next bar
        ghost = approach - (1 if next_root > root else -1)
        notes.append((3.5, ghost, 50, 0.25))    # vel 50 = ghost
    else:    # h == 6
        # 1-5-3-approach (inversion — different shape)
        for i, p in enumerate([root, fifth, third, approach]):
            v = 92 if i == 0 else (76 if i % 2 == 0 else 72)
            notes.append((float(i), p, v, 0.85))
    # Normalize every pitch into the upright-bass register so we don't
    # play above the sample range and trigger resampled out-of-tune
    # artifacts. The pitch CLASS is preserved; only octave changes.
    return [(off, _bass_register(p), v, ln) for off, p, v, ln in notes]


def _length_until_next(motif: list[tuple[float, str]], current_off: float,
                       max_bar_end: float) -> float:
    """Note length = time until next motif hit (or end of phrase)."""
    later = [o for o, _ in motif if o > current_off]
    nxt = min(later) if later else max_bar_end
    return max(0.2, nxt - current_off - 0.05)


# --------------------------------------------------------------------------- #
# MCP tool registration                                                       #
# --------------------------------------------------------------------------- #


HANDLERS = {"compose_jazz_generative": compose_jazz_generative}

TOOLS = [{
    "name": "compose_jazz_generative",
    "description": (
        "Generate a jazz/jaunty track with authentic walking bass "
        "(7-pattern Paul Chambers vocabulary), Charleston-rhythm "
        "Rhodes comping, and Salamander Grand Piano lead treated like "
        "a vocal (call/response motifs, vocal range, dynamic phrasing). "
        "Drums comp for the soloist (ghost rim shots on piano off-beats, "
        "phrase-end fills, sparse ride on solo phrases). Section-wide "
        "ensemble dynamics and per-section bass feels (two-feel for "
        "heads, walking for blowing, anticipations into the bridge)."
    ),
    "input_schema": {
        "type": "object",
        "required": ["out_path"],
        "properties": {
            "out_path": {"type": "string"},
            "seed":     {"type": "integer", "default": 0},
            "tempo":    {"type": "number"},
            "key_root": {"type": "string"},
            "key_mode": {"type": "string"},
            "target_seconds": {"type": "number"},
        },
    },
}]
