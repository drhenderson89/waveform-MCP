"""Production-tone analysis — reverb tail estimation, sidechain detection.

Two production characteristics that strongly affect a song's sonic
signature but aren't captured by the music-analyzer's harmonic/rhythmic
modules:

  * Reverb tail (RT60) — derived per stem from the decay of energy after
    a strong onset. Maps onto the plate reverb plugin's `decay`/`size`
    parameters in the builder.

  * Sidechain pumping — detected by cross-correlating drum onsets with
    the amplitude envelope of bass/pad/lead stems. If a stem's envelope
    consistently dips just after kick onsets, it's pumped. The builder
    can then add a kick-triggered ducking compressor to reproduce the
    feel.

This module is pure-numeric: it takes audio arrays + sample rates as
input, returns dicts of per-stem estimates. The integration with
demucs-separated stems happens in the orchestrator.

Made deliberately defensive: numpy is the only required dep, and we
fall back gracefully if scipy isn't available (some helpers are
stdlib-only). The functions never raise on bad input — they return
empty dicts / None.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Sequence

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Reverb (RT60) estimation                                                    #
# --------------------------------------------------------------------------- #


@dataclass
class ReverbEstimate:
    """Per-stem reverb characteristics.

    rt60_seconds: time for level to drop 60 dB after a strong onset
    wet_ratio: rough estimate of wet/dry mix in [0, 1]
    confidence: 0..1 indicator of how reliable the estimate is
    """
    rt60_seconds: float
    wet_ratio: float
    confidence: float

    def to_plate_reverb_params(self) -> dict[str, float]:
        """Map to Tracktion's plateReverb plugin parameter ranges.

        plateReverb knobs (from xml_writer):
          size:    0..1 — small to large
          decay:   0..1 — short to long
          mix:     0..1 — dry to wet
        """
        # RT60 maps to decay: 0.2s → 0.1, 1.0s → 0.4, 2.5s → 0.7, 5s+ → 0.9
        rt = max(0.1, min(8.0, self.rt60_seconds))
        decay = min(0.95, max(0.05, 0.1 + (math.log2(rt / 0.2) / math.log2(8.0 / 0.2)) * 0.85))
        # Size correlates with RT60 but with a different curve
        size = min(0.95, max(0.1, decay - 0.1))
        mix = max(0.0, min(0.5, self.wet_ratio))  # cap mix at 50% for taste
        return {"size": size, "decay": decay, "mix": mix}


def _safe_array(audio: Sequence[float] | "np.ndarray") -> "np.ndarray | None":
    try:
        import numpy as np
        a = np.asarray(audio, dtype=np.float32)
        if a.ndim == 0 or a.size < 2:
            return None
        if a.ndim > 1:
            a = a.mean(axis=tuple(range(a.ndim - 1)))  # collapse to mono
        return a
    except Exception as e:
        logger.debug("audio coerce failed: %s", e)
        return None


def estimate_rt60(
    audio: Sequence[float] | "np.ndarray",
    sample_rate: int,
    *,
    onset_threshold_db: float = -10.0,
    decay_target_db: float = -20.0,
    min_window_seconds: float = 0.05,
) -> ReverbEstimate | None:
    """Estimate RT60 from a mono audio array using Schroeder integration
    on the loudest onset's decay.

    Method (in plain English):
      1. Compute the energy envelope (RMS in 50ms windows)
      2. Find the loudest sustained-energy peak
      3. From the peak, integrate energy backwards to get the decay curve
      4. Fit a line in dB to the decay
      5. Extrapolate to -60 dB (RT60)

    Returns None if signal is too short, too quiet, or too noisy to
    estimate reliably.
    """
    a = _safe_array(audio)
    if a is None or sample_rate <= 0:
        return None

    try:
        import numpy as np
    except ImportError:
        logger.warning("numpy not available; cannot estimate reverb")
        return None

    # Step 1: 50 ms RMS envelope
    win = max(1, int(min_window_seconds * sample_rate))
    if a.shape[0] < win * 4:
        return None
    n_frames = a.shape[0] // win
    frames = a[: n_frames * win].reshape(n_frames, win)
    rms = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-10)
    rms_db = 20.0 * np.log10(rms + 1e-10)

    if rms_db.size < 10:
        return None

    # Step 2: Find loudest frame
    peak_idx = int(np.argmax(rms_db))
    peak_db = float(rms_db[peak_idx])
    if peak_db < -50:  # essentially silent
        return None

    # Step 3: Decay curve = the section after the peak until we're 30 dB down
    # or signal ends. Use Schroeder integration (energy reversed-cumsum,
    # then taken in dB) for a smooth decay measurement.
    decay = rms_db[peak_idx:]
    if decay.size < 5:
        return None
    decay_floor = peak_db + decay_target_db  # e.g., -20 dB from peak
    drop_idx_arr = np.where(decay <= decay_floor)[0]
    if drop_idx_arr.size == 0:
        # Never decayed that far — short or sustained signal; rough estimate
        # by using the slope of available decay
        if decay.size < 10:
            return None
        drop_idx = decay.size - 1
    else:
        drop_idx = int(drop_idx_arr[0])

    if drop_idx < 2:
        return None

    decay_used = decay[: drop_idx + 1]
    times_sec = np.arange(decay_used.size, dtype=np.float32) * (win / sample_rate)

    # Step 4: linear fit in dB
    try:
        slope, intercept = np.polyfit(times_sec, decay_used, deg=1)
    except Exception:
        return None
    if slope >= 0:  # not actually decaying
        return None

    # Step 5: extrapolate to -60 dB from peak
    rt60 = -60.0 / float(slope)
    rt60 = max(0.05, min(15.0, rt60))

    # Wet ratio: rough proxy from how slowly it decays vs. how loud it was.
    # Higher wet content → longer tail relative to peak duration.
    sustained_db = float(np.percentile(decay[: max(1, drop_idx)], 75) - peak_db)
    # sustained_db typically -3 (dry) .. -10+ (wet)
    wet_ratio = max(0.0, min(1.0, (-sustained_db - 3.0) / 12.0))

    # Confidence: based on residual fit and decay length
    fit_line = slope * times_sec + intercept
    residual = float(np.std(decay_used - fit_line))
    confidence = max(0.1, min(1.0, 1.0 - residual / 6.0))
    if drop_idx < 5:
        confidence *= 0.5

    return ReverbEstimate(
        rt60_seconds=rt60,
        wet_ratio=wet_ratio,
        confidence=confidence,
    )


# --------------------------------------------------------------------------- #
# Sidechain pumping detection                                                 #
# --------------------------------------------------------------------------- #


def detect_sidechain(
    target_audio: Sequence[float] | "np.ndarray",
    drum_audio: Sequence[float] | "np.ndarray",
    sample_rate: int,
    *,
    min_correlation: float = 0.45,
) -> bool:
    """Heuristic: is `target_audio` (a stem like bass/pad) sidechain-
    pumped by `drum_audio`?

    Method:
      1. Compute amplitude envelopes of both stems (50 ms RMS).
      2. Locate kick-like onsets in drum_audio (energy peaks below 250 Hz
         would be ideal, but here we just use overall onsets).
      3. Look at the target's envelope value at each onset and the
         envelope value 100 ms later.
      4. If, on average, the target dips after each onset (target_after
         < target_at_onset by > X%), it's pumped.

    Returns True if pumping is detected with confidence.
    """
    t = _safe_array(target_audio)
    d = _safe_array(drum_audio)
    if t is None or d is None or sample_rate <= 0:
        return False

    try:
        import numpy as np
    except ImportError:
        return False

    # 50 ms windows
    win = max(1, int(0.05 * sample_rate))
    n_frames = min(t.shape[0], d.shape[0]) // win
    if n_frames < 20:
        return False
    t_rms = np.sqrt(np.mean(t[: n_frames * win].reshape(n_frames, win) ** 2, axis=1))
    d_rms = np.sqrt(np.mean(d[: n_frames * win].reshape(n_frames, win) ** 2, axis=1))

    # Drum onsets: indices where d_rms > median + 1.5*std and is a local peak
    if d_rms.std() < 1e-8:
        return False
    threshold = float(d_rms.mean() + 1.0 * d_rms.std())
    onsets = []
    for i in range(1, n_frames - 1):
        if d_rms[i] > threshold and d_rms[i] >= d_rms[i - 1] and d_rms[i] >= d_rms[i + 1]:
            onsets.append(i)
    if len(onsets) < 4:
        return False

    # Compare target rms at onset vs. 100 ms later (~ 2 frames at 50 ms windows)
    look_ahead = 2
    pairs = [(t_rms[i], t_rms[i + look_ahead])
             for i in onsets if i + look_ahead < n_frames]
    if len(pairs) < 4:
        return False
    at_onset = np.array([p[0] for p in pairs])
    after = np.array([p[1] for p in pairs])
    # Average dip in dB
    eps = 1e-10
    dip_db = float(20.0 * np.log10((after.mean() + eps) / (at_onset.mean() + eps)))

    # Heuristic: pumped if dip > 1.5 dB consistently
    pumped = dip_db < -1.5
    # Strengthen by checking ratio of pairs that show a dip
    dip_ratio = float(np.mean(after < at_onset * 0.85))
    return pumped and dip_ratio > min_correlation


# --------------------------------------------------------------------------- #
# Stems-aware orchestration helpers                                           #
# --------------------------------------------------------------------------- #


@dataclass
class ProductionAnalysis:
    """All production-tone estimates for one song."""
    reverb_per_stem: dict[str, ReverbEstimate]
    sidechain_pumped_stems: list[str]  # stem names that are pumped by drums

    def to_dict(self) -> dict:
        return {
            "reverb": {
                name: {
                    "rt60_seconds": r.rt60_seconds,
                    "wet_ratio": r.wet_ratio,
                    "confidence": r.confidence,
                }
                for name, r in self.reverb_per_stem.items()
            },
            "sidechain_pumped_stems": list(self.sidechain_pumped_stems),
        }


def analyze_stems(
    stem_paths: dict[str, str],
    *,
    sample_rate: int = 22050,
) -> ProductionAnalysis:
    """Run reverb + sidechain analysis across a set of demucs stems.

    `stem_paths` maps stem names ('drums', 'bass', 'vocals', 'other')
    to file paths. Returns a ProductionAnalysis with per-stem reverb
    estimates and the list of stems detected as sidechain-pumped.

    Robustness: missing files / unreadable audio are skipped silently.
    """
    try:
        import librosa  # type: ignore[import-untyped]
    except ImportError:
        logger.warning("librosa not available; production_analysis returns empty")
        return ProductionAnalysis(reverb_per_stem={}, sidechain_pumped_stems=[])

    audio_by_name: dict[str, "tuple[any, int]"] = {}
    for name, path in stem_paths.items():
        try:
            y, sr = librosa.load(path, sr=sample_rate, mono=True)
            audio_by_name[name] = (y, sr)
        except Exception as e:
            logger.warning("could not load stem %s @ %s: %s", name, path, e)

    reverb: dict[str, ReverbEstimate] = {}
    for name, (y, sr) in audio_by_name.items():
        est = estimate_rt60(y, sr)
        if est is not None and est.confidence > 0.2:
            reverb[name] = est

    pumped: list[str] = []
    drums_audio = audio_by_name.get("drums")
    if drums_audio is not None:
        d, sr = drums_audio
        for name, (y, _) in audio_by_name.items():
            if name == "drums":
                continue
            try:
                if detect_sidechain(y, d, sr):
                    pumped.append(name)
            except Exception as e:
                logger.warning("sidechain detection failed on %s: %s", name, e)

    return ProductionAnalysis(reverb_per_stem=reverb, sidechain_pumped_stems=pumped)


# --------------------------------------------------------------------------- #
# Apply analysis to a CompositionSpec                                         #
# --------------------------------------------------------------------------- #


def apply_production_to_spec(
    production: ProductionAnalysis,
    spec_tracks: list,                 # list[TrackSpec]
    *,
    stem_to_role_map: dict[str, list[str]] | None = None,
) -> dict[str, dict]:
    """Map per-stem production estimates onto matching TrackSpecs.

    `stem_to_role_map` lets callers customize which stems' estimates
    apply to which roles. Default mapping is sensible for the standard
    demucs 4-stem split (drums/bass/vocals/other):

      drums   → drums
      bass    → bass, sub_bass
      vocals  → lead, melody                (vocal lead → synth lead)
      other   → keys, pad, lead, rhythm_*   (catch-all)

    Mutates spec_tracks in place. Returns a per-role report of what
    was applied.
    """
    if stem_to_role_map is None:
        stem_to_role_map = {
            "drums":  ["drums"],
            "bass":   ["bass", "sub_bass"],
            "vocals": ["lead", "melody"],
            "other":  ["keys", "pad", "rhythm_guitar",
                       "rhythm_guitar_l", "rhythm_guitar_r"],
        }

    applied: dict[str, dict] = {}
    for stem_name, est in production.reverb_per_stem.items():
        target_roles = stem_to_role_map.get(stem_name, [])
        for t in spec_tracks:
            if getattr(t, "role", None) in target_roles:
                t.reverb_rt60_s = est.rt60_seconds
                t.reverb_wet = est.wet_ratio
                applied[t.role] = {
                    "rt60_seconds": est.rt60_seconds,
                    "wet_ratio": est.wet_ratio,
                    "from_stem": stem_name,
                }

    for stem_name in production.sidechain_pumped_stems:
        target_roles = stem_to_role_map.get(stem_name, [])
        for t in spec_tracks:
            if getattr(t, "role", None) in target_roles:
                t.sidechain_to_drums = True
                applied.setdefault(t.role, {})["sidechain_to_drums"] = True

    return applied
