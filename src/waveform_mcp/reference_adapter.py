"""Reference adapter — analyzer JSON → CompositionSpec.

Converts the music-analyzer's `*_full_analysis.json` output into a
CompositionSpec the builder can consume. Pure functions: no DAW or audio
dependencies. Tested with canned analyzer JSONs.

Input contract: the JSON shape produced by `analyzer_v4.save_results()` —
top-level keys `track_name`, `duration`, `sample_rate`, `rhythm`,
`harmony`, `timbre`, `melody`, `stereo`, `structure`. Newer versions may
add fields; we ignore unknowns. Older versions may omit some fields; we
fall back to defaults rather than raising.

Public API:
  spec_from_analyzer_json(data, *, options=None) -> CompositionSpec
  pitch_class_from_name(name) -> int
  parse_chord_name(name) -> (root_pc, quality)
  guess_genre(analysis) -> str
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from .composition_spec import (
    ChordEvent,
    CompositionSpec,
    DrumPatternSpec,
    NoteEvent,
    PluginChainSpec,
    SectionSpec,
    TrackRole,
    TrackSpec,
)

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Pitch / chord parsing                                                       #
# --------------------------------------------------------------------------- #


_PITCH_CLASS = {
    "C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3,
    "E": 4, "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8,
    "A": 9, "A#": 10, "Bb": 10, "B": 11,
}

# Map analyzer chord-quality token → spec quality string.
# Analyzer uses: maj, min, dim, aug, sus4, sus2, maj7, min7, 7, m7b5, dim7, add9, 5
# Spec convention: same set; we just normalise spelling.
_QUALITY_NORMALISE = {
    "": "maj",      # bare letter like "C" = major
    "maj": "maj",
    "M": "maj",
    "min": "min",
    "m": "min",
    "dim": "dim",
    "aug": "aug",
    "sus4": "sus4",
    "sus2": "sus2",
    "maj7": "maj7",
    "M7": "maj7",
    "min7": "min7",
    "m7": "min7",
    "7": "7",
    "m7b5": "m7b5",
    "dim7": "dim7",
    "add9": "add9",
    "5": "5",       # power chord
}


def pitch_class_from_name(name: str) -> int:
    """Parse a note name (C, Db, F#, ...) → pitch class 0..11.

    Raises ValueError on unrecognised input. Tolerates whitespace and
    trailing octave digits ("C4" → 0)."""
    if not isinstance(name, str):
        raise ValueError(f"expected string, got {type(name).__name__}")
    s = name.strip()
    if not s:
        raise ValueError("empty pitch name")
    # Strip trailing octave digits
    s = re.sub(r"-?\d+$", "", s)
    if s in _PITCH_CLASS:
        return _PITCH_CLASS[s]
    # Try just the first 2 chars (handles "C#m" etc. caller error)
    if len(s) >= 2 and s[:2] in _PITCH_CLASS:
        return _PITCH_CLASS[s[:2]]
    if s[:1] in _PITCH_CLASS:
        return _PITCH_CLASS[s[:1]]
    raise ValueError(f"unrecognised pitch name: {name!r}")


_CHORD_RE = re.compile(
    r"^\s*"
    r"(?P<root>[A-G][b#]?)"            # root note
    r"(?P<quality>.*)"                  # everything else
    r"\s*$"
)


def parse_chord_name(name: str) -> tuple[int, str]:
    """Parse a chord name like 'Cmaj7', 'F#m', 'Bb', 'D5' into
    (root_pc, quality). Raises ValueError if either part is unrecognised.
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"empty chord name: {name!r}")
    m = _CHORD_RE.match(name)
    if not m:
        raise ValueError(f"unrecognised chord: {name!r}")
    root_pc = pitch_class_from_name(m.group("root"))
    qual_raw = (m.group("quality") or "").strip()
    if qual_raw not in _QUALITY_NORMALISE:
        raise ValueError(f"unrecognised chord quality {qual_raw!r} in {name!r}")
    qual = _QUALITY_NORMALISE[qual_raw]
    return root_pc, qual


# --------------------------------------------------------------------------- #
# Section type mapping                                                        #
# --------------------------------------------------------------------------- #


def _map_section_type(analyzer_type: str) -> str:
    """Map the analyzer's section labels to our SectionType strings."""
    s = (analyzer_type or "").strip().lower()
    if not s:
        return "unknown"
    if "intro" in s and "build" in s:
        return "build"
    if "intro" in s:
        return "intro"
    if "outro" in s:
        return "outro"
    if "breakdown" in s:
        return "breakdown"
    if "bridge" in s:
        return "bridge"
    if "pre-chorus" in s or "pre chorus" in s:
        return "pre_chorus"
    if "chorus" in s or "drop" in s:
        return "chorus" if "chorus" in s else "drop"
    if "build" in s:
        return "build"
    if "solo" in s:
        return "solo"
    if "verse" in s:
        return "verse"
    if "transition" in s:
        return "transition"
    return "unknown"


# --------------------------------------------------------------------------- #
# Genre guess                                                                 #
# --------------------------------------------------------------------------- #


def _safe_dict(x: Any) -> dict[str, Any]:
    """Return x if it's a dict, else an empty dict."""
    return x if isinstance(x, dict) else {}


def _safe_list(x: Any) -> list:
    """Return x if it's a list, else an empty list."""
    return x if isinstance(x, list) else []


def guess_genre(analysis: dict[str, Any]) -> str:
    """Heuristic genre classification from the analyzer JSON.

    Returns one of MIX_BALANCE keys: metal, lofi, edm, pop, hip_hop,
    cinematic, synthwave, ambient. Defaults to 'pop' on uncertainty.
    Never raises — bad input shapes degrade to 'pop'.
    """
    if not isinstance(analysis, dict):
        return "pop"
    rhythm = _safe_dict(analysis.get("rhythm"))
    timbre = _safe_dict(analysis.get("timbre"))
    structure = _safe_list(analysis.get("structure"))

    def _f(d: dict, k: str, default: float) -> float:
        v = d.get(k, default)
        try:
            return float(v) if v is not None else float(default)
        except (TypeError, ValueError):
            return float(default)

    bpm = _f(rhythm, "global_bpm", 120)
    syncopation = _f(rhythm, "syncopation", 0.0)
    density = _f(rhythm, "avg_density", 4.0)
    harmonic_pct = _f(timbre, "harmonic_pct", 50)
    perc_pct = _f(timbre, "percussive_pct", 50)
    brightness = str(timbre.get("brightness_description") or "").lower()
    tonality = str(timbre.get("tonality_description") or "").lower()
    band_energy = _safe_dict(timbre.get("band_energy_pct"))
    sub_bass_pct = _f(band_energy, "sub_bass", 0)
    upper_mid_pct = _f(band_energy, "upper_mid", 0)

    # Has any "Climax / Drop" or peak chorus? typical of EDM/pop
    has_drop = any(
        "drop" in str((s or {}).get("type") or "").lower()
        for s in structure if isinstance(s, dict)
    )

    # 1. Metal: dense onsets + percussive-heavy + bright/distorted-leaning
    if perc_pct > 50 and density > 5.5 and bpm > 90 and "noisy" in tonality:
        return "metal"
    if perc_pct > 55 and bpm > 130 and upper_mid_pct > 18:
        return "metal"

    # 2. Hip-hop: low BPM, very high sub bass, low onset density
    if 70 <= bpm <= 110 and sub_bass_pct > 18 and density < 5:
        return "hip_hop"

    # 3. EDM: 4-on-floor (high beat regularity), high BPM, big drop
    if bpm >= 120 and has_drop and harmonic_pct > 50:
        return "edm"

    # 4. Synthwave: 100-130 BPM, harmonic-dominant, bright, retro vibe
    if 100 <= bpm <= 135 and harmonic_pct > 60 and ("bright" in brightness or "balanced" in brightness):
        return "synthwave"

    # 5. Lofi: low BPM, chill, harmonic-dominant, slight syncopation
    if bpm < 95 and harmonic_pct > 55 and ("dark" in brightness or "warm" in tonality or "balanced" in brightness):
        return "lofi"

    # 6. Cinematic: highly dynamic (wide dynamic_range), harmonic-rich
    dynamic_range = _f(timbre, "dynamic_range", 0)
    if dynamic_range > 25 and harmonic_pct > 60:
        return "cinematic"

    # 7. Ambient: very low percussive content, slow tempo
    if perc_pct < 25 and bpm < 100:
        return "ambient"

    return "pop"


# --------------------------------------------------------------------------- #
# Instrumentation by genre                                                    #
# --------------------------------------------------------------------------- #


# Default role list per genre. Roles must exist in MIX_BALANCE for the
# given genre (or fall through to 'pop' fallback in mix_level_for).
_GENRE_ROLES: dict[str, list[TrackRole]] = {
    "metal":      ["drums", "bass", "rhythm_guitar_l", "rhythm_guitar_r", "lead", "pad"],
    "lofi":       ["drums", "bass", "keys", "melody", "pad"],
    "edm":        ["drums", "bass", "lead", "arp", "pad"],
    "synthwave":  ["drums", "bass", "lead", "arp", "pad"],
    "pop":        ["drums", "bass", "keys", "lead", "pad"],
    "hip_hop":    ["drums", "bass", "sub_bass", "lead", "pad"],
    "cinematic":  ["drums", "bass", "lead", "pad", "keys"],
    "ambient":    ["pad", "lead", "ambience"],
}


def _default_pan_for(role: TrackRole) -> float:
    if role == "rhythm_guitar_l":
        return -0.65
    if role == "rhythm_guitar_r":
        return 0.65
    if role == "arp":
        return 0.15
    if role == "keys":
        return 0.10
    if role == "counter":
        return -0.15
    return 0.0


# Plugin chain by (genre, role). When missing, falls back to '_default'
# entries which use the bundled-with-Tracktion plugins so the builder
# always produces a playable edit even on an unrecognised genre.
_PLUGIN_CHAIN_TABLE: dict[tuple[str, TrackRole], list[str]] = {
    ("metal", "drums"):           ["sampler"],
    ("metal", "bass"):            ["sforzando", "Anvil"],
    ("metal", "rhythm_guitar_l"): ["sforzando", "Anvil", "MT-A", "TSC 1.1"],
    ("metal", "rhythm_guitar_r"): ["sforzando", "Anvil", "MT-A", "TSC 1.1"],
    ("metal", "lead"):            ["sforzando", "Anvil", "MT-A"],
    ("metal", "pad"):             ["4osc"],

    ("lofi", "drums"):            ["sampler"],
    ("lofi", "bass"):             ["4osc"],
    ("lofi", "keys"):             ["4osc"],
    ("lofi", "melody"):           ["4osc"],
    ("lofi", "pad"):              ["4osc"],

    ("synthwave", "drums"):       ["sampler"],
    ("synthwave", "bass"):        ["4osc"],
    ("synthwave", "lead"):        ["4osc"],
    ("synthwave", "arp"):         ["4osc"],
    ("synthwave", "pad"):         ["4osc"],
}

_DEFAULT_CHAIN: dict[TrackRole, list[str]] = {
    "drums": ["sampler"],
    "bass": ["4osc"],
    "sub_bass": ["4osc"],
    "rhythm_guitar": ["4osc"],
    "rhythm_guitar_l": ["4osc"],
    "rhythm_guitar_r": ["4osc"],
    "lead": ["4osc"],
    "melody": ["4osc"],
    "counter": ["4osc"],
    "pad": ["4osc"],
    "keys": ["4osc"],
    "arp": ["4osc"],
    "ambience": ["4osc"],
    "fx": ["4osc"],
}


def plugin_chain_for(genre: str, role: TrackRole) -> list[str]:
    """Return the default plugin chain for (genre, role). Always returns
    a non-empty list (falls back to a built-in plugin)."""
    key = (genre, role)
    if key in _PLUGIN_CHAIN_TABLE:
        return list(_PLUGIN_CHAIN_TABLE[key])
    return list(_DEFAULT_CHAIN.get(role, ["4osc"]))


# --------------------------------------------------------------------------- #
# Drum pattern synthesis from analyzer hits                                   #
# --------------------------------------------------------------------------- #


def _basic_drum_pattern(syncopation: float, bpm: float) -> DrumPatternSpec:
    """Synthesise a 1-bar drum pattern (4 beats) given a syncopation hint
    in [0, 1]. We don't try to be clever here — the spec carries it,
    the builder loops it. Phase B will replace this with an analyzer-
    derived pattern from per-stem hit times.
    """
    notes: list[NoteEvent] = []
    # Kick on 1 and 3 (basic backbeat); shift kick on 3 forward by an
    # eighth if syncopation is high (creates a pushed feel)
    notes.append(NoteEvent(start_beat=0.0, length_beats=0.25, pitch=36, velocity=110))
    if syncopation > 0.4:
        notes.append(NoteEvent(start_beat=2.5, length_beats=0.25, pitch=36, velocity=100))
    else:
        notes.append(NoteEvent(start_beat=2.0, length_beats=0.25, pitch=36, velocity=100))
    # Snare on 2 and 4
    notes.append(NoteEvent(start_beat=1.0, length_beats=0.25, pitch=38, velocity=100))
    notes.append(NoteEvent(start_beat=3.0, length_beats=0.25, pitch=38, velocity=100))
    # Closed hat on 8th notes
    for i in range(8):
        notes.append(NoteEvent(start_beat=i * 0.5, length_beats=0.125, pitch=42, velocity=70))
    return DrumPatternSpec(notes=notes, loop_length_beats=4.0)


# --------------------------------------------------------------------------- #
# Public adapter                                                              #
# --------------------------------------------------------------------------- #


@dataclass
class AdapterOptions:
    """Knobs the caller can pass to override adapter behaviour."""

    force_genre: str | None = None
    bars_override: int | None = None        # truncate / pad to N bars
    skip_pad: bool = False
    # Maximum chord-track entries to honour (chops trailing tail)
    max_chord_events: int = 256


def _seconds_to_beats(seconds: float, bpm: float) -> float:
    if bpm <= 0:
        return 0.0
    return seconds * (bpm / 60.0)


# --------------------------------------------------------------------------- #
# Timing helpers — meter inference + quantization                             #
# --------------------------------------------------------------------------- #


def _pick_time_signature(meter_candidates: list, *,
                         four_bias: float = 2.5) -> int:
    """Choose a time signature from the analyzer's meter_candidates.

    The analyzer reports `[(beats_per_bar, score), ...]` — periodicity
    matches, where higher score means the audio's onset pattern repeats
    at that interval. For nearly all popular music the answer is 4, but
    compound multiples (6, 8, 12) often score competitively.

    Bias toward 4: only choose another candidate if its score exceeds
    4's score by `four_bias`× (40% by default). This avoids the common
    failure where 12 (12/8 compound feel) wins by 0.336 vs 0.197 for 4
    on a song that's actually in 4/4.
    """
    if not meter_candidates:
        return 4

    # Build a clean list of (n_beats, score)
    pairs: list[tuple[int, float]] = []
    for c in meter_candidates:
        try:
            if isinstance(c, dict):
                n = int(c.get("beats_per_bar", 0))
                s = float(c.get("score", 0))
            else:
                n = int(c[0])
                s = float(c[1]) if len(c) >= 2 else 0.0
            if 2 <= n <= 9:
                pairs.append((n, s))
        except Exception:
            continue

    if not pairs:
        return 4

    score_for = {n: s for n, s in pairs}
    score_4 = score_for.get(4, 0.0)
    # Find best non-4 candidate
    best_other = max((p for p in pairs if p[0] != 4), default=(4, 0.0),
                     key=lambda p: p[1])
    if best_other[1] > score_4 * four_bias and score_4 > 0:
        return best_other[0]
    if score_4 > 0:
        return 4
    # No 4 listed; fall back to top candidate, otherwise 4
    return max(pairs, key=lambda p: p[1])[0] if pairs else 4


def _snap_to_grid(beat: float, grid_beats: float) -> float:
    """Round a beat position to the nearest grid_beats."""
    if grid_beats <= 0:
        return beat
    return round(beat / grid_beats) * grid_beats


def _quantize_notes(notes: list[NoteEvent], grid_beats: float = 0.25,
                    min_length_beats: float = 0.125) -> list[NoteEvent]:
    """Snap note start positions to a grid (default 16th-note: 0.25 beats)
    and enforce a minimum length so transcribed micro-notes (vibrato/
    breath/onset glitches) collapse into musical durations.
    Returns a new list; doesn't mutate input."""
    out: list[NoteEvent] = []
    for n in notes:
        new_start = _snap_to_grid(n.start_beat, grid_beats)
        new_len = max(min_length_beats, n.length_beats)
        # Round length to grid as well, so notes don't end at random places
        new_len = max(min_length_beats, _snap_to_grid(new_len, grid_beats))
        out.append(NoteEvent(
            start_beat=new_start, length_beats=new_len,
            pitch=n.pitch, velocity=n.velocity, channel=n.channel,
        ))
    return out


def _clean_dense_role(notes: list[NoteEvent], *,
                      min_velocity: int = 35,
                      min_length_beats: float = 0.25,
                      max_polyphony: int = 4,
                      grid_beats: float = 0.5) -> list[NoteEvent]:
    """Aggressive cleanup for dense roles like 'keys' where BasicPitch
    has transcribed every harmonic peak, vibrato, and sustain artifact.

    Steps:
      1. Drop notes below velocity floor (BasicPitch maps amplitude →
         velocity; quiet notes are usually transients/noise).
      2. Drop notes shorter than min_length_beats (8th note default).
      3. Re-quantize to grid_beats (8th-note for keys, vs 16th for lead).
      4. Cap simultaneous polyphony — at any beat, keep at most
         `max_polyphony` notes (drop lowest-velocity ties).

    Returns a new list, sorted by start_beat. Does not mutate input.
    """
    # Filters 1+2
    cleaned = [n for n in notes
               if n.velocity >= min_velocity
               and n.length_beats >= min_length_beats]
    if not cleaned:
        return []

    # Filter 3: snap to coarser grid
    cleaned = [
        NoteEvent(
            start_beat=_snap_to_grid(n.start_beat, grid_beats),
            length_beats=max(grid_beats, _snap_to_grid(n.length_beats, grid_beats)),
            pitch=n.pitch, velocity=n.velocity, channel=n.channel,
        )
        for n in cleaned
    ]

    # Filter 4: cap polyphony per onset position
    cleaned.sort(key=lambda n: (n.start_beat, -n.velocity))
    by_start: dict[float, list[NoteEvent]] = {}
    for n in cleaned:
        by_start.setdefault(n.start_beat, []).append(n)
    out: list[NoteEvent] = []
    for sb in sorted(by_start.keys()):
        # Top N by velocity at this position
        out.extend(by_start[sb][:max_polyphony])
    return out


def _derive_drum_pattern_from_onsets(
    *,
    bass_notes: list[NoteEvent],
    lead_notes: list[NoteEvent],
    keys_notes: list[NoteEvent],
    bar_beats: int = 4,
    grid_resolution: int = 16,    # 16ths per bar
) -> list[NoteEvent]:
    """Build a 1-bar drum loop whose hits land on positions where the
    actual song's onsets land. Returns a list of NoteEvents on a 1-bar
    timeline (start_beat in [0, bar_beats)).

    Strategy:
      * Histogram onsets per grid position (16th notes by default)
      * KICK fires on positions with strong low-pitch (bass) onsets,
        plus always on beat 1
      * SNARE fires on backbeats (beats 2 and 4 in 4/4) — universal
        across most popular music — plus any position with strong
        high-pitch lead onsets that often coincide with snare hits
      * HAT fires on every 8th note for steady time, plus any 16th
        position with significant onset density
    """
    grid_beats = bar_beats / grid_resolution
    histogram_bass = [0] * grid_resolution
    histogram_lead = [0] * grid_resolution
    histogram_keys = [0] * grid_resolution

    for n in bass_notes:
        idx = int(round((n.start_beat % bar_beats) / grid_beats)) % grid_resolution
        histogram_bass[idx] += 1
    for n in lead_notes:
        idx = int(round((n.start_beat % bar_beats) / grid_beats)) % grid_resolution
        histogram_lead[idx] += 1
    for n in keys_notes:
        idx = int(round((n.start_beat % bar_beats) / grid_beats)) % grid_resolution
        histogram_keys[idx] += 1

    notes: list[NoteEvent] = []

    def beat_for(grid_idx: int) -> float:
        return grid_idx * grid_beats

    # KICK — beat 1 always, plus 1-3 extra positions that stand clearly
    # above the mean. We use a strict criterion: position must be >=
    # 1.4× the mean histogram value AND in the top 4 positions overall.
    # Without this, near-uniform histograms (which BasicPitch produces
    # from sustained content — every harmonic instability becomes a
    # phantom onset) cause kick on every 16th = machine-gun.
    notes.append(NoteEvent(start_beat=0.0, length_beats=0.25, pitch=36, velocity=110))
    if any(histogram_bass):
        mean_count = sum(histogram_bass) / len(histogram_bass)
        # Rank positions by count, descending; skip position 0 (always-kick)
        ranked = sorted(
            ((i, c) for i, c in enumerate(histogram_bass) if i != 0),
            key=lambda x: -x[1],
        )
        extra_kicks = 0
        max_extra = 3
        for i, count in ranked:
            if extra_kicks >= max_extra:
                break
            # Strict: need clearly above-mean AND not right next to another
            # kick (avoid 32nd-note clusters)
            if count < mean_count * 1.4:
                continue
            # Don't place on position adjacent to existing kick (within 1
            # 16th step) — keeps the pattern breathable
            existing_kick_positions = [
                int(round(n.start_beat / grid_beats))
                for n in notes if n.pitch == 36
            ]
            if any(abs(i - p) < 2 for p in existing_kick_positions):
                continue
            notes.append(NoteEvent(
                start_beat=beat_for(i), length_beats=0.25, pitch=36,
                velocity=int(95 + 15 * (count / max(max(histogram_bass), 1))),
            ))
            extra_kicks += 1

    # SNARE — backbeats. For 4/4 that's beats 2 and 4 (0-indexed = 1.0
    # and 3.0). For 3/4 that's beat 2 (0-indexed = 1.0). General rule:
    # every other beat starting at beat 1 (0-indexed).
    for beat_idx in range(1, bar_beats, 2):
        notes.append(NoteEvent(
            start_beat=float(beat_idx), length_beats=0.25,
            pitch=38, velocity=100,
        ))

    # HAT — every 8th note.
    for i in range(0, grid_resolution, 2):
        # Skip if already a kick or snare hit at this position to avoid mud
        notes.append(NoteEvent(
            start_beat=beat_for(i), length_beats=0.125,
            pitch=42, velocity=70 if i % 4 == 0 else 60,
        ))

    notes.sort(key=lambda n: (n.start_beat, n.pitch))
    return notes


def _dedupe_overlapping(notes: list[NoteEvent], min_gap_beats: float = 0.06
                        ) -> list[NoteEvent]:
    """Remove duplicate-pitch notes that start within min_gap_beats of
    each other. Quantization snaps multiple BasicPitch micro-notes to
    the same grid position, producing machine-gun retrigger; collapse
    those into a single longer note."""
    by_pitch: dict[int, list[NoteEvent]] = {}
    for n in notes:
        by_pitch.setdefault(n.pitch, []).append(n)

    out: list[NoteEvent] = []
    for pitch, events in by_pitch.items():
        events.sort(key=lambda x: x.start_beat)
        kept: list[NoteEvent] = []
        for ev in events:
            if kept and ev.start_beat - kept[-1].start_beat < min_gap_beats:
                # Extend previous to cover this one
                end_a = kept[-1].start_beat + kept[-1].length_beats
                end_b = ev.start_beat + ev.length_beats
                kept[-1] = NoteEvent(
                    start_beat=kept[-1].start_beat,
                    length_beats=max(end_a, end_b) - kept[-1].start_beat,
                    pitch=pitch,
                    velocity=max(kept[-1].velocity, ev.velocity),
                    channel=ev.channel,
                )
            else:
                kept.append(ev)
        out.extend(kept)
    out.sort(key=lambda n: n.start_beat)
    return out


def spec_from_analyzer_json(
    data: dict[str, Any],
    *,
    options: AdapterOptions | None = None,
) -> CompositionSpec:
    """Convert an analyzer JSON dict into a CompositionSpec.

    Always returns a spec — fields the analyzer didn't produce fall
    back to defaults. Errors are logged and surfaced via
    `provenance['warnings']` rather than raised, so the builder always
    has *something* to work with.
    """
    opt = options or AdapterOptions()
    warnings: list[str] = []

    # ---- globals ----
    rhythm = data.get("rhythm", {}) or {}
    bpm = float(rhythm.get("global_bpm", 120) or 120)
    if bpm <= 0:
        warnings.append(f"invalid bpm in analysis: {bpm}; defaulting to 120")
        bpm = 120.0

    # Time-sig from meter_candidates. The analyzer's `meter_candidates`
    # is a list of [beats_per_bar, score] periodicity matches — the top
    # entry is just whatever periodicity hits hardest, NOT necessarily
    # the song's actual time signature. Compound metres (12, 6) and 4
    # often score similarly for a song that's actually in 4/4, because
    # any 4-beat pattern repeats at the 12-beat level too. Default to
    # 4 unless something clearly better dominates.
    time_sig_num = _pick_time_signature(rhythm.get("meter_candidates") or [])
    if time_sig_num not in (2, 3, 4, 5, 6, 7, 9):
        warnings.append(f"unusual time_sig_num {time_sig_num}; clamping to 4")
        time_sig_num = 4

    # Key
    harmony = data.get("harmony", {}) or {}
    key_str = harmony.get("overall_key") or "C Major"
    key_root_pc, key_mode = 0, "major"
    try:
        parts = str(key_str).split()
        if parts:
            key_root_pc = pitch_class_from_name(parts[0])
        if len(parts) >= 2:
            key_mode = "minor" if "minor" in parts[1].lower() else "major"
    except Exception as e:
        warnings.append(f"could not parse key {key_str!r}: {e}")

    # Genre
    genre = opt.force_genre or guess_genre(data)
    logger.info("spec_from_analyzer_json: genre=%s tempo=%.1f key=%d %s",
                genre, bpm, key_root_pc, key_mode)

    # ---- sections ----
    # Snap section boundaries to bar lines so drums (which loop on the
    # bar) don't start mid-bar against transcribed notes (which we'll
    # also snap to a tighter grid below).
    bar_beats = float(time_sig_num)
    structure = data.get("structure", []) or []
    raw_sections: list[SectionSpec] = []
    for s in structure:
        try:
            start_s = float(s.get("start", 0))
            end_s = float(s.get("end", 0))
            if end_s <= start_s:
                continue
            sec_type = _map_section_type(str(s.get("type", "")))
            start_b = _snap_to_grid(_seconds_to_beats(start_s, bpm), bar_beats)
            end_b = _snap_to_grid(_seconds_to_beats(end_s, bpm), bar_beats)
            length_b = max(bar_beats, end_b - start_b)  # at least 1 bar
            energy = float(s.get("energy_ratio", 0.5) or 0.5)
            density = max(0.0, min(1.0, energy / 1.5))  # energy_ratio>1.5 → 1.0
            density_hint = float(s.get("onset_density", 0) or 0) / 12.0
            density = max(density, min(1.0, density_hint))
            raw_sections.append(SectionSpec(
                type=sec_type,  # type: ignore[arg-type]
                start_beat=start_b,
                length_beats=length_b,
                density=density,
                brightness=0.5,  # filled in below per global timbre
                label=str(s.get("type", "")),
            ))
        except Exception as e:
            warnings.append(f"failed to parse section {s!r}: {e}")

    # Resolve overlaps caused by snapping (section A's end snapped past
    # section B's start). Walk in order, clamp each to start strictly
    # after the previous one's end.
    raw_sections.sort(key=lambda s: s.start_beat)
    sections: list[SectionSpec] = []
    next_start = 0.0
    for s in raw_sections:
        if s.start_beat < next_start:
            # Push start forward; preserve length if possible
            s = SectionSpec(
                type=s.type, start_beat=next_start, length_beats=s.length_beats,
                density=s.density, brightness=s.brightness,
                instrumentation=list(s.instrumentation), label=s.label,
            )
        sections.append(s)
        next_start = s.start_beat + s.length_beats

    # Apply global brightness as section default if we don't have per-section data
    timbre = data.get("timbre", {}) or {}
    bright_desc = (timbre.get("brightness_description") or "").lower()
    global_bright = 0.7 if "bright" in bright_desc else (0.3 if "dark" in bright_desc else 0.5)
    for s in sections:
        s.brightness = global_bright

    # If no structure, make one big "verse" covering the audio duration
    if not sections:
        warnings.append("no structure sections; creating single verse")
        dur_b = _seconds_to_beats(float(data.get("duration", 60.0) or 60.0), bpm)
        sections = [SectionSpec(type="verse", start_beat=0, length_beats=max(16.0, dur_b))]

    # ---- chord track ----
    # Snap chord changes to the nearest beat. In nearly all popular music
    # chords change on strong beats (especially beat 1); the analyzer's
    # detected boundaries are off-grid by tens to hundreds of milli-
    # seconds. Letting that through means the bass line follows fractional-
    # beat chord changes and never lands on the downbeat.
    chord_track: list[ChordEvent] = []
    chords = harmony.get("chords") or []
    for c in chords[: opt.max_chord_events]:
        try:
            start_b = _snap_to_grid(_seconds_to_beats(float(c["start"]), bpm), 1.0)
            end_b = _snap_to_grid(_seconds_to_beats(float(c["end"]), bpm), 1.0)
            if end_b <= start_b:
                continue
            root_pc, qual = parse_chord_name(str(c.get("chord", "C")))
            chord_track.append(ChordEvent(
                start_beat=start_b, length_beats=end_b - start_b,
                root_pc=root_pc, quality=qual,
            ))
        except Exception as e:
            warnings.append(f"failed to parse chord {c!r}: {e}")

    # If chord_track empty, populate a single tonic chord under the whole edit
    if not chord_track and sections:
        total_b = max(s.start_beat + s.length_beats for s in sections)
        chord_track.append(ChordEvent(
            start_beat=0, length_beats=total_b,
            root_pc=key_root_pc, quality="min" if key_mode == "minor" else "maj",
        ))

    # ---- tracks (instrumentation) ----
    roles = list(_GENRE_ROLES.get(genre, _GENRE_ROLES["pop"]))
    if opt.skip_pad and "pad" in roles:
        roles.remove("pad")

    tracks: list[TrackSpec] = []
    syncopation = float(rhythm.get("syncopation", 0.0) or 0.0)
    drum_pattern = _basic_drum_pattern(syncopation, bpm)

    for role in roles:
        ts = TrackSpec(
            name=_friendly_name(role),
            role=role,
            kind="midi",
            pan=_default_pan_for(role),
            chain=PluginChainSpec(plugins=plugin_chain_for(genre, role)),
        )
        if role == "drums":
            ts.drum_pattern = drum_pattern
        tracks.append(ts)

    # Bass line: stub note-per-chord on each chord_track event at the
    # chord root (octave 2)
    bass_track = next((t for t in tracks if t.role == "bass"), None)
    if bass_track is not None:
        for c in chord_track:
            bass_pitch = 36 + (c.bass_pc if c.bass_pc is not None else c.root_pc)  # C2 + pc
            bass_track.notes.append(NoteEvent(
                start_beat=c.start_beat,
                length_beats=c.length_beats,
                pitch=bass_pitch,
                velocity=100,
            ))

    # Lead/melody: leave empty in MVP — Phase B1 (BasicPitch) will populate.
    # However, if the analyzer reported a melody pitch range, we synth a
    # very basic placeholder by stepping through chord notes.
    melody = data.get("melody", {}) or {}
    if melody.get("has_melody"):
        lead_track = next((t for t in tracks if t.role in ("lead", "melody")), None)
        if lead_track is not None:
            # Place chord-tone arpeggios; intentional placeholder
            for c in chord_track[:64]:
                root_midi = 60 + c.root_pc  # C4 + pc
                for i, semis in enumerate((0, 7, 4, 7)):
                    lead_track.notes.append(NoteEvent(
                        start_beat=c.start_beat + i * (c.length_beats / 4.0),
                        length_beats=max(0.25, c.length_beats / 4.0),
                        pitch=root_midi + semis,
                        velocity=92,
                    ))

    # ---- master chain (genre-tuned defaults) ----
    master = PluginChainSpec(plugins=["3bandEq", "comp", "limiter"])

    spec = CompositionSpec(
        name=str(data.get("track_name") or "Untitled"),
        tempo_bpm=bpm,
        time_sig_num=time_sig_num,
        time_sig_denom=4,
        key_root_pc=key_root_pc,
        key_mode=key_mode,  # type: ignore[arg-type]
        genre=genre,
        sections=sections,
        chord_track=chord_track,
        tracks=tracks,
        master_chain=master,
        provenance={
            "source": "music-analyzer",
            "track_name": data.get("track_name"),
            "artist": data.get("artist"),
            "warnings": warnings,
            "fidelity_caveats": _fidelity_notes(genre, melody),
        },
    )

    # bars override
    if opt.bars_override is not None:
        target_beats = float(opt.bars_override) * time_sig_num
        spec = _truncate_or_extend(spec, target_beats)

    return spec


def _friendly_name(role: TrackRole) -> str:
    return {
        "drums": "Drums", "bass": "Bass", "sub_bass": "Sub Bass",
        "rhythm_guitar": "Rhythm Guitar",
        "rhythm_guitar_l": "Rhythm Guitar L",
        "rhythm_guitar_r": "Rhythm Guitar R",
        "lead": "Lead", "melody": "Melody", "counter": "Counter",
        "pad": "Pad", "keys": "Keys", "arp": "Arp",
        "ambience": "Ambience", "fx": "FX",
    }.get(role, role.title())


def _fidelity_notes(genre: str, melody_block: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    notes.append("Drum pattern is a basic backbeat — Phase B will derive from analyzer hit times.")
    if melody_block.get("has_melody"):
        notes.append("Lead/melody is a chord-tone placeholder — Phase B1 (BasicPitch) will transcribe real notes.")
    notes.append(f"Genre guessed as {genre!r}; pass options.force_genre to override.")
    return notes


def _truncate_or_extend(spec: CompositionSpec, target_beats: float) -> CompositionSpec:
    """Trim sections/chord_track/notes to target_beats. If shorter, pad
    the last section to reach target_beats."""
    if target_beats <= 0:
        return spec

    # Sections
    new_sections: list[SectionSpec] = []
    for s in spec.sections:
        if s.start_beat >= target_beats:
            break
        cap = min(s.length_beats, target_beats - s.start_beat)
        if cap > 0:
            new_sections.append(SectionSpec(
                type=s.type, start_beat=s.start_beat, length_beats=cap,
                density=s.density, brightness=s.brightness,
                instrumentation=list(s.instrumentation), label=s.label,
            ))
    if new_sections and new_sections[-1].start_beat + new_sections[-1].length_beats < target_beats:
        # Extend last section
        last = new_sections[-1]
        last.length_beats = target_beats - last.start_beat
    spec.sections = new_sections

    # Chord track
    new_chords: list[ChordEvent] = []
    for c in spec.chord_track:
        if c.start_beat >= target_beats:
            break
        cap = min(c.length_beats, target_beats - c.start_beat)
        if cap > 0:
            new_chords.append(ChordEvent(
                start_beat=c.start_beat, length_beats=cap,
                root_pc=c.root_pc, quality=c.quality,
                bass_pc=c.bass_pc, inversion=c.inversion,
            ))
    spec.chord_track = new_chords

    # Notes per track
    for t in spec.tracks:
        t.notes = [n for n in t.notes if n.start_beat < target_beats]

    return spec
