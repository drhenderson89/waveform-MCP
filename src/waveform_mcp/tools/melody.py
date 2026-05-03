"""Melodic generation primitives — turn the LLM's high-level intent
("an arch-shaped 4-bar melody in A minor") into concrete notes.

These are SHARED tools — every composer can call them. The point is to
stop hardcoding melodies inside `_synthwave_lead`/`_lofi_melody` and
instead build songs by composing primitive calls.

Tools:
  - melody_generate    : rule-based melody from scale + contour + density
  - arp_pattern        : arpeggio over a chord
  - bassline_generate  : bass under a progression with selectable feel
  - motif_develop      : transform a motif (transpose/invert/retrograde/...)

All tools return notes in our standard shape:
    {pitch: int, start_beats: float, length_beats: float, velocity: int}
"""

from __future__ import annotations

import random
from typing import Any

from .. import music_theory_data as data


TOOLS: list[dict[str, Any]] = [
    {
        "name": "melody_generate",
        "description": (
            "Generate a melody from a scale and contour. Returns notes "
            "(pitch/start_beats/length_beats/velocity) ready to drop into a "
            "MIDI clip. contour ∈ {arch, descending, ascending, wave, "
            "question_answer, static}. density ∈ {sparse, medium, busy}. "
            "Notes are rhythmically diverse — quarter, eighth, dotted, "
            "with rests — humanized for natural feel."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["root", "mode", "length_beats"],
            "properties": {
                "root": {"type": "string", "description": "Note name e.g. 'A', 'C#'"},
                "mode": {"type": "string", "description": "Scale mode e.g. 'natural_minor', 'dorian'"},
                "length_beats": {"type": "number"},
                "contour": {"type": "string", "enum":
                            ["arch", "descending", "ascending", "wave", "question_answer", "static"],
                            "default": "arch"},
                "density": {"type": "string", "enum": ["sparse", "medium", "busy"], "default": "medium"},
                "register_octave": {"type": "integer", "default": 5,
                                    "description": "Center octave (4=middle, 5=above middle)"},
                "seed": {"type": "integer", "default": 0},
            },
        },
    },
    {
        "name": "arp_pattern",
        "description": (
            "Generate an arpeggio over a chord. Returns notes for the given "
            "length. direction ∈ {up, down, up_down, down_up, random, "
            "octave_alternate}. rate ∈ {quarter, eighth, sixteenth, triplet}. "
            "octaves controls how many octaves the cycle spans."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["chord_pitches", "length_beats"],
            "properties": {
                "chord_pitches": {"type": "array", "items": {"type": "integer"}},
                "length_beats": {"type": "number"},
                "rate": {"type": "string", "enum": ["quarter", "eighth", "sixteenth", "triplet"], "default": "sixteenth"},
                "direction": {"type": "string", "enum":
                              ["up", "down", "up_down", "down_up", "random", "octave_alternate"],
                              "default": "up"},
                "octaves": {"type": "integer", "default": 2},
                "start_beats": {"type": "number", "default": 0.0},
                "velocity_base": {"type": "integer", "default": 78},
                "accent_downbeats": {"type": "boolean", "default": True},
            },
        },
    },
    {
        "name": "bassline_generate",
        "description": (
            "Generate a bassline under a chord progression. feel ∈ {pump, "
            "walking, half_time, sub, octave_jumps, dub}. Each chord lasts "
            "chord_duration_beats. Roots are the chord-root MIDI pitches "
            "(typically octave 2-3)."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["roots", "chord_duration_beats"],
            "properties": {
                "roots": {"type": "array", "items": {"type": "integer"},
                          "description": "Bass roots, one per chord"},
                "chord_duration_beats": {"type": "number", "default": 8.0},
                "feel": {"type": "string", "enum":
                         ["pump", "walking", "half_time", "sub", "octave_jumps", "dub"],
                         "default": "pump"},
                "start_beats": {"type": "number", "default": 0.0},
                "scale_intervals": {"type": "array", "items": {"type": "integer"},
                                    "description": "Scale degrees in semitones from root, used by 'walking' feel"},
            },
        },
    },
    {
        "name": "motif_develop",
        "description": (
            "Transform a motif (set of notes) into a variation. transformation "
            "∈ {transpose, invert, retrograde, augment, diminish, sequence_up, "
            "sequence_down}. transpose: shift by `semitones`. invert: mirror "
            "around `axis_pitch`. retrograde: reverse note order. augment: "
            "stretch durations by `factor`. sequence_up/down: transpose by "
            "`semitones` AND repeat."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["notes", "transformation"],
            "properties": {
                "notes": {"type": "array"},
                "transformation": {"type": "string", "enum":
                                   ["transpose", "invert", "retrograde", "augment",
                                    "diminish", "sequence_up", "sequence_down"]},
                "semitones": {"type": "integer", "default": 0},
                "axis_pitch": {"type": "integer", "default": 60,
                               "description": "Mirror axis for inversion"},
                "factor": {"type": "number", "default": 2.0,
                           "description": "Duration scale for augment/diminish"},
                "offset_beats": {"type": "number", "default": 0.0,
                                 "description": "Shift the whole result"},
            },
        },
    },
]


# ---------- helpers ----------


_MODE_ALIASES = {
    "minor":   "natural_minor",   # callers commonly say "minor" when they
    "min":     "natural_minor",   #   mean "natural_minor"
    "maj":     "major",
}


def _scale_pitches(root: str, mode: str, octave: int, span_octaves: int = 2) -> list[int]:
    """Return a list of MIDI pitches in the given scale, spanning N octaves
    starting at `octave`. e.g. ('A', 'natural_minor', 4, 2) returns ascending
    A natural-minor pitches from A4 up across 2 octaves.

    Accepts shorthand aliases ("minor" → "natural_minor", etc.).
    """
    if root not in data.NOTE_NAMES:
        raise ValueError(f"unknown root: {root}")
    mode = _MODE_ALIASES.get(mode, mode)
    if mode not in data.SCALES:
        raise ValueError(f"unknown mode: {mode}")
    intervals = data.SCALES[mode]
    base_midi = (octave + 1) * 12 + data.NOTE_NAMES[root]
    out: list[int] = []
    for oct_offset in range(span_octaves + 1):
        for iv in intervals[:-1]:  # skip the octave repeat (last interval)
            out.append(base_midi + oct_offset * 12 + iv)
    out.append(base_midi + span_octaves * 12)  # final tonic at top
    return out


def _density_to_rates(density: str) -> tuple[list[float], list[float]]:
    """Map a density label to a candidate (durations, rest_durations) pool."""
    if density == "sparse":
        return ([1.0, 1.5, 2.0], [0.5, 1.0, 1.5])
    if density == "busy":
        return ([0.25, 0.5, 0.5, 0.5, 0.75], [0.0, 0.0, 0.25])
    return ([0.5, 0.75, 1.0, 1.0, 1.5], [0.0, 0.25, 0.5])  # medium


def _humanize(beats: float, vel: int, *, time_jitter: float = 0.012, vel_jitter: int = 8) -> tuple[float, int]:
    return (
        max(0.0, beats + random.uniform(-time_jitter, time_jitter)),
        max(1, min(127, vel + random.randint(-vel_jitter, vel_jitter))),
    )


# ---------- impls ----------


def melody_generate(args: dict) -> dict:
    """Build a melody by walking through scale degrees following a contour
    shape, with rhythmic variation drawn from the density profile.
    """
    rng = random.Random(int(args.get("seed", 0)))
    root = args["root"]
    mode = args["mode"]
    length_beats = float(args["length_beats"])
    contour = args.get("contour", "arch")
    density = args.get("density", "medium")
    octave = int(args.get("register_octave", 5))

    scale = _scale_pitches(root, mode, octave, span_octaves=1)
    n_steps = len(scale)
    durations, rests = _density_to_rates(density)

    # Build a target index curve (which scale degree at each percentage of the melody)
    def index_at(t: float) -> int:
        """t in 0..1 → scale-degree index"""
        if contour == "arch":
            # 0 → low, 0.5 → high, 1 → low
            x = 1.0 - abs(2.0 * t - 1.0)
            return int(x * (n_steps - 1))
        if contour == "ascending":
            return int(t * (n_steps - 1))
        if contour == "descending":
            return int((1.0 - t) * (n_steps - 1))
        if contour == "wave":
            import math
            x = 0.5 + 0.5 * math.sin(2 * math.pi * t * 2)  # 2 cycles
            return int(x * (n_steps - 1))
        if contour == "question_answer":
            # First half ascends, second half descends to a different point
            if t < 0.5:
                return int((t / 0.5) * (n_steps - 1))
            else:
                return int(((1.0 - t) / 0.5) * (n_steps - 2))
        return n_steps // 2  # static

    notes = []
    cursor = 0.0
    while cursor < length_beats - 0.05:
        # Choose a duration; clamp to remaining length
        dur = rng.choice(durations)
        if cursor + dur > length_beats:
            dur = max(0.25, length_beats - cursor)
        # Pick a scale degree near the contour target with a small random jitter
        t = cursor / length_beats
        target = index_at(t)
        idx = max(0, min(n_steps - 1, target + rng.randint(-1, 1)))
        pitch = scale[idx]
        # Velocity: slight crescendo through arch peak, descrescendo away
        contour_vel = 80 + int(20 * (1.0 - abs(2 * t - 1.0))) if contour == "arch" else 80
        ts, vel = _humanize(cursor, contour_vel)
        notes.append({"pitch": pitch, "start_beats": ts,
                      "length_beats": max(0.05, dur - 0.05), "velocity": vel})
        cursor += dur
        # Optional rest
        if rng.random() < 0.25 and rests:
            rest = rng.choice(rests)
            if cursor + rest <= length_beats:
                cursor += rest

    return {"ok": True, "notes": notes, "scale_pitches": scale, "n_notes": len(notes)}


def arp_pattern(args: dict) -> dict:
    """Cycle through chord pitches at a given rate and direction."""
    chord = list(args["chord_pitches"])
    length = float(args["length_beats"])
    rate = args.get("rate", "sixteenth")
    direction = args.get("direction", "up")
    octaves = int(args.get("octaves", 2))
    start = float(args.get("start_beats", 0.0))
    vel_base = int(args.get("velocity_base", 78))
    accent = bool(args.get("accent_downbeats", True))

    rate_map = {"quarter": 1.0, "eighth": 0.5, "sixteenth": 0.25, "triplet": 1.0 / 3.0}
    step = rate_map[rate]

    # Build the cycle pitches based on direction
    expanded = []
    for o in range(octaves):
        expanded += [p + o * 12 for p in chord]

    if direction == "up":
        cycle = expanded
    elif direction == "down":
        cycle = list(reversed(expanded))
    elif direction == "up_down":
        cycle = expanded + list(reversed(expanded[1:-1]))
    elif direction == "down_up":
        cycle = list(reversed(expanded)) + expanded[1:-1]
    elif direction == "octave_alternate":
        # alternate low/high octave per step
        cycle = []
        for i in range(max(len(expanded), 8)):
            base_idx = i % len(chord)
            oct_offset = (i % octaves) * 12 if octaves > 1 else 0
            cycle.append(chord[base_idx] + oct_offset)
    else:  # random
        cycle = list(expanded)
        random.shuffle(cycle)

    notes = []
    n_steps = int(length / step)
    for i in range(n_steps):
        pitch = cycle[i % len(cycle)]
        t = start + i * step
        v = vel_base + (10 if accent and i % 4 == 0 else 0)
        notes.append({"pitch": pitch, "start_beats": t,
                      "length_beats": step * 0.85, "velocity": v})
    return {"ok": True, "notes": notes, "n_notes": len(notes)}


def bassline_generate(args: dict) -> dict:
    """Bass under a chord progression. The `feel` argument is the main
    driver of rhythmic character.
    """
    roots = list(args["roots"])
    chord_dur = float(args.get("chord_duration_beats", 8.0))
    feel = args.get("feel", "pump")
    start = float(args.get("start_beats", 0.0))
    scale_intervals = args.get("scale_intervals", [0, 2, 3, 5, 7, 8, 10])

    notes = []
    for chord_idx, root in enumerate(roots):
        b = start + chord_idx * chord_dur

        if feel == "sub":
            # One held note per chord — minimal, like dub sub bass
            notes.append({"pitch": root, "start_beats": b,
                          "length_beats": chord_dur - 0.1, "velocity": 92})

        elif feel == "half_time":
            # Root on 1, root-up-octave on 3 (per 4-beat segment)
            for seg in range(int(chord_dur / 4)):
                seg_b = b + seg * 4
                notes.append({"pitch": root, "start_beats": seg_b,
                              "length_beats": 1.95, "velocity": 88})
                notes.append({"pitch": root + 12, "start_beats": seg_b + 2,
                              "length_beats": 1.95, "velocity": 80})

        elif feel == "pump":
            # 8th-note pump with octave jumps on offbeats of beats 2 and 4
            for i in range(int(chord_dur * 2)):
                pitch = root + (12 if i % 4 == 2 else 0)
                t = b + i * 0.5
                v = 95 if i % 2 == 0 else 80
                notes.append({"pitch": pitch, "start_beats": t,
                              "length_beats": 0.45, "velocity": v})

        elif feel == "octave_jumps":
            # Quarter notes alternating root, octave-up
            for i in range(int(chord_dur)):
                pitch = root + (12 if i % 2 == 1 else 0)
                notes.append({"pitch": pitch, "start_beats": b + i,
                              "length_beats": 0.95, "velocity": 90})

        elif feel == "walking":
            # Walking bass: root, third, fifth, seventh — quarter notes
            walk_intervals = [0, scale_intervals[2], scale_intervals[4],
                              scale_intervals[6 if len(scale_intervals) > 6 else -1]]
            for i in range(int(chord_dur)):
                interval = walk_intervals[i % len(walk_intervals)]
                notes.append({"pitch": root + interval, "start_beats": b + i,
                              "length_beats": 0.95, "velocity": 86})

        elif feel == "walking_jazz":
            # Authentic jazz walking bass — picks one of 7 patterns per
            # bar from the Paul Chambers / Ron Carter vocabulary
            # (1-3-5-approach, 1-3-6-5, 1-3-6-b6, triplet on beat 4,
            # octave drop, ghost 8th, 1-5-3-approach inversion).
            # Pattern index varies per bar so the line never repeats.
            si = scale_intervals
            third  = si[2] if len(si) > 2 else 4
            fifth  = si[4] if len(si) > 4 else 7
            sixth  = si[5] if len(si) > 5 else 9
            bar_len = 4    # assumes 4/4
            n_bars = max(1, int(chord_dur // bar_len))
            chord_seed = int(args.get("seed", 0)) + chord_idx * 11
            for inner_bar in range(n_bars):
                bar_b = b + inner_bar * bar_len
                next_root = roots[(chord_idx + 1) % len(roots)] \
                    if inner_bar == n_bars - 1 else root
                approach = next_root - 1 if next_root > root else next_root + 1
                h = (chord_seed + inner_bar * 13 + 5) % 7
                if h == 0:        # 1-3-5-approach
                    pat = [(0, root), (1, root+third), (2, root+fifth), (3, approach)]
                elif h == 1:      # 1-3-6-5
                    pat = [(0, root), (1, root+third), (2, root+sixth), (3, root+fifth)]
                elif h == 2:      # 1-3-6-b6
                    pat = [(0, root), (1, root+third), (2, root+sixth), (3, root+8)]
                elif h == 3:      # 1-3-5 + chromatic triplet on beat 4
                    pat = [(0, root), (1, root+third), (2, root+fifth)]
                    a2 = approach - (1 if next_root > root else -1)
                    for i, p in enumerate([a2, approach, next_root]):
                        notes.append({"pitch": p,
                                       "start_beats": bar_b + 3.0 + i*(1.0/3.0),
                                       "length_beats": 0.30, "velocity": 78})
                elif h == 4:      # octave drop
                    pat = [(0, root), (1, root+third), (2, root+fifth), (3, root-12)]
                elif h == 5:      # ghost 8th
                    pat = [(0, root), (1, root+third), (2, root+fifth), (3, approach)]
                    notes.append({"pitch": approach - (1 if next_root > root else -1),
                                   "start_beats": bar_b + 3.5,
                                   "length_beats": 0.25, "velocity": 50})
                else:             # 1-5-3-approach inversion
                    pat = [(0, root), (1, root+fifth), (2, root+third), (3, approach)]
                for off, p in pat:
                    v = 92 if off == 0 else (76 if off % 2 == 0 else 72)
                    notes.append({"pitch": p, "start_beats": bar_b + off,
                                   "length_beats": 0.85, "velocity": v})

        elif feel == "dub":
            # Dub bass: root on 1, off-beat root-up-fifth on 2.5
            for seg in range(int(chord_dur / 4)):
                seg_b = b + seg * 4
                notes.append({"pitch": root, "start_beats": seg_b,
                              "length_beats": 1.95, "velocity": 90})
                notes.append({"pitch": root + 7, "start_beats": seg_b + 2.5,
                              "length_beats": 1.45, "velocity": 75})

    return {"ok": True, "notes": notes, "n_notes": len(notes)}


def motif_develop(args: dict) -> dict:
    """Transform a motif. Returns a NEW notes list (input untouched)."""
    notes = list(args["notes"])
    if not notes:
        return {"ok": True, "notes": []}
    transformation = args["transformation"]
    semitones = int(args.get("semitones", 0))
    axis = int(args.get("axis_pitch", 60))
    factor = float(args.get("factor", 2.0))
    offset = float(args.get("offset_beats", 0.0))

    out: list[dict] = []

    if transformation == "transpose":
        out = [{**n, "pitch": n["pitch"] + semitones,
                "start_beats": n["start_beats"] + offset} for n in notes]

    elif transformation == "invert":
        # Mirror pitches around axis: new = 2*axis - old
        out = [{**n, "pitch": 2 * axis - n["pitch"],
                "start_beats": n["start_beats"] + offset} for n in notes]

    elif transformation == "retrograde":
        # Reverse note ORDER (with timing flipped within the motif's span)
        sorted_notes = sorted(notes, key=lambda n: n["start_beats"])
        first_t = sorted_notes[0]["start_beats"]
        last = sorted_notes[-1]
        last_end = last["start_beats"] + last["length_beats"]
        span = last_end - first_t
        for n in sorted_notes:
            new_start = first_t + (last_end - (n["start_beats"] + n["length_beats"]))
            out.append({**n, "start_beats": new_start + offset})

    elif transformation == "augment":
        # Stretch ALL durations and start times by factor
        if not notes:
            out = []
        else:
            first_t = min(n["start_beats"] for n in notes)
            for n in notes:
                rel = n["start_beats"] - first_t
                out.append({**n,
                            "start_beats": first_t + rel * factor + offset,
                            "length_beats": n["length_beats"] * factor})

    elif transformation == "diminish":
        first_t = min(n["start_beats"] for n in notes)
        for n in notes:
            rel = n["start_beats"] - first_t
            out.append({**n,
                        "start_beats": first_t + rel / factor + offset,
                        "length_beats": n["length_beats"] / factor})

    elif transformation == "sequence_up":
        # Original + a transposed copy after it
        first_t = min(n["start_beats"] for n in notes)
        last_end = max(n["start_beats"] + n["length_beats"] for n in notes)
        span = last_end - first_t
        for n in notes:
            out.append({**n, "start_beats": n["start_beats"] + offset})
        for n in notes:
            out.append({**n, "pitch": n["pitch"] + abs(semitones or 2),
                        "start_beats": n["start_beats"] + span + offset})

    elif transformation == "sequence_down":
        first_t = min(n["start_beats"] for n in notes)
        last_end = max(n["start_beats"] + n["length_beats"] for n in notes)
        span = last_end - first_t
        for n in notes:
            out.append({**n, "start_beats": n["start_beats"] + offset})
        for n in notes:
            out.append({**n, "pitch": n["pitch"] - abs(semitones or 2),
                        "start_beats": n["start_beats"] + span + offset})

    return {"ok": True, "notes": out, "n_notes": len(out)}


HANDLERS = {
    "melody_generate":   melody_generate,
    "arp_pattern":       arp_pattern,
    "bassline_generate": bassline_generate,
    "motif_develop":     motif_develop,
}
