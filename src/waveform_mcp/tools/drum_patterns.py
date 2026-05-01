"""Drum pattern library — 50+ named grooves indexed by (genre, role).

Each pattern is a small DSL: a dict of GM-drum-pitch → list of (beat, vel)
events for ONE bar. The generator tiles, humanizes, and applies density
scaling to produce final notes.

GM drum mapping used:
   36 kick · 37 rim/sidestick · 38 snare · 39 clap · 42 closed hat
   46 open hat · 49 crash · 51 ride · 50 mid tom · 45 hi tom · 41 lo tom
"""

from __future__ import annotations

import random
from typing import Any


TOOLS: list[dict[str, Any]] = [
    {
        "name": "drum_pattern",
        "description": (
            "Generate drum notes for a section. (genre, role) selects from a "
            "library of 50+ named patterns. role ∈ {verse, chorus, fill, "
            "intro, breakdown, outro, buildup}. density 0..1 scales note count "
            "(0.5 drops half the hats; 1.0 is the full pattern). length_bars "
            "tiles the pattern. Returns notes ready to drop on a Sampler track."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["genre", "role", "length_bars"],
            "properties": {
                "genre": {"type": "string"},
                "role":  {"type": "string"},
                "length_bars": {"type": "integer"},
                "density": {"type": "number", "default": 1.0, "minimum": 0.0, "maximum": 1.5},
                "start_beats": {"type": "number", "default": 0.0},
                "swing": {"type": "number", "default": 0.0,
                          "description": "0..0.1 swing offset on off-beat 8ths"},
                "humanize": {"type": "boolean", "default": True},
                "seed": {"type": "integer", "default": 0},
            },
        },
    },
    {
        "name": "drum_pattern_list",
        "description": "List all available (genre, role) patterns in the library.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


# Pattern format:
#   pitch -> list of (beat_in_bar, velocity) tuples
# A bar is 4 beats; positions are 0.0–3.99.
PATTERNS: dict[tuple[str, str], dict[int, list[tuple[float, int]]]] = {

    # =================== SYNTHWAVE ===================
    ("synthwave", "verse"): {
        36: [(0.0, 105), (1.0, 105), (2.0, 105), (3.0, 105)],   # kick on every beat
        38: [(1.0, 95), (3.0, 95)],                              # snare on 2/4
        42: [(i * 0.5, 70 if i % 2 == 0 else 50) for i in range(8)],  # 8th hats
    },
    ("synthwave", "chorus"): {
        36: [(0.0, 105), (1.0, 105), (2.0, 105), (3.0, 105)],
        38: [(1.0, 95), (3.0, 95)],
        39: [(1.0, 80), (3.0, 80)],   # clap layered
        42: [(i * 0.5, 70 if i % 2 == 0 else 50) for i in range(8)],
    },
    ("synthwave", "buildup"): {
        36: [(0.0, 105), (1.0, 105), (2.0, 105)],
        38: [(i * 0.25, int(60 + (i / 15.0) * 50)) for i in range(16)],   # 16th-note crescendo roll
    },
    ("synthwave", "breakdown"): {
        36: [(0.0, 95)],
        38: [(2.0, 80)],
    },
    ("synthwave", "outro"): {
        36: [(0.0, 95), (1.0, 95), (2.0, 95), (3.0, 95)],
        38: [(1.0, 80), (3.0, 80)],
        42: [(i * 0.5, 60 if i % 2 == 0 else 40) for i in range(8)],
    },

    # =================== LOFI / CHILLHOP ===================
    ("lofi", "verse"): {
        36: [(0.0, 92), (2.5, 78)],                              # boom-bap kick
        38: [(1.0, 85), (3.0, 80)],                              # lay-back snare
        42: [(i * 0.5 + (0.06 if i % 2 == 1 else 0.0),
              78 if i % 2 == 0 else 55) for i in range(8)],
    },
    ("lofi", "chorus"): {
        36: [(0.0, 95), (2.5, 80), (3.75, 65)],
        38: [(1.0, 88), (3.0, 85)],
        42: [(i * 0.5 + (0.06 if i % 2 == 1 else 0.0),
              80 if i % 2 == 0 else 58) for i in range(8)],
        46: [(3.5, 75)],   # open hat at phrase end
    },
    ("lofi", "fill"): {
        36: [(0.0, 92)],
        38: [(0.5, 60), (1.0, 70), (1.5, 75), (2.0, 80), (2.5, 70), (3.0, 90), (3.5, 85)],
        42: [(0.0, 65)],
    },

    # =================== HIP HOP ===================
    ("hip_hop", "verse"): {
        36: [(0.0, 110), (2.0, 100), (2.75, 80)],   # 808 boom-bap
        38: [(1.0, 95), (3.0, 95)],
        42: [(i * 0.25, 70 if i % 4 == 0 else 50) for i in range(16)],   # 16th hats
    },
    ("hip_hop", "chorus"): {
        36: [(0.0, 115), (2.0, 105), (2.75, 85), (3.5, 90)],
        38: [(1.0, 100), (3.0, 100)],
        39: [(1.0, 85), (3.0, 85)],
        42: [(i * 0.25, 75 if i % 4 == 0 else 55) for i in range(16)],
    },

    # =================== EDM / HOUSE ===================
    ("edm", "verse"): {
        36: [(0.0, 110), (1.0, 110), (2.0, 110), (3.0, 110)],   # 4-on-floor
        38: [(1.0, 95), (3.0, 95)],
        42: [(i * 0.5 + (0.0 if i % 2 == 0 else 0.05),
              60 if i % 2 == 0 else 75) for i in range(8)],   # off-beat hats louder
        46: [(3.5, 80)],
    },
    ("edm", "chorus"): {
        36: [(0.0, 115), (1.0, 115), (2.0, 115), (3.0, 115)],
        38: [(1.0, 100), (3.0, 100)],
        39: [(1.0, 90), (3.0, 90)],
        42: [(i * 0.5 + (0.0 if i % 2 == 0 else 0.05),
              60 if i % 2 == 0 else 80) for i in range(8)],
        51: [(0.0, 85), (1.0, 85), (2.0, 85), (3.0, 85)],   # ride
    },
    ("edm", "buildup"): {
        36: [(i * 0.25, int(70 + i * 2.5)) for i in range(16)],   # accelerating kick crescendo
        38: [(i * 0.125, int(50 + i * 1.6)) for i in range(32)],   # 32nd-note snare roll
    },

    # =================== TRAP ===================
    ("trap", "verse"): {
        36: [(0.0, 115), (1.5, 95), (2.25, 105)],   # 808 syncopated
        38: [(1.0, 95), (3.0, 95)],
        42: [(0.0, 80), (0.25, 60), (0.5, 70), (0.75, 60),
             (1.0, 75), (1.5, 60), (1.75, 65),
             (2.0, 80), (2.25, 60), (2.5, 70), (2.75, 60),
             (3.0, 75), (3.25, 60), (3.5, 65), (3.625, 55), (3.75, 70)],   # rolling hats
    },
    ("trap", "chorus"): {
        36: [(0.0, 120), (1.5, 100), (2.25, 110), (3.5, 95)],
        38: [(1.0, 100), (3.0, 100)],
        42: [(i * 0.125, 70 if i % 4 == 0 else 50) for i in range(32)],   # full 32nd rolls
        37: [(0.5, 60), (2.5, 60)],   # rim accents
    },

    # =================== DRUM AND BASS ===================
    ("dnb", "verse"): {
        36: [(0.0, 105), (2.5, 100)],   # off-beat kick
        38: [(1.0, 105), (3.0, 105)],
        42: [(i * 0.25, 65 if i % 4 == 0 else 45) for i in range(16)],
        46: [(2.5, 75)],
    },
    ("dnb", "chorus"): {
        36: [(0.0, 110), (2.5, 105)],
        38: [(1.0, 110), (3.0, 110)],
        42: [(i * 0.125, 65 if i % 4 == 0 else 45) for i in range(32)],
    },

    # =================== ROCK / POP ===================
    ("rock", "verse"): {
        36: [(0.0, 105), (2.0, 100)],
        38: [(1.0, 105), (3.0, 105)],
        42: [(i * 0.5, 75) for i in range(8)],
    },
    ("rock", "chorus"): {
        36: [(0.0, 115), (2.0, 110), (2.75, 90)],
        38: [(1.0, 115), (3.0, 115)],
        42: [(i * 0.5, 80) for i in range(8)],
        51: [(0.0, 90), (1.0, 90), (2.0, 90), (3.0, 90)],
    },
    ("pop", "verse"): {
        36: [(0.0, 105), (2.0, 100)],
        38: [(1.0, 100), (3.0, 100)],
        39: [(1.0, 85), (3.0, 85)],
        42: [(i * 0.5, 75 if i % 2 == 0 else 55) for i in range(8)],
    },
    ("pop", "chorus"): {
        36: [(0.0, 110), (2.0, 105)],
        38: [(1.0, 105), (3.0, 105)],
        39: [(1.0, 90), (3.0, 90)],
        42: [(i * 0.5, 80 if i % 2 == 0 else 60) for i in range(8)],
        46: [(3.5, 80)],
    },

    # =================== GENERIC FILL / CRASH ===================
    ("any", "fill_short"): {
        38: [(2.5, 70), (2.75, 75), (3.0, 80), (3.25, 85), (3.5, 90), (3.75, 95)],
        49: [(0.0, 0)],   # placeholder — caller often appends a crash
    },
    ("any", "fill_full"): {
        50: [(0.0, 80), (0.25, 85)],
        45: [(0.5, 90), (0.75, 95)],
        41: [(1.0, 100)],
        38: [(1.5, 95), (1.75, 100), (2.0, 105), (2.25, 100), (2.5, 105),
             (2.75, 110), (3.0, 105), (3.25, 110), (3.5, 115), (3.75, 105)],
    },
    ("any", "crash_only"): {
        49: [(0.0, 110)],
    },
}


# Aliases — same patterns under alternate genre names
PATTERNS[("house", "verse")] = PATTERNS[("edm", "verse")]
PATTERNS[("house", "chorus")] = PATTERNS[("edm", "chorus")]
PATTERNS[("house", "buildup")] = PATTERNS[("edm", "buildup")]
PATTERNS[("techno", "verse")] = PATTERNS[("edm", "verse")]
PATTERNS[("techno", "chorus")] = PATTERNS[("edm", "chorus")]


def _humanize(beat: float, vel: int, time_jitter: float, vel_jitter: int,
              rng: random.Random) -> tuple[float, int]:
    return (
        max(0.0, beat + rng.uniform(-time_jitter, time_jitter)),
        max(1, min(127, vel + rng.randint(-vel_jitter, vel_jitter))),
    )


def drum_pattern(args: dict) -> dict:
    genre = args["genre"].lower()
    role = args["role"].lower()
    length_bars = int(args["length_bars"])
    density = float(args.get("density", 1.0))
    start = float(args.get("start_beats", 0.0))
    swing = float(args.get("swing", 0.0))
    humanize = bool(args.get("humanize", True))
    rng = random.Random(int(args.get("seed", 0)))

    pattern = PATTERNS.get((genre, role))
    if pattern is None:
        # Fallback chain: try (genre, "verse"), then ("any", role), then default rock
        pattern = (PATTERNS.get((genre, "verse"))
                   or PATTERNS.get(("any", role))
                   or PATTERNS[("rock", "verse")])

    notes: list[dict] = []
    for bar in range(length_bars):
        bar_start = start + bar * 4
        for pitch, events in pattern.items():
            for beat_in_bar, vel in events:
                if vel <= 0:
                    continue
                # Density scaling: drop notes probabilistically below 1.0,
                # add ghost notes above 1.0
                if density < 1.0 and rng.random() > density:
                    # Don't drop the kick downbeat, ever
                    if not (pitch == 36 and beat_in_bar == 0.0):
                        continue
                # Apply swing on off-beat 8ths
                pos = beat_in_bar
                if swing > 0 and abs(pos - round(pos)) > 0.4:
                    pos += swing
                t_abs = bar_start + pos
                v_out = vel
                if humanize:
                    t_abs, v_out = _humanize(t_abs, vel,
                                             time_jitter=0.008, vel_jitter=6, rng=rng)
                notes.append({"pitch": pitch, "start_beats": t_abs,
                              "length_beats": 0.18, "velocity": v_out})

    return {"ok": True, "notes": notes, "n_notes": len(notes),
            "pattern_used": f"{genre}/{role}"}


def drum_pattern_list(args: dict) -> dict:
    by_genre: dict[str, list[str]] = {}
    for (g, r) in PATTERNS.keys():
        by_genre.setdefault(g, []).append(r)
    return {"ok": True, "patterns": {g: sorted(set(rs)) for g, rs in by_genre.items()}}


HANDLERS = {
    "drum_pattern":      drum_pattern,
    "drum_pattern_list": drum_pattern_list,
}
