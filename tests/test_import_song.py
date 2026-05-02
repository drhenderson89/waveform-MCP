"""Tests for tools.import_song.

The analyzer subprocess is heavy and uses real audio. These tests
exercise the public API without spawning the analyzer — except in one
optional integration test gated on the audio file being present.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from waveform_mcp.tools import import_song
from waveform_mcp.tools.import_song import (
    analyze_to_spec,
    compose_from_reference,
    compose_from_spec_json,
)


def _tmp_path(suffix: str = ".tracktionedit") -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    os.remove(path)
    return path


# A canonical fake analyzer output for unit tests
def _fake_analysis_dict() -> dict:
    return {
        "track_name": "FakeSong",
        "duration": 60.0,
        "rhythm": {"global_bpm": 120, "syncopation": 0.3, "avg_density": 4,
                   "tempo_min": 118, "tempo_max": 122,
                   "beat_regularity": 0.95, "meter_candidates": [(4,)]},
        "harmony": {
            "overall_key": "C Major",
            "chords": [
                {"chord": "C", "start": 0, "end": 4},
                {"chord": "G", "start": 4, "end": 8},
                {"chord": "Am", "start": 8, "end": 12},
                {"chord": "F", "start": 12, "end": 16},
            ],
        },
        "timbre": {
            "harmonic_pct": 60, "percussive_pct": 40,
            "brightness_description": "balanced",
            "tonality_description": "harmonic",
            "dynamic_range": 8.0,
            "band_energy_pct": {"sub_bass": 10, "bass": 18, "low_mid": 18,
                                "mid": 18, "upper_mid": 16, "presence": 12, "air": 8},
        },
        "melody": {"has_melody": True, "stepwise_pct": 60, "leap_pct": 20},
        "stereo": {"is_stereo": True},
        "structure": [
            {"type": "Intro", "start": 0, "end": 8, "energy_ratio": 0.3, "onset_density": 2},
            {"type": "Verse", "start": 8, "end": 30, "energy_ratio": 0.7, "onset_density": 5},
            {"type": "Chorus (peak)", "start": 30, "end": 50, "energy_ratio": 1.2, "onset_density": 7},
            {"type": "Outro", "start": 50, "end": 60, "energy_ratio": 0.3, "onset_density": 2},
        ],
    }


# --------------------------------------------------------------------------- #
# analyze_to_spec — argument validation                                       #
# --------------------------------------------------------------------------- #


class TestAnalyzeToSpecValidation:
    def test_missing_audio_path_returns_error(self):
        r = analyze_to_spec({})
        assert r["ok"] is False
        assert "audio_path" in r["error"]

    def test_nonexistent_file_returns_error(self):
        # Doesn't matter if analyzer present — file check happens early
        r = analyze_to_spec({"audio_path": "C:\\does\\not\\exist.wav"})
        assert r["ok"] is False


# --------------------------------------------------------------------------- #
# analyze_to_spec — mocked analyzer subprocess                                #
# --------------------------------------------------------------------------- #


class TestAnalyzeToSpecMocked:
    def test_happy_path_returns_spec(self, tmp_path):
        # Create a fake "analyzer JSON" the mock will return
        analysis = _fake_analysis_dict()

        with mock.patch.object(import_song, "_run_analyzer",
                               return_value=(analysis, "fake.json")):
            with mock.patch.object(Path, "exists", return_value=True):
                r = analyze_to_spec({"audio_path": "x.wav"})
        assert r["ok"] is True
        assert r["analyzer_json"] == "fake.json"
        assert "spec" in r
        assert r["summary"]["tempo"] == 120
        assert r["summary"]["genre"] in ("pop", "synthwave")  # matches the fixture

    def test_analyzer_failure_propagates(self, tmp_path):
        with mock.patch.object(import_song, "_run_analyzer",
                               return_value=(None, "synthetic failure")):
            with mock.patch.object(Path, "exists", return_value=True):
                r = analyze_to_spec({"audio_path": "x.wav"})
        assert r["ok"] is False
        assert "synthetic failure" in r["error"]

    def test_force_genre_passed_through(self, tmp_path):
        analysis = _fake_analysis_dict()
        with mock.patch.object(import_song, "_run_analyzer",
                               return_value=(analysis, "fake.json")):
            with mock.patch.object(Path, "exists", return_value=True):
                r = analyze_to_spec({"audio_path": "x.wav", "force_genre": "metal"})
        assert r["spec"]["genre"] == "metal"


# --------------------------------------------------------------------------- #
# compose_from_reference — full pipeline (mocked analyzer)                    #
# --------------------------------------------------------------------------- #


class TestComposeFromReferenceMocked:
    def test_full_pipeline_writes_edit(self, tmp_path):
        out = str(tmp_path / "out.tracktionedit")
        analysis = _fake_analysis_dict()
        with mock.patch.object(import_song, "_run_analyzer",
                               return_value=(analysis, "fake.json")):
            with mock.patch.object(Path, "exists", return_value=True):
                r = compose_from_reference({
                    "audio_path": "x.wav",
                    "out_path": out,
                })
        assert r["ok"] is True, r
        assert os.path.exists(out)
        assert r["bars"] >= 1
        assert "summary" in r
        # Pipeline plumbed through the analyzer JSON path
        assert r["analyzer_json"] == "fake.json"

    def test_missing_out_path(self):
        r = compose_from_reference({"audio_path": "x.wav"})
        assert r["ok"] is False
        assert "out_path" in r["error"]

    def test_analyzer_failure_aborts(self, tmp_path):
        out = str(tmp_path / "out.tracktionedit")
        with mock.patch.object(import_song, "_run_analyzer",
                               return_value=(None, "no analyzer")):
            with mock.patch.object(Path, "exists", return_value=True):
                r = compose_from_reference({
                    "audio_path": "x.wav", "out_path": out,
                })
        assert r["ok"] is False
        assert not os.path.exists(out)


# --------------------------------------------------------------------------- #
# compose_from_spec_json                                                      #
# --------------------------------------------------------------------------- #


class TestComposeFromSpecJson:
    def test_inline_spec_builds(self, tmp_path):
        out = str(tmp_path / "edit.tracktionedit")
        from waveform_mcp.composition_spec import (CompositionSpec, SectionSpec,
                                                    TrackSpec, PluginChainSpec)
        spec = CompositionSpec(
            name="X", tempo_bpm=110,
            sections=[SectionSpec(type="verse", start_beat=0, length_beats=16)],
            tracks=[TrackSpec(name="Drums", role="drums",
                              chain=PluginChainSpec(plugins=["sampler"]))],
        )
        r = compose_from_spec_json({"spec": spec.to_dict(), "out_path": out})
        assert r["ok"] is True
        assert os.path.exists(out)

    def test_path_spec_loads_and_builds(self, tmp_path):
        from waveform_mcp.composition_spec import CompositionSpec
        spec = CompositionSpec(name="Y", tempo_bpm=140)
        spec_path = str(tmp_path / "spec.json")
        with open(spec_path, "w") as f:
            json.dump(spec.to_dict(), f)
        out = str(tmp_path / "edit.tracktionedit")
        r = compose_from_spec_json({"spec_path": spec_path, "out_path": out})
        assert r["ok"] is True

    def test_missing_spec_and_path_errors(self):
        r = compose_from_spec_json({"out_path": "x.tracktionedit"})
        assert r["ok"] is False

    def test_bad_spec_path_errors(self, tmp_path):
        r = compose_from_spec_json({
            "spec_path": str(tmp_path / "nope.json"),
            "out_path": str(tmp_path / "edit.tracktionedit"),
        })
        assert r["ok"] is False


# --------------------------------------------------------------------------- #
# Analyzer locator                                                            #
# --------------------------------------------------------------------------- #


class TestLocator:
    def test_returns_paths_or_none(self):
        py, d = import_song._default_analyzer_paths()
        # Either both populated or both None
        assert (py is None) == (d is None)
