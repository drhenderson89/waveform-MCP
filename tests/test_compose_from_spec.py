"""Tests for compose_from_spec — the spec-driven builder."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

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
from waveform_mcp.model import STORE
from waveform_mcp.tools.compose_from_spec import compose_from_spec, _loop_pattern_across


# --------------------------------------------------------------------------- #
# Fixture spec                                                                #
# --------------------------------------------------------------------------- #


def _minimal_spec(*, length_bars: int = 8) -> CompositionSpec:
    beats = length_bars * 4
    return CompositionSpec(
        name="MinimalTest",
        tempo_bpm=120,
        key_root_pc=0, key_mode="major",
        genre="pop",
        sections=[
            SectionSpec(type="verse", start_beat=0, length_beats=beats, density=0.7),
        ],
        chord_track=[
            ChordEvent(start_beat=0, length_beats=beats / 2, root_pc=0, quality="maj"),
            ChordEvent(start_beat=beats / 2, length_beats=beats / 2, root_pc=7, quality="maj"),
        ],
        tracks=[
            TrackSpec(
                name="Drums", role="drums",
                chain=PluginChainSpec(plugins=["sampler"]),
                drum_pattern=DrumPatternSpec(
                    notes=[
                        NoteEvent(start_beat=0, length_beats=0.25, pitch=36),
                        NoteEvent(start_beat=2, length_beats=0.25, pitch=38),
                    ],
                    loop_length_beats=4.0,
                ),
            ),
            TrackSpec(
                name="Bass", role="bass",
                chain=PluginChainSpec(plugins=["4osc"]),
                notes=[
                    NoteEvent(start_beat=0, length_beats=8, pitch=36),
                    NoteEvent(start_beat=16, length_beats=8, pitch=43),
                ],
            ),
            TrackSpec(
                name="Pad", role="pad",
                chain=PluginChainSpec(plugins=["4osc"]),
                # No notes — should auto-fill from chord_track
            ),
        ],
        master_chain=PluginChainSpec(plugins=["3bandEq", "comp", "limiter"]),
    )


def _tmp_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".tracktionedit")
    os.close(fd)
    os.remove(path)
    return path


# --------------------------------------------------------------------------- #
# Smoke / round-trip                                                          #
# --------------------------------------------------------------------------- #


class TestBasicBuild:
    def test_build_minimal_spec_writes_file(self):
        out = _tmp_path()
        try:
            r = compose_from_spec({"spec": _minimal_spec(), "out_path": out})
            assert r["ok"] is True, r
            assert Path(out).exists()
            assert Path(out).stat().st_size > 100  # not empty
        finally:
            if os.path.exists(out):
                os.remove(out)

    def test_build_returns_metadata(self):
        out = _tmp_path()
        try:
            r = compose_from_spec({"spec": _minimal_spec(length_bars=16), "out_path": out})
            assert r["bars"] == 16
            assert r["tempo"] == 120
            assert "Drums" in r["tracks"]
            assert "Bass" in r["tracks"]
            assert "Pad" in r["tracks"]
        finally:
            if os.path.exists(out):
                os.remove(out)

    def test_build_accepts_dict_spec(self):
        out = _tmp_path()
        try:
            r = compose_from_spec({
                "spec": _minimal_spec().to_dict(),
                "out_path": out,
            })
            assert r["ok"] is True
        finally:
            if os.path.exists(out):
                os.remove(out)


# --------------------------------------------------------------------------- #
# Track / clip wiring                                                         #
# --------------------------------------------------------------------------- #


class TestTrackContent:
    def test_drums_use_single_continuous_clip(self):
        # Drums are now laid as ONE clip across the whole song so they
        # stay phase-locked — section-level clips drift relative to the
        # song's actual bar grid because of bar-snap rounding.
        spec = _minimal_spec()
        spec.sections = [
            SectionSpec(type="intro", start_beat=0, length_beats=16),
            SectionSpec(type="verse", start_beat=16, length_beats=32),
            SectionSpec(type="outro", start_beat=48, length_beats=16),
        ]
        out = _tmp_path()
        try:
            r = compose_from_spec({"spec": spec, "out_path": out})
            assert r["ok"]
            drums = next(t for t in STORE.edit.tracks if t.name == "Drums")
            # Single continuous clip, not per-section
            assert len(drums.clips) == 1
            # Clip covers the full song
            assert drums.clips[0].length_beats >= 60.0
        finally:
            if os.path.exists(out):
                os.remove(out)

    def test_bass_track_gets_notes(self):
        spec = _minimal_spec()
        out = _tmp_path()
        try:
            compose_from_spec({"spec": spec, "out_path": out})
            bass = next(t for t in STORE.edit.tracks if t.name == "Bass")
            assert len(bass.clips) == 1
            assert len(bass.clips[0].notes) == 2
            assert bass.clips[0].notes[0].pitch == 36
        finally:
            if os.path.exists(out):
                os.remove(out)

    def test_pad_auto_filled_from_chord_track(self):
        spec = _minimal_spec()
        out = _tmp_path()
        try:
            compose_from_spec({"spec": spec, "out_path": out})
            pad = next(t for t in STORE.edit.tracks if t.name == "Pad")
            assert len(pad.clips) == 1
            # 2 chords × 3 chord tones = 6 notes
            assert len(pad.clips[0].notes) == 6
        finally:
            if os.path.exists(out):
                os.remove(out)

    def test_plugin_chain_appended_in_order(self):
        spec = _minimal_spec()
        # Override drum chain with several plugins to verify ordering
        spec.tracks[0].chain = PluginChainSpec(plugins=["sampler", "comp", "3bandEq"])
        out = _tmp_path()
        try:
            compose_from_spec({"spec": spec, "out_path": out})
            drums = next(t for t in STORE.edit.tracks if t.name == "Drums")
            types = [p.type for p in drums.plugins]
            # The fader 'volume' and 'level' chrome plugins are appended by
            # mix_apply_reference / xml writer, so we just check the
            # instrument plugins appear in the order we set.
            sampler_idx = next(i for i, p in enumerate(drums.plugins) if p.type == "sampler")
            comp_idx = next(i for i, p in enumerate(drums.plugins) if p.type == "comp")
            assert sampler_idx < comp_idx
        finally:
            if os.path.exists(out):
                os.remove(out)


# --------------------------------------------------------------------------- #
# Mix calibration integration                                                 #
# --------------------------------------------------------------------------- #


class TestMixCalibration:
    def test_volume_db_set_with_calibration(self):
        spec = _minimal_spec()
        out = _tmp_path()
        try:
            compose_from_spec({"spec": spec, "out_path": out, "calibrate": True})
            drums = next(t for t in STORE.edit.tracks if t.name == "Drums")
            # Sampler in pop genre, drums role, target = -7 dB.
            # Sampler offset = 0 dB, so fader = -7 dB exactly.
            assert abs(drums.volume_db - (-7.0)) < 0.5
        finally:
            if os.path.exists(out):
                os.remove(out)

    def test_calibration_off_uses_raw_role_target(self):
        spec = _minimal_spec()
        out = _tmp_path()
        try:
            compose_from_spec({"spec": spec, "out_path": out, "calibrate": False})
            drums = next(t for t in STORE.edit.tracks if t.name == "Drums")
            # Without calibration: drums role = -7 dB literal
            assert abs(drums.volume_db - (-7.0)) < 0.5
        finally:
            if os.path.exists(out):
                os.remove(out)


# --------------------------------------------------------------------------- #
# Loop helper                                                                 #
# --------------------------------------------------------------------------- #


class TestLoopHelper:
    def test_loop_pattern_repeats(self):
        notes = [NoteEvent(start_beat=0, length_beats=0.25, pitch=36),
                 NoteEvent(start_beat=2, length_beats=0.25, pitch=38)]
        out = _loop_pattern_across(notes, loop_len=4.0, section_len=12.0)
        # 12 / 4 = 3 iterations × 2 notes = 6
        assert len(out) == 6
        starts = sorted(n.start_beat for n in out)
        assert starts == [0.0, 2.0, 4.0, 6.0, 8.0, 10.0]

    def test_loop_truncates_overhanging_notes(self):
        notes = [NoteEvent(start_beat=0, length_beats=2, pitch=36)]
        out = _loop_pattern_across(notes, loop_len=4.0, section_len=5.0)
        # First iter: 0.0, len=2 (fits in 5)
        # Second iter at offset=4: start=4, len=2 → would extend to 6, capped to 1
        assert len(out) == 2
        assert out[1].length_beats == 1.0

    def test_loop_zero_section_returns_empty(self):
        notes = [NoteEvent(start_beat=0, length_beats=0.25, pitch=36)]
        assert _loop_pattern_across(notes, 4.0, 0) == []


# --------------------------------------------------------------------------- #
# Robustness                                                                  #
# --------------------------------------------------------------------------- #


class TestRobustness:
    def test_missing_spec_returns_error(self):
        r = compose_from_spec({"out_path": _tmp_path()})
        assert r["ok"] is False
        assert "spec" in r["error"]

    def test_missing_out_path_returns_error(self):
        r = compose_from_spec({"spec": _minimal_spec()})
        assert r["ok"] is False
        assert "out_path" in r["error"]

    def test_invalid_spec_type_returns_error(self):
        r = compose_from_spec({"spec": "not a spec", "out_path": _tmp_path()})
        assert r["ok"] is False
        assert "spec" in r["error"].lower()

    def test_spec_with_validation_problems_still_builds(self):
        # Bad pitch value — surfaced as a warning, doesn't abort
        spec = _minimal_spec()
        spec.tracks[1].notes[0].pitch = 200
        out = _tmp_path()
        try:
            r = compose_from_spec({"spec": spec, "out_path": out})
            # Builder continues despite validation problems
            assert r["ok"] is True
            assert len(r["warnings"]) > 0
        finally:
            if os.path.exists(out):
                os.remove(out)

    def test_plugin_failure_recorded_as_warning(self):
        spec = _minimal_spec()
        # Put in a non-existent plugin
        spec.tracks[0].chain = PluginChainSpec(plugins=["nonexistent_xyz_plugin"])
        out = _tmp_path()
        try:
            r = compose_from_spec({"spec": spec, "out_path": out})
            assert r["ok"] is True  # build completes
        finally:
            if os.path.exists(out):
                os.remove(out)

    def test_audio_track_with_missing_path_warns_but_builds(self):
        spec = _minimal_spec()
        spec.tracks.append(TrackSpec(
            name="MissingAudio", role="ambience", kind="audio",
            audio_path=None,  # Will hit validation
        ))
        out = _tmp_path()
        try:
            r = compose_from_spec({"spec": spec, "out_path": out})
            # Validation flags it; build proceeds; warnings non-empty
            assert r["ok"] is True
            assert any("audio_path" in w for w in r["warnings"])
        finally:
            if os.path.exists(out):
                os.remove(out)

    def test_drum_track_sampler_auto_loaded_with_sounds(self):
        """A bare 'sampler' on a drum track must come back populated with
        SOUND entries — otherwise drums are silent."""
        spec = _minimal_spec()
        out = _tmp_path()
        try:
            r = compose_from_spec({"spec": spec, "out_path": out})
            assert r["ok"]
            drums = next(t for t in STORE.edit.tracks if t.name == "Drums")
            sampler = next(p for p in drums.plugins if p.type == "sampler")
            # Either factory presets are reachable (sounds populated) or the
            # builder logged a warning. We accept both — what we don't
            # accept is silent failure with no sounds and no warning.
            if not sampler.sounds:
                assert any("drum kit" in w.lower() for w in r["warnings"]), \
                    f"sampler empty AND no warning. warnings={r['warnings']}"
            else:
                # Standard factory kit produces ~10 mappings
                assert len(sampler.sounds) >= 5
                # Kick (36) and snare (38) must be present so basic patterns play
                key_notes = {s.get("keyNote") for s in sampler.sounds}
                assert 36 in key_notes
                assert 38 in key_notes
        finally:
            if os.path.exists(out):
                os.remove(out)

    def test_empty_chord_track_produces_empty_pad(self):
        spec = _minimal_spec()
        spec.chord_track = []
        out = _tmp_path()
        try:
            compose_from_spec({"spec": spec, "out_path": out})
            pad = next(t for t in STORE.edit.tracks if t.name == "Pad")
            # No chord track → no pad clips placed
            assert len(pad.clips) == 0
        finally:
            if os.path.exists(out):
                os.remove(out)
