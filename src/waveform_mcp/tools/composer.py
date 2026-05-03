"""High-level composition workflows.

These tools chain the lower-level edit/tracks/midi/plugins primitives into
named genre/song templates. They use the captured schemas (track, MIDI clip,
note, plugin) to produce complete `.tracktionedit` files that should open
in Waveform without further user intervention.

`compose_lofi_track` is the first end-to-end test of the full pipeline:
generates a 4-track lofi song (drums, bass, keys, pad) with real notes and
4OSC instrument plugins, saves to disk, returns the path.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import random

from ..model import STORE, Plugin, _id
from pathlib import Path

from . import audio, edit as edit_tools, midi, plugins, tracks
from .preset_library import _factory_presets_dir


def _load_preset(category: str, preset_filename: str) -> tuple[dict, list[dict]]:
    """Read a .trkpreset XML and return (params_dict, modmatrix_items).

    `params_dict` excludes id (we allocate our own) and `windowLocked` /
    `windowX` / `windowY` (UI state we don't want to copy). Everything else
    is the plugin's parameter state at the time the preset was saved.
    """
    base = _factory_presets_dir()
    if base is None:
        raise RuntimeError("Factory Presets dir not found")
    path = base / category / preset_filename
    if not path.exists():
        raise FileNotFoundError(f"preset not found: {path}")
    root = ET.parse(path).getroot()
    plug = root.find("PLUGIN")
    if plug is None:
        raise ValueError(f"no PLUGIN in {path}")
    SKIP = {"id", "type", "enabled", "windowLocked", "windowX", "windowY"}
    params = {k: _coerce(v) for k, v in plug.attrib.items() if k not in SKIP}

    modmatrix: list[dict] = []
    mm = plug.find("MODMATRIX")
    if mm is not None:
        for item in mm.findall("MODMATRIXITEM"):
            modmatrix.append({k: _coerce(v) for k, v in item.attrib.items()})
    return params, modmatrix


def _coerce(s: str):
    """Best-effort numeric coercion so attributes round-trip cleanly."""
    try:
        if "." in s or "e" in s.lower():
            return float(s)
        return int(s)
    except ValueError:
        return s


def _apply_preset_to_track(
    track_id: str,
    category: str,
    preset_filename: str,
    overrides: dict | None = None,
) -> None:
    """Load preset state and apply to the track's most-recent user plugin.

    `overrides` lets caller force specific params on top of the preset — used
    for things like forcing ampSustain=0 on a bass preset to cure the
    "synthy sustain" problem (every factory bass has tune1=-12 and most have
    high ampSustain, neither of which is right for plucked-bass realism).
    """
    params, modmatrix = _load_preset(category, preset_filename)
    if overrides:
        params.update(overrides)
    track = next(t for t in STORE.edit.tracks if t.id == track_id)
    if not track.plugins:
        return
    plugin = track.plugins[-1]
    plugin.params.update(params)
    plugin.modmatrix = modmatrix


# Smoother keys override — applied on top of the keys preset to tame the
# E-Piano R's brittle attack and bright top. The factory preset has a
# 0.001ms attack and filterFreq=80 which produces a clicky, abrasive
# Rhodes voice. These overrides give it a warmer, slower, jazzier tone:
SMOOTHER_KEYS_OVERRIDE = {
    "ampAttack": 0.045,        # 45ms — soft fingerpad attack instead of clicky pluck
    "ampDecay": 12.0,          # long natural decay
    "ampRelease": 0.55,        # longer release for warmth
    "ampVelocity": 28.0,       # less velocity sensitivity = more even tone
    "filterFreq": 50.0,        # MUCH darker — kills brittle top harmonics
    "filterAmount": 0.12,      # less filter envelope movement = smoother
    "filterResonance": 0.0,    # no peaking
    "chorusMix": 0.85,         # more chorus = wider and warmer
    "masterLevel": -15.5,      # slightly softer overall
}


# Plucked-bass envelope (kept for reference / alternative): notes decay
# toward 0 even while held — gives a "real bass guitar" pluck character.
PLUCKED_BASS_ENVELOPE = {
    "ampAttack": 0.005,
    "ampDecay": 0.45,
    "ampSustain": 0.0,
    "ampRelease": 0.25,
    "filterAttack": 0.0,
    "filterDecay": 0.30,
    "filterSustain": 15.0,
    "filterRelease": 0.20,
    "filterFreq": 60.0,
    "filterAmount": 0.5,
    "filterResonance": 20.0,
}


# Light + sustained bass envelope. "Weighted blanket but light" — sustained
# legato presence but in a higher register and with a brighter filter so
# it feels cozy without being heavy. Kills the preset's tune1=-12 so the
# bass plays in low-piano range instead of sub-bass, and softens the
# sustain so it's "ever present" not "constantly looming."
WEIGHTED_BASS_ENVELOPE = {
    "ampAttack": 0.080,      # 80ms — soft fingerpad onset
    "ampDecay": 0.40,
    "ampSustain": 60.0,      # 60% — sustains but not at full volume (lighter)
    "ampRelease": 1.10,      # 1.1s tail — bleeds into next note (legato)
    "filterAttack": 0.0,
    "filterDecay": 0.50,
    "filterSustain": 40.0,
    "filterRelease": 0.60,
    "filterFreq": 95.0,      # WAY brighter than before (was 55) — lighter feel
    "filterAmount": 0.10,
    "filterResonance": 0.0,  # no peaking
    # CRITICAL: kill the preset's tune1=-12. The Smack Bass preset detunes
    # an octave down, putting our bass in sub register. tune1=0 keeps it
    # in low-piano range — lighter, more "weighted-blanket-but-not-heavy."
    "tune1": 0.0,
}


def _add_track_fx_bass(track_id: str) -> None:
    """Per-track FX for the bass — sub cleanup ONLY. No makeup gain. The
    real-bass-guitar sample is already hot at source; we don't want to
    compress AND boost — both make it louder.
    """
    track = next(t for t in STORE.edit.tracks if t.id == track_id)

    # 3-band EQ: aggressive high-pass at 90Hz to kill sub-mud (was 60Hz —
    # too lenient given the bass guitar sample's natural body).
    eq = Plugin(
        id=_id("plg"),
        type="3 Band EQ",
        name="Bass Cleanup",
        params={
            "loFreq": 90.0,
            "loGain": -12.0,         # aggressive sub kill
            "midFreq": 200.0,
            "midGain": 0.0,          # neutral mid
            "midQ": 0.8,
            "hiFreq": 5000.0,
            "hiGain": 0.0,
        },
    )
    track.plugins.append(eq)

    # Compressor with NO makeup gain — controls peaks without boosting level
    comp = Plugin(
        id=_id("plg"),
        type="Compressor",
        name="Bass Comp",
        params={
            "threshold": -10.0,
            "ratio": 4.0,            # higher ratio for tighter peaks
            "attack": 15.0,
            "release": 200.0,
            "knee": 4.0,
            "outputDb": 0.0,         # NO makeup gain (was +3dB — compounding loudness)
        },
    )
    track.plugins.append(comp)


def _add_master_fx_lofi() -> None:
    """Lofi master "character chain" — recipe from production research:

      1. Subtle EQ tilt: lift lows, scoop low-mids, roll off highs
      2. Master glue compression (lofi is over-compressed by design)
      3. Compresaturator (tape-style saturation, even harmonics)
      4. Lowpass filter at 8kHz — "the under-a-pillow feel"
      5. Limiter to catch peaks, leave headroom (target ~-14 LUFS)
    """
    edit = STORE.edit

    # Sunny beach EQ: gentle low warmth, neutral mid, sparkling top.
    # Cut some low-mid mud (around 200-300Hz where bass and pad collide).
    eq = Plugin(
        id=_id("plg"),
        type="3 Band EQ",
        name="Master EQ",
        params={
            "loFreq": 90.0,
            "loGain": 0.5,         # very gentle low (was 2.0 — was getting muddy)
            "midFreq": 300.0,
            "midGain": -1.5,       # cut more mid mud
            "midQ": 0.7,
            "hiFreq": 9000.0,
            "hiGain": 1.5,         # MORE sparkle on top
        },
    )
    edit.master_chain.append(eq)

    # Barely-there compression — let dynamics flow
    comp = Plugin(
        id=_id("plg"),
        type="Compressor",
        name="Master Glue",
        params={
            "threshold": -8.0,
            "ratio": 1.6,           # was 2.0 — even gentler
            "attack": 40.0,
            "release": 250.0,
            "knee": 10.0,
            "outputDb": 0.5,
        },
    )
    edit.master_chain.append(comp)

    # Compresaturator — tape-style 2nd harmonic warmth
    sat = Plugin(
        id=_id("plg"),
        type="Compresaturator",
        name="Tape Saturator",
        params={},
    )
    edit.master_chain.append(sat)

    # Gentler lowpass — 11kHz preserves more brightness for the beach feel.
    # Pure lofi would be 8kHz but you want sun, not gloom.
    lpf = Plugin(
        id=_id("plg"),
        type="Lowpass Filter",
        name="Lofi Lowpass",
        params={
            "frequency": 11000.0,
            "q": 0.6,
        },
    )
    edit.master_chain.append(lpf)

    # Limiter
    lim = Plugin(
        id=_id("plg"),
        type="Limiter",
        name="Master Limiter",
        params={
            "threshold": -1.5,
            "release": 50.0,
        },
    )
    edit.master_chain.append(lim)


TOOLS: list[dict[str, Any]] = [
    {
        "name": "compose_lofi_track",
        "description": (
            "Generate a complete lofi track .tracktionedit file: 4 tracks (drums, bass, "
            "keys, pad), Fmaj7-Am7-Dm7-Cmaj7 progression, real 808 drum samples, master "
            "FX bus. Bass / keys / pad presets are configurable so you can A/B sounds "
            "without editing the composer. Saves to the given path."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["out_path"],
            "properties": {
                "out_path": {"type": "string", "description": "Where to write the .tracktionedit"},
                "tempo": {"type": "number", "default": 76.0, "description": "BPM (76 = beach-lazy lofi)"},
                "bars": {"type": "integer", "default": 16, "description": "Total bars (4 phrases × 4 bars)"},
                "bass_preset": {
                    "type": "string",
                    "default": "4OSC Smack Bass WMF.trkpreset",
                    "description": "Filename inside Factory Presets/4OSC/",
                },
                "keys_preset": {
                    "type": "string",
                    "default": "4OSC E-Piano R WMF.trkpreset",
                },
                "pad_preset": {
                    "type": "string",
                    "default": "4OSC Vox Pad WMF.trkpreset",
                },
                "bass_octave_offset": {
                    "type": "integer",
                    "default": 1,
                    "description": (
                        "Semitone offset for bass roots, in octaves. Most factory bass "
                        "presets have tune1=-12 (octave-down detune), so playing at +1 "
                        "octave (= MIDI roots in F3/A3/D3/C3) lands the actual sounding "
                        "pitch in the proper bass register (F2-A2). Default 1 fixes the "
                        "common sub-bass-mud issue. Use 0 for a deeper sub-bass."
                    ),
                },
                "seed": {"type": "integer", "default": 0,
                         "description": "Reproducible randomization seed for variation."},
            },
        },
    },
]


# ---------- helpers (musical primitives) ----------


# ============================================================================
# Musicianship: each part is programmed the way a professional player would
# actually play it, not as a quantized robot.
# ============================================================================


def _humanize(beats: float, vel: int, *, time_jitter: float = 0.012, vel_jitter: int = 6) -> tuple[float, int]:
    """Subtle timing + velocity randomness. Different roles want different
    amounts — drummers are tighter than pad players. Tweaked per-call."""
    return (
        max(0.0, beats + random.uniform(-time_jitter, time_jitter)),
        max(1, min(127, vel + random.randint(-vel_jitter, vel_jitter))),
    )


def _drum_pattern(start_bar: int, bars: int) -> list[dict]:
    """Drummer-grade lofi pattern.

    A real lofi drummer (think J Dilla, Knxwledge, Idealism):
      - Swung 16ths: lays back on the "a" of each beat
      - Kick on 1 and the "&" of 2 (boom-bap), occasionally a 3-kick variation
      - Snare on 2 and 4, slightly LATE (lay-back groove)
      - Hi-hats on 8ths but with skips — drops the closed hat at random spots
        for breathing space, replaces with open hat at end of phrases
      - Ghost snares on the "e" or "a" between hits (vel 25-40)
      - Drops the snare entirely on the last bar of every 4-bar phrase to set
        up the turnaround
      - Subtle velocity crescendo into bar starts (anticipation)

    All pitches map to GM drum kit indices loaded in our 808 Sampler:
      36=Kick  37=Rim  38=Snare  39=Clap  42=ClosedHat  46=OpenHat  49=Crash
    """
    rng = random.Random(42)
    notes = []

    # Lay-back: snare and kick land slightly late. Hi-hat off-beats also push
    # into the next 16th to create swing.
    LAY_BACK = 0.025      # ~25ms at 75 BPM
    SWING_PUSH = 0.06     # 16th-note swing on off-beats

    for bar in range(bars):
        b = start_bar * 4 + bar * 4
        is_last_of_phrase = (bar % 4 == 3)
        is_first_of_phrase = (bar % 4 == 0)

        # ---- Kick — softer for beach vibe. 95 max instead of 112. ----
        t, v = _humanize(b + 0.0, 95 if is_first_of_phrase else 88)
        notes.append({"pitch": 36, "start_beats": t, "length_beats": 0.3, "velocity": v})
        # "&" of 2 — boom-bap
        t, v = _humanize(b + 2.5 + SWING_PUSH * 0.5, 78)
        notes.append({"pitch": 36, "start_beats": t, "length_beats": 0.3, "velocity": v})
        # Bar 3 of phrase: extra kick on "a" of 4
        if bar % 4 == 2:
            t, v = _humanize(b + 3.75, 65)
            notes.append({"pitch": 36, "start_beats": t, "length_beats": 0.2, "velocity": v})

        # ---- Snare — softer too, lets the keys come forward ----
        if not is_last_of_phrase:
            t, v = _humanize(b + 1.0 + LAY_BACK, 85)
            notes.append({"pitch": 38, "start_beats": t, "length_beats": 0.25, "velocity": v})
            t, v = _humanize(b + 3.0 + LAY_BACK, 85)
            notes.append({"pitch": 38, "start_beats": t, "length_beats": 0.25, "velocity": v})
        else:
            t, v = _humanize(b + 1.0 + LAY_BACK, 80)
            notes.append({"pitch": 38, "start_beats": t, "length_beats": 0.25, "velocity": v})
            t, v = _humanize(b + 3.0 + LAY_BACK, 75)
            notes.append({"pitch": 39, "start_beats": t, "length_beats": 0.25, "velocity": v})

        # ---- Ghost snares ----
        # Sprinkle one or two per bar at low velocity on syncopated 16ths
        ghost_positions = [0.75, 2.25, 3.75]
        for gp in ghost_positions:
            if rng.random() < 0.35:
                t, v = _humanize(b + gp, rng.randint(28, 42))
                notes.append({"pitch": 38, "start_beats": t, "length_beats": 0.1, "velocity": v})

        # ---- Hi-hats ----
        # 8th-note pulse with swung off-beats + occasional skips.
        # Skip i=7 (the "&" of 4) on phrase-end bars — the open hat below
        # takes that slot. Two hats within ~50ms reads as a smeared flam.
        for i in range(8):
            if i == 7 and is_last_of_phrase:
                continue  # open hat owns this slot
            on_beat = (i % 2 == 0)
            offset = 0.0 if on_beat else SWING_PUSH
            beat_pos = b + i * 0.5 + offset

            # Skip ~15% of off-beats to create breathing room
            if not on_beat and rng.random() < 0.15:
                continue

            base_vel = 78 if on_beat else 55
            # Occasional accent on the "&" of 4 leading to next bar
            if i == 7 and bar < bars - 1:
                base_vel = 75
            t, v = _humanize(beat_pos, base_vel, time_jitter=0.02, vel_jitter=10)
            notes.append({"pitch": 42, "start_beats": t, "length_beats": 0.16, "velocity": v})

        # ---- Open hat ----
        # End of every 4-bar phrase, the open hat sets up the turnaround
        if is_last_of_phrase:
            t, v = _humanize(b + 3.5, 72)
            notes.append({"pitch": 46, "start_beats": t, "length_beats": 0.5, "velocity": v})

        # ---- Crash ----
        # Only on bar 1 of the whole song (not every phrase) — too much otherwise
        if bar == 0:
            notes.append({"pitch": 49, "start_beats": b + 0.0, "length_beats": 1.5, "velocity": 90})

    return notes


def _bass_progression(
    start_bar: int,
    bars: int,
    bass_pitches: list[int],
    chord_keys: list[int],
    octave_offset: int,
) -> list[dict]:
    """Bassist-grade walking line.

    A pro bassist on a lofi track:
      - Root on beat 1, sustained
      - 5th on beat 3 to lift the bar's middle
      - Chromatic approach tones into next chord on bar-end of phrases
      - Occasional ghost root on "&" of 2 for groove
      - Stays in proper bass register (after preset's tune1=-12 detune the
        sounding pitches land at MIDI ~36-48 — F2-C3, the heart of bass)

    `bass_pitches` are the actual MIDI roots played; `chord_keys` are the
    canonical chord-root keys; `octave_offset` lets approach tones use the
    same transposition.
    """
    rng = random.Random(43)
    notes = []
    LAY_BACK = 0.018

    for bar in range(bars):
        b = start_bar * 4 + bar * 4
        root = bass_pitches[bar % len(bass_pitches)]
        fifth = root + 7
        next_root = bass_pitches[(bar + 1) % len(bass_pitches)] if bar < bars - 1 else None
        is_last_of_phrase = (bar % 4 == 3)

        # Root on beat 1 — full-bar sustain + overlap into next bar (legato).
        # Velocities softer for lighter, cozier feel.
        vel_root = 65 if (bar % 4 == 0) else 58
        t, v = _humanize(b + 0.0 + LAY_BACK, vel_root, time_jitter=0.015)
        notes.append({"pitch": root, "start_beats": t, "length_beats": 4.2, "velocity": v})

        # 5th on beat 3 — sustained, overlapping
        t, v = _humanize(b + 2.0 + LAY_BACK, 50)
        notes.append({"pitch": fifth, "start_beats": t, "length_beats": 2.3, "velocity": v})

        # Ghost on & of 2 removed — was punchy, doesn't fit weighted-blanket feel

        # Chromatic walk removed — produced punchy short notes that
        # broke the "weighted blanket / ever present" feel. The weighted
        # envelope + sustained roots/fifths handle the transitions through
        # legato overlap instead.

    return notes


# Rootless jazz voicings, optimized for lofi piano stabs.
# Each chord lists pitches in ascending order (3-7-9 or 7-3-5 spread).
# Built around middle C (60 = C4):
#   Fmaj7  → A4(69) E5(76) G5(79)   [3rd  7th  9th]
#   Am7    → C5(72) G5(79) B5(83)   [3rd  7th  9th(maj9)]
#   Dm7    → F4(65) C5(72) E5(76)   [3rd  7th  9th]
#   Cmaj7  → E4(64) B4(71) D5(74)   [3rd  7th  9th]
JAZZ_VOICINGS = {
    41: [69, 76, 79],   # Fmaj7
    45: [72, 79, 83],   # Am7
    38: [65, 72, 76],   # Dm7
    36: [64, 71, 74],   # Cmaj7
}
# Snapshot of the original lookup so compose_lofi_track can restore it
# after a per-call override (otherwise custom chord_progression entries
# leak into subsequent calls).
_JAZZ_VOICINGS_DEFAULT = dict(JAZZ_VOICINGS)

# Populated per-call by compose_lofi_track when args["chord_progression"]
# overrides the default Fmaj7-Am7-Dm7-Cmaj7 set. Maps bass_root → 7th
# interval (10 for min7, 11 for maj7). Empty dict means no override.
_SEVENTH_INTERVAL_OVERRIDE: dict[int, int] = {}


def _chord_progression(start_bar: int, bars: int, chords: list[list[int]], chord_keys: list[int]) -> list[dict]:
    """Jazz piano comping.

    A pro keys player on lofi:
      - Plays ROOTLESS voicings (3-7-9) so they don't muddy with the bassist
      - Stabs on syncopated beats (the "&" of 2 and beat 4), NOT every bar's
        downbeat. Leaves space for the rhythm section to breathe.
      - Lets the chord ring just past the next downbeat for forward motion
      - Adds a "pickup" voicing on the "&" of 4 every 4 bars — leads into
        the next phrase
      - Slight delay on the lowest voice (root-of-voicing) — creates a
        natural "bloom" feel

    The voicings are looked up from JAZZ_VOICINGS keyed by bass root.
    """
    rng = random.Random(44)
    notes = []
    # Pianists commonly sit a bit behind the beat too — softer than the kick
    LAY_BACK = 0.015

    for bar in range(bars):
        b = start_bar * 4 + bar * 4
        key = chord_keys[bar % len(chord_keys)]
        voicing = JAZZ_VOICINGS.get(key, chords[bar % len(chords)])

        # First stab: slight pickup right on beat 1 with light velocity
        for i, p in enumerate(voicing):
            t, v = _humanize(b + 0.0 + LAY_BACK + i * 0.005, 70)
            notes.append({"pitch": p, "start_beats": t, "length_beats": 1.5, "velocity": v})

        # Second stab: "&" of 2 (the syncopated lift)
        for i, p in enumerate(voicing):
            t, v = _humanize(b + 1.5 + LAY_BACK + i * 0.004, 65)
            notes.append({"pitch": p, "start_beats": t, "length_beats": 0.6, "velocity": v})

        # Third stab: beat 4 (anticipating the bar end, leaves space on beat 3)
        for i, p in enumerate(voicing):
            t, v = _humanize(b + 3.0 + LAY_BACK + i * 0.005, 72)
            notes.append({"pitch": p, "start_beats": t, "length_beats": 0.9, "velocity": v})

        # Pickup into next phrase: "&" of 4 every 4 bars
        if bar < bars - 1 and (bar % 4 == 3):
            next_key = chord_keys[(bar + 1) % len(chord_keys)]
            next_voicing = JAZZ_VOICINGS.get(next_key, voicing)
            for i, p in enumerate(next_voicing):
                t, v = _humanize(b + 3.5 + i * 0.004, 60)
                notes.append({"pitch": p, "start_beats": t, "length_beats": 0.4, "velocity": v})

    return notes


def _melody_xylophone(bars: int) -> list[dict]:
    """Marimba melody with strict rhythmic discipline.

    Time signature is 4/4. Every measure is exactly 4 beats. Notes obey:
      - All start positions are on the 8th-note grid: 0.0, 0.5, 1.0, 1.5,
        2.0, 2.5, 3.0, 3.5
      - All durations are standard values: whole(4), half(2), dotted-half(3),
        quarter(1), dotted-quarter(1.5), 8th(0.5), 16th(0.25)
      - Notes do NOT cross bar lines (start + length ≤ 4 within each bar).
        If a note holds across measures it's deliberately tied (last note
        of phrase, resolves on next downbeat).
      - Phrases are 4 bars long. Each phrase resolves on its 4th bar's
        downbeat or beat 3 (the bass's strong beats).

    Chord progression: Fmaj7 - Am7 - Dm7 - Cmaj7 (one chord per bar).
    Bass plays root on beat 1 and fifth on beat 3 of every bar.
    Melody syncs with those when locking, decorates between when dancing.
    """
    notes = []

    # ===== CHORUS A (bars 8-15): two 4-bar phrases =====
    # Format: (beat_offset, midi_pitch, velocity, length).
    # Length 0.5=8th, 1=quarter, 1.5=dotted-qtr, 2=half, 3=dotted-half, 4=whole.
    # All start positions are on the 8th-note grid.
    chorus_a = [
        # ---- PHRASE 1 (bars 8-11): Statement ----
        # Bar 8 (Fmaj7): half + qtr + qtr — A held, then walking down
        [(0.0, 69, 70, 2.0),                     # A half note on bass-1
         (2.0, 67, 60, 1.0),                     # G quarter
         (3.0, 65, 65, 1.0)],                    # F quarter on bass-3
        # Bar 9 (Am7): half + rest + qtr — answer
        [(0.0, 76, 70, 2.0),                     # E half on bass-1
         (3.0, 72, 65, 1.0)],                    # C quarter on bass-3
        # Bar 10 (Dm7): qtr + qtr + rest + qtr — descending to D
        [(0.0, 77, 70, 1.0),                     # F quarter on bass-1
         (1.0, 74, 60, 1.0),                     # D quarter
         (3.0, 74, 65, 1.0)],                    # D quarter on bass-3
        # Bar 11 (Cmaj7): half + half — symmetric resolve
        [(0.0, 79, 70, 2.0),                     # G half on bass-1
         (2.0, 76, 65, 2.0)],                    # E half on beats 3-4

        # ---- PHRASE 2 (bars 12-15): Variation, more rest ----
        # Bar 12 (Fmaj7): rest + 8th 8th + half — pickup feel
        [(0.5, 65, 60, 0.5),                     # F 8th off-beat
         (1.0, 69, 65, 0.5),                     # A 8th
         (2.0, 72, 70, 2.0)],                    # C half on beats 3-4
        # Bar 13 (Am7): qtr + 8th 8th + qtr + qtr — dancing
        [(0.0, 76, 70, 1.0),                     # E quarter on bass-1
         (1.0, 79, 60, 0.5),                     # G 8th
         (1.5, 76, 60, 0.5),                     # E 8th
         (2.0, 72, 65, 1.0),                     # C quarter
         (3.0, 69, 65, 1.0)],                    # A quarter on bass-3
        # Bar 14 (Dm7): rest + 8th 8th 8th 8th + half — sparse buildup
        [(2.0, 74, 55, 0.5),                     # D 8th
         (2.5, 77, 60, 0.5),                     # F 8th
         (3.0, 81, 65, 1.0)],                    # A quarter on bass-3
        # Bar 15 (Cmaj7): qtr + qtr + half — resolve home
        [(0.0, 76, 70, 1.0),                     # E quarter on bass-1
         (1.0, 74, 60, 1.0),                     # D passing
         (2.0, 72, 75, 2.0)],                    # C half — resolves home on beats 3-4
    ]

    # ===== CHORUS B (bars 20-27): two 4-bar phrases, more 8th-note movement =====
    chorus_b = [
        # ---- PHRASE 3 (bars 20-23): higher register, more dancing ----
        # Bar 20 (Fmaj7): qtr + 8th 8th + half — climbs
        [(0.0, 72, 70, 1.0),                     # C quarter on bass-1
         (1.0, 76, 65, 0.5),                     # E 8th
         (1.5, 79, 65, 0.5),                     # G 8th
         (2.0, 81, 75, 2.0)],                    # A high half on beats 3-4
        # Bar 21 (Am7): half + qtr + qtr
        [(0.0, 79, 70, 2.0),                     # G half on bass-1
         (2.0, 81, 65, 1.0),                     # A quarter
         (3.0, 76, 65, 1.0)],                    # E quarter on bass-3
        # Bar 22 (Dm7): qtr + 4×8th — playful run
        [(0.0, 77, 70, 1.0),                     # F quarter on bass-1
         (1.0, 81, 65, 0.5),                     # A 8th
         (1.5, 79, 60, 0.5),                     # G 8th
         (2.0, 81, 65, 0.5),                     # A 8th
         (2.5, 77, 60, 0.5),                     # F 8th
         (3.0, 74, 70, 1.0)],                    # D quarter on bass-3
        # Bar 23 (Cmaj7): dotted-half + qtr — held, then resolve
        [(0.0, 79, 70, 3.0),                     # G dotted-half spans beats 1-3
         (3.0, 76, 65, 1.0)],                    # E quarter on bass-3

        # ---- PHRASE 4 (bars 24-27): culmination + final resolve ----
        # Bar 24 (Fmaj7): half + 8th 8th + qtr
        [(0.0, 69, 70, 2.0),                     # A half on bass-1
         (2.0, 72, 60, 0.5),                     # C 8th
         (2.5, 76, 65, 0.5),                     # E 8th
         (3.0, 77, 65, 1.0)],                    # F quarter on bass-3
        # Bar 25 (Am7): WHOLE NOTE (the "baaaaah")
        [(0.0, 76, 70, 4.0)],                    # E whole — fills bar exactly
        # Bar 26 (Dm7): qtr + qtr + 8th 8th + qtr
        [(0.0, 77, 65, 1.0),                     # F quarter on bass-1
         (1.0, 81, 65, 1.0),                     # A quarter
         (2.0, 79, 60, 0.5),                     # G 8th
         (2.5, 77, 60, 0.5),                     # F 8th
         (3.0, 74, 65, 1.0)],                    # D quarter on bass-3
        # Bar 27 (Cmaj7): half + half — final symmetric resolution
        [(0.0, 79, 70, 2.0),                     # G half on bass-1
         (2.0, 72, 80, 2.0)],                    # C HOME half on beats 3-4
    ]

    def emit(start_bar: int, phrase: list):
        for bar_offset, bar_notes in enumerate(phrase):
            b = (start_bar + bar_offset) * 4
            for offset, pitch, vel, length in bar_notes:
                # Confirm the note doesn't cross the bar line
                assert offset + length <= 4.0 + 1e-6, \
                    f"Note crosses bar line: bar {start_bar+bar_offset} offset={offset} length={length}"
                jitter = 0.010 if offset in (0.0, 3.0) else 0.018
                t, v = _humanize(b + offset, vel, time_jitter=jitter, vel_jitter=7)
                notes.append({"pitch": pitch, "start_beats": t,
                              "length_beats": length, "velocity": v})

    emit(8, chorus_a)
    emit(20, chorus_b)
    return notes


def _counter_melody(bars: int, chord_keys: list[int]) -> list[dict]:
    """Sparse counter-line for the second chorus — gives the return more
    depth than the first chorus had.

    Approach: an arpeggio of each chord's 5th and 9th, played in the upper
    octave on the off-beats so it weaves through the keys' on-beat stabs.
    Only generates notes; gating to the chorus_b section is done by the
    arrangement filter.
    """
    rng = random.Random(45)
    # 5th and 9th above each chord (jazzy, lifting)
    UPPER_TONES = {
        41: [76, 79],   # Fmaj9: A5, C6 (5th, 7th)
        45: [76, 79],   # Am9: E5, G5
        38: [72, 76],   # Dm9: A4, E5 (5th, 9th)
        36: [79, 83],   # Cmaj9: G5, B5 (5th, 7th)
    }
    notes = []
    for bar in range(bars):
        b = bar * 4
        key = chord_keys[bar % len(chord_keys)]
        upper = UPPER_TONES.get(key, [76, 79])
        # Two notes per bar: one on the "&" of 1, one on beat 3.5
        # (deliberately syncopated against the keys stabs)
        for t_offset, p, vel in [
            (0.5, upper[0], 70),
            (3.5, upper[1], 60),
        ]:
            t, v = _humanize(b + t_offset, vel, time_jitter=0.020)
            notes.append({"pitch": p, "start_beats": t, "length_beats": 0.7, "velocity": v})
    return notes


def _pad_notes(start_bar: int, bars: int, chord_keys: list[int]) -> list[dict]:
    """Atmospheric pad — guide tones only.

    A pro arranger uses a pad to glue the harmony, not to play the chord:
      - Just root + 7th (the chord's "guide tones") — no 3rd or 5th, those
        are in the keys part
      - Octave above the bass so they're audible but not muddy
      - Sustain through the entire bar with a very slight overlap into the
        next bar (prevents harmonic gaps)
      - Soft velocity (50-60), pad does not call attention

    For each bar, we play root and 7th of the prevailing chord. Mappings
    use the 7th interval pattern of each chord: maj7 = +11, m7 = +10.
    """
    SEVENTH_INTERVAL = {
        41: 11,   # Fmaj7  → E
        45: 10,   # Am7    → G
        38: 10,   # Dm7    → C
        36: 11,   # Cmaj7  → B
    }
    # Per-call override populated by compose_lofi_track when caller
    # passes a custom `chord_progression`. Lets callers introduce
    # chords with arbitrary 7th intervals without editing this table.
    if _SEVENTH_INTERVAL_OVERRIDE:
        SEVENTH_INTERVAL = {**SEVENTH_INTERVAL, **_SEVENTH_INTERVAL_OVERRIDE}
    notes = []
    for bar in range(bars):
        b = start_bar * 4 + bar * 4
        key = chord_keys[bar % len(chord_keys)]
        # Pad sits an octave above the canonical chord root
        root_pad = key + 12
        seventh = root_pad + SEVENTH_INTERVAL.get(key, 11)
        # Slight overlap into next bar
        length = 4.1
        notes.append({"pitch": root_pad, "start_beats": b, "length_beats": length, "velocity": 55})
        notes.append({"pitch": seventh, "start_beats": b, "length_beats": length, "velocity": 50})
    return notes


# Default factory presets per role. The user can swap any of these via the
# tool's inputs — no code edit required. State is loaded from disk via
# _apply_preset_to_track. Exact attributes copied from .trkpreset XMLs.
DEFAULT_PRESETS = {
    "bass": "4OSC Smack Bass WMF.trkpreset",   # ampSustain=64, sustains through bar
    "keys": "4OSC E-Piano R WMF.trkpreset",
    # Dark Pad WMF — warmer than Vox Pad. filterFreq=79 (mid-low), small
    # filter envelope amount so it stays dark, heavy chorus for width.
    "pad":  "4OSC Dark Pad WMF.trkpreset",
}


def _drum_kit_sounds() -> list[dict]:
    """Build SOUND entries mapping factory drum samples to GM drum MIDI notes.

    Schema from tracktion_engine SamplerPlugin.cpp: each SOUND has source
    (file path), keyNote (root note), minNote/maxNote (key range), name,
    gainDb, pan, startTime, length, openEnded.

    Single-key mappings (min == max == keyNote) so each drum hit is
    triggered only by its specific MIDI note.
    """
    base = _factory_presets_dir()
    if base is None:
        return []
    kit_dir = base / "Drum Sampler"

    mappings = [
        # (midi_note, sample_filename, friendly_name)
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
        path = kit_dir / fname
        if not path.exists():
            continue
        sounds.append({
            "source": str(path),
            "name": name,
            "keyNote": note,
            "minNote": note,
            "maxNote": note,
            "startTime": 0.0,
            "gainDb": 0.0,
            "pan": 0.0,
            # openEnded=1 = play the entire sample regardless of note length.
            # This prevents the choppy cutoff on hi-hats whose MIDI note
            # length is shorter than the sample's natural decay.
            "openEnded": 1,
        })
    return sounds


def _jazz_kit_sounds() -> list[dict]:
    """Build a Jazz Kit by remapping 808 Kit samples to a jazz layout.

    The 808 Kit lacks a ride cymbal — closed-hat (42) doubles for the
    ride. This kit also exposes:
      - 51 (ride cymbal) → Closed Hat sample
      - 53 (ride bell)   → Cymbal sample (shorter decay)
    Plus the standard kit notes work normally.

    Until a real Jazz Kit sample pack is bundled, this is the best
    approximation using factory samples.
    """
    base = _factory_presets_dir()
    if base is None:
        return []
    kit_dir = base / "Drum Sampler"

    mappings = [
        # (midi_note, sample_filename, friendly_name)
        (36, "808 Bass Drum 115.flac",   "Kick (soft)"),
        (38, "808 Snare Drum 253.flac",  "Snare (brushed)"),
        (37, "808 Rim Shot 1.flac",      "Cross-stick / Rimshot"),
        (42, "808 Hihat Closed 1.flac",  "Closed Hat"),
        (46, "808 Hihat Open 2.flac",    "Open Hat"),
        (51, "808 Hihat Closed 1.flac",  "Ride"),     # remapped — closest sound
        (53, "808 Cymbal 27.flac",       "Ride Bell"),
        (49, "808 Cymbal 27.flac",       "Crash"),
        (45, "808 Tom Low 44.flac",      "Low Tom"),
        (48, "808 Tom Mid 44.flac",      "Mid Tom"),
        (50, "808 Tom Hi 1.flac",        "Hi Tom"),
    ]
    sounds = []
    for note, fname, name in mappings:
        path = kit_dir / fname
        if not path.exists():
            continue
        # Jazz Kit hits softer than rock/electronic — drop the gain
        # 3 dB so the brushed feel translates without rebalancing
        sounds.append({
            "source": str(path),
            "name": name,
            "keyNote": note,
            "minNote": note,
            "maxNote": note,
            "startTime": 0.0,
            "gainDb": -3.0,
            "pan": 0.0,
            "openEnded": 1,
        })
    return sounds


# ============================================================================
# Section-based arrangement model — applied from the music theory research.
# Each section has a bar range, a velocity scale (the "swell" curve), and a
# mute table per layer. This is how the song builds depth into the chorus.
#
# For a 32-bar lofi loop:
#   intro      bars 0-3   sparse, atmospheric (just keys + pad, soft)
#   build      bars 4-7   bass enters, hats enter, building
#   chorus_a   bars 8-15  full ensemble, peak energy ("body")
#   breakdown  bars 16-19 drums + bass drop, keys/pad sustain
#   chorus_b   bars 20-27 full + counter-line (the "second hit", with extra)
#   outro      bars 28-31 layers strip away
# ============================================================================

LOFI_ARRANGEMENT = [
    # Each section now has a velocity ENVELOPE (vel_start → vel_end ramps
    # linearly across the section's bars), a drum density multiplier, hat
    # subdivision (8th vs 16th), and octave-doubling flags. The CHORUS_B
    # section is deliberately bigger than CHORUS_A — denser drums, double-time
    # hi-hats, octave-doubled bass + keys, slightly higher velocity peak —
    # which is the "second climax bigger than the first" arc of most songs.
    {
        "name": "intro",     "bars": 4,
        "vel_start": 0.45, "vel_end": 0.55,        # gentle entry
        "drum_density": 0.0, "hat_subdivision": 8,
        "bass_octave_double": False, "keys_octave_double": False,
        "layers": {"drums": False, "bass": False, "keys": True,  "pad": True},
    },
    {
        "name": "build",     "bars": 4,
        "vel_start": 0.55, "vel_end": 0.95,        # CRESCENDO into chorus_a
        "drum_density": 0.6, "hat_subdivision": 8,
        "bass_octave_double": False, "keys_octave_double": False,
        "layers": {"drums": "soft", "bass": True,  "keys": True,  "pad": True},
    },
    {
        "name": "chorus_a",  "bars": 8,
        "vel_start": 1.00, "vel_end": 1.00,        # full energy held
        "drum_density": 1.0, "hat_subdivision": 8,
        "bass_octave_double": False, "keys_octave_double": False,
        "layers": {"drums": True,  "bass": True,  "keys": True,  "pad": True, "melody": True},
    },
    {
        "name": "breakdown", "bars": 4,
        "vel_start": 0.70, "vel_end": 0.55,        # falling — release
        "drum_density": 0.0, "hat_subdivision": 8,
        "bass_octave_double": False, "keys_octave_double": False,
        "layers": {"drums": False, "bass": "soft", "keys": True, "pad": True},
    },
    {
        "name": "chorus_b",  "bars": 8,
        "vel_start": 1.00, "vel_end": 1.15,        # PEAK — rises across section
        "drum_density": 1.0, "hat_subdivision": 8,   # smooth 8ths + open-close + triplet fills
        "bass_octave_double": True, "keys_octave_double": True,    # fuller
        "layers": {"drums": True,  "bass": True,  "keys": True,  "pad": True, "counter": True, "melody": True},
    },
    {
        "name": "outro",     "bars": 4,
        "vel_start": 0.55, "vel_end": 0.25,        # decaying out
        "drum_density": 0.0, "hat_subdivision": 8,
        "bass_octave_double": False, "keys_octave_double": False,
        "layers": {"drums": False, "bass": False, "keys": True,  "pad": True},
    },
]


def _bar_to_section(bar: int) -> dict:
    """Look up which arrangement section a bar belongs to."""
    cursor = 0
    for sec in LOFI_ARRANGEMENT:
        if cursor <= bar < cursor + sec["bars"]:
            return sec
        cursor += sec["bars"]
    return LOFI_ARRANGEMENT[-1]


def _section_start_bar(section_name: str) -> int:
    cursor = 0
    for sec in LOFI_ARRANGEMENT:
        if sec["name"] == section_name:
            return cursor
        cursor += sec["bars"]
    return 0


def _vel_curve_at(bar: int, beat_in_bar: float = 0.0) -> float:
    """Linear velocity multiplier within the current section.
    Ramps from vel_start at the section's first bar to vel_end at the last bar.
    """
    sec = _bar_to_section(bar)
    section_start = _section_start_bar(sec["name"])
    section_bars = sec["bars"]
    # Position 0..1 across the section (continuous in beats)
    progress = ((bar - section_start) * 4 + beat_in_bar) / max(1, section_bars * 4)
    progress = max(0.0, min(1.0, progress))
    return sec["vel_start"] + (sec["vel_end"] - sec["vel_start"]) * progress


def _apply_section_to_notes(notes: list[dict], layer: str) -> list[dict]:
    """Filter + smooth-velocity-ramp notes by section/layer.

    The velocity ramp interpolates linearly across each section's bars, so
    a note in the middle of the build section gets a velocity scale halfway
    between vel_start (0.55) and vel_end (0.95). This produces a real
    crescendo, not a step change at section boundaries.
    """
    out: list[dict] = []
    for n in notes:
        bar = int(n["start_beats"] // 4)
        beat_in_bar = n["start_beats"] - bar * 4
        sec = _bar_to_section(bar)
        active = sec["layers"].get(layer, False)
        if active is False:
            continue
        scale = _vel_curve_at(bar, beat_in_bar)
        if active == "soft":
            scale *= 0.65
        new_vel = max(1, min(127, int(round(n["velocity"] * scale))))
        out.append({**n, "velocity": new_vel})
    return out


def _octave_double_for_section(notes: list[dict], layer: str, octaves: int) -> list[dict]:
    """Duplicate notes at +/- N octaves for sections where octave-doubling is
    enabled for this layer. Used to thicken bass (sub octave) and keys
    (octave-up) during chorus_b for the "fuller sound" climax effect.
    """
    out: list[dict] = list(notes)
    for n in notes:
        bar = int(n["start_beats"] // 4)
        sec = _bar_to_section(bar)
        active = sec["layers"].get(layer, False)
        if active is False:
            continue
        flag_key = {"bass": "bass_octave_double", "keys": "keys_octave_double"}.get(layer)
        if not flag_key or not sec.get(flag_key):
            continue
        # Sub-octave bass needs to be tucked further so it adds warmth not
        # mud. Keys octave-up can be at 70% — high register doesn't mask.
        scale = 0.50 if (layer == "bass" and octaves < 0) else 0.70
        out.append({
            **n,
            "pitch": n["pitch"] + octaves * 12,
            "velocity": max(1, int(n["velocity"] * scale)),
        })
    return out


def _add_drum_density(notes: list[dict], bars: int) -> list[dict]:
    """Drum embellishments per section. Replaces the previous "blast 16ths"
    approach for chorus_b with what a real drummer would do at the climax:

      - Open-then-closed hi-hat groove (the disco/chillhop classic): hit an
        open hat (46), let it ring for ~80ms, then close it with a closed
        hat (42). Used on the "&" of beats — most expressive groove mover
        in the kit.
      - Selective triplet fills: eighth-note triplets on specific bars to
        break up the 8th-note grid and add forward motion. Used sparingly
        (1-2 bars per chorus, not every bar).
      - Occasional ghost snares for groove, not for density.

    CRITICAL: any embellishment that lands on a beat where the base pattern
    already has a hat must REPLACE that hat, not stack on top of it. Two
    hi-hats within ~50ms of each other reads as a smeared flam, which was
    the "off" feeling in earlier mixes.
    """
    rng = random.Random(46)
    out = list(notes)
    chorus_b_start = _section_start_bar("chorus_b")

    def _remove_hats_in_window(start_b: float, end_b: float) -> None:
        """Drop any closed/open hat (42, 46) whose start falls in [start, end]."""
        nonlocal out
        out = [n for n in out
               if not (n["pitch"] in (42, 46) and start_b <= n["start_beats"] < end_b)]

    for bar in range(bars):
        sec = _bar_to_section(bar)
        b = bar * 4
        if not (sec.get("layers", {}).get("drums") is True):
            continue

        # ---- Open-close hi-hat groove (the underutilized move) ----
        # On chorus_b, deploy on most "&" beats. On chorus_a, occasional.
        if sec["name"] == "chorus_b":
            open_close_chance = 0.65
        elif sec["name"] == "chorus_a":
            open_close_chance = 0.20
        else:
            open_close_chance = 0.0
        if open_close_chance > 0:
            for beat in range(4):
                if rng.random() >= open_close_chance:
                    continue
                # Open hat on the "&", short duration, then immediate closed hat
                # to "pedal-close" it. Real drummers do this with the foot.
                # First REMOVE the underlying off-beat hat from the base pattern
                # so we don't get a flammy double-hit.
                _remove_hats_in_window(b + beat + 0.4, b + beat + 0.95)
                open_t = b + beat + 0.5 + rng.uniform(-0.012, 0.012)
                v_open = rng.randint(58, 75)
                out.append({"pitch": 46, "start_beats": open_t,
                            "length_beats": 0.07, "velocity": v_open})
                # Closed hat 1/16 later to "choke" the open
                close_t = open_t + 0.18
                v_close = rng.randint(48, 65)
                out.append({"pitch": 42, "start_beats": close_t,
                            "length_beats": 0.14, "velocity": v_close})

        # ---- Triplet fills in chorus_b ----
        # eighth-note triplets = 3 hits in 1 beat (positions 0, 0.333, 0.667)
        # Used on bars 2 and 5 of the 8-bar chorus_b for forward motion.
        if sec["name"] == "chorus_b":
            chorus_b_bar = bar - chorus_b_start
            if chorus_b_bar == 2:
                # Hi-hat triplet roll on beat 4 — leads into bar 3.
                # Clear the regular 8th hats on beat 4 so the triplet is clean.
                _remove_hats_in_window(b + 3.0, b + 4.0)
                for i in range(3):
                    t = b + 3.0 + i * (1.0 / 3.0) + rng.uniform(-0.008, 0.008)
                    v = 60 + i * 8  # slight crescendo
                    out.append({"pitch": 42, "start_beats": t,
                                "length_beats": 0.18, "velocity": v})
            if chorus_b_bar == 5:
                # Snare triplet flam on beat 4 — sets up the resolve.
                # Don't clear hats here — snare triplets go OVER the hat groove.
                # But do remove any duplicate snares in that beat.
                out = [n for n in out
                       if not (n["pitch"] == 38 and b + 3.0 <= n["start_beats"] < b + 4.0)]
                for i in range(3):
                    t = b + 3.0 + i * (1.0 / 3.0) + rng.uniform(-0.008, 0.008)
                    v = 70 + i * 7
                    out.append({"pitch": 38, "start_beats": t,
                                "length_beats": 0.13, "velocity": v})

        # ---- Light ghost snares (for groove, not density) ----
        if sec["name"] == "chorus_b":
            ghost_positions = [0.875, 2.875]   # only 2 spots, not every weak 16th
            for gp in ghost_positions:
                if rng.random() < 0.35:
                    t = b + gp + rng.uniform(-0.012, 0.012)
                    v = rng.randint(26, 38)
                    out.append({"pitch": 38, "start_beats": t,
                                "length_beats": 0.1, "velocity": v})
    return out


def _total_bars() -> int:
    return sum(s["bars"] for s in LOFI_ARRANGEMENT)


def compose_lofi_track(args: dict) -> dict:
    out_path = args["out_path"]
    tempo = float(args.get("tempo", 76.0))
    seed = int(args.get("seed", 0))
    # Re-seed module-level RNG for variation reproducibility
    random.seed(seed)
    bars = int(args.get("bars", _total_bars()))
    bass_preset = args.get("bass_preset", DEFAULT_PRESETS["bass"])
    keys_preset = args.get("keys_preset", DEFAULT_PRESETS["keys"])
    pad_preset = args.get("pad_preset", DEFAULT_PRESETS["pad"])
    bass_octave_offset = int(args.get("bass_octave_offset", 1))

    # Default progression: Fmaj7 - Am7 - Dm7 - Cmaj7 (classic lofi cadence).
    # Override via args["chord_progression"] — a list of chord descriptors:
    #   [{"bass_root": int, "voicing": [3 MIDI notes], "seventh_interval": 10|11}, ...]
    # All bass_roots in the progression must be distinct (the downstream
    # composition helpers look up by root). Voicings are 3-note rootless
    # 3-7-9 stacks the keys part will play.
    progression = args.get("chord_progression")
    # Reset module-level state so a custom progression in a previous
    # call doesn't leak into this one. JAZZ_VOICINGS is restored to its
    # original default; SEVENTH_INTERVAL_OVERRIDE is cleared.
    JAZZ_VOICINGS.clear()
    JAZZ_VOICINGS.update(_JAZZ_VOICINGS_DEFAULT)
    global _SEVENTH_INTERVAL_OVERRIDE
    _SEVENTH_INTERVAL_OVERRIDE = {}

    if progression:
        # Register the override into the module-level lookup tables so
        # _chord_progression / _pad_notes find them. Existing keys with
        # the same root are overwritten — the override wins for this call.
        for chord in progression:
            root = int(chord["bass_root"])
            JAZZ_VOICINGS[root] = list(chord["voicing"])
        chord_keys = [int(c["bass_root"]) for c in progression]
        chords = [list(c["voicing"]) for c in progression]
        _SEVENTH_INTERVAL_OVERRIDE = {
            int(c["bass_root"]): int(c.get("seventh_interval", 11))
            for c in progression
        }
    else:
        chord_keys = [41, 45, 38, 36]
        chords = [
            [53, 57, 60, 64],   # Fmaj7
            [57, 60, 64, 67],   # Am7
            [50, 53, 57, 60],   # Dm7
            [48, 52, 55, 59],   # Cmaj7
        ]
    octave = bass_octave_offset * 12
    bass_pitches = [k + octave for k in chord_keys]

    # 1. Create edit
    edit_tools.edit_create({
        "name": "MCP Lofi",
        "tempo": tempo,
        "time_sig_num": 4,
        "time_sig_denom": 4,
        "key_root": 0,        # C
        "key_mode": "major",
        "length_beats": bars * 4 + 4,
    })

    # 2. Drums — real Sampler with 808 samples mapped to GM drum MIDI notes.
    # Filtered + velocity-scaled by section so they're absent in intro/outro,
    # soft in build, full in chorus_a, dropped in breakdown, full in chorus_b.
    drums = tracks.track_add({"name": "Drums", "kind": "midi"})["track_id"]
    plugins.plugin_add({"track_id": drums, "type": "Sampler", "name": "808 Kit"})
    drum_track = next(t for t in STORE.edit.tracks if t.id == drums)
    drum_track.plugins[-1].sounds = _drum_kit_sounds()
    drum_clip = midi.midi_clip_add({
        "track_id": drums, "name": "Drums",
        "start_beats": 0, "length_beats": bars * 4,
    })["clip_id"]
    drum_notes = _add_drum_density(_drum_pattern(0, bars), bars)
    midi.midi_notes_add({
        "clip_id": drum_clip,
        "notes": _apply_section_to_notes(drum_notes, "drums"),
    })

    # Sample-library paths — used by the bass + melody Sampler tracks
    lofi_samples_dir = (
        Path.home() / "AppData" / "Roaming" / "Tracktion" / "Waveform"
        / "Factory Loops" / "Lofi Samples"
    )

    # 3. Bass — REAL BASS GUITAR via multi-sample Sampler. 4 Cymatics samples
    # (A, B, C, E) mapped with narrow ranges so each played note is at most
    # ~3 semitones from the nearest sample's natural pitch. This is the
    # technique that worked for the marimba and avoids the wide-range
    # pitch-shift artifacts that plagued the single-sample attempt earlier.
    bass = tracks.track_add({"name": "Bass", "kind": "midi"})["track_id"]
    bass_samples = {
        # All assumed at octave-2 range. Each covers 2-3 semitones around
        # its natural pitch.
        40: ("lofi_bass_E.wav",       "Bass E2"),  # E2 — covers MIDI 36-41
        45: ("lofi_bass_A.wav",       "Bass A2"),  # A2 — covers 42-46
        47: ("lofi_bass_B.wav",       "Bass B2"),  # B2 — covers 47
        48: ("lofi_bass_guitar_C.wav","Bass C3"),  # C3 — covers 48-57
    }
    if all((lofi_samples_dir / fn).exists() for _, (fn, _) in bass_samples.items()):
        plugins.plugin_add({"track_id": bass, "type": "Sampler", "name": "Lofi Bass"})
        bass_track_obj = next(t for t in STORE.edit.tracks if t.id == bass)
        bass_track_obj.plugins[-1].sounds = [
            {"source": str(lofi_samples_dir / "lofi_bass_E.wav"),
             "name": "Bass E2", "keyNote": 40, "minNote": 24, "maxNote": 41,
             "startTime": 0.0, "gainDb": 0.0, "pan": 0.0, "openEnded": 1},
            {"source": str(lofi_samples_dir / "lofi_bass_A.wav"),
             "name": "Bass A2", "keyNote": 45, "minNote": 42, "maxNote": 46,
             "startTime": 0.0, "gainDb": 0.0, "pan": 0.0, "openEnded": 1},
            {"source": str(lofi_samples_dir / "lofi_bass_B.wav"),
             "name": "Bass B2", "keyNote": 47, "minNote": 47, "maxNote": 47,
             "startTime": 0.0, "gainDb": 0.0, "pan": 0.0, "openEnded": 1},
            {"source": str(lofi_samples_dir / "lofi_bass_guitar_C.wav"),
             "name": "Bass C3", "keyNote": 48, "minNote": 48, "maxNote": 80,
             "startTime": 0.0, "gainDb": 0.0, "pan": 0.0, "openEnded": 1},
        ]
    else:
        # Fallback: synth with weighted envelope
        plugins.plugin_add({"track_id": bass, "type": "4OSC", "name": "Bass Synth"})
        _apply_preset_to_track(bass, "4OSC", bass_preset, overrides=WEIGHTED_BASS_ENVELOPE)
    # Per-track FX: high-pass to cut sub-mud + light compression to even out
    # the dynamics so notes sustain audibly through the bar
    _add_track_fx_bass(bass)
    bass_clip = midi.midi_clip_add({
        "track_id": bass, "name": "Bass",
        "start_beats": 0, "length_beats": bars * 4,
    })["clip_id"]
    bass_notes = _bass_progression(0, bars, bass_pitches, chord_keys, bass_octave_offset)
    # Octave-double bass in chorus_b (sub-octave layer) for fullness
    bass_notes = _octave_double_for_section(bass_notes, "bass", octaves=-1)
    midi.midi_notes_add({
        "clip_id": bass_clip,
        "notes": _apply_section_to_notes(bass_notes, "bass"),
    })

    # 4. Keys (chord stabs) — smoothed with override (slower attack, darker
    # filter, less velocity sensitivity, more chorus) so the Rhodes-ish tone
    # is warm and round instead of brittle and clicky.
    keys = tracks.track_add({"name": "Keys", "kind": "midi"})["track_id"]
    plugins.plugin_add({"track_id": keys, "type": "4OSC", "name": "Keys Synth"})
    _apply_preset_to_track(keys, "4OSC", keys_preset, overrides=SMOOTHER_KEYS_OVERRIDE)
    # Subtle plate reverb on the keys for ambience without washout — 12% wet,
    # short decay so it doesn't muddy up the bass.
    plugins.plugin_add_reverb({
        "track_id": keys, "type": "plate",
        "size": 0.45, "decay": 0.35, "mix": 0.12,
        "high_damp": 0.7, "high_cut": 0.85,
    })
    keys_clip = midi.midi_clip_add({
        "track_id": keys, "name": "Keys",
        "start_beats": 0, "length_beats": bars * 4,
    })["clip_id"]
    keys_notes = _chord_progression(0, bars, chords, chord_keys)
    # Octave-double keys in chorus_b (top voice up an octave) for sparkle
    keys_notes = _octave_double_for_section(keys_notes, "keys", octaves=+1)
    midi.midi_notes_add({
        "clip_id": keys_clip,
        "notes": _apply_section_to_notes(keys_notes, "keys"),
    })

    # 5. Pad (atmosphere)
    pad = tracks.track_add({"name": "Pad", "kind": "midi"})["track_id"]
    plugins.plugin_add({"track_id": pad, "type": "4OSC", "name": "Pad Synth"})
    _apply_preset_to_track(pad, "4OSC", pad_preset)
    pad_clip = midi.midi_clip_add({
        "track_id": pad, "name": "Pad",
        "start_beats": 0, "length_beats": bars * 4,
    })["clip_id"]
    midi.midi_notes_add({
        "clip_id": pad_clip,
        "notes": _apply_section_to_notes(_pad_notes(0, bars, chord_keys), "pad"),
    })

    # 5b. Ambience track — beach atmosphere from CC0 sound files plus the
    # real lofi bass guitar sample as accent clips at chord changes (where
    # the C-pitched sample lands on the C chord roots).
    ambience_dir = (
        Path.home() / "AppData" / "Roaming" / "Tracktion" / "Waveform"
        / "Factory Loops" / "Ambience"
    )
    lofi_samples_dir = (
        Path.home() / "AppData" / "Roaming" / "Tracktion" / "Waveform"
        / "Factory Loops" / "Lofi Samples"
    )
    if ambience_dir.exists():
        # ---- Birds track ---- pushed louder
        # Removed amazon_jungle file (had a bass rumble at the start that
        # bled into the mix). Cycle through 3 cleaner bird recordings.
        amb = tracks.track_add({"name": "Birds", "kind": "audio"})["track_id"]
        tracks.mix_set({"track_id": amb, "volume_db": -1.0, "pan": -0.05})

        # Cycle just the two birds the user said work: forest + garden+curlew.
        # Alternate every 8 bars across the song.
        bird_files = [
            ("birds_chirp_ambience.wav",  "Forest birds"),
            ("birds_garden_curlew.mp3",   "Garden + curlew"),
            ("birds_chirp_ambience.wav",  "Forest birds 2"),
            ("birds_garden_curlew.mp3",   "Garden + curlew 2"),
        ]
        for i, (fname, label) in enumerate(bird_files):
            path = ambience_dir / fname
            if not path.exists():
                continue
            audio.audio_clip_import({
                "track_id": amb,
                "file_path": str(path),
                "start_beats": float(i * 8 * 4),
                "length_beats": 32.0,
                "name": label,
                "warp_mode": "none",
            })

        # ---- Breeze track ----
        # CC0 "Soft Wind in the Trees" by Borgory (freesound #751473) — gentle
        # rustle, subtle. Replaces the chimes which didn't fit. Quiet so it
        # blends, plays continuously through the song.
        breeze = ambience_dir / "breeze_through_trees.mp3"
        if breeze.exists():
            breeze_track = tracks.track_add({"name": "Breeze", "kind": "audio"})["track_id"]
            tracks.mix_set({"track_id": breeze_track, "volume_db": -14.0, "pan": 0.05})
            # Tile the file twice to cover all 32 bars (file is ~30s, half the song)
            for i in range(2):
                audio.audio_clip_import({
                    "track_id": breeze_track,
                    "file_path": str(breeze),
                    "start_beats": float(i * 16 * 4),
                    "length_beats": 64.0,
                    "name": f"Breeze {i+1}",
                    "warp_mode": "none",
                })

        # ---- Glass track ----
        # CC0 "glass_ice_shaking" by airblock (freesound #407803) — sustained
        # shaking of ice in a glass, longer + more atmospheric than the
        # short clink we had before. Subtle, occasional placements.
        glass = ambience_dir / "glass_ice_shaking.mp3"
        if glass.exists():
            glass_track = tracks.track_add({"name": "Glass", "kind": "audio"})["track_id"]
            tracks.mix_set({"track_id": glass_track, "volume_db": -16.0, "pan": -0.10})
            for start_bar in (2, 14, 26):    # 3 placements: intro, breakdown, chorus_b
                audio.audio_clip_import({
                    "track_id": glass_track,
                    "file_path": str(glass),
                    "start_beats": float(start_bar * 4),
                    "length_beats": 16.0,
                    "name": f"Glass b{start_bar+1}",
                    "warp_mode": "none",
                })

    # 6. Counter-melody — a sparse piano line one octave up from the keys
    # voicing, ONLY active during chorus_b. This is the "lift" that makes
    # the second chorus feel bigger than the first (the research-cited
    # "final_chorus_lift" heuristic — added harmony layer for return).
    counter = tracks.track_add({"name": "Counter", "kind": "midi"})["track_id"]
    plugins.plugin_add({"track_id": counter, "type": "4OSC", "name": "Counter Synth"})
    _apply_preset_to_track(counter, "4OSC", keys_preset, overrides=SMOOTHER_KEYS_OVERRIDE)
    counter_clip = midi.midi_clip_add({
        "track_id": counter, "name": "Counter",
        "start_beats": 0, "length_beats": bars * 4,
    })["clip_id"]
    counter_notes = _counter_melody(bars, chord_keys)
    midi.midi_notes_add({
        "clip_id": counter_clip,
        "notes": _apply_section_to_notes(counter_notes, "counter"),
    })
    tracks.mix_apply_reference({"track_id": counter, "genre": "lofi", "role": "counter", "pan": 0.25})

    # 7. Melody — 4OSC simulating a wooden marimba. The Sampler approach
    # didn't trigger audio reliably for this track even after MP3→WAV.
    # Drums + bass Samplers work but melody Sampler stayed silent across
    # multiple attempts. Falling back to 4OSC with a percussive marimba
    # envelope: triangle wave (warm wooden timbre) + fast attack + quick
    # decay + zero sustain = bell-like wooden hit.
    melody = tracks.track_add({"name": "Melody", "kind": "midi"})["track_id"]
    plugins.plugin_add({"track_id": melody, "type": "4OSC", "name": "Marimba 4OSC"})
    # Light + airy + fading envelope. Slow attack so notes don't punch in,
    # long decay + light sustain so they fade naturally, long release so the
    # tail breathes after note-off.
    _apply_preset_to_track(melody, "4OSC", "4OSC Bell Pad WMF.trkpreset", overrides={
        "ampAttack": 0.040,        # 40ms soft entry — no click, no punch
        "ampDecay": 1.40,          # long decay so notes ring and fade
        "ampSustain": 22.0,        # light sustain — notes fade to ~22% then sit
        "ampRelease": 1.10,        # airy tail
        "filterAttack": 0.0,
        "filterDecay": 1.20,
        "filterSustain": 30.0,
        "filterRelease": 0.8,
        "filterFreq": 78.0,        # warmer (was 110 — was too bright)
        "filterAmount": 0.18,
        "filterResonance": 0.0,    # no peaking
        "waveShape1": 3,           # triangle (wooden warmth)
        "tune1": 0.0,
        "masterLevel": -3.0,
        "chorusOn": 1,
        "chorusMix": 0.55,         # more chorus = wider, airier
    })
    melody_clip = midi.midi_clip_add({
        "track_id": melody, "name": "Melody",
        "start_beats": 0, "length_beats": bars * 4,
    })["clip_id"]
    midi.midi_notes_add({
        "clip_id": melody_clip,
        "notes": _apply_section_to_notes(_melody_xylophone(bars), "melody"),
    })
    tracks.mix_apply_reference({"track_id": melody, "genre": "lofi", "role": "melody", "pan": -0.05})

    # Mix balance — sourced from MIX_BALANCE["lofi"] in music_theory_data.py.
    # Single source of truth across composers; tweak the table once and
    # every lofi composer benefits.
    tracks.mix_apply_reference({"track_id": drums, "genre": "lofi", "role": "drums"})
    tracks.mix_apply_reference({"track_id": bass,  "genre": "lofi", "role": "bass"})
    tracks.mix_apply_reference({"track_id": keys,  "genre": "lofi", "role": "keys", "pan":  0.10})
    tracks.mix_apply_reference({"track_id": pad,   "genre": "lofi", "role": "pad",  "pan": -0.10})

    # Tempo automation: subtle climb into chorus_b for a real climax feel,
    # then back down for the outro. The +2 BPM bump is small enough not to
    # feel jarring but big enough that listeners feel the lift.
    chorus_b_start_beat = _section_start_bar("chorus_b") * 4
    outro_start_beat = _section_start_bar("outro") * 4
    STORE.edit.tempo_changes = [
        (float(chorus_b_start_beat), tempo + 2.0),    # climax push
        (float(outro_start_beat), tempo),             # back to base
        (float((bars - 2) * 4), tempo - 2.0),         # gentle ritard at the very end
    ]

    # Volume automation:
    #   - Bass swells in across the build section instead of jumping in cold
    #   - Outro fade-out on the master is naturally handled by the tempo
    #     ritard; here we ride the pad and keys volumes down so the song
    #     dissolves rather than stops.
    from . import automation as automation_tools

    build_start = _section_start_bar("build") * 4
    chorus_a_start = _section_start_bar("chorus_a") * 4
    automation_tools.automation_envelope({
        "track_id": bass,
        "target": "volume",
        "start_beat": float(build_start),
        "end_beat": float(chorus_a_start),
        "start_value": -42.0,        # near-silent at start of build
        "end_value": -18.0,          # mix-balance level at start of chorus_a
        "shape": "exp_out",
        "steps": 12,
    })
    # Outro fade-out on pad + keys (drums and bass already drop out per arrangement)
    outro_end_beat = float(bars * 4)
    for trk in (pad, keys):
        automation_tools.automation_envelope({
            "track_id": trk,
            "target": "volume",
            "start_beat": float(outro_start_beat),
            "end_beat": outro_end_beat,
            "start_value": -2.0,
            "end_value": -45.0,
            "shape": "exp_in",
            "steps": 10,
        })

    # Master volume — modest. Track levels carry most of the loudness now,
    # so we only need ~+1dB of make-up here. Limiter eats fewer peaks =
    # cleaner dynamics.
    STORE.edit.master_volume_db = 1.0

    # Master bus FX: EQ → Comp → Limiter
    _add_master_fx_lofi()

    # Save
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    edit_tools.edit_save({"path": str(out)})

    summary = STORE.edit.summary()
    return {
        "ok": True,
        "path": str(out),
        "size_bytes": out.stat().st_size,
        "tracks": len(summary["tracks"]),
        "total_notes": sum(
            (c.get("note_count") or 0)
            for t in summary["tracks"]
            for c in t.get("clips", [])
        ),
        "tempo": tempo,
        "bars": bars,
    }


def _set_params(track_id: str, params: dict) -> None:
    """Set multiple params on the most-recently-added user plugin on a track."""
    track = next(t for t in STORE.edit.tracks if t.id == track_id)
    if not track.plugins:
        return
    plugin = track.plugins[-1]
    for k, v in params.items():
        plugin.params[k] = v


# ============================================================================
# Ambient: rainstorm — pure soundscape, no instruments.
# ============================================================================


def compose_rainstorm(args: dict) -> dict:
    """Build a rain + wind + distant-thunder ambient track.

    No music. Three audio tracks:
      1. Rain   — continuous downpour (rain_loop.mp3)
      2. Wind   — breeze through trees (existing breeze_through_trees.mp3),
                  tiled if shorter than the song
      3. Thunder — occasional rumbles, low-passed and pulled down in volume so
                   they read as "off in the distance"

    Tempo is fixed at 60 BPM so 1 beat == 1 second. Length defaults to 180s
    (3 minutes) — long enough for the storm to feel real but short enough to
    render quickly during iteration.
    """
    out_path = args["out_path"]
    length_seconds = float(args.get("length_seconds", 180.0))
    seed = int(args.get("seed", 42))

    ambience_dir = (
        Path.home() / "AppData" / "Roaming" / "Tracktion" / "Waveform"
        / "Factory Loops" / "Ambience"
    )
    rain_file = ambience_dir / "rain_loop.mp3"
    wind_file = ambience_dir / "breeze_through_trees.mp3"
    thunder_files = [
        ambience_dir / "thunder_distant_1.mp3",  # 20s — single rumble
        ambience_dir / "thunder_distant_2.mp3",  # 20s — clap + rumble tail
        ambience_dir / "thunder_distant_3.mp3",  # 7min — varied rumbles
    ]
    for f in [rain_file, wind_file, *thunder_files]:
        if not f.exists():
            raise FileNotFoundError(f"missing ambience file: {f}")

    # 60 BPM → 1 beat = 1 second. Easier mental model for ambient.
    tempo = 60.0
    length_beats = length_seconds  # 1 beat per second

    edit_tools.edit_create({
        "name": "MCP Rainstorm",
        "tempo": tempo,
        "time_sig_num": 4,
        "time_sig_denom": 4,
        "key_root": 0,
        "key_mode": "minor",
        "length_beats": length_beats + 4,
    })

    # Rain — primary bed. Plenty long (10min source) so single clip. Add a
    # 30-second fade-in so the storm builds rather than slams in, and a
    # 10-second fade-out so the listener isn't dropped off a cliff.
    rain = tracks.track_add({"name": "Rain", "kind": "audio", "volume_db": -3.0})["track_id"]
    audio.audio_clip_import({
        "track_id": rain,
        "file_path": str(rain_file),
        "start_beats": 0.0,
        "length_beats": length_beats,
        "name": "Rain",
        "warp_mode": "none",
        "fade_in_beats": 30.0,
        "fade_out_beats": 10.0,
    })

    # Wind — breeze layer, tiled to cover full length (source is ~147s).
    # Slow fade-in (15s) layers it under the rain instead of arriving in lockstep.
    wind = tracks.track_add({"name": "Wind", "kind": "audio", "volume_db": -6.0})["track_id"]
    wind_tile = 140.0  # leave a sliver of overlap to avoid edge clicks
    pos = 0.0
    is_first_wind_clip = True
    while pos < length_beats:
        seg = min(wind_tile, length_beats - pos)
        is_last_clip = (pos + wind_tile >= length_beats)
        audio.audio_clip_import({
            "track_id": wind,
            "file_path": str(wind_file),
            "start_beats": pos,
            "length_beats": seg,
            "name": "Wind",
            "warp_mode": "none",
            "fade_in_beats": 15.0 if is_first_wind_clip else 0.0,
            "fade_out_beats": 10.0 if is_last_clip else 0.0,
        })
        is_first_wind_clip = False
        pos += wind_tile

    # Thunder — drop a handful of rumbles at musically-uneven intervals so
    # they don't feel scheduled. Volume pulled way down + lowpass + EQ
    # high-cut on the track for the "distant, behind a wall of rain" feel.
    thunder = tracks.track_add({"name": "Thunder", "kind": "audio", "volume_db": 14.0})["track_id"]

    rng = random.Random(seed)
    # Schedule: roughly one event every 25-45s, with random jitter
    placements: list[tuple[float, Path, float]] = []  # (start_seconds, file, length_seconds)
    t_cursor = 12.0  # first thunder ~12s in (after listener settles into rain)
    while t_cursor < length_seconds - 25.0:
        # Skip thunder_distant_3 — peaks at only -12 dB, too quiet under rain.
        # 1 is loudest (peak -6); 2 has a 3s silent lead-in then bangs at 6s.
        f = rng.choice([thunder_files[0], thunder_files[1]])
        seg = 18.0
        placements.append((t_cursor, f, seg))
        # Next event in 22-42 seconds
        t_cursor += rng.uniform(22.0, 42.0)

    # Manual injection: extra thunder_2 between the last two thunder_1 hits
    # for a denser closing build-up. Find the last two T1 placements and
    # insert at their midpoint.
    t1_indices = [i for i, p in enumerate(placements) if p[1] == thunder_files[0]]
    if len(t1_indices) >= 2:
        a = placements[t1_indices[-2]][0]
        b = placements[t1_indices[-1]][0]
        placements.append(((a + b) / 2.0, thunder_files[1], 18.0))
        placements.sort(key=lambda x: x[0])

    for start_s, f, seg in placements:
        # Per-thunder variation: random gain (-3..+2 dB) + soft fade-out so
        # rumbles tail naturally into the rain rather than cutting hard.
        # thunder_distant_2 has a ~3-second silent lead-in before its bang —
        # skip it via offset_in_source_beats so every play hits the strike.
        per_clip_gain = rng.uniform(-3.0, 2.0)
        offset = 4.0 if f == thunder_files[1] else 0.0
        audio.audio_clip_import({
            "track_id": thunder,
            "file_path": str(f),
            "start_beats": start_s,  # 1 beat = 1 second
            "length_beats": seg,
            "name": f"Thunder ({f.stem})",
            "warp_mode": "none",
            "gain_db": per_clip_gain,
            "fade_out_beats": 4.0,
            "offset_in_source_beats": offset,
        })

    # Thunder track FX: heavy lowpass + high-shelf cut so what's left sounds
    # like it's been muffled by miles of air and rain.
    thunder_track = next(t for t in STORE.edit.tracks if t.id == thunder)
    thunder_track.plugins.append(Plugin(
        id=_id("plg"),
        type="3 Band EQ",
        name="Distant EQ",
        params={
            "loFreq": 60.0,
            "loGain": 2.0,           # slight low boost — the rumble *is* low end
            "midFreq": 400.0,
            "midGain": -3.0,         # cut presence band
            "midQ": 0.7,
            "hiFreq": 3000.0,
            "hiGain": -12.0,         # high-shelf cut for "distance" (was -18, eased to keep body)
        },
    ))
    thunder_track.plugins.append(Plugin(
        id=_id("plg"),
        type="Lowpass Filter",
        name="Distance LP",
        params={
            "frequency": 600.0,      # very dark — distant thunder has no top end
            "q": 0.6,
        },
    ))

    # Light master chain: gentle compression + limiter so peaks (thunder)
    # don't clip when they overlap a louder rain segment.
    edit = STORE.edit
    edit.master_chain.append(Plugin(
        id=_id("plg"),
        type="Compressor",
        name="Master Glue",
        params={
            "threshold": -10.0,
            "ratio": 1.8,
            "attack": 30.0,
            "release": 250.0,
            "knee": 8.0,
            "outputDb": 0.0,
        },
    ))
    edit.master_chain.append(Plugin(
        id=_id("plg"),
        type="Limiter",
        name="Master Limiter",
        params={"threshold": -1.0, "release": 50.0},
    ))

    # Save
    edit_tools.edit_save({"path": out_path})

    return {
        "ok": True,
        "path": out_path,
        "length_seconds": length_seconds,
        "thunder_events": len(placements),
        "summary": STORE.edit.summary(),
    }


TOOLS.append({
    "name": "compose_rainstorm",
    "description": (
        "Generate a rainstorm ambient .tracktionedit: continuous rain bed, "
        "tiled wind/breeze layer, occasional distant-muffled thunder rumbles "
        "(lowpass + EQ + low gain). 60 BPM (1 beat = 1 second). No music."
    ),
    "inputSchema": {
        "type": "object",
        "required": ["out_path"],
        "properties": {
            "out_path": {"type": "string"},
            "length_seconds": {"type": "number", "default": 180.0},
            "seed": {"type": "integer", "default": 42,
                     "description": "RNG seed for thunder placement variation."},
        },
    },
})


# ============================================================================
# Synthwave — driving 4-on-the-floor, arpeggiated lead, big plate verb.
# Showcases: clip fades, automation sweeps, plate reverb, modifier LFO,
#            section-gated arrangement, master glue chain.
# ============================================================================


SYNTHWAVE_ARRANGEMENT = [
    # 64-bar full song (~2:19 at 110 BPM). Each section is musically
    # distinct — different chord progression, different bass feel,
    # different arp theme. New "buildup" before chorusFinal lifts the climax.
    #
    # Form: intro → verseA → chorus → verseB → chorus2 → bridge → buildup → chorusFinal → outro
    #         4   →   8    →   8    →   8    →    8    →   8    →    4    →     8       →   8    = 64
    {"name": "intro",       "bars": 4,  "vel": 0.55,
     "layers": {"drums": "kick_only", "bass": False,    "pad": True, "padB": False, "lead": False, "arp": False,    "counter": False}},
    {"name": "verseA",      "bars": 8,  "vel": 0.75,
     "layers": {"drums": "verse",     "bass": "verse",  "pad": True, "padB": False, "lead": False, "arp": "verse",  "counter": False}},
    {"name": "chorus",      "bars": 8,  "vel": 1.00,
     "layers": {"drums": True,        "bass": "chorus", "pad": True, "padB": True,  "lead": False, "arp": "chorus", "counter": False}},
    {"name": "verseB",      "bars": 8,  "vel": 0.80,
     "layers": {"drums": "verse",     "bass": "verse",  "pad": True, "padB": False, "lead": False, "arp": "verseB", "counter": "lead"}},
    {"name": "chorus2",     "bars": 8,  "vel": 1.05,
     "layers": {"drums": True,        "bass": "chorus", "pad": True, "padB": True,  "lead": "intro", "arp": "chorus", "counter": False}},
    {"name": "bridge",      "bars": 8,  "vel": 0.90,    # new harmonic territory, walking bass, descending arp
     "layers": {"drums": "bridge",    "bass": "bridge", "pad": True, "padB": False, "lead": False, "arp": "bridge", "counter": "lead"}},
    {"name": "buildup",     "bars": 4,  "vel": 1.00,    # 4-bar tension lift: snare crescendo, no clap
     "layers": {"drums": "buildup",   "bass": "chorus", "pad": True, "padB": False, "lead": False, "arp": "chorus", "counter": False}},
    {"name": "chorusFinal", "bars": 8,  "vel": 1.15,    # peak: full mix + lead motif on top
     "layers": {"drums": True,        "bass": "chorus", "pad": True, "padB": True,  "lead": "peak",  "arp": "chorus", "counter": "harmony"}},
    {"name": "outro",       "bars": 8,  "vel": 0.55,    # progressive drop to pad-only chord ring
     "layers": {"drums": "fade",      "bass": False,    "pad": True, "padB": False, "lead": False, "arp": "fade",   "counter": "taper"}},
]


# ---------------------------------------------------------------------------
# Per-section chord progressions. These are the harmonic backbone of each
# section. The composer reads these to drive bass, pad, and arp lines so
# every section feels harmonically distinct, not just dynamically.
# Each list = one chord per 2 bars. 4 chords per 8-bar section = standard
# pop phrase length.
# ---------------------------------------------------------------------------

# All in A minor. Pitches are MIDI numbers chosen for the synth registers
# (bass roots in octave 2-3, pad chord centers in octave 3-4).
SYNTHWAVE_PROGRESSIONS = {
    # Verse: Am - Em - F - C (vi - iii - IV - I in C major)
    # Wandering, more inward feel than the chorus.
    "verse":  [
        {"root_bass": 45, "root_keys": 57, "voicing": [45, 52, 57, 60, 64]},   # Am
        {"root_bass": 40, "root_keys": 52, "voicing": [40, 47, 52, 55, 59]},   # Em
        {"root_bass": 41, "root_keys": 53, "voicing": [41, 48, 53, 57, 60]},   # F maj
        {"root_bass": 48, "root_keys": 60, "voicing": [48, 55, 60, 64, 67]},   # C maj
    ],
    # Chorus: Am - F - C - G (vi - IV - I - V — the synthwave hook)
    "chorus": [
        {"root_bass": 45, "root_keys": 57, "voicing": [45, 52, 57, 60, 64]},   # Am
        {"root_bass": 41, "root_keys": 53, "voicing": [41, 48, 53, 57, 60]},   # F maj
        {"root_bass": 48, "root_keys": 60, "voicing": [48, 55, 60, 64, 67]},   # C maj
        {"root_bass": 43, "root_keys": 55, "voicing": [43, 50, 55, 59, 62]},   # G maj
    ],
    # Bridge: Dm - A maj - Bb maj - F. Real harmonic surprise:
    #   - Dm (iv): subdued opening, true contrast to chorus's Am opening
    #   - A maj (parallel-major surprise — has C# OUT of the natural-minor key)
    #   - Bb maj (bII Phrygian flavor — borrowed, dark and unexpected)
    #   - F maj (bVI — pivots back home, sets up the chorusFinal return)
    "bridge": [
        {"root_bass": 38, "root_keys": 50, "voicing": [38, 45, 50, 53, 57]},   # Dm
        {"root_bass": 45, "root_keys": 57, "voicing": [45, 52, 57, 61, 64]},   # A maj (C#!)
        {"root_bass": 46, "root_keys": 58, "voicing": [46, 50, 53, 58, 65]},   # Bb maj (Phrygian bII)
        {"root_bass": 41, "root_keys": 53, "voicing": [41, 48, 53, 57, 60]},   # F maj
    ],
    # Buildup: F-G-Am-G — rising IV-V-i-V tension that resolves on chorusFinal Am
    "buildup": [
        {"root_bass": 41, "root_keys": 53, "voicing": [41, 48, 53, 57, 60]},   # F (1 bar)
        {"root_bass": 43, "root_keys": 55, "voicing": [43, 50, 55, 59, 62]},   # G (1 bar)
        {"root_bass": 45, "root_keys": 57, "voicing": [45, 52, 57, 60, 64]},   # Am (1 bar)
        {"root_bass": 43, "root_keys": 55, "voicing": [43, 50, 55, 59, 62]},   # G (1 bar — V leading to Am)
    ],
}


def _synth_chords_for(section_name: str) -> list[dict]:
    """Pick a progression for a section. Verses share verse chords; chorus,
    chorus2, chorusFinal all share the chorus progression; bridge + buildup
    have their own. Outro lingers on the chorus's final chord (Am)."""
    if section_name in ("verseA", "verseB"):
        return SYNTHWAVE_PROGRESSIONS["verse"]
    if section_name in ("chorus", "chorus2", "chorusFinal"):
        return SYNTHWAVE_PROGRESSIONS["chorus"]
    if section_name == "bridge":
        return SYNTHWAVE_PROGRESSIONS["bridge"]
    if section_name == "buildup":
        return SYNTHWAVE_PROGRESSIONS["buildup"]
    if section_name == "intro":
        return [SYNTHWAVE_PROGRESSIONS["chorus"][0]] * 2
    if section_name == "outro":
        return [SYNTHWAVE_PROGRESSIONS["chorus"][0]] * 4
    return SYNTHWAVE_PROGRESSIONS["chorus"]


def _synth_chord_idx(section_name: str, bar_in_section: int, n_chords: int) -> int:
    """How chords map to bars within a section. Buildup is 1-chord-per-bar
    (4 chords across 4 bars). All other sections are 2-bars-per-chord."""
    if section_name == "buildup":
        return bar_in_section % n_chords
    return (bar_in_section // 2) % n_chords


def _synth_section_at(bar: int) -> dict:
    cursor = 0
    for sec in SYNTHWAVE_ARRANGEMENT:
        if cursor <= bar < cursor + sec["bars"]:
            return sec
        cursor += sec["bars"]
    return SYNTHWAVE_ARRANGEMENT[-1]


def _synth_section_start(name: str) -> int:
    cursor = 0
    for sec in SYNTHWAVE_ARRANGEMENT:
        if sec["name"] == name:
            return cursor
        cursor += sec["bars"]
    return 0


def _synthwave_drums(bars: int) -> list[dict]:
    """Driving 4-on-the-floor with section-aware variations:
      - kick_only: just the kick, intro establishment
      - soft:      lower velocities, no clap layer
      - True:      full kit
      - buildup:   snare roll subdivided 16ths, NO clap — tension before climax
      - fade:      progressive density loss across the section, ends silent
    """
    rng = random.Random(108)
    notes: list[dict] = []
    buildup_start_bar = _synth_section_start("buildup")
    outro_start_bar = _synth_section_start("outro")
    outro_bars = next((s["bars"] for s in SYNTHWAVE_ARRANGEMENT if s["name"] == "outro"), 8)

    for bar in range(bars):
        sec = _synth_section_at(bar)
        layer = sec["layers"]["drums"]
        if not layer:
            continue
        b = bar * 4
        kick_only = (layer == "kick_only")
        soft = (layer == "soft")
        is_buildup = (layer == "buildup")
        is_fade = (layer == "fade")

        # Fade-out section: progressive density. First fade bar is full,
        # subsequent bars drop layers and last bar is silent.
        if is_fade:
            fade_idx = bar - outro_start_bar  # 0..outro_bars-1
            fade_progress = fade_idx / max(1, outro_bars - 1)
            # Last bar: no drums at all (chord ring out)
            if fade_idx >= outro_bars - 1:
                continue

        # ---- Kick on every beat ----
        # During buildup, drop kicks on the last bar to leave space for the
        # snare roll. During fade, kick volume rolls down (lower velocity).
        kick_velocity_base = 92 if soft else 105
        if is_fade:
            kick_velocity_base = int(kick_velocity_base * (1.0 - fade_progress * 0.55))
        if is_buildup and bar == buildup_start_bar + 3:
            # last bar of buildup — kick only on 1 and 3 (suspense)
            kick_beats = [0, 2]
        else:
            kick_beats = list(range(4))
        for beat in kick_beats:
            t, v = _humanize(b + float(beat), kick_velocity_base, time_jitter=0.006, vel_jitter=4)
            notes.append({"pitch": 36, "start_beats": t, "length_beats": 0.25, "velocity": v})

        if kick_only:
            continue

        # ---- Snare ----
        # Buildup: 16th-note snare ROLL on the last bar leading into chorusC.
        # Otherwise snare on 2 and 4.
        if is_buildup and bar == buildup_start_bar + 3:
            # 16th-note crescendo roll across the whole bar
            for i in range(16):
                t = b + i * 0.25 + rng.uniform(-0.005, 0.005)
                v = int(60 + (i / 15.0) * 50)  # 60 → 110
                notes.append({"pitch": 38, "start_beats": t, "length_beats": 0.18, "velocity": v})
        else:
            for beat in (1, 3):
                snare_v = 95 if not soft else 78
                if is_fade:
                    snare_v = int(snare_v * (1.0 - fade_progress * 0.65))
                t, v = _humanize(b + float(beat), snare_v, time_jitter=0.005, vel_jitter=5)
                notes.append({"pitch": 38, "start_beats": t, "length_beats": 0.25, "velocity": v})

            # Clap layered on top of snare in full sections (not soft, not buildup)
            if not soft and not is_buildup and not is_fade:
                for beat in (1, 3):
                    t, v = _humanize(b + float(beat), 80, time_jitter=0.005, vel_jitter=4)
                    notes.append({"pitch": 39, "start_beats": t, "length_beats": 0.2, "velocity": v})

        # ---- Closed hats every 8th ----
        # Buildup last bar: skip hats so the snare roll cuts through cleanly.
        # Fade: hats progressively drop out.
        skip_hats = (is_buildup and bar == buildup_start_bar + 3)
        if not skip_hats:
            for i in range(8):
                if is_fade and rng.random() < fade_progress * 0.7:
                    continue  # progressively skip hats
                hat_v_base = 70 if i % 2 == 0 else 50
                if is_fade:
                    hat_v_base = int(hat_v_base * (1.0 - fade_progress * 0.5))
                t, v = _humanize(b + i * 0.5, hat_v_base, time_jitter=0.008, vel_jitter=8)
                notes.append({"pitch": 42, "start_beats": t, "length_beats": 0.15, "velocity": v})

        # ---- Open hat on the "&" of 4, last bar of each phrase ----
        # Skip during buildup last bar (snare roll owns it) and fade-final.
        if bar % 4 == 3 and not (is_buildup and bar == buildup_start_bar + 3) and not is_fade:
            t, v = _humanize(b + 3.5, 80)
            notes.append({"pitch": 46, "start_beats": t, "length_beats": 0.5, "velocity": v})

        # ---- Crashes on the downbeat of every major section ----
        # chorus, chorus2, bridge, chorusFinal, and outro punctuation
        for sec_name, vel in [
            ("chorus", 95),
            ("chorus2", 100),
            ("bridge", 92),
            ("chorusFinal", 110),
        ]:
            if bar == _synth_section_start(sec_name):
                notes.append({"pitch": 49, "start_beats": b + 0.0,
                              "length_beats": 2.5, "velocity": vel})

        # Outro punctuation: one final big crash on the first beat of outro
        # that rings through the fade. This gives the song a "period" instead
        # of just trailing off into nothing.
        if bar == outro_start_bar:
            notes.append({"pitch": 49, "start_beats": b + 0.0,
                          "length_beats": 4.0, "velocity": 115})

        # ---- Transition fills ----
        # End of each 8-bar section (NOT during buildup or fade): a small
        # tom-or-snare fill on beat 4 — lets the listener feel the section
        # turning over.
        sec_start = _synth_section_start(sec["name"])
        is_last_bar = (bar - sec_start == sec["bars"] - 1)
        if is_last_bar and not is_buildup and not is_fade and not kick_only:
            # Skip the fill in the very last bar of each section if the
            # NEXT section is buildup (buildup itself owns the lead-in)
            next_bar = bar + 1
            next_sec = _synth_section_at(next_bar) if next_bar < bars else None
            next_is_buildup = next_sec and next_sec["name"] == "buildup"
            if not next_is_buildup:
                # 4-note tom fill (pitches 41=lo, 45=mid, 48=hi tom in GM map)
                # on the second half of beat 4 — quick, doesn't step on snare 4
                fill_pitches = [45, 48, 38, 39]   # mid tom, hi tom, snare, clap
                for i, p in enumerate(fill_pitches):
                    t = b + 3.5 + i * 0.125
                    v = 75 + i * 4  # crescendo
                    notes.append({"pitch": p, "start_beats": t,
                                  "length_beats": 0.12, "velocity": v})

        # ---- "Drop" — silent half-bar before chorusFinal ----
        # Last bar of buildup, beats 3.5+ → silent (we already skipped kick on
        # beat 3, and the snare roll handles the ramp). Add NOTHING here —
        # the silence between bar's end and chorusFinal downbeat IS the drop.
    return notes


def _synthwave_bass(bars: int) -> list[dict]:
    """Bass line that adapts to section feel:

      verse:  HALF-TIME — root on 1, root-up-octave on 3. Spaced, breathing.
      chorus: RELENTLESS 8th-note pump with octave on the "&" (the genre's
              signature driving feel).
      bridge: WALKING — root → 5th → root → 7th, quarter notes, climbs and
              descends across each chord. Departs from the chorus pump.
    """
    notes: list[dict] = []
    for bar in range(bars):
        sec = _synth_section_at(bar)
        feel = sec["layers"]["bass"]
        if not feel:
            continue
        b = bar * 4
        chords = _synth_chords_for(sec["name"])
        # Each section's chord cycles every 2 bars — derive within-section bar index
        sec_start = _synth_section_start(sec["name"])
        chord_idx = _synth_chord_idx(sec["name"], bar - sec_start, len(chords))
        root = chords[chord_idx]["root_bass"]

        if feel == "verse":
            # Half-time: root on 1, octave-up on 3. Long sustains.
            notes.append({"pitch": root, "start_beats": b + 0.0,
                          "length_beats": 1.95, "velocity": 88})
            notes.append({"pitch": root + 12, "start_beats": b + 2.0,
                          "length_beats": 1.95, "velocity": 80})

        elif feel == "chorus":
            # 8th-note pump with octave on offbeats of beats 2 and 4
            for i in range(8):
                pitch = root + (12 if i in (2, 6) else 0)
                t = b + i * 0.5
                v = 95 if i % 2 == 0 else 80
                notes.append({"pitch": pitch, "start_beats": t,
                              "length_beats": 0.45, "velocity": v})

        elif feel == "bridge":
            # Walking bass: root, 5, root+12, 7th — one per beat (quarter notes)
            walk = [root, root + 7, root + 12, root + 10]
            for i, pitch in enumerate(walk):
                notes.append({"pitch": pitch, "start_beats": b + float(i),
                              "length_beats": 0.95, "velocity": 86})
    return notes


def _synthwave_pad(bars: int) -> list[dict]:
    """Sustained pad chords. Voicing comes from the section's progression.
    Most sections play 8-beat sustains (one chord per 2 bars). Buildup uses
    4-beat sustains because its chords change every bar (F-G-Am-G)."""
    notes: list[dict] = []
    bar = 0
    while bar < bars:
        sec = _synth_section_at(bar)
        if not sec["layers"].get("pad"):
            bar += 1
            continue
        chords = _synth_chords_for(sec["name"])
        sec_start = _synth_section_start(sec["name"])
        chord_idx = _synth_chord_idx(sec["name"], bar - sec_start, len(chords))
        voicing = chords[chord_idx]["voicing"]
        # 1 bar per chord in buildup, 2 bars per chord elsewhere
        chord_bars = 1 if sec["name"] == "buildup" else 2
        length_beats = chord_bars * 4
        b = bar * 4
        for pitch in voicing:
            notes.append({"pitch": pitch, "start_beats": b,
                          "length_beats": float(length_beats), "velocity": 70})
        bar += chord_bars
    return notes


def _synthwave_arp(bars: int) -> list[dict]:
    """Section-specific arp PATTERNS so each section has a distinct theme:

      verse:   theme A — sparse, bottom 3 chord tones cycled in 8ths
                         (slower than chorus, leaves space)
      verseB:  theme A varied — same notes, syncopated rhythm pattern
      chorus:  theme B — full 16th-note pump, all chord tones + octave-up
                         (the established hook)
      bridge:  theme C — descending pattern, longer notes, contemplative
      fade:    theme B but progressively dropping notes
    """
    notes: list[dict] = []
    rng = random.Random(444)
    outro_start = _synth_section_start("outro")
    outro_bars = next((s["bars"] for s in SYNTHWAVE_ARRANGEMENT if s["name"] == "outro"), 8)

    for bar in range(bars):
        sec = _synth_section_at(bar)
        feel = sec["layers"].get("arp")
        if not feel:
            continue
        b = bar * 4
        chords = _synth_chords_for(sec["name"])
        sec_start = _synth_section_start(sec["name"])
        chord_idx = _synth_chord_idx(sec["name"], bar - sec_start, len(chords))
        chord = chords[chord_idx]["voicing"]

        if feel == "verse":
            # Theme A: bottom 3 notes, cycled at 8th-note pace
            theme = chord[:3]
            for i in range(8):
                pitch = theme[i % len(theme)]
                t = b + i * 0.5
                v = 65 + (8 if i % 2 == 0 else 0)
                notes.append({"pitch": pitch, "start_beats": t,
                              "length_beats": 0.45, "velocity": v})

        elif feel == "verseB":
            # Theme A varied: dotted-eighth syncopated pattern (rhythm changes)
            theme = chord[:3]
            # rhythm: 0.0, 0.75, 1.5, 2.0, 2.5, 3.25, 3.75 (7 notes per bar, syncopated)
            rhythms = [0.0, 0.75, 1.5, 2.0, 2.5, 3.25, 3.75]
            for i, r in enumerate(rhythms):
                pitch = theme[i % len(theme)]
                v = 65 + (10 if i in (0, 3) else 0)
                notes.append({"pitch": pitch, "start_beats": b + r,
                              "length_beats": 0.40, "velocity": v})

        elif feel == "chorus":
            # Theme B: full 16th-note pump with chord+octave cycle
            cycle = chord + [p + 12 for p in chord]
            for i in range(16):
                pitch = cycle[i % len(cycle)]
                t = b + i * 0.25
                v = 70 + (12 if i % 4 == 0 else 0)
                notes.append({"pitch": pitch, "start_beats": t,
                              "length_beats": 0.22, "velocity": v})

        elif feel == "bridge":
            # Theme C: descending, longer notes — contemplative, contrasts chorus
            descending = list(reversed(chord + [p + 12 for p in chord[:2]]))
            # 8 notes per bar at 8th-note pace, descending pitch each time
            for i in range(8):
                pitch = descending[i % len(descending)]
                t = b + i * 0.5
                v = 70
                notes.append({"pitch": pitch, "start_beats": t,
                              "length_beats": 0.45, "velocity": v})

        elif feel == "fade":
            # Theme B with progressive note-skipping
            fade_idx = bar - outro_start
            fade_progress = fade_idx / max(1, outro_bars - 1)
            cycle = chord + [p + 12 for p in chord]
            for i in range(16):
                if rng.random() < fade_progress * 0.85:
                    continue  # progressively drop notes
                pitch = cycle[i % len(cycle)]
                t = b + i * 0.25
                v = int((70 + (12 if i % 4 == 0 else 0)) * (1.0 - fade_progress * 0.4))
                notes.append({"pitch": pitch, "start_beats": t,
                              "length_beats": 0.22, "velocity": v})
    return notes


def _synthwave_padB(bars: int) -> list[dict]:
    """Second pad layer — only plays in chorus / chorus2 / chorusFinal
    (any section with padB layer flag True). Voicing is the chord top half
    (3rd, 5th, 7th, 9th) instead of full chord, so it doesn't pile mud
    on the main pad's low end."""
    notes: list[dict] = []
    bar = 0
    while bar < bars:
        sec = _synth_section_at(bar)
        if not sec["layers"].get("padB"):
            bar += 1
            continue
        chords = _synth_chords_for(sec["name"])
        sec_start = _synth_section_start(sec["name"])
        chord_idx = _synth_chord_idx(sec["name"], bar - sec_start, len(chords))
        voicing = chords[chord_idx]["voicing"]
        # Use only the top 3 notes (no bass-region tones)
        top_voicing = voicing[2:]
        chord_bars = 1 if sec["name"] == "buildup" else 2
        b = bar * 4
        for pitch in top_voicing:
            # Pitch up an octave to sit above main pad
            notes.append({"pitch": pitch + 12, "start_beats": b,
                          "length_beats": float(chord_bars * 4), "velocity": 65})
        bar += chord_bars
    return notes


def _synthwave_lead(bars: int) -> list[dict]:
    """The singable hook — a motivic top-line. Plays in chorus2 (sparse
    intro of the motif) and chorusFinal (full motif + variation).

    Motif: a 4-note "call" + 4-note "response" across 2 bars, anchored
    around the chord's 5th-3rd-7th-9th. Repeated 4× across each 8-bar
    section but with rhythmic variation in chorusFinal.

    Pitches are picked relative to the current chord's root_keys so the
    motif tracks the chord changes.
    """
    notes: list[dict] = []
    for bar in range(bars):
        sec = _synth_section_at(bar)
        stage = sec["layers"].get("lead")
        if not stage:
            continue
        b = bar * 4
        chords = _synth_chords_for(sec["name"])
        sec_start = _synth_section_start(sec["name"])
        chord_idx = _synth_chord_idx(sec["name"], bar - sec_start, len(chords))
        root = chords[chord_idx]["root_keys"]
        # Pitches up two octaves above root_keys → top of mix, around C5-G5
        fifth   = root + 7  + 12   # 5th up an octave
        third   = root + 4  + 12
        seventh = root + 10 + 12
        ninth   = root + 14 + 12
        octave  = root + 12 + 12

        bar_in_sec = bar - sec_start
        is_call_bar = (bar_in_sec % 2 == 0)

        if stage == "intro":
            # Sparse — just the call phrase, response is silence
            if is_call_bar:
                # 4-note call: 5-3-fifth-third (motif declaration)
                phrase = [
                    (fifth,  0.0,  0.9, 72),
                    (third,  1.0,  0.9, 70),
                    (fifth,  2.0,  0.9, 75),
                    (third,  3.0,  0.9, 68),
                ]
            else:
                phrase = []  # silence — give the pad room

        elif stage == "peak":
            # Full motif: call + response, each 2 bars. Chorusfinal repeats it
            # 4 times so the listener locks onto it.
            if is_call_bar:
                # call: ascending 5→3→7→9
                phrase = [
                    (fifth,   0.0, 0.95, 88),
                    (third,   1.0, 0.95, 84),
                    (seventh, 2.0, 0.95, 90),
                    (ninth,   3.0, 0.95, 92),
                ]
            else:
                # response: descending and resolving 9→7→5→octave
                phrase = [
                    (ninth,   0.0, 0.95, 90),
                    (seventh, 1.0, 0.95, 86),
                    (fifth,   2.0, 0.95, 84),
                    (octave,  3.0, 1.95, 95),  # resolve held longer
                ]
        else:
            phrase = []

        for pitch, start, length, vel in phrase:
            notes.append({"pitch": pitch, "start_beats": b + start,
                          "length_beats": length, "velocity": vel})
    return notes


def _synthwave_counter(bars: int) -> list[dict]:
    """Counter melody — sits in TENOR/ALTO register (root + 0..12), distinct
    from the lead's soprano range and the arp's top cycle. Plays only when
    it has a real role:

      "lead"    (verseB, bridge): IS the melody — lead synth is silent so
                  the counter carries the listener through the section.
      "harmony" (chorusFinal):    HARMONY a 3rd below the lead, on the
                  RESPONSE bars only (when lead sustains its octave note).
                  Counter and lead never fight for attention.
      "taper"   (outro):          progressive dissolve.

    chorus2 + buildup intentionally have NO counter — chorus2 belongs to
    the lead's intro, buildup belongs to the snare crescendo.
    """
    notes: list[dict] = []
    outro_start = _synth_section_start("outro")

    for bar in range(bars):
        sec = _synth_section_at(bar)
        stage = sec["layers"].get("counter")
        if not stage:
            continue
        b = bar * 4
        chords = _synth_chords_for(sec["name"])
        sec_start = _synth_section_start(sec["name"])
        chord_idx = _synth_chord_idx(sec["name"], bar - sec_start, len(chords))
        root = chords[chord_idx]["root_keys"]

        # Mid-range voice tones (tenor/alto range, root + 0..14).
        # For Am (root_keys=57) → tonic=57 (A3), third=61 (C#4), fifth=64 (E4),
        # seventh=67 (G4), ninth=71 (B4), tonic_up=69 (A4).
        # All sit cleanly BELOW the lead's soprano stack (root+19..root+24).
        rt        = root          # tonic
        third     = root + 4
        fifth     = root + 7
        seventh   = root + 10
        ninth     = root + 14
        tonic_up  = root + 12     # used as the "top" of the mid-range
        # Bar parity within section
        bar_in_sec = bar - sec_start

        # ---- LEAD-ROLE stage (verseB, bridge) ----
        # Counter IS the melody. Singable 4-note phrase per 2 bars.
        if stage == "lead":
            if bar_in_sec % 2 == 0:
                # Call: rt → third → fifth → third
                phrase = [
                    (tonic_up, 0.0, 0.95, 72),
                    (third + 12, 1.0, 0.95, 70),
                    (fifth + 12, 2.0, 0.95, 76),
                    (third + 12, 3.0, 0.95, 68),
                ]
            else:
                # Response: ninth → fifth → third → rt
                phrase = [
                    (ninth,   0.0, 1.4, 78),
                    (fifth + 12, 1.5, 0.9, 73),
                    (third + 12, 2.5, 0.9, 70),
                    (tonic_up, 3.5, 0.45, 65),
                ]
            for pitch, start, length, vel in phrase:
                notes.append({"pitch": pitch, "start_beats": b + start,
                              "length_beats": length, "velocity": vel})
            continue

        # ---- HARMONY-ROLE stage (chorusFinal) ----
        # Lead motif sits in soprano (root+19..+24 area). Counter plays a
        # tenor/alto harmony BELOW the lead — root + 0..14 range — on the
        # RESPONSE bars only, when the lead sustains. On call bars, counter
        # rests so the lead carries attention without competition.
        if stage == "harmony":
            if bar_in_sec % 2 == 0:
                continue  # call bar — let lead breathe
            # Response bar — counter plays sustained 5th + tonic_up below lead.
            phrase = [
                (fifth,    0.0, 2.0, 70),    # E4 for Am — solid tenor
                (tonic_up, 2.0, 1.95, 72),   # A4 for Am — alto, resolves with lead's octave
            ]
            for pitch, start, length, vel in phrase:
                notes.append({"pitch": pitch, "start_beats": b + start,
                              "length_beats": length, "velocity": vel})
            continue

        # ---- TAPER stage (outro) ----
        # Same gradual dissolve as before but in the new mid-register.
        if stage == "taper":
            outro_bar = bar - outro_start
            if outro_bar in (0, 1):
                vel_scale = 0.90 if outro_bar == 0 else 0.75
                phrase = [(tonic_up, 0.0, 1.95, 72), (fifth + 12, 2.0, 1.95, 68)]
                for pitch, start, length, vel in phrase:
                    notes.append({"pitch": pitch, "start_beats": b + start,
                                  "length_beats": length,
                                  "velocity": int(vel * vel_scale)})
            elif outro_bar in (2, 3):
                pitch = third + 12 if outro_bar == 2 else fifth + 12
                notes.append({"pitch": pitch, "start_beats": b + 0.0,
                              "length_beats": 3.5,
                              "velocity": 60 if outro_bar == 2 else 52})
            elif outro_bar == 4:
                notes.append({"pitch": tonic_up, "start_beats": b + 0.0,
                              "length_beats": 2.5, "velocity": 42})
            # outro_bar >= 5: silence; clip fade_out_beats handles any tail
    return notes


def compose_synthwave_track(args: dict) -> dict:
    """A 32-bar synthwave instrumental at 110 BPM in A minor.

    Showcases the new MCP toolset:
      - plugin_add_reverb: plate verb on the lead, gated non-linear on snare
      - automation_envelope: filter sweeps on bass + pad swells
      - plugin_add_modifier: LFO on pad filter for movement
      - clip fades: master fade-in/out on pad
      - section-gated layer presence (intro/verse/chorus/breakdown/chorus)
    """
    out_path = args["out_path"]
    tempo = float(args.get("tempo", 110.0))
    seed = int(args.get("seed", 0))
    random.seed(seed)
    bars = sum(s["bars"] for s in SYNTHWAVE_ARRANGEMENT)  # 64

    # Chord progressions are per-section (verse / chorus / bridge), defined
    # in SYNTHWAVE_PROGRESSIONS at module scope. The generator functions
    # (_synthwave_bass / _synthwave_pad / _synthwave_arp / _synthwave_counter)
    # look up the right progression for each bar via _synth_chords_for.

    # 1. Create edit
    edit_tools.edit_create({
        "name": "MCP Synthwave",
        "tempo": tempo,
        "time_sig_num": 4, "time_sig_denom": 4,
        "key_root": 9,           # A
        "key_mode": "minor",
        "length_beats": bars * 4 + 4,
    })

    # 2. Drums — Sampler with 808 kit
    drums = tracks.track_add({"name": "Drums", "kind": "midi"})["track_id"]
    plugins.plugin_add({"track_id": drums, "type": "Sampler", "name": "808 Kit"})
    drum_track = next(t for t in STORE.edit.tracks if t.id == drums)
    drum_track.plugins[-1].sounds = _drum_kit_sounds()
    # Gated non-linear reverb on the snare track style — added as track FX
    plugins.plugin_add_reverb({
        "track_id": drums, "type": "non_linear",
        "size": 0.35, "decay": 0.25, "mix": 0.10,
        "high_damp": 0.4, "high_cut": 0.95,
    })
    drum_clip = midi.midi_clip_add({
        "track_id": drums, "name": "Drums",
        "start_beats": 0, "length_beats": bars * 4,
    })["clip_id"]
    midi.midi_notes_add({"clip_id": drum_clip, "notes": _synthwave_drums(bars)})

    # 3. Bass — 4OSC sub bass with a saw lead. We use the Smack Bass preset
    # with envelope override to make it punchier than lofi.
    bass = tracks.track_add({"name": "Bass", "kind": "midi"})["track_id"]
    plugins.plugin_add({"track_id": bass, "type": "4OSC", "name": "Synth Bass"})
    _apply_preset_to_track(bass, "4OSC", "4OSC Smack Bass WMF.trkpreset", overrides={
        "ampAttack": 0.002,    # immediate
        "ampDecay": 0.20,
        "ampSustain": 75.0,    # holds — driving bass needs body
        "ampRelease": 0.08,
        "filterFreq": 105.0,   # within 4OSC's typical 0..127 range (factory presets cap ~100)
        "filterAmount": 0.55,
        "filterResonance": 0.10,
        "tune1": 0.0,
        "masterLevel": 0.0,    # no makeup — track-level volume_db handles balance
    })
    bass_clip = midi.midi_clip_add({
        "track_id": bass, "name": "Bass",
        "start_beats": 0, "length_beats": bars * 4,
    })["clip_id"]
    midi.midi_notes_add({"clip_id": bass_clip, "notes": _synthwave_bass(bars)})

    # 4. Pad — atmospheric 4OSC pad. We *would* attach an LFO modifier on
    # filterFreq for the synthwave "breathing pad" sound, but
    # plugin_add_modifier writes a generic modmatrix shape that 4OSC
    # rejects (the real 4OSC modmatrix uses internal index codes, not
    # param-name strings). Until we capture a real fixture, we fake the
    # movement with automation on the volume instead.
    pad = tracks.track_add({"name": "Pad", "kind": "midi"})["track_id"]
    plugins.plugin_add({"track_id": pad, "type": "4OSC", "name": "Pad Synth"})
    _apply_preset_to_track(pad, "4OSC", "4OSC Vox Pad WMF.trkpreset", overrides={
        "ampAttack": 0.8,      # slow swell — defining pad shape
        "ampRelease": 1.5,
        "filterFreq": 95.0,    # within 4OSC's typical range; lets harmonics through
        "filterAmount": 0.30,
        "masterLevel": 1.0,
        # Stereo widening: enable osc2 same shape with slight detune
        "waveShape2": 3,
        "tune2": 0.07,         # +7 cents — small spread
        "voicesDetune": 0.15,  # voice-level detune for unison stereo width
    })
    # Pad high-pass at 250 Hz to keep low-end clean (no overlap with bass).
    pad_track_obj = next(t for t in STORE.edit.tracks if t.id == pad)
    pad_track_obj.plugins.append(Plugin(
        id=_id("plg"), type="3 Band EQ", name="Pad HP",
        params={
            "loFreq": 250.0, "loGain": -24.0,    # aggressive HP at 250
            "midFreq": 1000.0, "midGain": 0.0, "midQ": 0.8,
            "hiFreq": 8000.0, "hiGain": 0.0,
        },
    ))
    pad_clip = midi.midi_clip_add({
        "track_id": pad, "name": "Pad",
        "start_beats": 0, "length_beats": bars * 4,
    })["clip_id"]
    midi.midi_notes_add({"clip_id": pad_clip, "notes": _synthwave_pad(bars)})

    # 4b. Pad B — second pad layer for choruses only. String-like saw pad
    # panned opposite the main pad, +cents detune for stereo width.
    padB = tracks.track_add({"name": "Pad B", "kind": "midi"})["track_id"]
    plugins.plugin_add({"track_id": padB, "type": "4OSC", "name": "Pad B Synth"})
    _apply_preset_to_track(padB, "4OSC", "4OSC Vox Pad WMF.trkpreset", overrides={
        "ampAttack": 0.6,
        "ampRelease": 1.3,
        "filterFreq": 88.0,
        "filterAmount": 0.35,
        "masterLevel": 0.0,
        "waveShape1": 1,       # saw — distinct timbre from Pad's triangle
        "waveShape2": 1,
        "tune2": -0.12,        # opposite detune to Pad
        "voicesDetune": 0.20,
        "chorusOn": 1, "chorusMix": 0.45,
    })
    padB_track_obj = next(t for t in STORE.edit.tracks if t.id == padB)
    padB_track_obj.plugins.append(Plugin(
        id=_id("plg"), type="3 Band EQ", name="Pad B HP",
        params={"loFreq": 300.0, "loGain": -24.0,
                "midFreq": 1500.0, "midGain": 0.0, "midQ": 0.8,
                "hiFreq": 8000.0, "hiGain": 0.0},
    ))
    padB_clip = midi.midi_clip_add({
        "track_id": padB, "name": "Pad B",
        "start_beats": 0, "length_beats": bars * 4,
    })["clip_id"]
    midi.midi_notes_add({"clip_id": padB_clip, "notes": _synthwave_padB(bars)})

    # 5. Counter melody — sustained lyrical line above the arp during
    # chorus2, bridge, and chorusFinal. Same Bell Pad timbre as the arp
    # but with longer notes — the ear locks onto it as a singable melody.
    counter = tracks.track_add({"name": "Counter", "kind": "midi"})["track_id"]
    plugins.plugin_add({"track_id": counter, "type": "4OSC", "name": "Counter Synth"})
    # Counter timbre — soft saw with closed filter for a vocal/distant-horn
    # quality, distinct from arp's bell triangle so the two voices read
    # separately. Slow attack so the counter "swells in" instead of plucking.
    _apply_preset_to_track(counter, "4OSC", "4OSC Bell Pad WMF.trkpreset", overrides={
        "ampAttack": 0.18,      # was 0.05 — slower swell, less pluck
        "ampDecay": 0.8,
        "ampSustain": 80.0,
        "ampRelease": 1.2,
        "filterFreq": 80.0,     # was 105 — darker, more "horn" less "bell"
        "filterAmount": 0.35,
        "filterResonance": 0.05,
        "waveShape1": 2,        # saw — was triangle (which matched arp)
        "tune1": 0.0,
        "waveShape2": 2,
        "tune2": -0.08,         # detune for width
        "voicesDetune": 0.18,
        "chorusOn": 1,
        "chorusMix": 0.40,
    })
    plugins.plugin_add_reverb({
        "track_id": counter, "type": "plate",
        "size": 0.55, "decay": 0.50, "mix": 0.30,
    })
    counter_clip = midi.midi_clip_add({
        "track_id": counter, "name": "Counter",
        "start_beats": 0, "length_beats": bars * 4,
    })["clip_id"]
    midi.midi_notes_add({"clip_id": counter_clip, "notes": _synthwave_counter(bars)})

    # 6. Arp — the chorus lift. Bell-like 4OSC plucks at 16th-note speed,
    # plate verb to glue.
    arp = tracks.track_add({"name": "Arp", "kind": "midi"})["track_id"]
    plugins.plugin_add({"track_id": arp, "type": "4OSC", "name": "Arp Synth"})
    _apply_preset_to_track(arp, "4OSC", "4OSC Bell Pad WMF.trkpreset", overrides={
        "ampAttack": 0.001,
        "ampDecay": 0.18,
        "ampSustain": 0.0,      # plucked, no sustain
        "ampRelease": 0.30,
        "filterFreq": 115.0,    # within 4OSC's typical range
        "filterAmount": 0.45,
        "waveShape1": 3,        # triangle — softer than saw
        "tune1": 12.0,          # an octave up — chimes register
        "chorusOn": 1,
        "chorusMix": 0.55,
    })
    plugins.plugin_add_reverb({
        "track_id": arp, "type": "plate",
        "size": 0.55, "decay": 0.40, "mix": 0.30,
    })
    arp_clip = midi.midi_clip_add({
        "track_id": arp, "name": "Arp",
        "start_beats": 0, "length_beats": bars * 4,
    })["clip_id"]
    midi.midi_notes_add({"clip_id": arp_clip, "notes": _synthwave_arp(bars)})

    # 7. Lead — the singable hook. Saw lead with plate verb, only plays in
    # chorus2 (intro of motif) and chorusFinal (full motif at the climax).
    lead = tracks.track_add({"name": "Lead", "kind": "midi"})["track_id"]
    plugins.plugin_add({"track_id": lead, "type": "4OSC", "name": "Lead Synth"})
    _apply_preset_to_track(lead, "4OSC", "4OSC Bell Pad WMF.trkpreset", overrides={
        "ampAttack": 0.015,
        "ampDecay": 0.30,
        "ampSustain": 78.0,
        "ampRelease": 0.55,
        "filterFreq": 108.0,
        "filterAmount": 0.50,
        "filterResonance": 0.12,
        "waveShape1": 2,        # saw — classic synthwave lead timbre
        "tune1": 0.0,
        "chorusOn": 1, "chorusMix": 0.45,
        "voicesDetune": 0.18,
    })
    plugins.plugin_add_reverb({
        "track_id": lead, "type": "plate",
        "size": 0.55, "decay": 0.45, "mix": 0.28,
        "pre_delay": 0.03, "high_damp": 0.55, "high_cut": 0.85,
    })
    lead_clip = midi.midi_clip_add({
        "track_id": lead, "name": "Lead",
        "start_beats": 0, "length_beats": bars * 4,
    })["clip_id"]
    midi.midi_notes_add({"clip_id": lead_clip, "notes": _synthwave_lead(bars)})

    # ---- Mix balance via the MCP reference table ----
    tracks.mix_apply_reference({"track_id": drums,   "genre": "synthwave", "role": "drums"})
    tracks.mix_apply_reference({"track_id": bass,    "genre": "synthwave", "role": "bass"})
    tracks.mix_apply_reference({"track_id": pad,     "genre": "synthwave", "role": "pad",     "pan": -0.18})
    tracks.mix_apply_reference({"track_id": padB,    "genre": "synthwave", "role": "pad",     "pan":  0.18, "offset_db": -3.0})
    tracks.mix_apply_reference({"track_id": counter, "genre": "synthwave", "role": "counter", "pan":  0.08})
    tracks.mix_apply_reference({"track_id": arp,     "genre": "synthwave", "role": "arp",     "pan":  0.15})
    tracks.mix_apply_reference({"track_id": lead,    "genre": "synthwave", "role": "lead",    "pan": -0.05})

    # ---- Outro fades via clip-level fade_out_beats ----
    # Volume AUTOMATIONCURVE is unreliable (silences tracks in Waveform);
    # clip-level fades are proven to work. Apply fade_out_beats to the last
    # bars of pad / counter / arp clips so the song actually dissolves.
    # Bass intentionally has NO note generator output during outro, so it
    # ends naturally without needing automation.
    from . import clips as clips_tools
    outro_bars = next((s["bars"] for s in SYNTHWAVE_ARRANGEMENT if s["name"] == "outro"), 8)
    pad_fade_beats = float(outro_bars * 4)        # pad fades the entire outro (rings the chord out)
    counter_fade_beats = float(outro_bars * 4)
    arp_fade_beats = float((outro_bars - 2) * 4)  # arp fades faster — out by last 2 bars

    clips_tools.clip_set({"clip_id": pad_clip,     "fade_out_beats": pad_fade_beats})
    clips_tools.clip_set({"clip_id": padB_clip,    "fade_out_beats": pad_fade_beats})
    clips_tools.clip_set({"clip_id": counter_clip, "fade_out_beats": counter_fade_beats})
    clips_tools.clip_set({"clip_id": arp_clip,     "fade_out_beats": arp_fade_beats})

    # ---- Tempo automation ----
    # Subtle climb across the bridge into chorusFinal for climax energy,
    # then back to base for the outro. +2 BPM is small enough to not feel
    # jarring but big enough that listeners feel the lift.
    bridge_start_beat = float(_synth_section_start("bridge") * 4)
    chorus_final_start_beat = float(_synth_section_start("chorusFinal") * 4)
    outro_start_beat = float(_synth_section_start("outro") * 4)
    STORE.edit.tempo_changes = [
        (bridge_start_beat, tempo + 1.0),
        (chorus_final_start_beat, tempo + 2.0),
        (outro_start_beat, tempo),
    ]

    # ---- Section markers ----
    # Drop a marker at the start of every section so the Waveform timeline
    # shows section labels for navigation.
    for sec in SYNTHWAVE_ARRANGEMENT:
        start_bar = _synth_section_start(sec["name"])
        tracks.marker_add({
            "name": sec["name"],
            "start_beats": float(start_bar * 4),
            "length_beats": float(sec["bars"] * 4),
        })

    # ---- Sidechain pump on pad via filterFreq automation ----
    # plugin/<plugin_id>/<param> automation works because it routes to the
    # plugin's element directly (NOT the broken volume-plugin path). We pump
    # the pad's filter open on each downbeat → fast close → so the pad
    # "breathes" with the kick. Across the chorus regions only.
    pad_4osc_id = next(p.id for p in pad_track_obj.plugins if p.type == "4OSC")
    pump_target = f"plugin/{pad_4osc_id}/filterFreq"
    pump_points: list[dict] = []
    chorus_sec_starts = [
        _synth_section_start("chorus"),
        _synth_section_start("chorus2"),
        _synth_section_start("chorusFinal"),
    ]
    for sec_start_bar in chorus_sec_starts:
        # 8 bars × 4 beats = 32 beats per chorus section
        for beat in range(32):
            kick_beat = float(sec_start_bar * 4 + beat)
            # Each kick: filter open at 110, close to 70 over the next half-beat
            pump_points.append({"beat": kick_beat, "value": 110.0, "curve": 0.0})
            pump_points.append({"beat": kick_beat + 0.45, "value": 70.0, "curve": 0.0})
    if pump_points:
        from . import automation as automation_tools
        automation_tools.automation_add({
            "track_id": pad, "target": pump_target, "points": pump_points,
        })

    # ---- Master FX: tamer hi shelf ----
    edit = STORE.edit
    edit.master_chain.append(Plugin(
        id=_id("plg"), type="3 Band EQ", name="Master EQ",
        params={
            "loFreq": 80.0, "loGain": 1.0,         # tight low end, slight lift
            "midFreq": 500.0, "midGain": -1.0,
            "midQ": 0.8,
            "hiFreq": 12000.0, "hiGain": 1.0,      # was +2.5 @ 10k — too bright; backed off
        },
    ))
    edit.master_chain.append(Plugin(
        id=_id("plg"), type="Compressor", name="Master Glue",
        params={
            "threshold": -10.0, "ratio": 2.2,
            "attack": 12.0, "release": 180.0,
            "knee": 6.0, "outputDb": 0.5,
        },
    ))
    edit.master_chain.append(Plugin(
        id=_id("plg"), type="Limiter", name="Master Limiter",
        params={"threshold": -1.0, "release": 50.0},
    ))
    edit.master_volume_db = 1.0

    # Save
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    edit_tools.edit_save({"path": out_path})

    return {
        "ok": True,
        "path": out_path,
        "tempo": tempo,
        "bars": bars,
        "tracks": [t.name for t in STORE.edit.tracks],
        "total_notes": sum(
            (c.get("note_count") or 0)
            for t in STORE.edit.summary()["tracks"]
            for c in t.get("clips", [])
        ),
    }


TOOLS.append({
    "name": "compose_synthwave_track",
    "description": (
        "Generate a 64-bar synthwave instrumental .tracktionedit at 110 BPM "
        "in A minor. Properly developed song form: intro → verseA → chorus → "
        "verseB (variation) → chorus2 (with counter) → bridge (new chord set "
        "+ walking bass + descending arp) → chorusFinal (peak) → outro "
        "(progressive instrument drop with pad chord ring-out). Per-section "
        "chord progressions, bass feels (verse=half-time, chorus=8th pump, "
        "bridge=walking), and arp themes (verse / verseB syncopated / "
        "chorus pump / bridge descending). Showcases the full MCP toolset."
    ),
    "inputSchema": {
        "type": "object",
        "required": ["out_path"],
        "properties": {
            "out_path": {"type": "string"},
            "tempo": {"type": "number", "default": 110.0},
            "seed": {"type": "integer", "default": 0,
                     "description": "RNG seed for variation reproducibility."},
        },
    },
})


# ============================================================================
# Generic primitive-based composer — the "this is what the architecture
# unlocks" demonstration. Plans an arrangement, generates a chord
# progression, voices it with voice-leading, then for each section calls
# drum_pattern + bassline_generate + arp_pattern + melody_generate +
# motif_develop. Can produce any genre our drum library supports.
# ============================================================================


def compose_from_primitives(args: dict) -> dict:
    """Build a song bottom-up from composition primitives. Demonstrates the
    full T1 stack: arrangement_plan → progression_generate → voice_lead →
    drum_pattern → bassline_generate → arp_pattern → melody_generate +
    motif_develop. Sections drive velocity scaling via their `energy` value
    from arrangement_plan.

    The result is intentionally simpler-sounding than compose_synthwave_track
    (which has hand-tuned details) — this composer is about being a clear,
    short, primitive-driven REFERENCE for how the LLM should build songs.
    """
    out_path = args["out_path"]
    genre = args.get("genre", "synthwave")
    target_seconds = float(args.get("target_seconds", 100.0))
    tempo = float(args.get("tempo", 110.0))
    root = args.get("root", "A")
    mode = args.get("mode", "natural_minor")
    energy_curve = args.get("energy_curve", "slow_burn")
    seed = int(args.get("seed", 0))
    random.seed(seed)

    from . import composition as comp_tools
    from . import melody as melody_tools
    from . import drum_patterns as drum_tools
    from .. import music_theory_data as data

    # 1) Plan the arrangement
    plan = comp_tools.arrangement_plan({
        "genre": genre, "target_length_seconds": target_seconds,
        "tempo": tempo, "energy_curve": energy_curve,
    })
    sections = plan["sections"]
    total_bars = plan["total_bars"]

    # 2) Generate ONE chord progression and re-use it across the song
    n_chords = 4
    prog = comp_tools.progression_generate({
        "root": root, "mode": mode, "length_chords": n_chords,
        "style": "synthwave" if genre == "synthwave" else "pop",
        "end_cadence": "authentic", "octave": 3, "seed": seed,
    })
    chords = prog["chords"]
    bass_roots = [c["root_midi"] for c in chords]

    # Voice-lead the chord voicings so pad motion is smooth
    voicings: list[list[int]] = []
    initial_voicing = [chords[0]["chord_pitches"][0] + 12,    # tonic up an octave
                       chords[0]["chord_pitches"][1] + 12,
                       chords[0]["chord_pitches"][2] + 12,
                       chords[0]["chord_pitches"][2] + 24]   # 5th two octaves up for top voice
    voicings.append(initial_voicing)
    for c in chords[1:]:
        led = comp_tools.voice_lead({"prev_voicing": voicings[-1],
                                     "next_chord": c["chord_pitches"]})
        voicings.append(led["voicing"])

    # 3) Create the edit
    edit_tools.edit_create({
        "name": f"Primitive {genre.title()}",
        "tempo": tempo, "time_sig_num": 4, "time_sig_denom": 4,
        "key_root": NOTE_NAMES_MAP.get(root, 0),
        "key_mode": "minor" if "minor" in mode else "major",
        "length_beats": total_bars * 4 + 4,
    })

    # 4) Section markers (free quality-of-life)
    section_starts: dict[str, int] = {}
    cursor = 0
    for s in sections:
        section_starts[s["name"]] = cursor
        tracks.marker_add({"name": s["name"],
                           "start_beats": float(cursor * 4),
                           "length_beats": float(s["bars"] * 4)})
        cursor += s["bars"]

    # 4b) Populate Edit.progression so chord-chart export works.
    # Each section repeats the progression once across its bars.
    from ..model import ChordSymbol
    progression: list[ChordSymbol] = []
    for s in sections:
        sec_start_beat = section_starts[s["name"]] * 4
        chord_dur_beats = (s["bars"] * 4) / len(chords)
        for ci, c in enumerate(chords):
            chord_pc = c["root_midi"] % 12
            note_letter = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"][chord_pc]
            chord_name = note_letter + ("m" if c["quality"].startswith("min") else
                                         "" if c["quality"].startswith("maj") else "°")
            progression.append(ChordSymbol(
                name=chord_name, roman=c["roman"],
                start_beats=float(sec_start_beat + ci * chord_dur_beats),
                length_beats=float(chord_dur_beats),
                root_midi=c["root_midi"],
            ))
    STORE.edit.progression = progression

    # 5) Drums — one drum_pattern call per section
    drums = tracks.track_add({"name": "Drums", "kind": "midi"})["track_id"]
    plugins.plugin_add({"track_id": drums, "type": "Sampler", "name": "Kit"})
    drum_track = next(t for t in STORE.edit.tracks if t.id == drums)
    drum_track.plugins[-1].sounds = _drum_kit_sounds()
    drum_clip = midi.midi_clip_add({"track_id": drums, "name": "Drums",
                                    "start_beats": 0, "length_beats": total_bars * 4})["clip_id"]
    drum_notes: list[dict] = []
    for s in sections:
        # Map section role to drum-library role
        drum_role = s["role"] if s["role"] in ("verse", "chorus", "buildup",
                                                "breakdown", "outro", "intro") else "verse"
        if s["role"] == "intro":
            continue   # let intro be drumless; drums kick in at first verse
        if s["role"] == "outro":
            continue
        ds = drum_tools.drum_pattern({
            "genre": genre, "role": drum_role, "length_bars": s["bars"],
            "density": s["energy"],
            "start_beats": float(section_starts[s["name"]] * 4),
            "humanize": True, "seed": seed,
        })
        drum_notes.extend(ds["notes"])
    midi.midi_notes_add({"clip_id": drum_clip, "notes": drum_notes})

    # 6) Bass — one bassline_generate call per section
    bass = tracks.track_add({"name": "Bass", "kind": "midi"})["track_id"]
    plugins.plugin_add({"track_id": bass, "type": "4OSC", "name": "Bass"})
    _apply_preset_to_track(bass, "4OSC", "4OSC Smack Bass WMF.trkpreset",
                           overrides={"tune1": 0.0, "ampSustain": 70.0,
                                      "filterFreq": 100.0, "filterAmount": 0.5,
                                      "masterLevel": 0.0})
    bass_clip = midi.midi_clip_add({"track_id": bass, "name": "Bass",
                                    "start_beats": 0, "length_beats": total_bars * 4})["clip_id"]
    bass_notes: list[dict] = []
    for s in sections:
        if s["role"] in ("intro", "outro", "breakdown"):
            continue
        # Choose feel based on role — verse half-time, chorus pump, bridge walking
        feel_map = {"verse": "half_time", "chorus": "pump",
                     "bridge": "walking", "buildup": "pump",
                     "prechorus": "pump", "drop": "pump"}
        feel = feel_map.get(s["role"], "pump")
        bs = melody_tools.bassline_generate({
            "roots": bass_roots,
            "chord_duration_beats": (s["bars"] * 4) / len(bass_roots),
            "feel": feel,
            "start_beats": float(section_starts[s["name"]] * 4),
            "scale_intervals": data.SCALES.get(mode, data.SCALES["major"]),
        })
        bass_notes.extend(bs["notes"])
    midi.midi_notes_add({"clip_id": bass_clip, "notes": bass_notes})

    # 7) Pad — voiced chords sustained per section
    pad = tracks.track_add({"name": "Pad", "kind": "midi"})["track_id"]
    plugins.plugin_add({"track_id": pad, "type": "4OSC", "name": "Pad"})
    _apply_preset_to_track(pad, "4OSC", "4OSC Vox Pad WMF.trkpreset",
                           overrides={"ampAttack": 0.5, "ampRelease": 1.5,
                                      "filterFreq": 92.0, "filterAmount": 0.3,
                                      "masterLevel": 1.0})
    pad_clip = midi.midi_clip_add({"track_id": pad, "name": "Pad",
                                   "start_beats": 0, "length_beats": total_bars * 4})["clip_id"]
    pad_notes: list[dict] = []
    for s in sections:
        sec_start_bar = section_starts[s["name"]]
        chord_dur_bars = s["bars"] / len(voicings)
        for ci, voicing in enumerate(voicings):
            t = float((sec_start_bar + ci * chord_dur_bars) * 4)
            length = float(chord_dur_bars * 4 - 0.05)
            for pitch in voicing:
                pad_notes.append({"pitch": pitch, "start_beats": t,
                                  "length_beats": length,
                                  "velocity": int(60 + 20 * s["energy"])})
    midi.midi_notes_add({"clip_id": pad_clip, "notes": pad_notes})

    # 8) Arp — chorus-style sections only
    arp = tracks.track_add({"name": "Arp", "kind": "midi"})["track_id"]
    plugins.plugin_add({"track_id": arp, "type": "4OSC", "name": "Arp"})
    _apply_preset_to_track(arp, "4OSC", "4OSC Bell Pad WMF.trkpreset",
                           overrides={"ampAttack": 0.001, "ampDecay": 0.18,
                                      "ampSustain": 0.0, "ampRelease": 0.25,
                                      "filterFreq": 110.0, "filterAmount": 0.45,
                                      "waveShape1": 3, "tune1": 12.0,
                                      "chorusOn": 1, "chorusMix": 0.5})
    plugins.plugin_add_reverb({"track_id": arp, "type": "plate",
                                "size": 0.5, "decay": 0.4, "mix": 0.25})
    arp_clip = midi.midi_clip_add({"track_id": arp, "name": "Arp",
                                   "start_beats": 0, "length_beats": total_bars * 4})["clip_id"]
    arp_notes: list[dict] = []
    for s in sections:
        if s["role"] not in ("chorus", "drop", "buildup"):
            continue
        sec_start_bar = section_starts[s["name"]]
        chord_dur_bars = s["bars"] / len(voicings)
        for ci, voicing in enumerate(voicings):
            ap = melody_tools.arp_pattern({
                "chord_pitches": voicing,
                "length_beats": chord_dur_bars * 4,
                "rate": "sixteenth", "direction": "up_down", "octaves": 2,
                "start_beats": float((sec_start_bar + ci * chord_dur_bars) * 4),
                "velocity_base": int(60 + 20 * s["energy"]),
                "accent_downbeats": True,
            })
            arp_notes.extend(ap["notes"])
    midi.midi_notes_add({"clip_id": arp_clip, "notes": arp_notes})

    # 9) Lead melody — peak section only, with motif development.
    # Build one motif, repeat with augment/transpose variations across the
    # peak section.
    peak_section = next((s for s in sections if s["role"] in ("chorus", "drop")
                         and s["energy"] >= 0.85), None)
    if peak_section:
        lead = tracks.track_add({"name": "Lead", "kind": "midi"})["track_id"]
        plugins.plugin_add({"track_id": lead, "type": "4OSC", "name": "Lead"})
        _apply_preset_to_track(lead, "4OSC", "4OSC Bell Pad WMF.trkpreset",
                               overrides={"ampAttack": 0.02, "ampDecay": 0.3,
                                          "ampSustain": 75.0, "ampRelease": 0.5,
                                          "filterFreq": 110.0, "filterAmount": 0.55,
                                          "waveShape1": 2, "tune1": 0.0,
                                          "chorusOn": 1, "chorusMix": 0.4})
        plugins.plugin_add_reverb({"track_id": lead, "type": "plate",
                                    "size": 0.55, "decay": 0.45, "mix": 0.30})
        lead_clip = midi.midi_clip_add({"track_id": lead, "name": "Lead",
                                        "start_beats": 0,
                                        "length_beats": total_bars * 4})["clip_id"]
        # Build a 2-bar motif around the chord's 5th, two octaves up
        motif_root = chords[0]["root_midi"] + 24
        motif = melody_tools.melody_generate({
            "root": root, "mode": mode, "length_beats": 8.0,
            "contour": "arch", "density": "medium",
            "register_octave": 5, "seed": seed,
        })["notes"]
        # Repeat motif across peak section with variations
        peak_start = section_starts[peak_section["name"]]
        lead_notes: list[dict] = []
        for rep in range(peak_section["bars"] // 2):
            t_offset = float((peak_start + rep * 2) * 4)
            if rep == 0:
                shifted = [{**n, "start_beats": n["start_beats"] + t_offset} for n in motif]
            elif rep == 1:
                # Sequence up
                shifted = melody_tools.motif_develop({
                    "notes": motif, "transformation": "sequence_up",
                    "semitones": 2, "offset_beats": t_offset,
                })["notes"][:len(motif)]   # take only first half
            elif rep % 2 == 0:
                shifted = melody_tools.motif_develop({
                    "notes": motif, "transformation": "transpose",
                    "semitones": 0, "offset_beats": t_offset,
                })["notes"]
            else:
                shifted = melody_tools.motif_develop({
                    "notes": motif, "transformation": "invert",
                    "axis_pitch": motif[0]["pitch"], "offset_beats": t_offset,
                })["notes"]
            lead_notes.extend(shifted)
        midi.midi_notes_add({"clip_id": lead_clip, "notes": lead_notes})

    # 10) Mix balance via reference table
    tracks.mix_apply_reference({"track_id": drums, "genre": genre, "role": "drums"})
    tracks.mix_apply_reference({"track_id": bass,  "genre": genre, "role": "bass"})
    tracks.mix_apply_reference({"track_id": pad,   "genre": genre, "role": "pad", "pan": -0.10})
    tracks.mix_apply_reference({"track_id": arp,   "genre": genre, "role": "arp", "pan":  0.15})
    if peak_section:
        tracks.mix_apply_reference({"track_id": lead, "genre": genre, "role": "lead"})

    # 11) Master chain via template
    from . import workflow as wf_tools
    wf_tools.master_chain_apply({"template": "clean_pop"})

    # Save
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    edit_tools.edit_save({"path": out_path})
    summary = STORE.edit.summary()

    return {
        "ok": True, "path": out_path,
        "tempo": tempo, "bars": total_bars,
        "tracks": [t["name"] for t in summary["tracks"]],
        "total_notes": sum((c.get("note_count") or 0) for t in summary["tracks"] for c in t.get("clips", [])),
        "progression": [c["roman"] for c in chords],
        "sections": [{"name": s["name"], "bars": s["bars"], "energy": s["energy"]} for s in sections],
    }


NOTE_NAMES_MAP = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4,
                  "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8,
                  "A": 9, "A#": 10, "Bb": 10, "B": 11}


TOOLS.append({
    "name": "compose_from_primitives",
    "description": (
        "Build a song bottom-up from composition primitives — the recipe "
        "demonstrating arrangement_plan + progression_generate + voice_lead "
        "+ drum_pattern + bassline_generate + arp_pattern + melody_generate "
        "+ motif_develop wired together. Genre selects drum patterns, bass "
        "feels, and mix-balance reference. energy_curve drives velocity "
        "scaling per section."
    ),
    "inputSchema": {
        "type": "object",
        "required": ["out_path"],
        "properties": {
            "out_path":       {"type": "string"},
            "genre":          {"type": "string", "default": "synthwave"},
            "target_seconds": {"type": "number", "default": 100.0},
            "tempo":          {"type": "number", "default": 110.0},
            "root":           {"type": "string", "default": "A"},
            "mode":           {"type": "string", "default": "natural_minor"},
            "energy_curve":   {"type": "string", "default": "slow_burn"},
            "seed":           {"type": "integer", "default": 0},
        },
    },
})


# ============================================================================
# Progressive metal / djent — heavy down-tuned guitars, polyrhythmic kicks,
# instrumental song with a guitar solo section.
# ============================================================================

# D MINOR riffs — pitches match the available power-chord samples natively.
# Sample pitches available (MIDI): D2=38, D#2=39, F#2=42, G2=43, G#2=44, F3=53, D#3=51.
# Both palm-muted (PMute) and sustained variants for D2, D#2, F#2, G2, G#2, D#3.
# We use MIDI pitches as TRIGGERS that the Sampler maps to the right sample
# (palm-muted samples mapped to the lower octave, sustained to the upper).
# Convention used here:
#   38..44 = palm-muted samples (D2 PMute, D#2 PMute, F#2 PMute, G2 PMute, G#2 PMute)
#   50..53 = sustained samples (D#3, F3 — used for sustained chord stabs)
DJENT_RIFF_NOTES = {
    # Palm-mute chug riff in D — 3-3-2 polyrhythm with chromatic stabs
    "riffA": [
        # Bar 1: 16th palm-mute chugs on D with F# and G stabs
        (0.0,  38, 0.20, 115), (0.25, 38, 0.20, 95),
        (0.5,  38, 0.20, 105), (0.75, 42, 0.20, 110),   # F# stab (PMute)
        (1.0,  38, 0.20, 115), (1.25, 38, 0.20, 95),
        (1.5,  38, 0.20, 105), (1.75, 38, 0.20, 95),
        (2.0,  38, 0.20, 115), (2.25, 43, 0.20, 105),   # G stab (PMute)
        (2.5,  38, 0.20, 105), (2.75, 38, 0.20, 95),
        (3.0,  38, 0.20, 115), (3.25, 42, 0.20, 110),   # F# stab
        (3.5,  38, 0.20, 105), (3.75, 38, 0.20, 95),
    ],
    # Sustained power-chord stabs every 2 beats — uses sustained samples
    "riffB": [
        (0.0, 51, 1.85, 120),     # D#3 sustained (third interval up from D = D# in Phrygian)
        (2.0, 53, 1.85, 120),     # F3 sustained
    ],
    # Breakdown: huge sustained crush + palm-mute fills
    "breakdown": [
        (0.0, 51, 0.45, 127),     # D#3 sustained crush
        (0.5, 38, 0.20, 105), (0.75, 38, 0.20, 105),
        (1.5, 38, 0.20, 105),
        (2.0, 53, 0.45, 127),     # F3 sustained
        (2.5, 38, 0.20, 105), (2.75, 38, 0.20, 105),
        (3.5, 38, 0.20, 105),
    ],
}


def _djent_rhythm_guitar(bars: int, sections: list[dict]) -> list[dict]:
    """Rhythm guitar pattern that adapts to section role."""
    notes: list[dict] = []
    cursor = 0
    for sec in sections:
        sec_start_beats = cursor * 4
        for bar_in_sec in range(sec["bars"]):
            b = sec_start_beats + bar_in_sec * 4
            # Pick pattern based on section
            if sec["role"] == "intro":
                # Sustain: hit B5 once and let it ring
                if bar_in_sec == 0:
                    notes.append({"pitch": 35, "start_beats": b, "length_beats": 16.0, "velocity": 100})
                    notes.append({"pitch": 42, "start_beats": b, "length_beats": 16.0, "velocity": 80})
                continue
            if sec["role"] == "buildup":
                # Tremolo-pick rising notes — 16ths on open B that crescendo
                for i in range(16):
                    notes.append({"pitch": 35, "start_beats": b + i * 0.25,
                                  "length_beats": 0.20, "velocity": int(80 + i * 2.5)})
                continue
            if sec["role"] == "breakdown":
                pattern = DJENT_RIFF_NOTES["breakdown"]
            elif sec["name"] == "riffB":
                pattern = DJENT_RIFF_NOTES["riffB"]
            else:
                pattern = DJENT_RIFF_NOTES["riffA"]
            for beat, pitch, length, vel in pattern:
                notes.append({"pitch": pitch, "start_beats": b + beat,
                              "length_beats": length, "velocity": vel})
        cursor += sec["bars"]
    return notes


def _djent_bass(bars: int, sections: list[dict]) -> list[dict]:
    """Bass follows the rhythm guitar root in D minor — D1 (MIDI 26) drone
    with breakdown chord stabs to F1 (29)."""
    notes: list[dict] = []
    cursor = 0
    D_ROOT = 26   # D1
    F_ROOT = 29   # F1 (minor 3rd up)
    for sec in sections:
        sec_start_beats = cursor * 4
        for bar_in_sec in range(sec["bars"]):
            b = sec_start_beats + bar_in_sec * 4
            if sec["role"] == "intro":
                if bar_in_sec == 0:
                    notes.append({"pitch": D_ROOT, "start_beats": b, "length_beats": 16.0, "velocity": 90})
                continue
            if sec["role"] == "buildup":
                for i in range(16):
                    notes.append({"pitch": D_ROOT, "start_beats": b + i * 0.25,
                                  "length_beats": 0.20, "velocity": int(80 + i * 2.5)})
                continue
            if sec["role"] == "breakdown":
                # Match guitar breakdown stabs
                notes.append({"pitch": D_ROOT, "start_beats": b + 0.0, "length_beats": 0.45, "velocity": 115})
                notes.append({"pitch": D_ROOT, "start_beats": b + 0.5, "length_beats": 0.20, "velocity": 100})
                notes.append({"pitch": D_ROOT, "start_beats": b + 0.75, "length_beats": 0.20, "velocity": 100})
                notes.append({"pitch": F_ROOT, "start_beats": b + 2.0, "length_beats": 0.45, "velocity": 115})
                notes.append({"pitch": F_ROOT, "start_beats": b + 2.5, "length_beats": 0.20, "velocity": 100})
                notes.append({"pitch": F_ROOT, "start_beats": b + 2.75, "length_beats": 0.20, "velocity": 100})
                continue
            # Verse / chorus — 16th-note chugs on D
            for beat in [0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75,
                         2.0, 2.25, 2.5, 2.75, 3.0, 3.25, 3.5, 3.75]:
                accent = beat in (0.0, 0.75, 1.5, 2.25, 3.0)
                notes.append({"pitch": D_ROOT, "start_beats": b + beat,
                              "length_beats": 0.18, "velocity": 110 if accent else 92})
        cursor += sec["bars"]
    return notes


def _djent_solo(bars: int, sections: list[dict]) -> list[dict]:
    """Lead guitar solo in D MINOR PENTATONIC over the 'solo' section.
    D minor pent: D-F-G-A-C, plus chromatic passing tones.
    Two octaves up: D4=62, F4=65, G4=67, A4=69, C5=72, D5=74, F5=77,
    G5=79, A5=81, C6=84, D6=86.
    """
    notes: list[dict] = []
    rng = random.Random(99)
    pent = [62, 65, 67, 69, 72, 74, 77, 79, 81, 84, 86]
    chromatic_extras = [63, 64, 66, 68, 71]

    solo_sec = next((s for s in sections if s["name"] == "solo"), None)
    if not solo_sec:
        return notes

    cursor_bars = 0
    for s in sections:
        if s["name"] == "solo":
            break
        cursor_bars += s["bars"]
    solo_start_beat = cursor_bars * 4

    # Phrase structure across solo:
    # Bars 1-2: opening — long bend-style hold then descend
    # Bars 3-4: ascending fast 16th-note run (climax buildup)
    # Bars 5-6: blues-bend style middle (mid-register motif + repeat varied)
    # Bars 7-8: peak — high bends + final fast lick
    solo_bars = solo_sec["bars"]

    # Phrase 1 (bars 0-1) — opening sustained bend
    notes.append({"pitch": 77, "start_beats": solo_start_beat + 0.0,
                  "length_beats": 2.5, "velocity": 110})   # F5 hold
    notes.append({"pitch": 81, "start_beats": solo_start_beat + 2.5,
                  "length_beats": 1.4, "velocity": 115})   # A5 reach
    # Bar 2: descent down the pentatonic
    descent = [86, 84, 81, 79, 77, 74, 72, 69]
    for i, p in enumerate(descent):
        notes.append({"pitch": p, "start_beats": solo_start_beat + 4.0 + i * 0.5,
                      "length_beats": 0.40, "velocity": 95 + (i % 2) * 8})

    # Phrase 2 (bars 3-4) — ascending 16th run with chromatic passing tones
    cursor = solo_start_beat + 8.0
    run = [62, 65, 67, 68, 69, 71, 72, 74, 77, 79, 81, 84, 86, 84, 81, 79]
    for i, p in enumerate(run):
        notes.append({"pitch": p, "start_beats": cursor + i * 0.25,
                      "length_beats": 0.22, "velocity": 95 + i * 1.5})
    # Bar 4 — held high D with vibrato
    cursor = solo_start_beat + 12.0
    for i in range(6):
        notes.append({"pitch": 86,
                      "start_beats": cursor + i * 0.5 + rng.uniform(-0.02, 0.02),
                      "length_beats": 0.45, "velocity": 110 + rng.randint(-8, 8)})
    notes.append({"pitch": 86, "start_beats": cursor + 3.0, "length_beats": 0.95, "velocity": 115})

    # Phrase 3 (bars 5-6) — call/response pentatonic
    cursor = solo_start_beat + 16.0
    if solo_bars >= 6:
        # Call: D5 → F5 → G5 → D5
        for i, (p, t_off) in enumerate([(74, 0.0), (77, 0.5), (79, 1.0), (74, 1.5)]):
            notes.append({"pitch": p, "start_beats": cursor + t_off,
                          "length_beats": 0.45, "velocity": 100 + i * 4})
        # Response: A5 → G5 → F5 → D5 → C5 (descending)
        for i, (p, t_off) in enumerate([(81, 2.0), (79, 2.4), (77, 2.7), (74, 3.0), (72, 3.4)]):
            notes.append({"pitch": p, "start_beats": cursor + t_off,
                          "length_beats": 0.30, "velocity": 100})
        # Bar 6 — tasty bend up to A5 (held)
        notes.append({"pitch": 81, "start_beats": cursor + 4.0,
                      "length_beats": 1.5, "velocity": 118})
        # Walking down chromatically
        chrom_walk = [79, 78, 77, 75, 74, 72, 71]
        for i, p in enumerate(chrom_walk):
            notes.append({"pitch": p, "start_beats": cursor + 5.5 + i * 0.25,
                          "length_beats": 0.22, "velocity": 95})

    # Phrase 4 (last 2 bars) — peak climax
    if solo_bars >= 8:
        cursor = solo_start_beat + (solo_bars - 2) * 4
        # Fast 32nd-note pull-off lick
        lick = [86, 84, 81, 79, 77, 79, 81, 84]
        for i, p in enumerate(lick):
            notes.append({"pitch": p, "start_beats": cursor + i * 0.125,
                          "length_beats": 0.12, "velocity": 110})
        # Climbing screaming finale
        notes.append({"pitch": 86, "start_beats": cursor + 1.0, "length_beats": 0.45, "velocity": 120})
        notes.append({"pitch": 89, "start_beats": cursor + 1.5, "length_beats": 0.45, "velocity": 122})  # F6
        notes.append({"pitch": 98, "start_beats": cursor + 2.0,    # D7 peak
                      "length_beats": 6.0, "velocity": 127})
        # Vibrato on held note
        for i in range(1, 5):
            notes.append({"pitch": 98, "start_beats": cursor + 2.0 + i,
                          "length_beats": 0.95, "velocity": 115 + (i % 2) * 8})
    return notes


def compose_djent_track(args: dict) -> dict:
    """Progressive metal / djent instrumental with a guitar solo.

    Form (64 bars at 110 BPM = ~2:20):
      intro (4)  → riffA (8)  → riffB (8)  → breakdown (8)  →
      riffA2 (8) → buildup (4) → solo (8)  → riffFinal (8)  → outro (4)

    Tracks:
      Drums          — metal kit with polyrhythmic 3-3-2 kicks + double-kick chorus
      Bass           — locked to guitar root (B1), 16th-note chugs
      Rhythm Guitar L— palm-mute riff, panned -0.7
      Rhythm Guitar R— palm-mute riff doubled, panned +0.7 (the wall)
      Lead Guitar    — solo over the 'solo' section in B minor pentatonic
      Pad            — dark sustained background (very quiet, atmosphere only)

    Production: heavy compression, EQ scoop at 500 Hz (mid scoop = "metal V"),
    high-shelf at 6 kHz cut to keep top from screaming.
    """
    out_path = args["out_path"]
    tempo = float(args.get("tempo", 110.0))
    seed = int(args.get("seed", 0))
    random.seed(seed)

    from . import composition as comp_tools
    from . import drum_patterns as drum_tools

    # Plan arrangement using the metal form
    plan = comp_tools.arrangement_plan({
        "genre": "metal",
        "target_length_seconds": 140,
        "tempo": tempo,
        "energy_curve": "build_drop_build",
    })
    sections = plan["sections"]
    total_bars = plan["total_bars"]

    edit_tools.edit_create({
        "name": "MCP Djent",
        "tempo": tempo, "time_sig_num": 4, "time_sig_denom": 4,
        "key_root": 2,            # D — matches the available power-chord samples
        "key_mode": "minor",
        "length_beats": total_bars * 4 + 4,
    })

    # Section markers
    cursor = 0
    for s in sections:
        tracks.marker_add({"name": s["name"],
                           "start_beats": float(cursor * 4),
                           "length_beats": float(s["bars"] * 4)})
        cursor += s["bars"]

    # 1. Drums — REAL metal samples (CC-BY freesound pack #9368 by xIceCoffeex)
    # plus the factory 808 closed hat for the missing closed-hat slot.
    drums = tracks.track_add({"name": "Drums", "kind": "midi"})["track_id"]
    plugins.plugin_add({"track_id": drums, "type": "Sampler", "name": "Metal Kit"})
    drum_track = next(t for t in STORE.edit.tracks if t.id == drums)
    metal_dir = (Path.home() / "AppData" / "Roaming" / "Tracktion" / "Waveform"
                 / "Factory Loops" / "Metal Samples")
    closed_hat_factory = _drum_kit_sounds()  # use factory for the closed hat slot
    closed_hat_sound = next((s for s in closed_hat_factory if s["keyNote"] == 42), None)
    drum_track.plugins[-1].sounds = [
        {"source": str(metal_dir / "kick.mp3"),
         "name": "Metal Kick", "keyNote": 36, "minNote": 36, "maxNote": 36,
         "startTime": 0.0, "gainDb": 0.0, "pan": 0.0, "openEnded": 1},
        {"source": str(metal_dir / "snare.mp3"),
         "name": "Metal Snare", "keyNote": 38, "minNote": 37, "maxNote": 39,
         "startTime": 0.0, "gainDb": 0.0, "pan": 0.0, "openEnded": 1},
        # Closed hat slot uses 808 sample (no closed hat in metal pack)
        closed_hat_sound if closed_hat_sound else
            {"source": str(metal_dir / "open_hh.mp3"),
             "name": "Hat (open as closed)", "keyNote": 42, "minNote": 40, "maxNote": 44,
             "startTime": 0.0, "gainDb": -2.0, "pan": 0.0, "openEnded": 0},
        {"source": str(metal_dir / "open_hh.mp3"),
         "name": "Open Hat", "keyNote": 46, "minNote": 45, "maxNote": 48,
         "startTime": 0.0, "gainDb": 0.0, "pan": 0.0, "openEnded": 1},
        {"source": str(metal_dir / "crash_right.mp3"),
         "name": "Crash", "keyNote": 49, "minNote": 49, "maxNote": 52,
         "startTime": 0.0, "gainDb": 0.0, "pan": 0.0, "openEnded": 1},
        {"source": str(metal_dir / "crash_mid.mp3"),
         "name": "Ride/Crash 2", "keyNote": 51, "minNote": 50, "maxNote": 53,
         "startTime": 0.0, "gainDb": -2.0, "pan": 0.05, "openEnded": 1},
    ]
    # Aggressive compression on drum bus
    plugins.compress_smash({"track_id": drums})
    drum_clip = midi.midi_clip_add({"track_id": drums, "name": "Drums",
                                    "start_beats": 0,
                                    "length_beats": total_bars * 4})["clip_id"]
    drum_notes = []
    cur_bar = 0
    for s in sections:
        role = s["role"]
        # Map "verse"-like sections to "verse" and "chorus"-like to "chorus";
        # solo section uses "chorus" pattern (driving). buildup, breakdown,
        # intro, outro are explicit.
        drum_role_map = {"verse": "verse", "chorus": "chorus",
                          "buildup": "buildup", "breakdown": "breakdown",
                          "intro": "intro", "outro": "outro"}
        drum_role = drum_role_map.get(role, "verse")
        if s["name"] == "solo":
            drum_role = "chorus"  # solo wants driving drums
        ds = drum_tools.drum_pattern({
            "genre": "metal", "role": drum_role, "length_bars": s["bars"],
            "density": s["energy"],
            "start_beats": float(cur_bar * 4),
            "humanize": True, "seed": seed,
        })
        drum_notes.extend(ds["notes"])
        cur_bar += s["bars"]
    midi.midi_notes_add({"clip_id": drum_clip, "notes": drum_notes})

    # 2. Bass — 4OSC saw bass with grit
    bass = tracks.track_add({"name": "Bass", "kind": "midi"})["track_id"]
    plugins.plugin_add({"track_id": bass, "type": "4OSC", "name": "Metal Bass"})
    _apply_preset_to_track(bass, "4OSC", "4OSC Smack Bass WMF.trkpreset", overrides={
        "ampAttack": 0.001, "ampDecay": 0.15, "ampSustain": 80.0, "ampRelease": 0.10,
        "filterFreq": 95.0, "filterAmount": 0.35, "filterResonance": 0.10,
        "tune1": 0.0, "masterLevel": 0.0,
    })
    plugins.compress_glue({"track_id": bass, "amount": 0.7})
    bass_clip = midi.midi_clip_add({"track_id": bass, "name": "Bass",
                                    "start_beats": 0, "length_beats": total_bars * 4})["clip_id"]
    midi.midi_notes_add({"clip_id": bass_clip, "notes": _djent_bass(total_bars, sections)})

    # 3 + 4. Rhythm Guitars — REAL distorted-guitar samples via Sampler.
    # CC-BY freesound pack #14939 by Ax_Grinder. Two tracks, one panned hard
    # left and one hard right, both playing the same notes for the
    # double-tracked-guitar wall sound.
    def _build_metal_guitar_sounds() -> list[dict]:
        """Map MIDI triggers to power-chord samples.
          Palm-muted samples cover the chug pitches (38..46).
          Sustained samples cover the sustained-chord triggers (50..53).
        """
        return [
            # PALM-MUTED — chug-pitch range
            {"source": str(metal_dir / "D2_PMute.mp3"),
             "name": "D2 PM", "keyNote": 38, "minNote": 36, "maxNote": 38,
             "startTime": 0.0, "gainDb": 0.0, "openEnded": 1},
            {"source": str(metal_dir / "Dsharp2_PMute.mp3"),
             "name": "D#2 PM", "keyNote": 39, "minNote": 39, "maxNote": 39,
             "startTime": 0.0, "gainDb": 0.0, "openEnded": 1},
            {"source": str(metal_dir / "Fsharp2_PMute.mp3"),
             "name": "F#2 PM", "keyNote": 42, "minNote": 40, "maxNote": 42,
             "startTime": 0.0, "gainDb": 0.0, "openEnded": 1},
            {"source": str(metal_dir / "G2_PMute.mp3"),
             "name": "G2 PM", "keyNote": 43, "minNote": 43, "maxNote": 43,
             "startTime": 0.0, "gainDb": 0.0, "openEnded": 1},
            {"source": str(metal_dir / "Gsharp2_PMute.mp3"),
             "name": "G#2 PM", "keyNote": 44, "minNote": 44, "maxNote": 46,
             "startTime": 0.0, "gainDb": 0.0, "openEnded": 1},
            # SUSTAINED — chord-stab range
            {"source": str(metal_dir / "Dsharp3.mp3"),
             "name": "D#3 sus", "keyNote": 51, "minNote": 50, "maxNote": 52,
             "startTime": 0.0, "gainDb": -3.0, "openEnded": 1},
            {"source": str(metal_dir / "F3.mp3"),
             "name": "F3 sus", "keyNote": 53, "minNote": 53, "maxNote": 60,
             "startTime": 0.0, "gainDb": -3.0, "openEnded": 1},
        ]

    rg_l = tracks.track_add({"name": "Rhythm Guitar L", "kind": "midi"})["track_id"]
    plugins.plugin_add({"track_id": rg_l, "type": "Sampler", "name": "Guitar L"})
    rg_l_track = next(t for t in STORE.edit.tracks if t.id == rg_l)
    rg_l_track.plugins[-1].sounds = _build_metal_guitar_sounds()
    # The samples are already distorted — just add subtle metal-V EQ + glue comp
    rg_l_track.plugins.append(Plugin(
        id=_id("plg"), type="3 Band EQ", name="Metal V",
        params={"loFreq": 100.0, "loGain": 1.0,
                "midFreq": 500.0, "midGain": -3.0, "midQ": 0.7,
                "hiFreq": 4000.0, "hiGain": 1.5},
    ))
    rg_l_clip = midi.midi_clip_add({"track_id": rg_l, "name": "Riff",
                                     "start_beats": 0, "length_beats": total_bars * 4})["clip_id"]
    midi.midi_notes_add({"clip_id": rg_l_clip,
                          "notes": _djent_rhythm_guitar(total_bars, sections)})

    rg_r = tracks.track_add({"name": "Rhythm Guitar R", "kind": "midi"})["track_id"]
    plugins.plugin_add({"track_id": rg_r, "type": "Sampler", "name": "Guitar R"})
    rg_r_track = next(t for t in STORE.edit.tracks if t.id == rg_r)
    rg_r_track.plugins[-1].sounds = _build_metal_guitar_sounds()
    rg_r_track.plugins.append(Plugin(
        id=_id("plg"), type="3 Band EQ", name="Metal V",
        params={"loFreq": 100.0, "loGain": 1.0,
                "midFreq": 500.0, "midGain": -3.0, "midQ": 0.7,
                "hiFreq": 4000.0, "hiGain": 1.5},
    ))
    rg_r_clip = midi.midi_clip_add({"track_id": rg_r, "name": "Riff",
                                     "start_beats": 0, "length_beats": total_bars * 4})["clip_id"]
    midi.midi_notes_add({"clip_id": rg_r_clip,
                          "notes": _djent_rhythm_guitar(total_bars, sections)})

    # 5. Lead Guitar — bright saw with feedback-like sustain + plate verb
    lead = tracks.track_add({"name": "Lead Guitar", "kind": "midi"})["track_id"]
    plugins.plugin_add({"track_id": lead, "type": "4OSC", "name": "Lead"})
    _apply_preset_to_track(lead, "4OSC", "4OSC Bell Pad WMF.trkpreset", overrides={
        "ampAttack": 0.005, "ampDecay": 0.6, "ampSustain": 90.0, "ampRelease": 0.8,
        "filterFreq": 115.0, "filterAmount": 0.55, "filterResonance": 0.30,
        "waveShape1": 2,    # saw
        "tune1": 0.0,
        "chorusOn": 1, "chorusMix": 0.40,
        "masterLevel": -2.0,
        "voicesDetune": 0.20,
    })
    plugins.plugin_add_reverb({"track_id": lead, "type": "plate",
                                "size": 0.55, "decay": 0.50, "mix": 0.30,
                                "high_damp": 0.55})
    lead_track = next(t for t in STORE.edit.tracks if t.id == lead)
    lead_track.plugins.append(Plugin(
        id=_id("plg"), type="3 Band EQ", name="Lead Boost",
        params={"loFreq": 250.0, "loGain": -6.0,             # cut low end mud
                "midFreq": 2500.0, "midGain": 4.0, "midQ": 0.8,   # presence boost
                "hiFreq": 6000.0, "hiGain": 2.0},
    ))
    lead_clip = midi.midi_clip_add({"track_id": lead, "name": "Solo",
                                     "start_beats": 0, "length_beats": total_bars * 4})["clip_id"]
    midi.midi_notes_add({"clip_id": lead_clip, "notes": _djent_solo(total_bars, sections)})

    # 6. Atmospheric pad — dark, very quiet, just texture
    pad = tracks.track_add({"name": "Pad", "kind": "midi"})["track_id"]
    plugins.plugin_add({"track_id": pad, "type": "4OSC", "name": "Dark Pad"})
    _apply_preset_to_track(pad, "4OSC", "4OSC Vox Pad WMF.trkpreset", overrides={
        "ampAttack": 1.5, "ampRelease": 2.0,
        "filterFreq": 75.0, "filterAmount": 0.20,
    })
    pad_clip = midi.midi_clip_add({"track_id": pad, "name": "Atmosphere",
                                    "start_beats": 0, "length_beats": total_bars * 4})["clip_id"]
    # Single low B drone the whole song
    midi.midi_notes_add({"clip_id": pad_clip,
                          "notes": [{"pitch": 47, "start_beats": 0,
                                     "length_beats": float(total_bars * 4),
                                     "velocity": 65}]})

    # ---- Mix balance from the metal reference ----
    tracks.mix_apply_reference({"track_id": drums, "genre": "metal", "role": "drums"})
    tracks.mix_apply_reference({"track_id": bass,  "genre": "metal", "role": "bass"})
    tracks.mix_apply_reference({"track_id": rg_l,  "genre": "metal", "role": "rhythm_guitar_l", "pan": -0.65})
    tracks.mix_apply_reference({"track_id": rg_r,  "genre": "metal", "role": "rhythm_guitar_r", "pan":  0.65})
    tracks.mix_apply_reference({"track_id": lead,  "genre": "metal", "role": "lead",            "pan":  0.0})
    tracks.mix_apply_reference({"track_id": pad,   "genre": "metal", "role": "pad"})

    # Master chain — loud-edm-ish (heavy compression + bright limiter)
    from . import workflow as wf_tools
    wf_tools.master_chain_apply({"template": "loud_edm"})

    # Save
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    edit_tools.edit_save({"path": out_path})

    summary = STORE.edit.summary()
    return {
        "ok": True, "path": out_path, "tempo": tempo, "bars": total_bars,
        "tracks": [t["name"] for t in summary["tracks"]],
        "sections": [{"name": s["name"], "bars": s["bars"]} for s in sections],
        "total_notes": sum((c.get("note_count") or 0) for t in summary["tracks"] for c in t.get("clips", [])),
    }


TOOLS.append({
    "name": "compose_djent_track",
    "description": (
        "Generate a progressive metal / djent instrumental in B minor. "
        "Form: intro → riffA → riffB → breakdown → riffA2 → buildup → "
        "solo (8 bars) → riffFinal → outro. Tracks: drums (polyrhythmic "
        "3-3-2 kicks), bass, dual rhythm guitars panned hard L/R for the "
        "wall, lead solo over solo section, atmospheric pad. ~140 seconds "
        "at 110 BPM."
    ),
    "inputSchema": {
        "type": "object",
        "required": ["out_path"],
        "properties": {
            "out_path": {"type": "string"},
            "tempo": {"type": "number", "default": 110.0},
            "seed":  {"type": "integer", "default": 0},
        },
    },
})


def compose_djent_real(args: dict) -> dict:
    """Djent track using REAL VST3 instruments and amp simulators —
    not synth-pretending-to-be-guitars.

    Signal chain per rhythm guitar track:
      sforzando (Karoryfer Emilyguitar SFZ) → The Anvil drive →
      MT-A (Mercuriall Metal Area amp) → TSC cabinet sim

    Same form/notes/drums/bass as compose_djent_track but the rhythm
    guitar signal is now: clean multi-articulation sampled guitar +
    real amp & cab modeling, the way real metal producers track guitars.

    REQUIREMENT: After opening the project in Waveform, double-click
    each rhythm guitar's sforzando plugin and load:
        C:/Users/jarms/AppData/Roaming/Tracktion/Waveform/
        Factory Loops/SFZ/Emilyguitar/emily_basic.sfz
    (one-time per session — sforzando state isn't saved in .tracktionedit
    state we can write yet).
    """
    out_path = args["out_path"]
    tempo = float(args.get("tempo", 110.0))
    seed = int(args.get("seed", 0))
    random.seed(seed)

    from . import composition as comp_tools
    from . import drum_patterns as drum_tools

    # Same arrangement as compose_djent_track
    plan = comp_tools.arrangement_plan({
        "genre": "metal", "target_length_seconds": 140,
        "tempo": tempo, "energy_curve": "build_drop_build",
    })
    sections = plan["sections"]
    total_bars = plan["total_bars"]

    edit_tools.edit_create({
        "name": "MCP Djent (Real VSTs)",
        "tempo": tempo, "time_sig_num": 4, "time_sig_denom": 4,
        "key_root": 2, "key_mode": "minor",
        "length_beats": total_bars * 4 + 4,
    })

    cursor = 0
    for s in sections:
        tracks.marker_add({"name": s["name"], "start_beats": float(cursor * 4),
                            "length_beats": float(s["bars"] * 4)})
        cursor += s["bars"]

    # 1. Drums (same as before)
    drums = tracks.track_add({"name": "Drums", "kind": "midi"})["track_id"]
    plugins.plugin_add({"track_id": drums, "type": "Sampler", "name": "Metal Kit"})
    drum_track = next(t for t in STORE.edit.tracks if t.id == drums)
    metal_dir = (Path.home() / "AppData" / "Roaming" / "Tracktion" / "Waveform"
                 / "Factory Loops" / "Metal Samples")
    closed_hat_factory = _drum_kit_sounds()
    closed_hat_sound = next((s for s in closed_hat_factory if s["keyNote"] == 42), None)
    drum_track.plugins[-1].sounds = [
        {"source": str(metal_dir / "kick.mp3"),
         "name": "Kick", "keyNote": 36, "minNote": 36, "maxNote": 36,
         "startTime": 0.0, "openEnded": 1},
        {"source": str(metal_dir / "snare.mp3"),
         "name": "Snare", "keyNote": 38, "minNote": 37, "maxNote": 39,
         "startTime": 0.0, "openEnded": 1},
        closed_hat_sound,
        {"source": str(metal_dir / "open_hh.mp3"),
         "name": "Open Hat", "keyNote": 46, "minNote": 45, "maxNote": 48,
         "startTime": 0.0, "openEnded": 1},
        {"source": str(metal_dir / "crash_right.mp3"),
         "name": "Crash", "keyNote": 49, "minNote": 49, "maxNote": 52,
         "startTime": 0.0, "openEnded": 1},
        {"source": str(metal_dir / "crash_mid.mp3"),
         "name": "Ride", "keyNote": 51, "minNote": 50, "maxNote": 53,
         "startTime": 0.0, "gainDb": -2.0, "openEnded": 1},
    ]
    plugins.compress_smash({"track_id": drums})
    drum_clip = midi.midi_clip_add({"track_id": drums, "name": "Drums",
                                    "start_beats": 0,
                                    "length_beats": total_bars * 4})["clip_id"]
    drum_notes = []
    cur_bar = 0
    for s in sections:
        role_map = {"verse": "verse", "chorus": "chorus",
                    "buildup": "buildup", "breakdown": "breakdown",
                    "intro": "intro", "outro": "outro"}
        drum_role = role_map.get(s["role"], "verse")
        if s["name"] == "solo":
            drum_role = "chorus"
        ds = drum_tools.drum_pattern({"genre": "metal", "role": drum_role,
                                        "length_bars": s["bars"],
                                        "density": s["energy"],
                                        "start_beats": float(cur_bar * 4),
                                        "humanize": True, "seed": seed})
        drum_notes.extend(ds["notes"])
        cur_bar += s["bars"]
    midi.midi_notes_add({"clip_id": drum_clip, "notes": drum_notes})

    # 2. Bass
    bass = tracks.track_add({"name": "Bass", "kind": "midi"})["track_id"]
    plugins.plugin_add({"track_id": bass, "type": "4OSC", "name": "Bass"})
    _apply_preset_to_track(bass, "4OSC", "4OSC Smack Bass WMF.trkpreset", overrides={
        "ampAttack": 0.001, "ampDecay": 0.15, "ampSustain": 80.0, "ampRelease": 0.10,
        "filterFreq": 95.0, "filterAmount": 0.35, "filterResonance": 0.10,
        "tune1": 0.0, "masterLevel": 0.0,
    })
    plugins.compress_glue({"track_id": bass, "amount": 0.7})
    bass_clip = midi.midi_clip_add({"track_id": bass, "name": "Bass",
                                    "start_beats": 0, "length_beats": total_bars * 4})["clip_id"]
    midi.midi_notes_add({"clip_id": bass_clip, "notes": _djent_bass(total_bars, sections)})

    # 3 + 4. Rhythm Guitars — REAL signal chain
    # sforzando (clean guitar SFZ) → The Anvil (drive) → MT-A (amp) → TSC (cab)
    SFORZANDO_VST3 = "1026018B"   # sforzando uniqueId from plugin_discover
    ANVIL_VST3     = "96925B51"   # The Anvil
    MTA_VST3       = "255875C0"   # Mercuriall Metal Area
    TSC_VST2_FILE  = r"C:\Program Files\Steinberg\VSTPlugins\TSC 1.1.dll"
    SFZ_PATH       = (Path.home() / "AppData" / "Roaming" / "Tracktion" / "Waveform"
                      / "Factory Loops" / "SFZ" / "Emilyguitar" / "emily_basic.sfz")

    def _add_real_guitar_track(name: str, pan: float) -> str:
        track_id = tracks.track_add({"name": name, "kind": "midi"})["track_id"]
        # 1. sforzando — instrument loading the SFZ
        plugins.plugin_add({"track_id": track_id, "type": SFORZANDO_VST3,
                            "name": "sforzando"})
        # 2. The Anvil — drive pedal
        plugins.plugin_add({"track_id": track_id, "type": ANVIL_VST3,
                            "name": "Anvil Drive"})
        # 3. MT-A — Metal Area amp
        plugins.plugin_add({"track_id": track_id, "type": MTA_VST3,
                            "name": "Metal Area"})
        # 4. TSC — Tube Cabinet Sim (VST2 via file path)
        plugins.plugin_add({"track_id": track_id, "type": "TSC 1.1",
                            "name": "TSC Cab", "preset_path": TSC_VST2_FILE})
        return track_id

    rg_l = _add_real_guitar_track("Rhythm Guitar L", -0.65)
    rg_r = _add_real_guitar_track("Rhythm Guitar R", 0.65)

    rg_l_clip = midi.midi_clip_add({"track_id": rg_l, "name": "Riff",
                                     "start_beats": 0, "length_beats": total_bars * 4})["clip_id"]
    rg_r_clip = midi.midi_clip_add({"track_id": rg_r, "name": "Riff",
                                     "start_beats": 0, "length_beats": total_bars * 4})["clip_id"]
    rhythm_notes = _djent_rhythm_guitar(total_bars, sections)
    midi.midi_notes_add({"clip_id": rg_l_clip, "notes": rhythm_notes})
    midi.midi_notes_add({"clip_id": rg_r_clip, "notes": rhythm_notes})

    # 5. Lead Guitar — also via sforzando + lighter amp chain
    lead = tracks.track_add({"name": "Lead Guitar", "kind": "midi"})["track_id"]
    plugins.plugin_add({"track_id": lead, "type": SFORZANDO_VST3, "name": "sforzando"})
    plugins.plugin_add({"track_id": lead, "type": ANVIL_VST3, "name": "Lead Drive"})
    plugins.plugin_add({"track_id": lead, "type": MTA_VST3, "name": "Lead Amp"})
    plugins.plugin_add({"track_id": lead, "type": "TSC 1.1", "name": "Lead Cab",
                        "preset_path": TSC_VST2_FILE})
    plugins.plugin_add_reverb({"track_id": lead, "type": "plate",
                                "size": 0.55, "decay": 0.50, "mix": 0.30})
    lead_clip = midi.midi_clip_add({"track_id": lead, "name": "Solo",
                                     "start_beats": 0, "length_beats": total_bars * 4})["clip_id"]
    midi.midi_notes_add({"clip_id": lead_clip, "notes": _djent_solo(total_bars, sections)})

    # 6. Pad — atmospheric (synth, this one's fine as 4OSC)
    pad = tracks.track_add({"name": "Pad", "kind": "midi"})["track_id"]
    plugins.plugin_add({"track_id": pad, "type": "4OSC", "name": "Pad"})
    _apply_preset_to_track(pad, "4OSC", "4OSC Vox Pad WMF.trkpreset", overrides={
        "ampAttack": 1.5, "ampRelease": 2.0, "filterFreq": 75.0, "filterAmount": 0.20,
    })
    pad_clip = midi.midi_clip_add({"track_id": pad, "name": "Atmosphere",
                                    "start_beats": 0, "length_beats": total_bars * 4})["clip_id"]
    midi.midi_notes_add({"clip_id": pad_clip,
                          "notes": [{"pitch": 47, "start_beats": 0,
                                     "length_beats": float(total_bars * 4),
                                     "velocity": 65}]})

    # Mix
    tracks.mix_apply_reference({"track_id": drums, "genre": "metal", "role": "drums"})
    tracks.mix_apply_reference({"track_id": bass,  "genre": "metal", "role": "bass"})
    tracks.mix_apply_reference({"track_id": rg_l,  "genre": "metal",
                                  "role": "rhythm_guitar_l", "pan": -0.65})
    tracks.mix_apply_reference({"track_id": rg_r,  "genre": "metal",
                                  "role": "rhythm_guitar_r", "pan":  0.65})
    tracks.mix_apply_reference({"track_id": lead,  "genre": "metal", "role": "lead"})
    tracks.mix_apply_reference({"track_id": pad,   "genre": "metal", "role": "pad"})

    from . import workflow as wf_tools
    wf_tools.master_chain_apply({"template": "loud_edm"})

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    edit_tools.edit_save({"path": out_path})
    summary = STORE.edit.summary()
    return {
        "ok": True, "path": out_path, "tempo": tempo, "bars": total_bars,
        "tracks": [t["name"] for t in summary["tracks"]],
        "total_notes": sum((c.get("note_count") or 0) for t in summary["tracks"] for c in t.get("clips", [])),
        "manual_step_required": (
            "Open each Rhythm Guitar / Lead Guitar track's sforzando plugin "
            "and load the SFZ file: " + str(SFZ_PATH)
        ),
    }


TOOLS.append({
    "name": "compose_djent_real",
    "description": (
        "Djent track using REAL VST3 instruments + amp/cab simulators "
        "(sforzando + Karoryfer Emilyguitar SFZ → The Anvil drive → "
        "Mercuriall Metal Area amp → TSC cab) instead of synthetic 4OSC. "
        "Returns a manual_step_required note explaining you must load the "
        "SFZ in sforzando once after opening the project — sforzando state "
        "isn't preserved in our .tracktionedit writer yet."
    ),
    "inputSchema": {
        "type": "object",
        "required": ["out_path"],
        "properties": {
            "out_path": {"type": "string"},
            "tempo": {"type": "number", "default": 110.0},
            "seed":  {"type": "integer", "default": 0},
        },
    },
})


HANDLERS = {
    "compose_lofi_track": compose_lofi_track,
    "compose_rainstorm": compose_rainstorm,
    "compose_synthwave_track": compose_synthwave_track,
    "compose_from_primitives": compose_from_primitives,
    "compose_djent_track": compose_djent_track,
    "compose_djent_real": compose_djent_real,
}
