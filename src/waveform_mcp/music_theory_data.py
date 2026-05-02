"""Music theory data — the durable knowledge layer of the MCP.

Encoded from a structured research synthesis covering: song form, harmony
(scales/modes/cadences/progressions), melody, rhythm/meter, dynamics,
arrangement, genre conventions, and songwriting heuristics. Every dict here
maps directly to either a tool query (in tools/music_theory.py) or to a
generator decision in tools/composer.py.

Sources: Hooktheory, Open Music Theory, musictheory.net, Berklee Online,
Splice/Native Instruments production blogs, Aldwell & Schachter, Kostka &
Payne, Levine, Caplin, Schoenberg, Toussaint. Full bibliography in the
research-agent transcript; we keep the data here, not the citations.
"""

from __future__ import annotations

# ============================================================================
# 1. Scales / modes — interval patterns (semitones from root)
# ============================================================================

SCALES: dict[str, list[int]] = {
    "major":              [0, 2, 4, 5, 7, 9, 11],
    "natural_minor":      [0, 2, 3, 5, 7, 8, 10],
    "harmonic_minor":     [0, 2, 3, 5, 7, 8, 11],
    "melodic_minor_asc":  [0, 2, 3, 5, 7, 9, 11],
    "ionian":             [0, 2, 4, 5, 7, 9, 11],     # = major
    "dorian":             [0, 2, 3, 5, 7, 9, 10],
    "phrygian":           [0, 1, 3, 5, 7, 8, 10],
    "lydian":             [0, 2, 4, 6, 7, 9, 11],
    "mixolydian":         [0, 2, 4, 5, 7, 9, 10],
    "aeolian":            [0, 2, 3, 5, 7, 8, 10],     # = natural minor
    "locrian":            [0, 1, 3, 5, 6, 8, 10],
    "major_pentatonic":   [0, 2, 4, 7, 9],
    "minor_pentatonic":   [0, 3, 5, 7, 10],
    "blues":              [0, 3, 5, 6, 7, 10],
    "whole_tone":         [0, 2, 4, 6, 8, 10],
    "half_whole_dim":     [0, 1, 3, 4, 6, 7, 9, 10],
    "whole_half_dim":     [0, 2, 3, 5, 6, 8, 9, 11],
    "chromatic":          [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11],
    "hungarian_minor":    [0, 2, 3, 6, 7, 8, 11],
    "phrygian_dominant":  [0, 1, 4, 5, 7, 8, 10],     # 5th mode of harmonic minor
}


MODE_CHARACTER: dict[str, dict[str, str]] = {
    "ionian":     {"degree": "maj 3 / maj 7", "vibe": "bright, resolved",         "use": "pop, Western default"},
    "dorian":     {"degree": "min 3 / maj 6",  "vibe": "sophisticated minor",     "use": "funk, jazz, modal rock — 'So What', 'Mad World'"},
    "phrygian":   {"degree": "♭2",             "vibe": "dark, Spanish",           "use": "flamenco, metal"},
    "lydian":     {"degree": "♯4",             "vibe": "floating, ethereal",      "use": "film scores, dream sequences"},
    "mixolydian": {"degree": "♭7",             "vibe": "bluesy, country, rock",   "use": "rock, blues, Britpop"},
    "aeolian":    {"degree": "min 3 / ♭6 / ♭7", "vibe": "standard sad",           "use": "pop ballads, rock"},
    "locrian":    {"degree": "♭5",             "vibe": "unstable",                "use": "rare; metal, jazz fragments"},
}


# ============================================================================
# 2. Diatonic harmony
# ============================================================================

DIATONIC_TRIADS: dict[str, list[str]] = {
    "major":          ["I",   "ii",  "iii", "IV",  "V",   "vi",  "vii°"],
    "natural_minor":  ["i",   "ii°", "III", "iv",  "v",   "VI",  "VII"],
    "harmonic_minor": ["i",   "ii°", "III+", "iv", "V",   "VI",  "vii°"],
}


DIATONIC_SEVENTHS: dict[str, list[str]] = {
    "major":         ["Imaj7", "ii7",   "iii7",   "IVmaj7", "V7",  "vi7",  "viim7b5"],
    "natural_minor": ["im7",   "iim7b5","IIImaj7","ivm7",   "vm7", "VImaj7","VII7"],
}


CHORD_FUNCTION: dict[str, list[str]] = {
    "tonic":       ["I", "i", "vi", "VI", "iii", "III"],
    "predominant": ["ii", "IV", "iv", "ii°", "II"],
    "dominant":    ["V", "V7", "vii°", "viidim7"],
}


# ============================================================================
# 3. Cadences
# ============================================================================

CADENCES: dict[str, dict] = {
    "perfect_authentic":   {"pattern": ["V", "I"],         "feel": "fully_resolved",       "use": "section_end"},
    "imperfect_authentic": {"pattern": ["V", "I"],         "feel": "resolved_weaker",      "use": "phrase_end",
                            "constraint": "soprano not on tonic, or in inversion"},
    "plagal":              {"pattern": ["IV", "I"],        "feel": "amen_soft",            "use": "tag_outro"},
    "deceptive":           {"pattern": ["V", "vi"],        "feel": "surprise",             "use": "delay_resolution"},
    "half":                {"pattern": ["*", "V"],         "feel": "open_question",        "use": "phrase_midpoint"},
    "phrygian":            {"pattern": ["iv6", "V"],       "feel": "dark_classical",       "use": "minor_key_half_cadence"},
    "phrygian_bii_i":      {"pattern": ["bII", "I"],       "feel": "exotic",               "use": "metal/flamenco cadence"},
    "tierce_de_picardie":  {"pattern": ["V", "I_major"],   "feel": "unexpected_brightness","use": "final chord of minor piece"},
    "backdoor":            {"pattern": ["bVII7", "Imaj7"], "feel": "jazzy",                "use": "jazz substitute for V-I"},
}


# ============================================================================
# 4. Common chord progressions across genres
# ============================================================================

PROGRESSIONS: dict[str, dict] = {
    "axis_pop":            {"chords": ["I", "V", "vi", "IV"],
                            "tags": ["pop", "rock"], "examples": "'Don't Stop Believin'', 'Let It Be'"},
    "fifties":             {"chords": ["I", "vi", "IV", "V"],
                            "tags": ["doo_wop", "pop"], "examples": "'Stand By Me', 50s pop"},
    "sensitive_female":    {"chords": ["vi", "IV", "I", "V"],
                            "tags": ["pop"], "examples": "'Kiss From a Rose' rotation of axis"},
    "blues_12bar":         {"chords": ["I","I","I","I","IV","IV","I","I","V","IV","I","V"],
                            "tags": ["blues"], "examples": "12-bar blues form"},
    "minor_blues":         {"chords": ["i","i","i","i","iv","iv","i","i","V7","iv","i","V7"],
                            "tags": ["blues", "minor"], "examples": "minor 12-bar"},
    "ii_V_I":              {"chords": ["ii7", "V7", "Imaj7"],
                            "tags": ["jazz"], "examples": "the jazz cadence"},
    "ii_V_I_minor":        {"chords": ["iim7b5", "V7", "im7"],
                            "tags": ["jazz", "minor"], "examples": "minor jazz cadence"},
    "rhythm_changes_a":    {"chords": ["I", "vi", "ii", "V"],
                            "tags": ["jazz"], "examples": "Rhythm Changes A section"},
    "I_IV_V":              {"chords": ["I", "IV", "V"],
                            "tags": ["folk", "rock"], "examples": "three-chord rock"},
    "I_IV_V_IV":           {"chords": ["I", "IV", "V", "IV"],
                            "tags": ["rock"], "examples": "classic rock vamp"},
    "pachelbel":           {"chords": ["I", "V", "vi", "iii", "IV", "I", "IV", "V"],
                            "tags": ["classical", "pop"], "examples": "Canon in D, 'Basket Case'"},
    "andalusian":          {"chords": ["i", "VII", "VI", "V"],
                            "tags": ["flamenco", "rock"], "examples": "'Hit the Road Jack', flamenco descending"},
    "phrygian":            {"chords": ["i", "bII", "bIII", "bII"],
                            "tags": ["flamenco", "metal"], "examples": "Spanish/metal feel"},
    "circle_of_fifths":    {"chords": ["vi", "ii", "V", "I"],
                            "tags": ["jazz", "classical"], "examples": "circle progression"},
    "lament_bass":         {"chords": ["i", "VII", "VI", "V"],
                            "tags": ["classical", "rock"], "examples": "descending bass lament"},
    "creep":               {"chords": ["I", "III", "IV", "iv"],
                            "tags": ["alt_rock"], "examples": "Radiohead 'Creep' chromatic mediant"},
    "royal_road":          {"chords": ["IVmaj7", "V7", "iii7", "vi"],
                            "tags": ["jpop"], "examples": "ubiquitous in J-pop"},
    "jazz_turnaround":     {"chords": ["I", "VI7", "ii7", "V7"],
                            "tags": ["jazz"], "examples": "ending vamp turnaround"},
    "modal_dorian_vamp":   {"chords": ["i", "IV"],
                            "tags": ["modal", "lofi", "funk"], "examples": "i to IV in Dorian — 'Oye Como Va'"},
    "modal_mixo_vamp":     {"chords": ["I", "bVII", "IV"],
                            "tags": ["rock"], "examples": "'Sweet Home Alabama' / Mixolydian rock"},
    "sad_descend":         {"chords": ["vi", "V", "IV", "V"],
                            "tags": ["pop"], "examples": "minor lament with dominant lift"},
    "trance_emotional":    {"chords": ["vi", "IV", "I", "V"],
                            "tags": ["trance", "edm"], "examples": "uplifting trance progression"},
    "lofi_jazz_loop":      {"chords": ["Imaj7", "vi7", "ii7", "V7"],
                            "tags": ["lofi", "jazz"], "examples": "ii-V-I extended loop"},
    "lofi_dorian_loop":    {"chords": ["im7", "IVmaj7"],
                            "tags": ["lofi", "modal"], "examples": "modal Dorian vamp for lofi"},
}


# Borrowed chord moves (modal interchange)
BORROWED_CHORDS_MAJOR: dict[str, str] = {
    "bVII":   "from Mixolydian — 'Hey Jude', rock pop staple",
    "iv":     "from minor — bittersweet, 'Creep', 'Blackbird'",
    "bVI":    "from minor — anthemic, film scores",
    "bIII":   "from minor — Beatles, Radiohead",
    "bII":    "Neapolitan / Phrygian — dramatic",
    "iim7b5": "from minor — minor ii-V into vi",
}


# ============================================================================
# 5. Voice leading (encoded as scoring rules — penalty-based)
# ============================================================================

VOICE_LEADING_RULES: dict[str, str] = {
    "common_tone":           "If two adjacent chords share a pitch, hold it in the same voice.",
    "stepwise_preference":   "Move other voices by step (1-2 semitones) when possible.",
    "leap_compensation":     "After a leap > 4 semitones, move stepwise in opposite direction.",
    "no_parallel_perfects":  "Avoid two voices moving in parallel perfect 5ths or octaves (classical).",
    "no_direct_perfects":    "Avoid arriving at a perfect 5th/8ve in similar motion in outer voices unless top voice moves by step.",
    "leading_tone_resolves": "Scale degree 7 should resolve up to 1 in outer voices at cadences.",
    "seventh_resolves_down": "The 7th of any 7th chord resolves down by step.",
    "tritone_resolves_inward":"In V7→I, scale degrees 4 and 7 resolve inward (4↓3, 7↑1).",
    "contrary_motion_pref":  "Prefer contrary or oblique motion between bass and soprano.",
    "exception_pop_rock":    "Parallel 5ths are fine in rock/pop power-chord textures and EDM stabs.",
}


# ============================================================================
# 6. Song form / sections
# ============================================================================

SECTIONS: dict[str, dict] = {
    "intro":       {"bars": [2, 4, 8],     "role": "establish_key_groove",   "dynamics": "low",      "next": ["verse", "drop", "head"]},
    "verse":       {"bars": [8, 16],       "role": "narrate",                "dynamics": "low_mid",  "next": ["pre_chorus", "chorus", "verse"]},
    "pre_chorus":  {"bars": [4, 8],        "role": "lift_tension",           "dynamics": "rising",   "next": ["chorus"]},
    "chorus":      {"bars": [8, 16],       "role": "payoff_hook",            "dynamics": "high",     "next": ["verse", "post_chorus", "bridge"]},
    "post_chorus": {"bars": [4, 8],        "role": "extend_payoff",          "dynamics": "high",     "next": ["verse", "bridge"]},
    "bridge":      {"bars": [4, 8, 16],    "role": "contrast_relocate",      "dynamics": "varied",   "next": ["chorus", "final_chorus"]},
    "drop":        {"bars": [16, 32],      "role": "energy_peak",            "dynamics": "max",      "next": ["breakdown"]},
    "build":       {"bars": [8, 16, 32],   "role": "ramp_tension",           "dynamics": "rising",   "next": ["drop"]},
    "breakdown":   {"bars": [16, 32],      "role": "release_strip_down",     "dynamics": "low",      "next": ["build"]},
    "hook":        {"bars": [4, 8],        "role": "memorable_refrain",      "dynamics": "high",     "next": ["verse"]},
    "head":        {"bars": [16, 32],      "role": "state_theme_jazz",       "dynamics": "mid",      "next": ["solo"]},
    "solo":        {"bars": [16, 32, 64],  "role": "improv_development",     "dynamics": "rising",   "next": ["solo", "head"]},
    "outro":       {"bars": [4, 8, 16],    "role": "wind_down",              "dynamics": "falling",  "next": []},
    "interlude":   {"bars": [4, 8],        "role": "transition_breath",      "dynamics": "low_mid",  "next": ["verse", "chorus"]},
    "tag":         {"bars": [2, 4],        "role": "extend_final_cadence",   "dynamics": "high_low", "next": ["outro"]},
}


FORMS: dict[str, dict] = {
    "verse_chorus":           {"layout": ["intro","verse","chorus","verse","chorus","bridge","chorus","outro"],
                               "genres": ["pop","rock","country","rnb"], "total_bars": [96, 144]},
    "verse_chorus_no_bridge": {"layout": ["intro","verse","chorus","verse","chorus","chorus","outro"],
                               "genres": ["pop","edm_pop"], "total_bars": [96, 128]},
    "vc_with_prechorus":      {"layout": ["intro","verse","pre_chorus","chorus","verse","pre_chorus","chorus","bridge","chorus","chorus"],
                               "genres": ["modern_pop"], "total_bars": [112, 160]},
    "aaba_32bar":             {"layout": ["A","A","B","A"],
                               "genres": ["jazz_standard","tin_pan_alley"], "total_bars": [32]},
    "strophic":               {"layout": ["A","A","A","A"],
                               "genres": ["folk","hymn","ballad"], "total_bars": [32, 96]},
    "through_composed":       {"layout": ["A","B","C","D","..."],
                               "genres": ["art_song","prog","classical"], "total_bars": [64, 300]},
    "twelve_bar_blues":       {"layout": ["12_bar_chorus"],
                               "genres": ["blues","early_rock"], "total_bars": [12, 48]},
    "binary":                 {"layout": ["A","B"],
                               "genres": ["baroque","folk"], "total_bars": [16, 32]},
    "ternary":                {"layout": ["A","B","A"],
                               "genres": ["minuet","art_song"], "total_bars": [24, 48]},
    "rondo":                  {"layout": ["A","B","A","C","A"],
                               "genres": ["classical","film"], "total_bars": [64, 128]},
    "edm_build_drop":         {"layout": ["intro","build","drop","breakdown","build","drop","outro"],
                               "genres": ["house","trance","dubstep"], "total_bars": [128, 192]},
    "hiphop_loop":            {"layout": ["intro","verse","hook","verse","hook","verse","hook","outro"],
                               "genres": ["hip_hop","trap"], "total_bars": [64, 96]},
    "lofi_loop":              {"layout": ["intro","body","breakdown","body","outro"],
                               "genres": ["lofi","chillhop"], "total_bars": [48, 96]},
    "ambient_evolving":       {"layout": ["continuous_bed_with_gradual_changes"],
                               "genres": ["ambient","drone"], "total_bars": [0]},
}


# ============================================================================
# 7. Melody primitives
# ============================================================================

CONTOURS: dict[str, str] = {
    "arch":          "rise to climax around 60-75% of phrase, fall to cadence",
    "inverted_arch": "fall then rise; introspective",
    "ascending":     "stepwise/leap rise to end; question feel",
    "descending":    "fall from peak; resigned, lullaby",
    "wave":          "multiple peaks; conversational",
    "static":        "narrow range; chant/rap-adjacent, hypnotic",
    "terraced":      "stepwise plateaus rising or falling; gospel/anthem build",
}


DEGREE_STABILITY: dict[int, str] = {
    1: "most stable (tonic)",
    3: "stable (defines mode)",
    5: "stable (dominant pole)",
    6: "moderately stable",
    2: "unstable, leans to 1 or 3",
    4: "unstable, pulls down to 3",
    7: "unstable, pulls up to 1 (leading tone)",
}


NON_CHORD_TONES: dict[str, str] = {
    "passing":      "stepwise between two chord tones",
    "neighbor":     "step away and back to same chord tone",
    "appoggiatura": "leap in, step out (resolution)",
    "escape_tone":  "step in, leap out",
    "suspension":   "held over from previous chord, resolves down by step",
    "anticipation": "early arrival of next chord tone",
    "pedal_point":  "sustained note while chords change above/below",
}


# ============================================================================
# 8. Rhythm and meter
# ============================================================================

TIME_SIGS: dict[str, dict] = {
    "4/4":  {"feel": "default_pop_rock",   "examples": "almost everything"},
    "3/4":  {"feel": "waltz_lilt",         "examples": "country waltz, 'My Favorite Things'"},
    "6/8":  {"feel": "compound_rolling",   "examples": "ballads, fast Irish jig, 'House of the Rising Sun'"},
    "12/8": {"feel": "slow_blues_swing",   "examples": "doo-wop ballads, slow blues"},
    "2/4":  {"feel": "march_polka",        "examples": "marches, polka"},
    "5/4":  {"feel": "asymmetric_brood",   "examples": "'Take Five', Mission Impossible"},
    "7/8":  {"feel": "asymmetric_drive",   "examples": "prog rock, Balkan dances"},
    "5/8":  {"feel": "lopsided_short",     "examples": "Greek dances"},
    "9/8":  {"feel": "compound_triple",    "examples": "Irish slip jig"},
    "cut":  {"feel": "fast_2_feel",        "examples": "bluegrass, swing"},
}


SYNCOPATION: dict[str, str] = {
    "backbeat":      "snare on 2 and 4 — rock/pop foundation",
    "four_on_floor": "kick every beat — house, disco",
    "tresillo":      "3-3-2 sixteenth subdivision — Latin, reggaeton",
    "cinquillo":     "3-3-2 with 5 hits — Cuban",
    "son_clave":     "3-2 or 2-3 clave — Latin",
    "shuffle":       "long-short eighth subdivision — blues",
    "amen_break":    "specific drum loop — DnB, jungle",
    "dembow":        "tresillo + kick on 1, snare 3 — reggaeton/dancehall",
    "boom_bap":      "kick on 1, snare on 3 with swing — 90s hip-hop",
    "samba":         "16th-note kick syncopation — samba",
    "anticipation":  "chord/note arrives 1/8 or 1/16 before downbeat — Latin pop",
}


SWING_RATIOS: dict[str, float] = {
    "straight":     0.50,
    "light_swing":  0.55,
    "medium_swing": 0.60,
    "hard_swing":   0.67,
    "lofi_dilla":   0.62,   # ~5:3 micro-pocket
}


# ============================================================================
# 9. Dynamics + arrangement
# ============================================================================

VELOCITY_MAP: dict[str, int] = {
    "ppp": 16,
    "pp":  32,
    "p":   48,
    "mp":  64,
    "mf":  80,
    "f":   96,
    "ff":  112,
    "fff": 127,
}

ACCENT_BUMP = 15
GHOST_NOTE_MAX_VEL = 25


ARRANGEMENT_LAYERS: dict[str, list[str]] = {
    "foundation":     ["kick", "snare", "hi_hat", "bass"],
    "harmonic_glue":  ["pad", "piano_chord", "guitar_strum", "rhodes"],
    "lead":           ["lead_vocal", "lead_synth", "lead_guitar", "trumpet"],
    "counter_melody": ["bg_vocal", "sax_line", "string_counter"],
    "atmosphere":     ["reverb_throw", "ambient_pad", "noise_riser", "field_recording"],
    "ear_candy":      ["risers", "downlifters", "reverse_cymbal", "vinyl_crackle"],
}


# Per-section "presence" of each layer (0..1). Drives an arrangement engine
# that fades layers in/out across the song.
LAYER_PRESENCE_BY_SECTION: dict[str, dict[str, float]] = {
    "intro":        {"foundation": 0.3, "harmonic_glue": 0.6, "lead": 0.2, "atmosphere": 0.6, "ear_candy": 0.1},
    "verse":        {"foundation": 0.7, "harmonic_glue": 0.6, "lead": 0.9, "counter_melody": 0.2, "atmosphere": 0.4},
    "pre_chorus":   {"foundation": 0.8, "harmonic_glue": 0.7, "lead": 0.9, "atmosphere": 0.5, "ear_candy": 0.6},
    "chorus":       {"foundation": 1.0, "harmonic_glue": 1.0, "lead": 1.0, "counter_melody": 0.7, "atmosphere": 0.7, "ear_candy": 0.5},
    "bridge":       {"foundation": 0.6, "harmonic_glue": 0.7, "lead": 0.8, "atmosphere": 0.8},
    "drop":         {"foundation": 1.0, "harmonic_glue": 0.8, "lead": 1.0, "atmosphere": 0.6, "ear_candy": 0.8},
    "breakdown":    {"foundation": 0.1, "harmonic_glue": 0.7, "lead": 0.5, "atmosphere": 0.9},
    "outro":        {"foundation": 0.5, "harmonic_glue": 0.5, "lead": 0.4, "atmosphere": 0.6},
}


# ============================================================================
# 10. Genre conventions
# ============================================================================

GENRES: dict[str, dict] = {
    "pop":         {"bpm": [90, 130],  "time": "4/4", "key_tendency": "major (60%)",
                    "instruments": ["vocal", "drum_kit", "bass", "keys/synth", "guitar"],
                    "form": "verse_chorus with pre/post chorus",
                    "hallmark_progressions": ["axis_pop", "fifties"]},
    "rock":        {"bpm": [100, 140], "time": "4/4", "key_tendency": "major or minor",
                    "instruments": ["vocal", "electric_guitar", "bass", "drum_kit"],
                    "form": "verse_chorus, sometimes AABA",
                    "hallmark_progressions": ["I_IV_V", "modal_mixo_vamp"]},
    "folk":        {"bpm": [80, 120],  "time": "4/4 or 3/4 or 6/8",
                    "key_tendency": "major, often modal (mixolydian, dorian)",
                    "instruments": ["acoustic_guitar", "vocal", "fiddle", "banjo", "mandolin", "upright_bass"],
                    "form": "strophic, verse_chorus",
                    "hallmark_progressions": ["I_IV_V", "axis_pop"]},
    "country":     {"bpm": [90, 130],  "time": "4/4 or 3/4 (waltz)", "key_tendency": "major",
                    "instruments": ["vocal", "acoustic_guitar", "electric_guitar", "pedal_steel", "bass", "drum_kit", "fiddle"],
                    "form": "verse_chorus",
                    "hallmark_progressions": ["I_IV_V", "axis_pop"]},
    "blues":       {"bpm": [60, 130],  "time": "4/4 or 12/8",
                    "key_tendency": "major-key with dominant 7s on every chord",
                    "instruments": ["vocal", "guitar", "harmonica", "piano", "bass", "drum_kit"],
                    "form": "twelve_bar_blues",
                    "hallmark_progressions": ["blues_12bar"]},
    "jazz_standard":{"bpm": [60, 260], "time": "4/4 (swing) or 3/4",
                    "key_tendency": "major or minor; modulates frequently",
                    "instruments": ["piano", "upright_bass", "drum_kit", "horn (sax/trumpet)", "vocal"],
                    "form": "32_bar_AABA, Rhythm Changes, ABAC",
                    "hallmark_progressions": ["ii_V_I", "rhythm_changes_a"]},
    "rnb":         {"bpm": [60, 110],  "time": "4/4, often 12/8 ballads", "key_tendency": "major with extensions",
                    "instruments": ["vocal", "keys (rhodes/wurli)", "bass", "drum_kit", "strings"],
                    "form": "verse_chorus, often with extended bridge",
                    "hallmark_progressions": ["ii_V_I", "royal_road"]},
    "hip_hop":     {"bpm": [70, 100],  "time": "4/4", "key_tendency": "minor (60%)",
                    "instruments": ["drum_machine_or_kit", "808_bass", "sample", "synth", "vocal_rap"],
                    "form": "hiphop_loop",
                    "hallmark_progressions": ["modal_dorian_vamp", "lament_bass"]},
    "house":       {"bpm": [118, 128], "time": "4/4", "key_tendency": "minor",
                    "instruments": ["drum_machine", "synth_bass", "pad", "piano_stab", "vocal_chop"],
                    "form": "edm_build_drop",
                    "hallmark_progressions": ["modal_dorian_vamp"]},
    "techno":      {"bpm": [120, 140], "time": "4/4", "key_tendency": "minor or modal/atonal",
                    "instruments": ["drum_machine", "synth", "sub_bass"],
                    "form": "edm_build_drop, often single chord",
                    "hallmark_progressions": ["phrygian"]},
    "trance":      {"bpm": [130, 145], "time": "4/4", "key_tendency": "minor",
                    "instruments": ["supersaw", "pad", "drum_machine", "vocal"],
                    "form": "edm_build_drop, anthemic",
                    "hallmark_progressions": ["trance_emotional"]},
    "lofi":        {"bpm": [70, 90],   "time": "4/4 with swing",
                    "key_tendency": "major 7 / minor 7 jazz palette",
                    "instruments": ["sampled_keys", "mpc_drums", "upright_bass_sample", "vinyl_noise"],
                    "form": "lofi_loop",
                    "hallmark_progressions": ["lofi_jazz_loop", "lofi_dorian_loop", "ii_V_I"]},
    "punk":        {"bpm": [150, 220], "time": "4/4", "key_tendency": "major",
                    "instruments": ["distorted_guitar", "bass", "drum_kit", "vocal"],
                    "form": "verse_chorus, short",
                    "hallmark_progressions": ["I_IV_V", "axis_pop"]},
    "metal":       {"bpm": [100, 200], "time": "4/4, 6/8, odd meters",
                    "key_tendency": "minor (Aeolian, Phrygian, Phrygian dominant)",
                    "instruments": ["distorted_guitar_x2", "bass", "drum_kit", "vocal"],
                    "form": "verse_chorus, prog through_composed",
                    "hallmark_progressions": ["phrygian"]},
    "indie":       {"bpm": [100, 140], "time": "4/4 mostly", "key_tendency": "major or modal",
                    "instruments": ["jangle_guitar", "bass", "drum_kit", "synth", "vocal"],
                    "form": "verse_chorus with experimental forms",
                    "hallmark_progressions": ["axis_pop", "modal_dorian_vamp"]},
    "bossa_nova":  {"bpm": [120, 140], "time": "2/4 or 4/4",
                    "key_tendency": "major with extended jazz harmony",
                    "instruments": ["nylon_guitar", "vocal", "bass", "brushed_drums", "flute/sax"],
                    "form": "aaba_32bar",
                    "hallmark_progressions": ["ii_V_I", "circle_of_fifths"]},
    "salsa":       {"bpm": [180, 220], "time": "cut time, 2-3 or 3-2 clave",
                    "key_tendency": "minor common",
                    "instruments": ["piano_montuno", "bass_tumbao", "congas", "timbales", "horns", "vocal"],
                    "form": "intro-verso-coro-mambo-coro",
                    "hallmark_progressions": ["ii_V_I", "I_IV_V"]},
    "reggaeton":   {"bpm": [85, 100],  "time": "4/4 with dembow", "key_tendency": "minor",
                    "instruments": ["dembow_drum_loop", "synth_bass", "pad", "vocal"],
                    "form": "verse_chorus",
                    "hallmark_progressions": ["lament_bass", "andalusian"]},
    "reggae":      {"bpm": [60, 90],   "time": "4/4", "key_tendency": "major often, modal",
                    "instruments": ["clean_guitar_skank", "organ_bubble", "bass", "drum_kit_one_drop", "vocal"],
                    "form": "verse_chorus",
                    "hallmark_progressions": ["I_IV_V"]},
    "classical":   {"bpm": [40, 200],  "time": "varies", "key_tendency": "tonal, modulates",
                    "instruments": ["orchestra"],
                    "form": "sonata, rondo, ternary, theme & variations",
                    "hallmark_progressions": ["circle_of_fifths"]},
    "film_score":  {"bpm": [60, 110],  "time": "4/4 or rubato",
                    "key_tendency": "modal: Lydian for wonder, Aeolian for grief",
                    "instruments": ["orchestra", "hybrid_synths", "percussion_ensemble"],
                    "form": "through_composed with motifs (leitmotif)",
                    "hallmark_progressions": ["lament_bass"]},
    "ambient":     {"bpm": [40, 80],   "time": "free or 4/4", "key_tendency": "modal, often single key",
                    "instruments": ["pads", "granular_synths", "field_recording", "piano"],
                    "form": "ambient_evolving",
                    "hallmark_progressions": ["modal_dorian_vamp"]},
}


# ============================================================================
# 11a. Mix balance reference — track-level dB targets per role per genre.
# ============================================================================
#
# Sources (cross-referenced, March 2026):
#   - Mastering The Mix: kick→bass relationship, ~6 dB headroom per element
#   - eMastered: balance kick + bass first, then build up
#   - Sound on Sound: -18 dB RMS bus headroom rule
#   - Genre-specific tutorials for synthwave / lofi / EDM / pop
#
# Convention: all values are TRACK volume in dB, assuming the master bus
# limiter handles final loudness. Kick is the anchor — every other element
# is set relative to it. Master volume sits at 0 to +1 dB.
#
# Roles (canonical names — composers should use these):
#   "drums"        → drum bus / kit (in genres that have one bus for drums)
#   "kick"         → kick drum on its own track
#   "snare"        → snare drum on its own track
#   "hat"          → closed/open hi-hats
#   "bass"         → bass instrument (synth or guitar)
#   "sub_bass"     → dedicated sub-only layer (rare)
#   "lead"         → primary melody instrument (synth lead, vocal, top-line)
#   "pad"          → atmospheric sustained chords
#   "keys"         → piano/Rhodes/keys playing chords
#   "arp"          → arpeggiated decoration
#   "counter"      → secondary melody / counter-line
#   "melody"       → secondary melodic instrument (e.g. xylophone)
#   "ambience"     → field recordings, rain, birds, etc.
#   "fx"           → risers, impacts, transitions
#   "reverb_send"  → effect-bus return for shared reverb

MIX_BALANCE: dict[str, dict[str, float]] = {
    # Metal / djent: drums and rhythm guitars dominate, bass is THICK and
    # follows the riff, lead solo is forward but not on top of guitars.
    # Wall-of-sound aesthetic — everything fights for the same midrange,
    # mix is about balancing within a constrained dynamic window.
    "metal": {
        "drums": -5.0, "kick": -4.0, "snare": -5.0, "hat": -12.0,
        "bass": -10.0, "sub_bass": -14.0,
        "lead": -10.0,                          # the solo
        "rhythm_guitar": -8.0,                  # the wall
        "rhythm_guitar_l": -8.0, "rhythm_guitar_r": -8.0,
        "pad": -22.0,                           # atmosphere only
        "keys": -20.0,
        "ambience": -28.0, "fx": -16.0,
    },
    # Synthwave (user-approved): arp is the melody focus, bass is background-
    # level texture not a driver, lead is muted (saw + plate verb sounded
    # thin and got in the arp's way). Pad sits underneath as atmosphere.
    "synthwave": {
        "drums": -7.0, "kick": -6.0, "snare": -10.0, "hat": -16.0,
        "bass": -25.0, "sub_bass": -28.0,        # background, not a driver
        "lead": -15.0, "pad": -19.0,             # lead is muted by composer
        "arp": -8.0, "keys": -14.0,              # arp is the melody focus
        "counter": -14.0, "ambience": -22.0, "fx": -18.0,
    },
    # Lofi: values tuned by ear across multiple beach-vibe iterations with
    # the user's blessing. Keys forward (signature jazz Rhodes), bass
    # weighted but not dominant, melody/counter sit prominent in the top
    # register, pad subtle.
    "lofi": {
        "drums": -8.0, "kick": -8.0, "snare": -10.0, "hat": -16.0,
        "bass": -18.0, "sub_bass": -22.0,
        "lead": -10.0, "melody": -8.0, "keys": -2.0, "pad": -18.0,
        "counter": -8.0, "ambience": -14.0, "fx": -20.0,
    },
    # EDM/house: kick LEADS the mix (it's the dance-floor heartbeat), bass
    # tightly under, lead and arp loud, pad atmospheric.
    "edm": {
        "drums": -5.0, "kick": -4.0, "snare": -9.0, "hat": -14.0,
        "bass": -10.0, "sub_bass": -13.0,
        "lead": -8.0, "pad": -18.0, "arp": -12.0, "keys": -14.0,
        "counter": -13.0, "ambience": -22.0, "fx": -10.0,
    },
    # Pop: vocals are the king (covered as "lead" here). Kick + snare loud,
    # bass forward, everything else supports the lead.
    "pop": {
        "drums": -7.0, "kick": -6.0, "snare": -7.0, "hat": -13.0,
        "bass": -11.0, "sub_bass": -16.0,
        "lead": -8.0, "pad": -20.0, "keys": -14.0, "arp": -16.0,
        "counter": -14.0, "ambience": -22.0, "fx": -16.0,
    },
    # Hip-hop: 808 sub crucial — below kick. Snare/clap loud. Vocal room only.
    "hip_hop": {
        "drums": -7.0, "kick": -6.0, "snare": -7.0, "hat": -13.0,
        "bass": -10.0, "sub_bass": -8.0,    # 808 sub IS the bass; keep it hot
        "lead": -8.0, "pad": -18.0, "keys": -12.0, "arp": -14.0,
        "ambience": -22.0, "fx": -14.0,
    },
    # Cinematic / score: dynamic — quiet sections deep below mix peak, climaxes
    # near reference. Pads and strings carry, percussion is impact-driven.
    "cinematic": {
        "drums": -8.0, "kick": -8.0, "snare": -10.0,
        "bass": -14.0, "sub_bass": -14.0,
        "lead": -10.0, "pad": -16.0, "keys": -12.0,
        "counter": -14.0, "ambience": -18.0, "fx": -10.0,
    },
    # Ambient: no traditional drums; everything sits within ~10 dB of each
    # other so individual layers are equally important.
    "ambient": {
        "pad": -12.0, "lead": -12.0, "ambience": -10.0,
        "fx": -16.0, "counter": -14.0, "drums": -20.0,
    },
}

# Default fallback used when a (genre, role) pair isn't in the table — order
# of preference: genre-specific role → "pop" role → -14 dB safe default.
MIX_BALANCE_FALLBACK_GENRE = "pop"
MIX_BALANCE_DEFAULT_DB = -14.0


def mix_level_for(genre: str, role: str) -> float:
    """Lookup a (genre, role) → dB target. Falls back gracefully."""
    g = MIX_BALANCE.get(genre.lower())
    if g and role in g:
        return g[role]
    fallback = MIX_BALANCE.get(MIX_BALANCE_FALLBACK_GENRE, {})
    if role in fallback:
        return fallback[role]
    return MIX_BALANCE_DEFAULT_DB


# ============================================================================
# 11. Songwriting heuristics — the unwritten rules
# ============================================================================

HEURISTICS: dict[str, str] = {
    "rule_of_3":               "Listeners need a hook 3+ times to remember it. Chorus minimum 3 plays.",
    "contrast_required":       "Adjacent sections must differ in at least one of: harmonic_rhythm, register, density, dynamic, chord palette.",
    "repetition_threshold":    "A phrase can repeat literally up to ~3x before requiring variation.",
    "complexity_budget":       "Per section, vary ONE of: harmony, melody, rhythm, instrumentation. Varying all four scrambles the listener.",
    "surprise_quota":          "Exactly one harmonic surprise per song (deceptive cadence, key change, borrowed chord) — more dilutes each.",
    "hook_position":           "Title/hook lyric should land on or just after the first downbeat of the chorus.",
    "section_length_doubling": "Whatever bar count Section A is, Section B should be the same or doubled, rarely halved.",
    "intro_under_15s":         "Pop intros under 15s; otherwise skip rate spikes (Spotify data).",
    "first_chorus_by_60s":     "In a 3-min song, first chorus should hit by 0:60.",
    "final_chorus_lift":       "Final chorus benefits from one of: key change up by step, added harmony layer, drop-and-build, double-time drums.",
    "genre_fluency":           "75% of cues should match genre conventions; the remaining 25% is the signature.",
    "introduce_develop_resolve":"Every scale of song (phrase, section, song, set) follows: introduce → develop → resolve.",
    "silence_is_loud":         "A 1-beat full-mix cut before a chorus makes the chorus hit harder than added layers can.",
}


# Concrete "surprise" devices — one per song, never several
SURPRISE_DEVICES: dict[str, str] = {
    "truck_driver_modulation": "Up by half-step or whole-step before final chorus.",
    "deceptive_cadence":       "V → vi at a moment where I is expected (last chord of bridge).",
    "borrowed_chord_pivot":    "Drop in a single bIII or bVI in an otherwise diatonic progression.",
    "meter_break":             "Insert a single 2/4 bar in a 4/4 phrase ('Here Comes the Sun').",
    "instrument_drop":         "Pull all but voice + one instrument for a half-bar before final chorus.",
    "tempo_shift":             "Half-time feel in bridge, return to time in final chorus.",
    "surprise_rest":           "Full-mix gap of 1-2 beats just before the hook.",
}


# ============================================================================
# 12. MIDI helpers
# ============================================================================

# Note name → semitones from C
NOTE_NAMES: dict[str, int] = {
    "C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4,
    "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11,
}

# Reverse mapping (canonical sharp spelling)
SEMITONE_TO_NAME: dict[int, str] = {
    0: "C", 1: "C#", 2: "D", 3: "D#", 4: "E", 5: "F",
    6: "F#", 7: "G", 8: "G#", 9: "A", 10: "A#", 11: "B",
}


def midi_for(note: str, octave: int = 4) -> int:
    """Convert a note name + octave to MIDI number. C4 = 60."""
    if note not in NOTE_NAMES:
        raise ValueError(f"unknown note: {note}")
    return (octave + 1) * 12 + NOTE_NAMES[note]


def name_for(midi: int) -> tuple[str, int]:
    """Convert MIDI number to (note, octave)."""
    return SEMITONE_TO_NAME[midi % 12], (midi // 12) - 1


def scale_notes(root: str, mode: str, octave: int = 4) -> list[int]:
    """Return MIDI notes of a scale starting at root in given octave."""
    if mode not in SCALES:
        raise ValueError(f"unknown mode: {mode}")
    base = midi_for(root, octave)
    return [base + iv for iv in SCALES[mode]]


def diatonic_chord_pitches(root: str, mode: str, degree: int, octave: int = 4) -> list[int]:
    """Return the triad pitches for the Nth scale degree (1-indexed) of root/mode."""
    notes = scale_notes(root, mode, octave) * 2  # extend an octave for stacking
    idx = degree - 1
    return [notes[idx], notes[idx + 2], notes[idx + 4]]
