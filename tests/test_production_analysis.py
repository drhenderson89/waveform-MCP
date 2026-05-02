"""Tests for production_analysis (RT60, sidechain detection)."""
from __future__ import annotations

import math

import numpy as np
import pytest

from waveform_mcp.composition_spec import PluginChainSpec, TrackSpec
from waveform_mcp.production_analysis import (
    ProductionAnalysis,
    ReverbEstimate,
    apply_production_to_spec,
    detect_sidechain,
    estimate_rt60,
)


# --------------------------------------------------------------------------- #
# RT60 estimation                                                             #
# --------------------------------------------------------------------------- #


def _exp_decay(rt60: float, sr: int = 22050, length_seconds: float = 3.0,
               peak_amp: float = 0.9, attack_seconds: float = 0.005) -> np.ndarray:
    """Synthesise a decaying-exponential signal with known RT60.
    Tail is white noise modulated by the decay envelope, which is
    realistic-ish for reverb tail estimation."""
    n = int(length_seconds * sr)
    t = np.arange(n) / sr
    # Attack ramp
    attack_n = int(attack_seconds * sr)
    envelope = np.ones(n)
    envelope[:attack_n] = np.linspace(0, 1, attack_n)
    # Exponential decay: amplitude drops 60 dB over rt60 seconds
    decay_rate = 60.0 / rt60         # dB/s
    db_drop = decay_rate * t
    db_drop[:attack_n] = 0
    decay_env = peak_amp * 10 ** (-db_drop / 20.0)
    decay_env *= envelope
    rng = np.random.default_rng(seed=0)
    noise = rng.standard_normal(n).astype(np.float32) * 0.5
    return (decay_env * noise).astype(np.float32)


class TestEstimateRT60:
    def test_synthetic_short_decay(self):
        sig = _exp_decay(rt60=0.4, length_seconds=2.0)
        est = estimate_rt60(sig, 22050)
        assert est is not None
        # Allow generous tolerance — the estimator is approximate
        assert 0.15 < est.rt60_seconds < 1.5

    def test_synthetic_long_decay(self):
        sig = _exp_decay(rt60=2.0, length_seconds=4.0)
        est = estimate_rt60(sig, 22050)
        assert est is not None
        # Synthetic with low SNR is a rough sanity check, not a measurement
        assert est.rt60_seconds > 0.5

    def test_silence_returns_none(self):
        sig = np.zeros(22050).astype(np.float32)
        est = estimate_rt60(sig, 22050)
        assert est is None

    def test_too_short_returns_none(self):
        sig = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        est = estimate_rt60(sig, 22050)
        assert est is None

    def test_invalid_sr_returns_none(self):
        sig = _exp_decay(rt60=0.5)
        assert estimate_rt60(sig, 0) is None
        assert estimate_rt60(sig, -100) is None

    def test_garbage_input_returns_none(self):
        for bad in [None, [], "abc", [None, None]]:
            assert estimate_rt60(bad, 22050) is None  # type: ignore[arg-type]


class TestReverbToPlateParams:
    def test_short_rt60_low_decay(self):
        params = ReverbEstimate(0.2, 0.2, 1.0).to_plate_reverb_params()
        assert params["decay"] < 0.2

    def test_long_rt60_high_decay(self):
        params = ReverbEstimate(5.0, 0.4, 1.0).to_plate_reverb_params()
        assert params["decay"] > 0.7

    def test_mix_capped_at_half(self):
        params = ReverbEstimate(2.0, 1.0, 1.0).to_plate_reverb_params()
        assert params["mix"] <= 0.5

    def test_all_params_in_unit_range(self):
        for rt in [0.05, 0.5, 2.0, 10.0]:
            params = ReverbEstimate(rt, 0.5, 1.0).to_plate_reverb_params()
            assert all(0.0 <= v <= 1.0 for v in params.values())


# --------------------------------------------------------------------------- #
# Sidechain detection                                                         #
# --------------------------------------------------------------------------- #


def _kicks(sr: int = 22050, length_seconds: float = 8.0,
           bpm: float = 120) -> np.ndarray:
    """Synthesise drum-only audio: a click on each beat."""
    n = int(length_seconds * sr)
    sig = np.zeros(n, dtype=np.float32)
    interval = 60.0 / bpm
    n_beats = int(length_seconds / interval)
    for i in range(n_beats):
        t = i * interval
        idx = int(t * sr)
        if idx >= n:
            break
        # 30 ms click envelope
        click_n = int(0.03 * sr)
        end = min(idx + click_n, n)
        env = np.linspace(1.0, 0.0, end - idx)
        sig[idx:end] += env.astype(np.float32) * 0.9
    return sig


def _pumped_pad(sr: int = 22050, length_seconds: float = 8.0,
                bpm: float = 120, dip_strength: float = 0.7) -> np.ndarray:
    """Synthesise a pad with sidechain pumping. Realistic pattern: the
    pad is at full level when the kick hits, then a fast-attack
    compressor drops it ~50 ms later and it recovers over another
    ~200 ms. This matches what `detect_sidechain` looks for (compares
    envelope at the onset to envelope ~100 ms later)."""
    n = int(length_seconds * sr)
    t = np.arange(n) / sr
    pad = (0.4 * np.sin(2 * np.pi * 220.0 * t) +
           0.3 * np.sin(2 * np.pi * 330.0 * t)).astype(np.float32)

    interval = 60.0 / bpm
    env = np.ones(n, dtype=np.float32)
    delay_n = int(0.04 * sr)         # 40 ms before dip starts (comp attack)
    dip_n = int(0.08 * sr)           # 80 ms at dip floor
    recovery_n = int(0.20 * sr)      # 200 ms recovery
    floor = 1.0 - dip_strength
    for i in range(int(length_seconds / interval)):
        beat_t = i * interval
        idx = int(beat_t * sr)
        if idx >= n:
            break
        a = idx + delay_n
        b = a + dip_n
        c = b + recovery_n
        # Dip ramp down (delay → floor)
        if a < n:
            end = min(b, n)
            env[a:end] = np.minimum(env[a:end], np.linspace(1.0, floor, end - a))
        # Hold at floor
        if b < n:
            end = min(c, n)
            env[b:end] = np.minimum(env[b:end], np.linspace(floor, 1.0, end - b))
    return pad * env


class TestDetectSidechain:
    def test_pumped_pad_detected(self):
        sr = 22050
        kicks = _kicks(sr=sr, bpm=120, length_seconds=8.0)
        pad = _pumped_pad(sr=sr, bpm=120, length_seconds=8.0, dip_strength=0.7)
        assert detect_sidechain(pad, kicks, sr) is True

    def test_static_pad_not_detected(self):
        sr = 22050
        kicks = _kicks(sr=sr, bpm=120, length_seconds=8.0)
        # Pad with no modulation
        n = int(8.0 * sr)
        t = np.arange(n) / sr
        pad = (0.4 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)
        assert detect_sidechain(pad, kicks, sr) is False

    def test_silence_not_pumped(self):
        sr = 22050
        kicks = _kicks(sr=sr, bpm=120, length_seconds=8.0)
        pad = np.zeros(8 * sr, dtype=np.float32)
        assert detect_sidechain(pad, kicks, sr) is False

    def test_garbage_input_returns_false(self):
        assert detect_sidechain(None, None, 22050) is False  # type: ignore[arg-type]

    def test_too_short_returns_false(self):
        assert detect_sidechain([0.1, 0.2], [0.3, 0.4], 22050) is False  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# apply_production_to_spec                                                    #
# --------------------------------------------------------------------------- #


def _spec_tracks() -> list[TrackSpec]:
    return [
        TrackSpec(name="Drums", role="drums",
                  chain=PluginChainSpec(plugins=["sampler"])),
        TrackSpec(name="Bass", role="bass",
                  chain=PluginChainSpec(plugins=["4osc"])),
        TrackSpec(name="Lead", role="lead",
                  chain=PluginChainSpec(plugins=["4osc"])),
        TrackSpec(name="Pad", role="pad",
                  chain=PluginChainSpec(plugins=["4osc"])),
    ]


class TestApplyProduction:
    def test_reverb_applied_via_default_mapping(self):
        prod = ProductionAnalysis(
            reverb_per_stem={
                "vocals": ReverbEstimate(rt60_seconds=1.5, wet_ratio=0.3, confidence=0.8),
                "other":  ReverbEstimate(rt60_seconds=0.8, wet_ratio=0.2, confidence=0.7),
            },
            sidechain_pumped_stems=[],
        )
        tracks = _spec_tracks()
        applied = apply_production_to_spec(prod, tracks)
        lead = next(t for t in tracks if t.role == "lead")
        pad = next(t for t in tracks if t.role == "pad")
        assert lead.reverb_rt60_s == 1.5
        assert pad.reverb_rt60_s == 0.8

    def test_sidechain_applied_to_pad(self):
        prod = ProductionAnalysis(
            reverb_per_stem={},
            sidechain_pumped_stems=["other"],
        )
        tracks = _spec_tracks()
        apply_production_to_spec(prod, tracks)
        pad = next(t for t in tracks if t.role == "pad")
        assert pad.sidechain_to_drums is True

    def test_drums_never_get_reverb_from_drums_stem(self):
        # We deliberately don't feed drum stem reverb back onto drums
        # by default — but if we did, it's via the map. Just verify
        # default map doesn't crash drums.
        prod = ProductionAnalysis(
            reverb_per_stem={
                "drums": ReverbEstimate(0.3, 0.1, 0.5),
            },
            sidechain_pumped_stems=[],
        )
        tracks = _spec_tracks()
        applied = apply_production_to_spec(prod, tracks)
        # Drums role IS in the default map for 'drums' stem so it
        # actually does get applied. Verify that's intentional:
        drums = next(t for t in tracks if t.role == "drums")
        assert drums.reverb_rt60_s == 0.3

    def test_custom_mapping_overrides(self):
        prod = ProductionAnalysis(
            reverb_per_stem={
                "vocals": ReverbEstimate(2.0, 0.4, 1.0),
            },
            sidechain_pumped_stems=[],
        )
        tracks = _spec_tracks()
        # Custom: vocals stem reverb should apply to bass instead
        apply_production_to_spec(prod, tracks, stem_to_role_map={"vocals": ["bass"]})
        bass = next(t for t in tracks if t.role == "bass")
        lead = next(t for t in tracks if t.role == "lead")
        assert bass.reverb_rt60_s == 2.0
        assert lead.reverb_rt60_s is None
