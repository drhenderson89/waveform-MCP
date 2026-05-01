"""Music theory MCP tool surface.

Exposes the durable knowledge layer in music_theory_data.py as queryable tools.
The agent can ask things like:
  - "give me a Dorian vamp progression"
  - "what chords are diatonic to A minor"
  - "what tempo range is typical for hip-hop"
  - "what's the velocity for mf"
and get structured answers it can drop into a composition.

Tools deliberately match the data model 1:1 — no clever inference, no
generation. Generation lives in tools/composer.py and queries this module
for inputs.
"""

from __future__ import annotations

from typing import Any

from .. import music_theory_data as data


TOOLS: list[dict[str, Any]] = [
    {
        "name": "theory_scale",
        "description": (
            "Return the notes of a scale starting at a root. Returns MIDI numbers, "
            "note names, and interval pattern. mode is one of: major, natural_minor, "
            "harmonic_minor, melodic_minor_asc, ionian, dorian, phrygian, lydian, "
            "mixolydian, aeolian, locrian, major_pentatonic, minor_pentatonic, blues, "
            "whole_tone, half_whole_dim, whole_half_dim, chromatic, hungarian_minor, "
            "phrygian_dominant."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["root", "mode"],
            "properties": {
                "root": {"type": "string", "description": "Note name like 'C', 'F#', 'Bb'"},
                "mode": {"type": "string"},
                "octave": {"type": "integer", "default": 4, "description": "C4 = 60"},
            },
        },
    },
    {
        "name": "theory_modes",
        "description": "List the 7 church modes with their characteristic degree, vibe, and typical use.",
        "inputSchema": {"type": "object", "properties": {"mode": {"type": "string", "description": "Optional: one mode"}}},
    },
    {
        "name": "theory_diatonic_chords",
        "description": (
            "Return the 7 diatonic chords for a key/mode. Set sevenths=true for 7th-chord "
            "qualities (Imaj7, ii7, etc.) instead of triads (I, ii, etc.). Returns Roman "
            "numerals + the actual chord pitches (MIDI) for each."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["root", "mode"],
            "properties": {
                "root": {"type": "string"},
                "mode": {"type": "string", "enum": ["major", "natural_minor", "harmonic_minor"]},
                "sevenths": {"type": "boolean", "default": False},
                "octave": {"type": "integer", "default": 4},
            },
        },
    },
    {
        "name": "theory_chord_progression",
        "description": (
            "Look up a named chord progression. Returns the Roman-numeral sequence + tags + "
            "examples. Without args, lists all 24 named progressions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Progression name (e.g. 'axis_pop', 'ii_V_I')"},
                "tag": {"type": "string", "description": "Filter by tag (e.g. 'lofi', 'jazz', 'pop')"},
            },
        },
    },
    {
        "name": "theory_cadences",
        "description": "Return cadence types — pattern, feel, and use. Optional `name` for one specific cadence.",
        "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}},
    },
    {
        "name": "theory_song_form",
        "description": (
            "Return a macro song form layout. Without args, lists all 14 forms. With name, returns "
            "the section sequence, applicable genres, and total bar range."
        ),
        "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}},
    },
    {
        "name": "theory_section",
        "description": (
            "Return song section info: typical bar counts, role, dynamic level, valid next sections. "
            "Without args, lists all 15 section types (intro, verse, chorus, bridge, drop, etc.)."
        ),
        "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}},
    },
    {
        "name": "theory_genre",
        "description": (
            "Return genre conventions: BPM range, time signature, key tendency, instruments, form, "
            "hallmark progressions. Without args, lists all 22 genres."
        ),
        "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}},
    },
    {
        "name": "theory_arrangement_layers",
        "description": (
            "Return arrangement layers: foundation, harmonic_glue, lead, counter_melody, atmosphere, "
            "ear_candy. Optional `section` returns per-section presence values for each layer (0..1) "
            "from LAYER_PRESENCE_BY_SECTION — drives the 'which layers should be active' decision."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "section": {"type": "string", "description": "intro/verse/chorus/etc."},
            },
        },
    },
    {
        "name": "theory_velocity",
        "description": (
            "Convert dynamic markings (ppp/pp/p/mp/mf/f/ff/fff) to MIDI velocity values, or look up "
            "all of them. Includes ACCENT_BUMP and GHOST_NOTE_MAX_VEL constants."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"dynamic": {"type": "string", "description": "ppp/pp/p/mp/mf/f/ff/fff"}},
        },
    },
    {
        "name": "theory_rhythm",
        "description": (
            "Return rhythm/meter info: time signatures, syncopation patterns, swing ratios. "
            "Pass `time_sig` for one specific time signature, `syncopation` for one pattern, or "
            "neither for the full reference."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "time_sig": {"type": "string"},
                "syncopation": {"type": "string"},
            },
        },
    },
    {
        "name": "theory_voice_leading_rules",
        "description": "Return the 10 codified voice-leading rules + scoring guidance.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "theory_heuristics",
        "description": "Return the 13 songwriting heuristics (rule_of_3, contrast_required, etc.).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "theory_surprise_devices",
        "description": "Return the 7 surprise devices (truck_driver_modulation, deceptive_cadence, etc.).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "theory_borrowed_chords",
        "description": "Return the borrowed-chord (modal interchange) palette for major keys with usage notes.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "theory_mix_balance",
        "description": (
            "Return reference track-volume dB targets for a genre. Without args, "
            "returns the full table of (genre, role) → dB. With `genre`, returns "
            "the role→dB map for that genre. With `genre` AND `role`, returns the "
            "single dB value with a brief rationale. Roles: drums, kick, snare, "
            "hat, bass, sub_bass, lead, pad, keys, arp, counter, melody, ambience, "
            "fx. Genres: synthwave, lofi, edm, pop, hip_hop, cinematic, ambient."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "genre": {"type": "string"},
                "role":  {"type": "string"},
            },
        },
    },
    {
        "name": "theory_search",
        "description": (
            "Search across all theory data by substring. Returns hits across scales, progressions, "
            "cadences, sections, forms, genres, heuristics. Useful for fuzzy questions like "
            "'find anything related to lofi' or 'what mentions Phrygian'."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["q"],
            "properties": {"q": {"type": "string", "description": "Search query (case-insensitive)"}},
        },
    },
]


# ---------- impls ----------


def theory_scale(args: dict) -> dict:
    root = args["root"]
    mode = args["mode"]
    octave = int(args.get("octave", 4))
    if mode not in data.SCALES:
        return {"ok": False, "error": f"unknown mode: {mode}",
                "available_modes": list(data.SCALES.keys())}
    if root not in data.NOTE_NAMES:
        return {"ok": False, "error": f"unknown note: {root}",
                "available_roots": list(data.NOTE_NAMES.keys())}
    midi = data.scale_notes(root, mode, octave)
    names = [data.SEMITONE_TO_NAME[m % 12] for m in midi]
    return {
        "ok": True,
        "root": root, "mode": mode, "octave": octave,
        "intervals_semitones": data.SCALES[mode],
        "midi": midi,
        "notes": names,
    }


def theory_modes(args: dict) -> dict:
    name = args.get("mode")
    if name:
        info = data.MODE_CHARACTER.get(name)
        if not info:
            return {"ok": False, "error": f"unknown mode: {name}"}
        return {"ok": True, "mode": name, **info, "intervals": data.SCALES.get(name)}
    return {"ok": True, "modes": data.MODE_CHARACTER}


def theory_diatonic_chords(args: dict) -> dict:
    root = args["root"]
    mode = args["mode"]
    sevenths = bool(args.get("sevenths", False))
    octave = int(args.get("octave", 4))
    if mode not in data.DIATONIC_TRIADS:
        return {"ok": False, "error": f"mode must be one of {list(data.DIATONIC_TRIADS)}"}
    qualities = data.DIATONIC_SEVENTHS[mode] if sevenths else data.DIATONIC_TRIADS[mode]
    out = []
    for degree in range(1, 8):
        pitches = data.diatonic_chord_pitches(root, mode, degree, octave)
        out.append({
            "degree": degree,
            "roman": qualities[degree - 1],
            "midi": pitches,
            "notes": [data.SEMITONE_TO_NAME[p % 12] for p in pitches],
        })
    return {"ok": True, "root": root, "mode": mode, "sevenths": sevenths, "chords": out}


def theory_chord_progression(args: dict) -> dict:
    name = args.get("name")
    tag = args.get("tag")
    if name:
        prog = data.PROGRESSIONS.get(name)
        if not prog:
            return {"ok": False, "error": f"unknown progression: {name}"}
        return {"ok": True, "name": name, **prog}
    if tag:
        matches = {k: v for k, v in data.PROGRESSIONS.items() if tag in v.get("tags", [])}
        return {"ok": True, "tag": tag, "matches": matches}
    return {"ok": True, "all": data.PROGRESSIONS}


def theory_cadences(args: dict) -> dict:
    name = args.get("name")
    if name:
        c = data.CADENCES.get(name)
        if not c:
            return {"ok": False, "error": f"unknown cadence: {name}"}
        return {"ok": True, "name": name, **c}
    return {"ok": True, "cadences": data.CADENCES}


def theory_song_form(args: dict) -> dict:
    name = args.get("name")
    if name:
        f = data.FORMS.get(name)
        if not f:
            return {"ok": False, "error": f"unknown form: {name}"}
        return {"ok": True, "name": name, **f}
    return {"ok": True, "forms": data.FORMS}


def theory_section(args: dict) -> dict:
    name = args.get("name")
    if name:
        s = data.SECTIONS.get(name)
        if not s:
            return {"ok": False, "error": f"unknown section: {name}"}
        return {"ok": True, "name": name, **s}
    return {"ok": True, "sections": data.SECTIONS}


def theory_genre(args: dict) -> dict:
    name = args.get("name")
    if name:
        g = data.GENRES.get(name)
        if not g:
            return {"ok": False, "error": f"unknown genre: {name}"}
        return {"ok": True, "name": name, **g}
    return {"ok": True, "genres": data.GENRES}


def theory_arrangement_layers(args: dict) -> dict:
    section = args.get("section")
    if section:
        presence = data.LAYER_PRESENCE_BY_SECTION.get(section)
        if presence is None:
            return {"ok": False, "error": f"unknown section: {section}"}
        return {"ok": True, "section": section, "layer_presence": presence}
    return {
        "ok": True,
        "layers": data.ARRANGEMENT_LAYERS,
        "presence_by_section": data.LAYER_PRESENCE_BY_SECTION,
    }


def theory_velocity(args: dict) -> dict:
    dynamic = args.get("dynamic")
    if dynamic:
        v = data.VELOCITY_MAP.get(dynamic)
        if v is None:
            return {"ok": False, "error": f"unknown dynamic: {dynamic}"}
        return {"ok": True, "dynamic": dynamic, "velocity": v}
    return {
        "ok": True,
        "map": data.VELOCITY_MAP,
        "accent_bump": data.ACCENT_BUMP,
        "ghost_note_max_vel": data.GHOST_NOTE_MAX_VEL,
    }


def theory_rhythm(args: dict) -> dict:
    ts = args.get("time_sig")
    syn = args.get("syncopation")
    if ts:
        info = data.TIME_SIGS.get(ts)
        if not info:
            return {"ok": False, "error": f"unknown time signature: {ts}"}
        return {"ok": True, "time_sig": ts, **info}
    if syn:
        s = data.SYNCOPATION.get(syn)
        if s is None:
            return {"ok": False, "error": f"unknown syncopation: {syn}"}
        return {"ok": True, "syncopation": syn, "description": s}
    return {
        "ok": True,
        "time_signatures": data.TIME_SIGS,
        "syncopation_patterns": data.SYNCOPATION,
        "swing_ratios": data.SWING_RATIOS,
    }


def theory_voice_leading_rules(args: dict) -> dict:
    return {"ok": True, "rules": data.VOICE_LEADING_RULES}


def theory_heuristics(args: dict) -> dict:
    return {"ok": True, "heuristics": data.HEURISTICS}


def theory_surprise_devices(args: dict) -> dict:
    return {"ok": True, "devices": data.SURPRISE_DEVICES}


def theory_mix_balance(args: dict) -> dict:
    """Mix-balance reference levels by genre/role. See MIX_BALANCE in
    music_theory_data.py for the full table and rationale.
    """
    genre = args.get("genre")
    role = args.get("role")
    if genre and role:
        return {
            "ok": True,
            "genre": genre,
            "role": role,
            "volume_db": data.mix_level_for(genre, role),
            "rationale": (
                "Anchor: kick. Other elements stack relative to kick, with "
                "bass 5-6 dB under, lead similar to bass, pad/arp 6-9 dB under "
                "lead, ambience deepest. Master compressor + limiter expected."
            ),
        }
    if genre:
        g = data.MIX_BALANCE.get(genre.lower())
        if not g:
            return {"ok": False, "error": f"unknown genre: {genre}",
                    "available_genres": sorted(data.MIX_BALANCE.keys())}
        return {"ok": True, "genre": genre, "levels_db": g}
    return {
        "ok": True,
        "balance": data.MIX_BALANCE,
        "fallback_genre": data.MIX_BALANCE_FALLBACK_GENRE,
        "fallback_default_db": data.MIX_BALANCE_DEFAULT_DB,
    }


def theory_borrowed_chords(args: dict) -> dict:
    return {"ok": True, "borrowed_chords_major": data.BORROWED_CHORDS_MAJOR}


def theory_search(args: dict) -> dict:
    q = args["q"].lower()
    hits: dict[str, list] = {}

    def matches_dict(d: dict, label: str) -> None:
        out = []
        for key, val in d.items():
            blob = (str(key) + " " + str(val)).lower()
            if q in blob:
                out.append({"key": key, "value": val})
        if out:
            hits[label] = out

    matches_dict(data.SCALES, "scales")
    matches_dict(data.MODE_CHARACTER, "modes")
    matches_dict(data.PROGRESSIONS, "progressions")
    matches_dict(data.CADENCES, "cadences")
    matches_dict(data.SECTIONS, "sections")
    matches_dict(data.FORMS, "forms")
    matches_dict(data.GENRES, "genres")
    matches_dict(data.HEURISTICS, "heuristics")
    matches_dict(data.SURPRISE_DEVICES, "surprise_devices")
    matches_dict(data.SYNCOPATION, "syncopation")
    matches_dict(data.TIME_SIGS, "time_signatures")
    matches_dict(data.VOICE_LEADING_RULES, "voice_leading_rules")
    matches_dict(data.NON_CHORD_TONES, "non_chord_tones")
    matches_dict(data.CONTOURS, "melodic_contours")
    matches_dict(data.BORROWED_CHORDS_MAJOR, "borrowed_chords")
    return {"ok": True, "query": q, "hits": hits, "total_categories": len(hits)}


HANDLERS = {
    "theory_scale": theory_scale,
    "theory_modes": theory_modes,
    "theory_diatonic_chords": theory_diatonic_chords,
    "theory_chord_progression": theory_chord_progression,
    "theory_cadences": theory_cadences,
    "theory_song_form": theory_song_form,
    "theory_section": theory_section,
    "theory_genre": theory_genre,
    "theory_arrangement_layers": theory_arrangement_layers,
    "theory_velocity": theory_velocity,
    "theory_rhythm": theory_rhythm,
    "theory_voice_leading_rules": theory_voice_leading_rules,
    "theory_heuristics": theory_heuristics,
    "theory_surprise_devices": theory_surprise_devices,
    "theory_borrowed_chords": theory_borrowed_chords,
    "theory_mix_balance": theory_mix_balance,
    "theory_search": theory_search,
}
