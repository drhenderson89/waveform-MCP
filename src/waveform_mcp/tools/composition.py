"""High-level composition primitives — chord progression generation,
voice leading, arrangement planning. These are the "compose a song from
scratch" tools the LLM uses BEFORE picking notes.

Tools:
  - progression_generate : invent a chord progression from constraints
  - voice_lead           : voice next chord with minimal motion from prev
  - arrangement_plan     : produce a section list for a song from genre +
                           target length + energy curve
"""

from __future__ import annotations

import random
from typing import Any

from .. import music_theory_data as data


TOOLS: list[dict[str, Any]] = [
    {
        "name": "progression_generate",
        "description": (
            "Invent a chord progression in a key and mode. Returns a list of "
            "chord dicts ({roman, root_note, root_midi, chord_pitches, "
            "quality}). Constraints: end_cadence ∈ {authentic, plagal, "
            "deceptive, half}; allow_borrowed enables modal interchange "
            "(bIII/bVI/bVII in major, V/VII for minor); style ∈ {pop, jazz, "
            "modal, synthwave, sad, uplifting} biases the chord palette."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["root", "mode", "length_chords"],
            "properties": {
                "root": {"type": "string"},
                "mode": {"type": "string"},
                "length_chords": {"type": "integer"},
                "end_cadence": {"type": "string",
                                "enum": ["authentic", "plagal", "deceptive", "half"],
                                "default": "authentic"},
                "allow_borrowed": {"type": "boolean", "default": False},
                "style": {"type": "string",
                          "enum": ["pop", "jazz", "modal", "synthwave", "sad", "uplifting"],
                          "default": "pop"},
                "octave": {"type": "integer", "default": 3},
                "seed": {"type": "integer", "default": 0},
            },
        },
    },
    {
        "name": "voice_lead",
        "description": (
            "Voice the next chord with minimal voice motion from the previous "
            "voicing. Standard SATB-style voice leading: each voice moves the "
            "smallest interval to a target chord tone. Returns the new "
            "voicing as a list of MIDI pitches (same length as prev_voicing)."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["prev_voicing", "next_chord"],
            "properties": {
                "prev_voicing": {"type": "array", "items": {"type": "integer"}},
                "next_chord":   {"type": "array", "items": {"type": "integer"},
                                 "description": "Target chord tones (any octave; will be re-voiced)"},
            },
        },
    },
    {
        "name": "arrangement_plan",
        "description": (
            "Produce a section list for a song. genre selects characteristic "
            "form; target_length_seconds + tempo set total bars; energy_curve "
            "shapes the intensity pattern. Returns sections with bar counts, "
            "roles, and an energy 0..1 the composer can use to gate layers / "
            "set velocities."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["genre", "target_length_seconds", "tempo"],
            "properties": {
                "genre": {"type": "string"},
                "target_length_seconds": {"type": "number"},
                "tempo": {"type": "number"},
                "energy_curve": {"type": "string",
                                 "enum": ["slow_burn", "immediate_hook",
                                          "build_drop_build", "call_response"],
                                 "default": "slow_burn"},
                "include_bridge": {"type": "boolean", "default": True},
                "include_buildup": {"type": "boolean", "default": True},
            },
        },
    },
]


# ---------- helpers ----------


def _roman_to_degree(roman: str) -> tuple[int, str]:
    """Parse a roman numeral like 'iv', 'V7', 'bVI' into (degree, quality_marker).
    Returns (degree 1..7, quality 'maj'/'min'/'dim'/'aug').
    """
    s = roman.strip()
    flat = s.startswith("b")
    if flat:
        s = s[1:]
    # Trailing modifiers
    qual = "maj" if s[0].isupper() else "min"
    # Strip trailing 7, sus, etc. for degree parsing
    base = ""
    for ch in s:
        if ch in "iIvV":
            base += ch
        else:
            break
    degree_map = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7}
    degree = degree_map[base.upper()]
    if "°" in s or "dim" in s:
        qual = "dim"
    return (degree, qual + ("_flat" if flat else ""))


def _chord_pitches_for_degree(root_note: str, mode: str, degree: int,
                              borrowed_flat: bool, octave: int = 3) -> dict:
    """Build chord pitches for a scale degree (1..7) in a key+mode.
    Returns {root_midi, chord_pitches, quality}.
    """
    if root_note not in data.NOTE_NAMES:
        raise ValueError(f"unknown root: {root_note}")
    root_midi = (octave + 1) * 12 + data.NOTE_NAMES[root_note]
    intervals = data.SCALES.get(mode, data.SCALES["major"])
    scale_pitches = [root_midi + iv for iv in intervals]
    if borrowed_flat:
        # Borrowed bIII / bVI / bVII = lower the regular III/VI/VII by a semitone
        idx = degree - 1
        deg_root = scale_pitches[idx] - 1
    else:
        deg_root = scale_pitches[degree - 1]
    # Triad: root, third (+4 or +3 semitones depending on quality), fifth
    # DIATONIC_TRIADS uses Roman-numeral case for quality: uppercase = major,
    # lowercase = minor, anything with ° = diminished.
    triads = data.DIATONIC_TRIADS.get(mode, data.DIATONIC_TRIADS["major"])
    rn = triads[degree - 1]
    if "°" in rn:
        quality = "dim"
    elif rn[0].isupper():
        quality = "maj"
    else:
        quality = "min"
    third_iv = {"maj": 4, "min": 3, "dim": 3, "aug": 4}[quality]
    fifth_iv = {"maj": 7, "min": 7, "dim": 6, "aug": 8}[quality]
    return {
        "root_midi": deg_root,
        "chord_pitches": [deg_root, deg_root + third_iv, deg_root + fifth_iv],
        "quality": quality,
    }


# ---------- progression generator ----------


# Style → degree-transition probabilities. Higher number = more likely.
# Major-mode transitions (rows = current degree 1..7, cols = next degree)
_MAJOR_STYLE_TRANSITIONS: dict[str, list[list[float]]] = {
    "pop": [   # I, ii, iii, IV, V, vi, vii°
        [0.0, 1, 1, 3, 3, 3, 1],   # I →
        [1, 0, 0, 1, 3, 1, 0],     # ii →
        [1, 1, 0, 2, 1, 3, 0],     # iii →
        [2, 1, 0, 0, 3, 1, 0],     # IV → V dominant
        [3, 0, 0, 1, 0, 3, 0],     # V → I or vi
        [1, 2, 0, 3, 1, 0, 0],     # vi → IV
        [3, 0, 0, 0, 0, 0, 0],     # vii° → I
    ],
    "jazz": [
        [0, 3, 1, 1, 1, 1, 0],
        [0, 0, 0, 0, 3, 0, 0],     # ii → V
        [0, 0, 0, 0, 0, 3, 0],
        [1, 1, 0, 0, 3, 0, 0],
        [3, 0, 0, 0, 0, 0, 0],
        [0, 3, 0, 0, 0, 0, 0],
        [3, 0, 0, 0, 0, 0, 0],
    ],
    "synthwave": [
        [0, 0, 0, 2, 2, 3, 0],
        [0, 0, 0, 1, 2, 0, 0],
        [0, 0, 0, 0, 0, 0, 0],
        [1, 0, 0, 0, 3, 1, 0],
        [3, 0, 0, 0, 0, 1, 0],
        [0, 0, 0, 3, 1, 0, 0],     # vi → IV (axis pop)
        [3, 0, 0, 0, 0, 0, 0],
    ],
    "sad": [
        [0, 0, 0, 0, 0, 3, 0],
        [0, 0, 0, 0, 3, 0, 0],
        [0, 0, 0, 0, 0, 3, 0],
        [0, 0, 0, 0, 0, 1, 0],
        [0, 0, 0, 0, 0, 3, 0],     # V → vi deceptive
        [0, 0, 0, 3, 0, 0, 0],
        [0, 0, 0, 0, 0, 1, 0],
    ],
    "uplifting": [
        [0, 0, 0, 3, 2, 1, 0],
        [0, 0, 0, 0, 3, 0, 0],
        [0, 0, 0, 0, 0, 1, 0],
        [3, 0, 0, 0, 2, 0, 0],
        [3, 0, 0, 1, 0, 0, 0],
        [0, 0, 0, 3, 1, 0, 0],
        [3, 0, 0, 0, 0, 0, 0],
    ],
}
# Aliases
_MAJOR_STYLE_TRANSITIONS["modal"] = _MAJOR_STYLE_TRANSITIONS["jazz"]


def _weighted_pick(weights: list[float], rng: random.Random) -> int:
    total = sum(weights)
    if total <= 0:
        return rng.randint(0, len(weights) - 1)
    r = rng.uniform(0, total)
    cum = 0.0
    for i, w in enumerate(weights):
        cum += w
        if r <= cum:
            return i
    return len(weights) - 1


def progression_generate(args: dict) -> dict:
    rng = random.Random(int(args.get("seed", 0)))
    root = args["root"]
    mode = args["mode"]
    length = int(args["length_chords"])
    cadence = args.get("end_cadence", "authentic")
    allow_borrowed = bool(args.get("allow_borrowed", False))
    style = args.get("style", "pop")
    octave = int(args.get("octave", 3))

    # Pick transition table — major modes use the major table, minor uses
    # a shifted version (we treat minor tonic = 1 with appropriate qualities).
    transitions = _MAJOR_STYLE_TRANSITIONS.get(style, _MAJOR_STYLE_TRANSITIONS["pop"])

    # Start on I; build out to full length first, then replace last 2 with cadence
    sequence: list[int] = [1]
    while len(sequence) < length:
        prev = sequence[-1]
        weights = transitions[prev - 1].copy()
        next_deg = _weighted_pick(weights, rng) + 1
        sequence.append(next_deg)

    cadence_endings = {
        "authentic": [5, 1],
        "plagal":    [4, 1],
        "deceptive": [5, 6],
        "half":      [1, 5],
    }
    cad = cadence_endings[cadence]
    if length >= 2:
        sequence[-2:] = cad
    elif length == 1:
        sequence = cad[1:2]
    sequence = sequence[:length]

    # Build chord dicts
    chords: list[dict] = []
    for deg in sequence:
        # ~10% chance of borrowed if allowed (only on III/VI/VII)
        is_borrowed = allow_borrowed and deg in (3, 6, 7) and rng.random() < 0.10
        info = _chord_pitches_for_degree(root, mode, deg, is_borrowed, octave)
        roman = _degree_to_roman(deg, info["quality"], is_borrowed)
        chords.append({
            "roman": roman,
            "degree": deg,
            "root_midi": info["root_midi"],
            "chord_pitches": info["chord_pitches"],
            "quality": info["quality"],
            "borrowed": is_borrowed,
        })
    return {"ok": True, "chords": chords, "n_chords": len(chords)}


def _degree_to_roman(degree: int, quality: str, borrowed: bool) -> str:
    base = ["I", "II", "III", "IV", "V", "VI", "VII"][degree - 1]
    if quality.startswith("min"):
        base = base.lower()
    elif quality.startswith("dim"):
        base = base.lower() + "°"
    if borrowed:
        return "b" + base
    return base


# ---------- voice leading ----------


def voice_lead(args: dict) -> dict:
    """Voice the next chord with smart voice leading. Constraints:
      1. Each voice moves to the nearest pitch with one of the next chord's
         pitch classes (greedy minimal motion).
      2. NO VOICE CROSSING — voice ordering preserved (bass < tenor < alto < soprano).
      3. AVOID PARALLEL FIFTHS / OCTAVES with the bass voice when possible.

    Algorithm: enumerate all assignments (target pitch classes × octave choices
    per voice), score each by (motion_cost + crossing_penalty + parallel_penalty),
    pick the lowest-score assignment.

    For 4-voice voicings this is fast (256 candidates per voice → 256^4=4.3B
    is too much; we prune by limiting to ±2 octaves per voice → ~5^4=625
    candidate sets).
    """
    prev = list(args["prev_voicing"])
    target_pcs = sorted({p % 12 for p in args["next_chord"]})
    n_voices = len(prev)

    # Build candidate pitches per voice: closest pitches in any target PC
    # within ±2 octaves of the original voice pitch
    voice_candidates: list[list[int]] = []
    for old in prev:
        cands: list[int] = []
        for pc in target_pcs:
            for oct_shift in range(-2, 3):
                candidate = ((old // 12) + oct_shift) * 12 + pc
                cands.append(candidate)
        # Keep distinct, sorted by distance from old
        cands = sorted(set(cands), key=lambda p: abs(p - old))
        # Top-6 closest per voice — keeps the search small
        voice_candidates.append(cands[:6])

    def score(assignment: list[int]) -> float:
        motion = sum(abs(assignment[i] - prev[i]) for i in range(n_voices))
        # Voice crossing: penalize if assignment doesn't preserve the
        # ordering of `prev`. We compare adjacent pairs.
        crossing = 0
        for i in range(n_voices - 1):
            prev_lo, prev_hi = prev[i], prev[i + 1]
            new_lo, new_hi = assignment[i], assignment[i + 1]
            if prev_lo <= prev_hi and new_lo > new_hi:
                crossing += 50   # heavy penalty
            if prev_lo >= prev_hi and new_lo < new_hi:
                crossing += 50
        # Parallel-fifths/octaves penalty between bass (voice 0) and any other
        parallel = 0
        if n_voices >= 2:
            bass_motion = assignment[0] - prev[0]
            for j in range(1, n_voices):
                voice_motion = assignment[j] - prev[j]
                if bass_motion == voice_motion and bass_motion != 0:
                    # Same direction & magnitude — check if interval is fifth or octave
                    interval_prev = (prev[j] - prev[0]) % 12
                    interval_new = (assignment[j] - assignment[0]) % 12
                    if interval_prev == interval_new and interval_prev in (0, 7):
                        parallel += 8
        return motion + crossing + parallel

    # Enumerate assignments via Cartesian product (limited search space)
    import itertools
    best: list[int] = list(prev)
    best_score = float("inf")
    for combo in itertools.product(*voice_candidates):
        s = score(list(combo))
        if s < best_score:
            best_score = s
            best = list(combo)

    return {"ok": True, "voicing": best, "score": best_score}


# ---------- arrangement planner ----------


# Genre-typical forms. Each is a list of (section_name, role, weight) pairs
# the planner can scale. Roles: intro, verse, prechorus, chorus, bridge,
# buildup, breakdown, drop, outro. Weight = relative bar count.
_GENRE_FORMS: dict[str, list[tuple[str, str, float]]] = {
    "synthwave": [
        ("intro", "intro", 0.5), ("verseA", "verse", 1.0),
        ("chorus", "chorus", 1.0), ("verseB", "verse", 1.0),
        ("chorus2", "chorus", 1.0), ("bridge", "bridge", 1.0),
        ("buildup", "buildup", 0.5), ("chorusFinal", "chorus", 1.0),
        ("outro", "outro", 1.0),
    ],
    "lofi": [
        ("intro", "intro", 0.5), ("section_a", "verse", 1.0),
        ("section_b", "chorus", 1.0), ("breakdown", "breakdown", 0.5),
        ("section_c", "chorus", 1.0), ("outro", "outro", 0.5),
    ],
    "edm": [
        ("intro", "intro", 1.0), ("verse", "verse", 1.0),
        ("buildup", "buildup", 0.5), ("drop", "drop", 1.0),
        ("breakdown", "breakdown", 1.0), ("buildup2", "buildup", 0.5),
        ("drop2", "drop", 1.0), ("outro", "outro", 1.0),
    ],
    "house": [
        ("intro", "intro", 1.5), ("verseA", "verse", 1.0),
        ("breakdown", "breakdown", 0.5), ("chorus", "chorus", 1.0),
        ("verseB", "verse", 1.0), ("chorus2", "chorus", 1.5),
        ("outro", "outro", 1.0),
    ],
    "pop": [
        ("intro", "intro", 0.5), ("verseA", "verse", 1.0),
        ("prechorus", "prechorus", 0.5), ("chorus", "chorus", 1.0),
        ("verseB", "verse", 1.0), ("prechorus2", "prechorus", 0.5),
        ("chorus2", "chorus", 1.0), ("bridge", "bridge", 0.75),
        ("chorusFinal", "chorus", 1.0), ("outro", "outro", 0.5),
    ],
    "ambient": [
        ("emergence", "intro", 1.0), ("development", "verse", 2.0),
        ("apex", "chorus", 1.5), ("settle", "outro", 1.5),
    ],
    "hip_hop": [
        ("intro", "intro", 0.5), ("verse1", "verse", 1.0),
        ("hook", "chorus", 1.0), ("verse2", "verse", 1.0),
        ("hook2", "chorus", 1.0), ("verse3", "verse", 1.0),
        ("hook3", "chorus", 1.0), ("outro", "outro", 0.5),
    ],
}
_GENRE_FORMS["techno"] = _GENRE_FORMS["edm"]

_GENRE_FORMS["metal"] = [
    ("intro", "intro", 0.5),
    ("riffA", "verse", 1.0),
    ("riffB", "chorus", 1.0),
    ("breakdown", "breakdown", 1.0),
    ("riffA2", "verse", 1.0),
    ("buildup", "buildup", 0.5),
    ("solo", "chorus", 1.5),       # solo section gets extra space
    ("riffFinal", "chorus", 1.0),
    ("outro", "outro", 0.5),
]


# Energy curves: (section_role, base_energy)
_ENERGY_CURVES: dict[str, dict[str, float]] = {
    "slow_burn": {
        "intro": 0.30, "verse": 0.55, "prechorus": 0.70, "chorus": 0.90,
        "bridge": 0.65, "buildup": 0.85, "breakdown": 0.45, "drop": 1.00,
        "outro": 0.30,
    },
    "immediate_hook": {
        "intro": 0.65, "verse": 0.70, "prechorus": 0.80, "chorus": 1.00,
        "bridge": 0.60, "buildup": 0.95, "breakdown": 0.50, "drop": 1.00,
        "outro": 0.50,
    },
    "build_drop_build": {
        "intro": 0.40, "verse": 0.60, "prechorus": 0.75, "chorus": 0.85,
        "bridge": 0.55, "buildup": 0.95, "breakdown": 0.30, "drop": 1.00,
        "outro": 0.35,
    },
    "call_response": {
        "intro": 0.45, "verse": 0.85, "prechorus": 0.65, "chorus": 0.95,
        "bridge": 0.70, "buildup": 0.85, "breakdown": 0.55, "drop": 0.95,
        "outro": 0.40,
    },
}


def arrangement_plan(args: dict) -> dict:
    genre = args["genre"].lower()
    target_seconds = float(args["target_length_seconds"])
    tempo = float(args["tempo"])
    curve = args.get("energy_curve", "slow_burn")
    include_bridge = bool(args.get("include_bridge", True))
    include_buildup = bool(args.get("include_buildup", True))

    form = _GENRE_FORMS.get(genre)
    if form is None:
        # Fallback to pop with simpler structure
        form = _GENRE_FORMS["pop"]

    # Filter sections per flags
    filtered = []
    for name, role, weight in form:
        if role == "bridge" and not include_bridge:
            continue
        if role == "buildup" and not include_buildup:
            continue
        filtered.append((name, role, weight))

    # Compute total weight, then map to bars
    total_weight = sum(w for _, _, w in filtered)
    target_beats = (target_seconds * tempo) / 60.0
    target_bars_total = target_beats / 4.0

    # Snap each section to a multiple-of-4 bars (musical phrase rounding),
    # except short sections (intro/outro/buildup/breakdown) which can be 4.
    energy_map = _ENERGY_CURVES.get(curve, _ENERGY_CURVES["slow_burn"])
    sections_out = []
    for name, role, weight in filtered:
        share = weight / total_weight
        raw_bars = share * target_bars_total
        # Snap to musical phrase length
        if role in ("intro", "outro", "breakdown", "buildup", "prechorus"):
            bars = max(4, round(raw_bars / 2) * 2)   # multiples of 2, min 4
        else:
            bars = max(4, round(raw_bars / 4) * 4)   # multiples of 4, min 4
        sections_out.append({
            "name": name,
            "role": role,
            "bars": bars,
            "energy": energy_map.get(role, 0.6),
        })

    total_bars_actual = sum(s["bars"] for s in sections_out)
    total_seconds_actual = (total_bars_actual * 4 * 60) / tempo

    return {
        "ok": True,
        "genre": genre,
        "tempo": tempo,
        "energy_curve": curve,
        "sections": sections_out,
        "total_bars": total_bars_actual,
        "total_seconds": total_seconds_actual,
    }


HANDLERS = {
    "progression_generate": progression_generate,
    "voice_lead":           voice_lead,
    "arrangement_plan":     arrangement_plan,
}
