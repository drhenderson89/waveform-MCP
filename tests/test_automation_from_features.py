"""Tests for automation_from_features and B3 builder wiring."""
from __future__ import annotations

import os
import tempfile

import numpy as np
import pytest

from waveform_mcp.automation_from_features import (
    filter_cutoff_curve,
    pan_breakpoints_from_stereo,
    volume_ride_segments,
)
from waveform_mcp.composition_spec import (
    CompositionSpec, NoteEvent, PluginChainSpec, SectionSpec, TrackSpec,
)
from waveform_mcp.model import STORE
from waveform_mcp.tools.compose_from_spec import compose_from_spec


def _tmp_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".tracktionedit")
    os.close(fd)
    os.remove(path)
    return path


# --------------------------------------------------------------------------- #
# pan_breakpoints_from_stereo                                                 #
# --------------------------------------------------------------------------- #


class TestPanFromStereo:
    def test_centered_stereo_returns_zero_pan(self):
        sr = 22050
        n = sr * 4
        rng = np.random.default_rng(0)
        sig = rng.standard_normal((n, 2)).astype(np.float32) * 0.3
        # Equal L+R energy → pan = 0
        bps = pan_breakpoints_from_stereo(sig, sr, bpm=120, smoothing_windows=1, deadzone=0.0)
        assert bps  # non-empty
        # Most points near zero
        pans = [v for _, v in bps]
        mean_pan = float(np.mean(pans))
        assert abs(mean_pan) < 0.1

    def test_hard_left_returns_negative_pan(self):
        sr = 22050
        n = sr * 4
        rng = np.random.default_rng(0)
        left = rng.standard_normal(n).astype(np.float32) * 0.5
        right = np.zeros(n, dtype=np.float32)
        sig = np.stack([left, right], axis=1)
        bps = pan_breakpoints_from_stereo(sig, sr, bpm=120, smoothing_windows=1, deadzone=0.0)
        pans = [v for _, v in bps]
        assert all(p < -0.5 for p in pans)

    def test_mono_returns_neutral_only(self):
        sr = 22050
        sig = np.zeros(sr * 4, dtype=np.float32)
        bps = pan_breakpoints_from_stereo(sig, sr, bpm=120)
        assert bps == [(0.0, 0.0)]

    def test_invalid_sr_returns_empty(self):
        sig = np.zeros((1000, 2), dtype=np.float32)
        assert pan_breakpoints_from_stereo(sig, 0, 120) == []

    def test_invalid_bpm_returns_empty(self):
        sig = np.zeros((1000, 2), dtype=np.float32)
        assert pan_breakpoints_from_stereo(sig, 22050, 0) == []

    def test_panning_motion_sweeps_left_to_right(self):
        sr = 22050
        n = sr * 6
        rng = np.random.default_rng(0)
        # Left fades out as right fades in
        env_l = np.linspace(1, 0, n).astype(np.float32)
        env_r = np.linspace(0, 1, n).astype(np.float32)
        noise = rng.standard_normal(n).astype(np.float32) * 0.5
        sig = np.stack([noise * env_l, noise * env_r], axis=1)
        bps = pan_breakpoints_from_stereo(sig, sr, bpm=120, smoothing_windows=1, deadzone=0.0)
        # First and last points: left then right
        assert bps[0][1] < 0
        assert bps[-1][1] > 0


# --------------------------------------------------------------------------- #
# volume_ride_segments                                                        #
# --------------------------------------------------------------------------- #


class TestVolumeRides:
    def test_uniform_signal_few_segments(self):
        sr = 22050
        # 10 second steady noise → mostly one segment
        rng = np.random.default_rng(0)
        sig = rng.standard_normal(sr * 10).astype(np.float32) * 0.5
        segs = volume_ride_segments(sig, sr, bpm=120, window_seconds=2.0)
        # Should be very few segments
        assert len(segs) <= 2

    def test_loud_quiet_loud_three_segments(self):
        sr = 22050
        rng = np.random.default_rng(0)
        loud1 = rng.standard_normal(sr * 4).astype(np.float32) * 0.8
        quiet = rng.standard_normal(sr * 4).astype(np.float32) * 0.05
        loud2 = rng.standard_normal(sr * 4).astype(np.float32) * 0.8
        sig = np.concatenate([loud1, quiet, loud2])
        segs = volume_ride_segments(sig, sr, bpm=120, window_seconds=2.0,
                                     min_segment_beats=2.0)
        # Expect at least the quiet section to be its own segment
        assert len(segs) >= 2
        # Middle segment should have negative gain offset
        gains = [g for _, _, g in segs]
        assert min(gains) < -3.0

    def test_silence_returns_empty(self):
        sr = 22050
        sig = np.zeros(sr * 10, dtype=np.float32)
        # Silence has weird stats but should not crash
        segs = volume_ride_segments(sig, sr, bpm=120)
        assert isinstance(segs, list)


# --------------------------------------------------------------------------- #
# filter_cutoff_curve                                                         #
# --------------------------------------------------------------------------- #


class TestFilterCutoff:
    def test_low_freq_signal_low_centroid(self):
        sr = 22050
        n = sr * 4
        t = np.arange(n) / sr
        sig = (np.sin(2 * np.pi * 100 * t) * 0.5).astype(np.float32)
        curve = filter_cutoff_curve(sig, sr, bpm=120)
        cutoffs = [c for _, c in curve]
        # 100 Hz tone → centroid clusters near 100 Hz, but clamped to 200 floor
        assert cutoffs and max(cutoffs) < 1000

    def test_high_freq_signal_high_centroid(self):
        sr = 22050
        n = sr * 4
        t = np.arange(n) / sr
        sig = (np.sin(2 * np.pi * 5000 * t) * 0.5).astype(np.float32)
        curve = filter_cutoff_curve(sig, sr, bpm=120)
        cutoffs = [c for _, c in curve]
        assert cutoffs and min(cutoffs) > 3000

    def test_silence_returns_empty(self):
        sr = 22050
        sig = np.zeros(sr * 4, dtype=np.float32)
        curve = filter_cutoff_curve(sig, sr, bpm=120)
        assert curve == []


# --------------------------------------------------------------------------- #
# B3 wiring: pan automation lands on track                                    #
# --------------------------------------------------------------------------- #


class TestB3Wiring:
    def test_pan_automation_creates_lane(self):
        track = TrackSpec(
            name="Lead", role="lead",
            chain=PluginChainSpec(plugins=["4osc"]),
            notes=[NoteEvent(0, 1, pitch=60)],
            pan_automation=[(0.0, -0.5), (16.0, 0.0), (32.0, 0.5)],
        )
        spec = CompositionSpec(
            name="X", tempo_bpm=120,
            sections=[SectionSpec(type="verse", start_beat=0, length_beats=32)],
            tracks=[track],
        )
        out = _tmp_path()
        try:
            r = compose_from_spec({"spec": spec, "out_path": out})
            assert r["ok"]
            t = next(x for x in STORE.edit.tracks if x.name == "Lead")
            pan_lanes = [l for l in t.automation if l.target == "pan"]
            assert len(pan_lanes) == 1
            assert len(pan_lanes[0].points) == 3
        finally:
            if os.path.exists(out):
                os.remove(out)

    def test_volume_automation_warns_but_doesnt_crash(self):
        track = TrackSpec(
            name="Lead", role="lead",
            chain=PluginChainSpec(plugins=["4osc"]),
            volume_automation=[(0.0, -6), (16.0, 0)],
        )
        spec = CompositionSpec(
            name="X", tempo_bpm=120,
            sections=[SectionSpec(type="verse", start_beat=0, length_beats=16)],
            tracks=[track],
        )
        out = _tmp_path()
        try:
            r = compose_from_spec({"spec": spec, "out_path": out})
            assert r["ok"] is True
            assert any("volume_automation" in w and "ignored" in w
                       for w in r["warnings"])
        finally:
            if os.path.exists(out):
                os.remove(out)
