"""Reference song database — small curated lookup for "make a song like X."

Each entry maps a song name → key musical hints the planner / composers
can use as inputs (genre, BPM, key/mode, form, signature elements).

This is a SEED database — extend over time as new references come up.
"""

from __future__ import annotations

from typing import Any


REFERENCE_SONGS: dict[str, dict[str, Any]] = {
    "drive_kavinsky": {
        "title": "Nightcall (Kavinsky)",
        "year": 2010,
        "genre": "synthwave",
        "bpm": 84,
        "key": ["A", "minor"],
        "form_hint": "verse_chorus_bridge",
        "energy_curve": "slow_burn",
        "signatures": ["arpeggiated lead", "deep synth bass", "vocoder vocals", "plate verb"],
    },
    "midnight_city_m83": {
        "title": "Midnight City (M83)",
        "year": 2011,
        "genre": "synthwave",
        "bpm": 105,
        "key": ["F#", "minor"],
        "form_hint": "build_drop_build",
        "energy_curve": "build_drop_build",
        "signatures": ["fast arp", "saxophone hook", "huge reverb", "shouted vocal"],
    },
    "sweet_dreams_eurythmics": {
        "title": "Sweet Dreams (Eurythmics)",
        "year": 1983,
        "genre": "pop",
        "bpm": 125,
        "key": ["C", "minor"],
        "form_hint": "verse_chorus",
        "energy_curve": "immediate_hook",
        "signatures": ["minor vamp", "arpeggiated synth bass", "vocal hook on the 5"],
    },
    "africa_toto": {
        "title": "Africa (Toto)",
        "year": 1982,
        "genre": "pop",
        "bpm": 93,
        "key": ["B", "major"],
        "form_hint": "verse_chorus_bridge",
        "energy_curve": "slow_burn",
        "signatures": ["marimba intro", "axis_pop progression", "lush pad chords"],
    },
    "in_the_air_tonight_collins": {
        "title": "In the Air Tonight (Phil Collins)",
        "year": 1981,
        "genre": "pop",
        "bpm": 96,
        "key": ["D", "minor"],
        "form_hint": "long_intro_drop",
        "energy_curve": "build_drop_build",
        "signatures": ["3-min sparse intro", "gated reverb snare drop", "Roland CR-78"],
    },
    "strobe_deadmau5": {
        "title": "Strobe (deadmau5)",
        "year": 2009,
        "genre": "house",
        "bpm": 128,
        "key": ["B", "major"],
        "form_hint": "ambient_intro_dance_chorus",
        "energy_curve": "slow_burn",
        "signatures": ["pad-driven intro", "4-on-floor dance section", "exteneded arp lead"],
    },
    "miami_disclosure": {
        "title": "Miami (Disclosure)",
        "year": 2018,
        "genre": "house",
        "bpm": 120,
        "key": ["C", "minor"],
        "form_hint": "verse_chorus",
        "energy_curve": "immediate_hook",
        "signatures": ["sidechain pump pad", "soulful vocal hook", "minimal bassline"],
    },
    "hide_and_seek_imogen": {
        "title": "Hide and Seek (Imogen Heap)",
        "year": 2005,
        "genre": "ambient",
        "bpm": 90,
        "key": ["F#", "major"],
        "form_hint": "vocal_only_minimal",
        "energy_curve": "slow_burn",
        "signatures": ["vocoder harmony stack", "no drums", "minor IV deceptive cadence"],
    },
    "still_alive_glados": {
        "title": "Still Alive (Portal)",
        "year": 2007,
        "genre": "synthpop",
        "bpm": 124,
        "key": ["D", "major"],
        "form_hint": "verse_chorus_bridge",
        "energy_curve": "immediate_hook",
        "signatures": ["repeating arpeggio", "deadpan vocal", "8-bar phrases"],
    },
    "lofi_chillhop_generic": {
        "title": "Generic chillhop beat",
        "genre": "lofi",
        "bpm": 76,
        "key": ["F", "major"],
        "form_hint": "loop_8bars",
        "energy_curve": "slow_burn",
        "signatures": ["jazzy 7th chords", "boom-bap drums", "vinyl crackle", "tape saturation"],
    },
    "synthwave_outrun_generic": {
        "title": "Generic outrun synthwave",
        "genre": "synthwave",
        "bpm": 110,
        "key": ["A", "minor"],
        "form_hint": "verse_chorus_bridge_drop",
        "energy_curve": "build_drop_build",
        "signatures": ["arpeggiated saw lead", "gated reverb snare", "8th note bass pump", "plate verb"],
    },
}


TOOLS: list[dict[str, Any]] = [
    {
        "name": "reference_song_lookup",
        "description": (
            "Look up musical hints for a song. Pass `name` (one of the keys "
            "in REFERENCE_SONGS) OR `genre` to list all references in that "
            "genre. Returns BPM, key, form, energy curve, signatures the "
            "composer can use as inputs."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name":  {"type": "string"},
                "genre": {"type": "string"},
            },
        },
    },
    {
        "name": "reference_song_list",
        "description": "List all known reference songs in the curated database.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def reference_song_lookup(args: dict) -> dict:
    name = args.get("name")
    genre = args.get("genre")
    if name:
        info = REFERENCE_SONGS.get(name.lower())
        if info is None:
            return {"ok": False, "error": f"unknown reference: {name}",
                    "available": sorted(REFERENCE_SONGS.keys())}
        return {"ok": True, "name": name, **info}
    if genre:
        matches = {k: v for k, v in REFERENCE_SONGS.items() if v.get("genre") == genre.lower()}
        return {"ok": True, "genre": genre, "matches": matches}
    return {"ok": False, "error": "pass `name` or `genre`"}


def reference_song_list(args: dict) -> dict:
    return {"ok": True,
            "names": sorted(REFERENCE_SONGS.keys()),
            "by_genre": {g: sorted(k for k, v in REFERENCE_SONGS.items() if v.get("genre") == g)
                          for g in sorted({v["genre"] for v in REFERENCE_SONGS.values()})}}


HANDLERS = {
    "reference_song_lookup": reference_song_lookup,
    "reference_song_list":   reference_song_list,
}
