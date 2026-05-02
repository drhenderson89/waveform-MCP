"""Reference song database — small curated lookup for "make a song like X."

Each entry maps a song name → key musical hints the planner / composers
can use as inputs (genre, BPM, key/mode, form, signature elements).

This is a SEED database — extend over time as new references come up.
"""

from __future__ import annotations

from typing import Any


# Reference spectral profiles — RMS dB per band at the listed center
# frequencies. Values are TYPICAL targets for each genre, drawn from
# mastering-engineer cheat sheets and analyzed reference tracks.
# Use audio_compare to delta against these targets.
#
# Bands: 60 / 133 / 296 / 657 / 1460 / 3243 / 7204 / 16000 Hz
# (the default 8-band log-spaced layout audio_spectrum returns)
REFERENCE_SPECTRA: dict[str, list[float]] = {
    "metal":        [-22, -20, -20, -22, -22, -26, -34, -55],   # mid-forward wall, dark top, big low
    "synthwave":    [-23, -28, -26, -28, -34, -38, -42, -68],   # tight low end, controlled mids, sparkly top
    "pop":          [-24, -27, -25, -26, -29, -33, -38, -55],   # vocal mid forward
    "lofi":         [-22, -26, -27, -29, -34, -42, -50, -75],   # rolled-off top, full low
    "edm":          [-18, -22, -24, -27, -30, -34, -38, -55],   # everything loud, especially low end
    "house":        [-20, -23, -25, -27, -30, -34, -39, -58],
    "hip_hop":      [-19, -21, -25, -28, -31, -36, -42, -65],   # huge sub, rolled top
    "rock":         [-25, -27, -23, -22, -26, -30, -34, -50],   # mid-forward (guitars)
    "ambient":      [-30, -34, -34, -34, -38, -42, -46, -55],   # flat-ish, no emphasis
    "cinematic":    [-25, -28, -28, -28, -32, -36, -40, -55],   # broad, dynamic
    "jazz":         [-28, -30, -27, -25, -28, -32, -36, -50],   # mid-forward (horns / piano)
    "classical":    [-32, -34, -32, -30, -32, -34, -36, -42],   # bright top preserved
}


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
        "name": "reference_spectrum_lookup",
        "description": (
            "Get the target spectral profile (RMS dB per log-spaced band) "
            "for a genre — a mastering-engineer-cheat-sheet target for what "
            "a well-mixed track in this genre should look like spectrally. "
            "Bands: 60 / 133 / 296 / 657 / 1460 / 3243 / 7204 / 16000 Hz."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["genre"],
            "properties": {"genre": {"type": "string"}},
        },
    },
    {
        "name": "mix_match_reference",
        "description": (
            "Compare a rendered file against a genre's reference spectrum "
            "and return a per-band diff in dB plus a plain-English diagnosis "
            "('low end is +4 dB hot vs synthwave reference; consider HP "
            "filter on bass'). Use after render to inform mix adjustments."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["file_path", "genre"],
            "properties": {
                "file_path": {"type": "string"},
                "genre":     {"type": "string"},
            },
        },
    },
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


def reference_spectrum_lookup(args: dict) -> dict:
    genre = args["genre"].lower()
    spec = REFERENCE_SPECTRA.get(genre)
    if spec is None:
        return {"ok": False, "error": f"unknown genre: {genre}",
                "available": sorted(REFERENCE_SPECTRA.keys())}
    bands = [60, 133, 296, 657, 1460, 3243, 7204, 16000]
    return {"ok": True, "genre": genre,
            "centers_hz": bands, "rms_db_target": spec}


_BAND_NAMES = ["sub", "low", "low-mid", "mid", "high-mid", "presence", "brilliance", "air"]


def mix_match_reference(args: dict) -> dict:
    """Compare a rendered file's spectrum to a genre reference spectrum
    and return a per-band diff with a diagnostic narrative.
    """
    from . import audio_analysis
    file_path = args["file_path"]
    genre = args["genre"].lower()
    target = REFERENCE_SPECTRA.get(genre)
    if target is None:
        return {"ok": False, "error": f"unknown genre: {genre}"}

    measured_res = audio_analysis.audio_spectrum({"file_path": file_path,
                                                    "bands": len(target)})
    if not measured_res.get("ok"):
        return {"ok": False, "error": "spectrum measurement failed",
                "detail": measured_res.get("error")}
    measured = measured_res["rms_db"]

    # We compare SPECTRAL SHAPE not absolute level — different masters and
    # different LUFS targets mean raw dB diffs are dominated by overall
    # loudness offset. So we subtract the average offset between measured
    # and target, then compare RELATIVE differences per band.
    valid_pairs = [(m, t) for m, t in zip(measured, target) if m != float("-inf")]
    if not valid_pairs:
        return {"ok": False, "error": "no valid spectral data"}
    offset = sum(m - t for m, t in valid_pairs) / len(valid_pairs)

    diffs: list[dict] = []
    for m, t, name, hz in zip(measured, target, _BAND_NAMES, measured_res["centers_hz"]):
        if m == float("-inf"):
            diffs.append({"band": name, "hz": hz, "measured_db": m,
                          "target_db": t, "shape_diff_db": None})
            continue
        # SHAPE diff = how this band deviates AFTER removing the global level offset
        shape_d = (m - t) - offset
        diffs.append({"band": name, "hz": hz, "measured_db": m,
                      "target_db": t, "shape_diff_db": shape_d})

    notes: list[str] = []
    notes.append(f"Overall level offset vs {genre} reference: {offset:+.1f} dB "
                  f"({'louder' if offset > 0 else 'quieter'})")
    for entry in diffs:
        d = entry["shape_diff_db"]
        if d is None:
            continue
        if d > 4:
            notes.append(f"{entry['band']} band ({int(entry['hz'])} Hz) "
                          f"is +{d:.1f} dB HOT vs {genre} spectral shape")
        elif d < -4:
            notes.append(f"{entry['band']} band ({int(entry['hz'])} Hz) "
                          f"is {d:.1f} dB DARK vs {genre} spectral shape")
    if len(notes) == 1:
        notes.append(f"spectral shape matches {genre} reference within ±4 dB across all bands")

    return {"ok": True, "file": file_path, "genre": genre,
            "level_offset_db": offset,
            "diffs": diffs, "diagnosis": notes}


def reference_song_list(args: dict) -> dict:
    return {"ok": True,
            "names": sorted(REFERENCE_SONGS.keys()),
            "by_genre": {g: sorted(k for k, v in REFERENCE_SONGS.items() if v.get("genre") == g)
                          for g in sorted({v["genre"] for v in REFERENCE_SONGS.values()})}}


HANDLERS = {
    "reference_song_lookup":     reference_song_lookup,
    "reference_song_list":       reference_song_list,
    "reference_spectrum_lookup": reference_spectrum_lookup,
    "mix_match_reference":       mix_match_reference,
}
