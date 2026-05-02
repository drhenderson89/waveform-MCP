"""Tests for waveform_mcp.composition_spec."""
from __future__ import annotations

import json
import pytest

from waveform_mcp.composition_spec import (
    ChordEvent,
    CompositionSpec,
    DrumPatternSpec,
    NoteEvent,
    PluginChainSpec,
    SectionSpec,
    TrackSpec,
)


# --------------------------------------------------------------------------- #
# Construction defaults                                                       #
# --------------------------------------------------------------------------- #


class TestDefaults:
    def test_empty_spec_is_valid(self):
        spec = CompositionSpec()
        assert spec.is_valid()
        assert spec.validate() == []

    def test_total_length_empty_is_zero(self):
        assert CompositionSpec().total_length_beats == 0.0

    def test_total_length_picks_max_end_beat(self):
        spec = CompositionSpec(sections=[
            SectionSpec(type="intro", start_beat=0, length_beats=16),
            SectionSpec(type="verse", start_beat=16, length_beats=32),
            SectionSpec(type="outro", start_beat=48, length_beats=8),
        ])
        assert spec.total_length_beats == 56.0
        assert spec.total_length_bars == 14.0  # 4/4

    def test_plugin_chain_pads_params(self):
        chain = PluginChainSpec(plugins=["sampler", "comp", "limiter"])
        assert len(chain.params) == 3
        assert all(p == {} for p in chain.params)


# --------------------------------------------------------------------------- #
# Validation                                                                  #
# --------------------------------------------------------------------------- #


class TestValidation:
    def test_bad_tempo_flagged(self):
        spec = CompositionSpec(tempo_bpm=10)
        problems = spec.validate()
        assert any("tempo" in p for p in problems)

    def test_bad_key_flagged(self):
        spec = CompositionSpec(key_root_pc=12)
        problems = spec.validate()
        assert any("key_root_pc" in p for p in problems)

    def test_bad_mode_flagged(self):
        spec = CompositionSpec(key_mode="dorian")  # type: ignore[arg-type]
        problems = spec.validate()
        assert any("key_mode" in p for p in problems)

    def test_overlapping_sections_flagged(self):
        spec = CompositionSpec(sections=[
            SectionSpec(type="intro", start_beat=0, length_beats=16),
            SectionSpec(type="verse", start_beat=12, length_beats=20),  # overlap
        ])
        problems = spec.validate()
        assert any("overlap" in p for p in problems)

    def test_negative_length_flagged(self):
        spec = CompositionSpec(sections=[
            SectionSpec(type="verse", start_beat=0, length_beats=-4),
        ])
        problems = spec.validate()
        assert any("non-positive length" in p for p in problems)

    def test_audio_track_without_path_flagged(self):
        spec = CompositionSpec(tracks=[TrackSpec(name="X", role="ambience", kind="audio")])
        problems = spec.validate()
        assert any("no audio_path" in p for p in problems)

    def test_midi_track_with_audio_path_flagged(self):
        spec = CompositionSpec(tracks=[
            TrackSpec(name="X", role="lead", kind="midi", audio_path="x.wav")
        ])
        problems = spec.validate()
        assert any("audio_path is set" in p for p in problems)

    def test_pitch_out_of_midi_range_flagged(self):
        spec = CompositionSpec(tracks=[
            TrackSpec(name="X", role="lead",
                      notes=[NoteEvent(start_beat=0, length_beats=1, pitch=200)]),
        ])
        assert any("pitch out of MIDI range" in p for p in spec.validate())

    def test_velocity_out_of_range_flagged(self):
        spec = CompositionSpec(tracks=[
            TrackSpec(name="X", role="lead",
                      notes=[NoteEvent(start_beat=0, length_beats=1, pitch=60, velocity=200)]),
        ])
        assert any("velocity" in p for p in spec.validate())

    def test_pan_out_of_range_flagged(self):
        spec = CompositionSpec(tracks=[TrackSpec(name="X", role="lead", pan=2.5)])
        assert any("pan out of" in p for p in spec.validate())

    def test_chord_root_pc_validated(self):
        spec = CompositionSpec(chord_track=[
            ChordEvent(start_beat=0, length_beats=4, root_pc=15),
        ])
        assert any("root_pc out of range" in p for p in spec.validate())

    def test_density_clamp_validated(self):
        spec = CompositionSpec(sections=[
            SectionSpec(type="verse", start_beat=0, length_beats=4, density=2.5),
        ])
        assert any("density out of" in p for p in spec.validate())


# --------------------------------------------------------------------------- #
# Serialisation round-trip                                                    #
# --------------------------------------------------------------------------- #


def _make_full_spec() -> CompositionSpec:
    """Produce a maximally-populated spec exercising every field."""
    return CompositionSpec(
        name="Test Song",
        tempo_bpm=140,
        time_sig_num=4, time_sig_denom=4,
        key_root_pc=2, key_mode="minor",
        genre="metal",
        sections=[
            SectionSpec(type="intro", start_beat=0, length_beats=16, density=0.3,
                        brightness=0.4, instrumentation=["pad", "drums"], label="Intro"),
            SectionSpec(type="verse", start_beat=16, length_beats=32, density=0.7,
                        brightness=0.6,
                        instrumentation=["drums", "bass", "rhythm_guitar_l", "rhythm_guitar_r"]),
            SectionSpec(type="chorus", start_beat=48, length_beats=32, density=0.95,
                        brightness=0.8),
        ],
        chord_track=[
            ChordEvent(start_beat=0, length_beats=8, root_pc=2, quality="min"),
            ChordEvent(start_beat=8, length_beats=8, root_pc=10, quality="maj"),
            ChordEvent(start_beat=16, length_beats=8, root_pc=9, quality="min7",
                       bass_pc=4, inversion=1),
        ],
        tracks=[
            TrackSpec(
                name="Drums", role="drums",
                chain=PluginChainSpec(
                    plugins=["sampler", "comp"],
                    params=[{"library": "metal_kit"}, {"threshold": -10}],
                ),
                drum_pattern=DrumPatternSpec(
                    notes=[
                        NoteEvent(start_beat=0,    length_beats=0.25, pitch=36),
                        NoteEvent(start_beat=1,    length_beats=0.25, pitch=38),
                        NoteEvent(start_beat=2,    length_beats=0.25, pitch=36),
                        NoteEvent(start_beat=3,    length_beats=0.25, pitch=38),
                    ],
                    loop_length_beats=4.0,
                    section_variants={
                        2: [NoteEvent(start_beat=0, length_beats=0.25, pitch=36)],  # half-time
                    },
                ),
            ),
            TrackSpec(
                name="Lead", role="lead", pan=0.1,
                chain=PluginChainSpec(plugins=["sforzando"], params=[{"sfz": "metal_lead"}]),
                notes=[
                    NoteEvent(start_beat=0, length_beats=2, pitch=62, velocity=110),
                    NoteEvent(start_beat=2, length_beats=2, pitch=65, velocity=100),
                ],
                mix_offset_db=2.0,
                reverb_rt60_s=1.4, reverb_wet=0.25,
                sidechain_to_drums=False,
                volume_automation=[(0, -6), (16, 0), (48, 3)],
                pan_automation=[(0, 0), (32, 0.2)],
            ),
        ],
        master_chain=PluginChainSpec(plugins=["3bandEq", "comp", "limiter"]),
        provenance={"source": "test", "analyzer_version": "v5"},
    )


class TestSerialisation:
    def test_round_trip_dict_preserves_data(self):
        spec = _make_full_spec()
        data = spec.to_dict()
        rebuilt = CompositionSpec.from_dict(data)
        assert rebuilt.to_dict() == data

    def test_round_trip_json_preserves_data(self):
        spec = _make_full_spec()
        raw = spec.to_json()
        rebuilt = CompositionSpec.from_json(raw)
        assert rebuilt.to_dict() == spec.to_dict()

    def test_from_dict_tolerates_missing_fields(self):
        spec = CompositionSpec.from_dict({"name": "minimal"})
        assert spec.name == "minimal"
        assert spec.tempo_bpm == 120.0  # default

    def test_from_dict_rejects_non_dict(self):
        with pytest.raises(TypeError):
            CompositionSpec.from_dict("not a dict")  # type: ignore[arg-type]

    def test_round_trip_preserves_section_variants(self):
        spec = _make_full_spec()
        rebuilt = CompositionSpec.from_dict(spec.to_dict())
        drum_pat = rebuilt.tracks[0].drum_pattern
        assert drum_pat is not None
        assert 2 in drum_pat.section_variants
        assert len(drum_pat.section_variants[2]) == 1

    def test_round_trip_preserves_automation_tuples(self):
        spec = _make_full_spec()
        rebuilt = CompositionSpec.from_dict(spec.to_dict())
        lead = rebuilt.tracks[1]
        assert lead.volume_automation == [(0.0, -6.0), (16.0, 0.0), (48.0, 3.0)]
        assert lead.pan_automation == [(0.0, 0.0), (32.0, 0.2)]

    def test_round_trip_preserves_chord_inversion_and_bass(self):
        spec = _make_full_spec()
        rebuilt = CompositionSpec.from_dict(spec.to_dict())
        c2 = rebuilt.chord_track[2]
        assert c2.bass_pc == 4
        assert c2.inversion == 1


# --------------------------------------------------------------------------- #
# Robustness                                                                  #
# --------------------------------------------------------------------------- #


class TestRobustness:
    def test_validate_never_raises_on_full_spec(self):
        # Even on a deeply-populated spec, validate() should return a list
        spec = _make_full_spec()
        assert isinstance(spec.validate(), list)

    def test_to_json_produces_valid_json(self):
        spec = _make_full_spec()
        # Round-trip through stdlib json to ensure no non-serialisable types
        parsed = json.loads(spec.to_json())
        assert parsed["name"] == "Test Song"
        assert parsed["genre"] == "metal"

    def test_from_dict_ignores_unknown_fields(self):
        # Forward compatibility: unknown fields don't crash
        spec = CompositionSpec.from_dict({
            "name": "x", "future_field_42": "ignored",
        })
        assert spec.name == "x"
