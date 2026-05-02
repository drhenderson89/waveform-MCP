"""Tests for poly_transcribe — the BasicPitch integration.

These tests don't require basic-pitch to be installed; the module is
tested via mocks. The one integration test is gated on availability.
"""
from __future__ import annotations

import json
from unittest import mock

import pytest

from waveform_mcp import poly_transcribe
from waveform_mcp.composition_spec import NoteEvent, TrackSpec, PluginChainSpec
from waveform_mcp.poly_transcribe import (
    TranscriptionOptions,
    TranscriptionResult,
    filter_by_role,
    is_available,
    populate_spec_tracks_from_transcription,
    transcribe,
)


# --------------------------------------------------------------------------- #
# is_available                                                                #
# --------------------------------------------------------------------------- #


class TestIsAvailable:
    def test_returns_bool(self):
        # Whatever the host, this returns a bool (no crash)
        assert isinstance(is_available(), bool)


# --------------------------------------------------------------------------- #
# transcribe — error paths                                                    #
# --------------------------------------------------------------------------- #


class TestTranscribeErrors:
    def test_missing_audio_file_returns_error(self):
        r = transcribe("C:\\does\\not\\exist.wav", bpm=120)
        assert r.ok is False
        assert "not found" in r.error

    def test_no_basic_pitch_returns_helpful_error(self, tmp_path, monkeypatch):
        # Force "basic-pitch not installed"
        monkeypatch.setattr(poly_transcribe, "_AVAILABLE_PY", None)
        monkeypatch.setattr(poly_transcribe, "_python_for_basic_pitch", lambda: None)
        # Real existing file
        audio = tmp_path / "x.wav"
        audio.write_bytes(b"RIFF\0\0\0\0WAVE")  # minimal "exists" file
        r = transcribe(str(audio), bpm=120)
        assert r.ok is False
        assert "basic-pitch" in r.error.lower()

    def test_subprocess_crash_propagates(self, tmp_path, monkeypatch):
        audio = tmp_path / "x.wav"
        audio.write_bytes(b"RIFF\0\0\0\0WAVE")
        # Pretend basic-pitch is installed at a fake interpreter
        monkeypatch.setattr(poly_transcribe, "_python_for_basic_pitch",
                            lambda: "/fake/python")
        # Mock subprocess.run to return non-zero
        with mock.patch("waveform_mcp.poly_transcribe.subprocess.run") as m:
            m.return_value = mock.MagicMock(
                returncode=1, stdout="", stderr="boom")
            r = transcribe(str(audio), bpm=120)
        assert r.ok is False
        assert "exited 1" in r.error or "boom" in r.error


# --------------------------------------------------------------------------- #
# transcribe — happy path with mocked subprocess                              #
# --------------------------------------------------------------------------- #


class TestTranscribeMocked:
    def test_parses_subprocess_output_and_converts_to_beats(self, tmp_path, monkeypatch):
        audio = tmp_path / "x.wav"
        audio.write_bytes(b"RIFF\0\0\0\0WAVE")
        monkeypatch.setattr(poly_transcribe, "_python_for_basic_pitch",
                            lambda: "/fake/python")
        # Mock subprocess output
        fake_payload = {
            "notes": [
                {"start_sec": 0.0, "end_sec": 0.5, "pitch": 60, "amplitude": 0.7},
                {"start_sec": 0.5, "end_sec": 1.0, "pitch": 62, "amplitude": 0.8},
            ],
            "bpm": None,
        }
        with mock.patch("waveform_mcp.poly_transcribe.subprocess.run") as m:
            m.return_value = mock.MagicMock(
                returncode=0, stdout=json.dumps(fake_payload), stderr="")
            r = transcribe(str(audio), bpm=120)
        assert r.ok is True
        assert len(r.notes) == 2
        # 0.5 sec @ 120 BPM = 1 beat
        assert r.notes[0].pitch == 60
        assert abs(r.notes[1].start_beat - 1.0) < 1e-6
        # Velocity from amplitude 0.7 ≈ 89
        assert 80 <= r.notes[0].velocity <= 95


# --------------------------------------------------------------------------- #
# filter_by_role                                                              #
# --------------------------------------------------------------------------- #


class TestFilterByRole:
    def _make_notes(self) -> list[NoteEvent]:
        return [
            NoteEvent(0, 1, pitch=p) for p in [
                28,   # bass range
                36,   # bass range
                48,   # keys/lead crossover
                60,   # mid
                72,   # lead
                84,   # high lead
            ]
        ]

    def test_bass_filters_to_low_pitches(self):
        n = filter_by_role(self._make_notes(), "bass")
        assert all(24 <= x.pitch <= 50 for x in n)
        assert any(x.pitch == 28 for x in n)

    def test_lead_filters_to_high_pitches(self):
        n = filter_by_role(self._make_notes(), "lead")
        assert all(60 <= x.pitch <= 96 for x in n)
        assert any(x.pitch == 84 for x in n)

    def test_unknown_role_returns_all(self):
        all_notes = self._make_notes()
        assert filter_by_role(all_notes, "fx") == all_notes


# --------------------------------------------------------------------------- #
# populate_spec_tracks_from_transcription                                     #
# --------------------------------------------------------------------------- #


class TestPopulateSpec:
    def _spec_tracks(self) -> list[TrackSpec]:
        return [
            TrackSpec(name="Drums", role="drums",
                      chain=PluginChainSpec(plugins=["sampler"])),
            TrackSpec(name="Bass", role="bass",
                      chain=PluginChainSpec(plugins=["4osc"])),
            TrackSpec(name="Lead", role="lead",
                      chain=PluginChainSpec(plugins=["4osc"])),
        ]

    def test_assigns_by_pitch_range(self):
        notes = [
            NoteEvent(0, 1, pitch=28),  # bass
            NoteEvent(1, 1, pitch=36),  # bass
            NoteEvent(2, 1, pitch=72),  # lead
            NoteEvent(3, 1, pitch=84),  # lead
        ]
        tracks = self._spec_tracks()
        counts = populate_spec_tracks_from_transcription(tracks, notes)
        bass = next(t for t in tracks if t.role == "bass")
        lead = next(t for t in tracks if t.role == "lead")
        assert len(bass.notes) == 2
        assert len(lead.notes) == 2
        assert "bass" in counts and counts["bass"] == 2

    def test_drum_tracks_never_touched(self):
        notes = [NoteEvent(0, 1, pitch=36)]  # might be in bass range
        tracks = self._spec_tracks()
        populate_spec_tracks_from_transcription(tracks, notes)
        drums = next(t for t in tracks if t.role == "drums")
        assert drums.notes == []

    def test_existing_notes_preserved_unless_overwrite(self):
        tracks = self._spec_tracks()
        bass = next(t for t in tracks if t.role == "bass")
        bass.notes.append(NoteEvent(0, 1, pitch=36, velocity=99))
        populate_spec_tracks_from_transcription(
            tracks, [NoteEvent(0, 1, pitch=40)],
        )
        # Bass already had a note; new one isn't appended (overwrite=False default)
        assert len(bass.notes) == 1
        assert bass.notes[0].velocity == 99

    def test_overwrite_replaces_existing(self):
        tracks = self._spec_tracks()
        bass = next(t for t in tracks if t.role == "bass")
        bass.notes.append(NoteEvent(0, 1, pitch=36, velocity=99))
        populate_spec_tracks_from_transcription(
            tracks, [NoteEvent(0, 1, pitch=40)], overwrite=True,
        )
        assert len(bass.notes) == 1
        assert bass.notes[0].pitch == 40


# --------------------------------------------------------------------------- #
# Integration (gated on basic-pitch being installed)                          #
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(not is_available(),
                    reason="basic-pitch not installed in any locatable Python")
class TestRealTranscription:
    """Only runs when basic-pitch is installed somewhere we can find it."""

    def test_roundtrip_on_short_clip(self, tmp_path):
        # If a short test WAV exists in fixtures, use it; otherwise skip
        import os
        fixtures = os.path.join(os.path.dirname(__file__), "fixtures")
        wavs = [f for f in os.listdir(fixtures) if f.endswith(".wav")] if os.path.isdir(fixtures) else []
        if not wavs:
            pytest.skip("no wav fixtures available")
        audio = os.path.join(fixtures, wavs[0])
        r = transcribe(audio, bpm=120)
        assert r.ok is True, r.error
        # We don't know exact note count but it should be > 0
        assert len(r.notes) > 0
