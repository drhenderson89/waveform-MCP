"""Tests for waveform_mcp.reference_adapter."""
from __future__ import annotations

import pytest

from waveform_mcp.composition_spec import CompositionSpec
from waveform_mcp.reference_adapter import (
    AdapterOptions,
    guess_genre,
    parse_chord_name,
    pitch_class_from_name,
    plugin_chain_for,
    spec_from_analyzer_json,
)


# --------------------------------------------------------------------------- #
# pitch_class_from_name                                                       #
# --------------------------------------------------------------------------- #


class TestPitchClass:
    def test_natural_notes(self):
        assert pitch_class_from_name("C") == 0
        assert pitch_class_from_name("D") == 2
        assert pitch_class_from_name("E") == 4
        assert pitch_class_from_name("F") == 5
        assert pitch_class_from_name("G") == 7
        assert pitch_class_from_name("A") == 9
        assert pitch_class_from_name("B") == 11

    def test_sharps_and_flats_equivalent(self):
        assert pitch_class_from_name("C#") == pitch_class_from_name("Db")
        assert pitch_class_from_name("F#") == pitch_class_from_name("Gb")
        assert pitch_class_from_name("A#") == pitch_class_from_name("Bb")

    def test_strips_octave(self):
        assert pitch_class_from_name("C4") == 0
        assert pitch_class_from_name("Eb-2") == 3

    def test_whitespace_tolerated(self):
        assert pitch_class_from_name("  G#  ") == 8

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            pitch_class_from_name("X")
        with pytest.raises(ValueError):
            pitch_class_from_name("")
        with pytest.raises(ValueError):
            pitch_class_from_name(123)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# parse_chord_name                                                            #
# --------------------------------------------------------------------------- #


class TestParseChord:
    def test_bare_root_is_major(self):
        assert parse_chord_name("C") == (0, "maj")
        assert parse_chord_name("F#") == (6, "maj")

    def test_minor(self):
        assert parse_chord_name("Dm") == (2, "min")
        assert parse_chord_name("Bbm") == (10, "min")

    def test_seventh_variants(self):
        assert parse_chord_name("G7") == (7, "7")
        assert parse_chord_name("Cmaj7") == (0, "maj7")
        assert parse_chord_name("Am7") == (9, "min7")

    def test_power_chord(self):
        assert parse_chord_name("E5") == (4, "5")
        assert parse_chord_name("D5") == (2, "5")

    def test_extended(self):
        assert parse_chord_name("Cadd9") == (0, "add9")
        assert parse_chord_name("Bm7b5") == (11, "m7b5")
        assert parse_chord_name("Dsus4") == (2, "sus4")

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            parse_chord_name("")
        with pytest.raises(ValueError):
            parse_chord_name("   ")


# --------------------------------------------------------------------------- #
# guess_genre                                                                 #
# --------------------------------------------------------------------------- #


def _metal_analysis() -> dict:
    return {
        "duration": 180,
        "rhythm": {"global_bpm": 140, "syncopation": 0.4, "avg_density": 7.5},
        "harmony": {"overall_key": "D Minor", "chords": []},
        "timbre": {
            "harmonic_pct": 35, "percussive_pct": 65,
            "brightness_description": "bright",
            "tonality_description": "noisy / inharmonic",
            "dynamic_range": 4.0,
            "band_energy_pct": {"sub_bass": 8, "bass": 18, "low_mid": 22,
                                "mid": 18, "upper_mid": 22, "presence": 8, "air": 4},
        },
        "structure": [{"type": "Verse", "start": 0, "end": 30}],
    }


def _lofi_analysis() -> dict:
    return {
        "duration": 180,
        "rhythm": {"global_bpm": 78, "syncopation": 0.3, "avg_density": 3.5},
        "harmony": {"overall_key": "F Major", "chords": []},
        "timbre": {
            "harmonic_pct": 65, "percussive_pct": 35,
            "brightness_description": "dark",
            "tonality_description": "warm",
            "dynamic_range": 8.0,
            "band_energy_pct": {"sub_bass": 12, "bass": 20},
        },
        "structure": [{"type": "Verse", "start": 0, "end": 30}],
    }


def _hip_hop_analysis() -> dict:
    return {
        "duration": 180,
        "rhythm": {"global_bpm": 90, "syncopation": 0.5, "avg_density": 4.0},
        "harmony": {"overall_key": "G Minor", "chords": []},
        "timbre": {
            "harmonic_pct": 50, "percussive_pct": 50,
            "brightness_description": "balanced",
            "tonality_description": "warm",
            "dynamic_range": 6.0,
            "band_energy_pct": {"sub_bass": 25, "bass": 22},
        },
        "structure": [{"type": "Verse", "start": 0, "end": 30}],
    }


def _edm_analysis() -> dict:
    return {
        "duration": 180,
        "rhythm": {"global_bpm": 128, "syncopation": 0.1, "avg_density": 8.0},
        "harmony": {"overall_key": "A Minor", "chords": []},
        "timbre": {
            "harmonic_pct": 60, "percussive_pct": 40,
            "brightness_description": "bright",
            "tonality_description": "harmonic",
            "dynamic_range": 4.0,
            "band_energy_pct": {"sub_bass": 15, "bass": 20},
        },
        "structure": [{"type": "Climax / Drop", "start": 60, "end": 120}],
    }


class TestGuessGenre:
    def test_metal(self):
        assert guess_genre(_metal_analysis()) == "metal"

    def test_lofi(self):
        assert guess_genre(_lofi_analysis()) == "lofi"

    def test_hip_hop(self):
        assert guess_genre(_hip_hop_analysis()) == "hip_hop"

    def test_edm_drop(self):
        assert guess_genre(_edm_analysis()) == "edm"

    def test_unknown_falls_back_to_pop(self):
        assert guess_genre({}) == "pop"

    def test_never_raises_on_garbage(self):
        for garbage in [{}, {"rhythm": None}, {"timbre": "bad"}, {"structure": "x"}]:
            try:
                g = guess_genre(garbage)  # type: ignore[arg-type]
            except Exception as e:
                pytest.fail(f"guess_genre raised on {garbage!r}: {e}")
            assert isinstance(g, str)


# --------------------------------------------------------------------------- #
# plugin_chain_for                                                            #
# --------------------------------------------------------------------------- #


class TestPluginChain:
    def test_metal_rhythm_uses_full_chain(self):
        chain = plugin_chain_for("metal", "rhythm_guitar_l")
        assert chain == ["sforzando", "Anvil", "MT-A", "TSC 1.1"]

    def test_unknown_genre_falls_back_to_default(self):
        chain = plugin_chain_for("klezmer", "drums")
        assert chain == ["sampler"]

    def test_unknown_role_falls_back_to_4osc(self):
        chain = plugin_chain_for("metal", "fx")
        # No 'fx' entry in metal table; should hit default
        assert chain == ["4osc"]

    def test_always_returns_non_empty(self):
        for genre in ["metal", "lofi", "edm", "synthwave", "pop", "hip_hop", "cinematic"]:
            for role in ["drums", "bass", "lead", "pad"]:
                chain = plugin_chain_for(genre, role)  # type: ignore[arg-type]
                assert chain, f"empty chain for ({genre}, {role})"


# --------------------------------------------------------------------------- #
# spec_from_analyzer_json                                                     #
# --------------------------------------------------------------------------- #


class TestSpecFromJson:
    def test_metal_produces_metal_genre(self):
        spec = spec_from_analyzer_json(_metal_analysis())
        assert spec.genre == "metal"
        # Metal roles include rhythm_guitar_l/r
        roles = [t.role for t in spec.tracks]
        assert "rhythm_guitar_l" in roles
        assert "rhythm_guitar_r" in roles

    def test_lofi_produces_lofi_tracks(self):
        spec = spec_from_analyzer_json(_lofi_analysis())
        assert spec.genre == "lofi"
        roles = [t.role for t in spec.tracks]
        assert "keys" in roles
        assert "rhythm_guitar_l" not in roles  # no metal guitars in lofi

    def test_force_genre_overrides_heuristic(self):
        # Pass a clearly metal song with force_genre='lofi'
        spec = spec_from_analyzer_json(
            _metal_analysis(),
            options=AdapterOptions(force_genre="lofi"),
        )
        assert spec.genre == "lofi"

    def test_tempo_passed_through(self):
        spec = spec_from_analyzer_json(_metal_analysis())
        assert spec.tempo_bpm == 140

    def test_key_parsed(self):
        spec = spec_from_analyzer_json(_metal_analysis())
        assert spec.key_root_pc == 2     # D
        assert spec.key_mode == "minor"

    def test_sections_converted_to_beats(self):
        # Verse 0..30s @ 140 BPM → 0..70 beats
        spec = spec_from_analyzer_json(_metal_analysis())
        assert len(spec.sections) == 1
        s = spec.sections[0]
        assert abs(s.start_beat - 0) < 0.01
        assert abs(s.length_beats - 70) < 1.0

    def test_no_structure_creates_one_verse(self):
        data = _metal_analysis()
        data["structure"] = []
        spec = spec_from_analyzer_json(data)
        assert len(spec.sections) == 1
        assert spec.sections[0].type == "verse"

    def test_chord_track_built_from_chords(self):
        data = _metal_analysis()
        data["harmony"]["chords"] = [
            {"chord": "Dm", "start": 0, "end": 4},
            {"chord": "Bb", "start": 4, "end": 8},
            {"chord": "F", "start": 8, "end": 12},
            {"chord": "C", "start": 12, "end": 16},
        ]
        spec = spec_from_analyzer_json(data)
        assert len(spec.chord_track) == 4
        # First chord: Dm, root_pc=2, quality=min
        c = spec.chord_track[0]
        assert c.root_pc == 2
        assert c.quality == "min"
        # Beats: 0..(4 sec * 140/60) ≈ 9.33 beats
        assert abs(c.length_beats - (4 * 140 / 60)) < 0.5

    def test_empty_chords_synthesises_tonic(self):
        data = _metal_analysis()
        data["harmony"]["chords"] = []
        spec = spec_from_analyzer_json(data)
        assert len(spec.chord_track) == 1
        # Should match key
        assert spec.chord_track[0].root_pc == spec.key_root_pc

    def test_bass_line_seeded_from_chord_track(self):
        data = _metal_analysis()
        data["harmony"]["chords"] = [{"chord": "Dm", "start": 0, "end": 4}]
        spec = spec_from_analyzer_json(data)
        bass = next(t for t in spec.tracks if t.role == "bass")
        assert len(bass.notes) == 1
        # MIDI 36 + 2 (D) = 38
        assert bass.notes[0].pitch == 38

    def test_drum_track_has_pattern(self):
        spec = spec_from_analyzer_json(_metal_analysis())
        drums = next(t for t in spec.tracks if t.role == "drums")
        assert drums.drum_pattern is not None
        assert len(drums.drum_pattern.notes) > 0

    def test_provenance_includes_warnings(self):
        # Bad chord name → warning, not crash
        data = _metal_analysis()
        data["harmony"]["chords"] = [{"chord": "GARBAGE!", "start": 0, "end": 4}]
        spec = spec_from_analyzer_json(data)
        warnings = spec.provenance.get("warnings", [])
        assert any("GARBAGE" in w for w in warnings)

    def test_bars_override_truncates(self):
        spec = spec_from_analyzer_json(
            _metal_analysis(),
            options=AdapterOptions(bars_override=8),
        )
        # 8 bars * 4 beats = 32 beats max
        for s in spec.sections:
            assert s.start_beat + s.length_beats <= 32 + 0.01

    def test_skip_pad_drops_pad_role(self):
        spec = spec_from_analyzer_json(
            _metal_analysis(),
            options=AdapterOptions(skip_pad=True),
        )
        roles = [t.role for t in spec.tracks]
        assert "pad" not in roles

    def test_resulting_spec_validates(self):
        spec = spec_from_analyzer_json(_metal_analysis())
        assert spec.is_valid(), f"validation issues: {spec.validate()}"

    def test_robust_to_completely_empty_input(self):
        spec = spec_from_analyzer_json({})
        # Should produce a usable default spec
        assert isinstance(spec, CompositionSpec)
        assert spec.tempo_bpm == 120.0
        assert len(spec.sections) >= 1
