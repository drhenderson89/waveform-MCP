"""Feature-driven automation: extract per-track time-series from audio
features and convert to automation breakpoint lists for the spec/builder.

What this module produces:

  pan_breakpoints       — (beat, pan_value) timeline from stereo balance
  volume_ride_segments  — list of (start_beat, end_beat, gain_db) bands
                          used to lay multiple clips with different gains.
                          Volume *automation* is broken in our XML pipeline
                          (the track goes silent — see automation.py),
                          so we ride volume by splitting clips.
  filter_cutoff_curve   — (beat, freq_hz) timeline from spectral centroid
                          trajectory; useful for filter-sweep automation
                          on plugins that expose cutoff.

Conventions:
  * `bpm` is required for time→beat conversion. The caller passes the
    detected tempo from the analyzer.
  * All values are clamped to safe ranges. Out-of-range or silent
    inputs produce empty lists rather than raising.
  * The functions take audio arrays (numpy 1-D or 2-D), not file paths.
    The caller is responsible for loading.
"""
from __future__ import annotations

import logging
import math
from typing import Sequence

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Pan from stereo balance                                                     #
# --------------------------------------------------------------------------- #


def pan_breakpoints_from_stereo(
    audio: "np.ndarray",
    sample_rate: int,
    bpm: float,
    *,
    window_seconds: float = 0.5,
    smoothing_windows: int = 3,
    deadzone: float = 0.05,
) -> list[tuple[float, float]]:
    """Compute per-window pan from a stereo audio buffer.

    Args:
      audio: shape (n_samples, 2) for stereo, or (n_samples,) for mono
             (mono returns single neutral pan point at 0).
      sample_rate, bpm: positive numbers.
      window_seconds: analysis window length.
      smoothing_windows: moving-average window count for de-noising.
      deadzone: pan magnitudes within ±deadzone are snapped to 0.

    Returns:
      Sorted list of (beat, pan_value) where pan_value ∈ [-1, 1].
      Returns [] for invalid inputs.
    """
    try:
        import numpy as np
    except ImportError:
        logger.warning("numpy required for pan extraction")
        return []

    if sample_rate <= 0 or bpm <= 0:
        return []
    a = np.asarray(audio, dtype=np.float32)
    if a.ndim == 1:
        return [(0.0, 0.0)]
    if a.ndim != 2 or a.shape[1] < 2:
        return [(0.0, 0.0)]

    left = a[:, 0]
    right = a[:, 1]
    win = max(1, int(window_seconds * sample_rate))
    n_frames = a.shape[0] // win
    if n_frames < 2:
        return [(0.0, 0.0)]

    l_rms = np.sqrt(np.mean(left[: n_frames * win].reshape(n_frames, win) ** 2, axis=1) + 1e-12)
    r_rms = np.sqrt(np.mean(right[: n_frames * win].reshape(n_frames, win) ** 2, axis=1) + 1e-12)
    # Pan = (R - L) / (R + L) ∈ [-1, +1]
    total = l_rms + r_rms
    pan = (r_rms - l_rms) / total
    pan = np.clip(pan, -1.0, 1.0)

    # Smooth
    if smoothing_windows > 1:
        kernel = np.ones(smoothing_windows) / smoothing_windows
        pan = np.convolve(pan, kernel, mode="same")

    # Deadzone
    pan = np.where(np.abs(pan) < deadzone, 0.0, pan)

    # Subsample: keep only points where pan changed significantly
    breakpoints: list[tuple[float, float]] = []
    last_kept = -10.0
    for i, v in enumerate(pan):
        beat = (i * win / sample_rate) * (bpm / 60.0)
        if not breakpoints or abs(v - last_kept) > 0.05 or i == len(pan) - 1:
            breakpoints.append((round(beat, 3), round(float(v), 3)))
            last_kept = float(v)
    return breakpoints


# --------------------------------------------------------------------------- #
# Volume rides — multi-clip approach (since automation_add('volume') is       #
# disabled in our pipeline)                                                   #
# --------------------------------------------------------------------------- #


def volume_ride_segments(
    audio: "np.ndarray",
    sample_rate: int,
    bpm: float,
    *,
    target_lufs: float = -14.0,
    window_seconds: float = 2.0,
    min_segment_beats: float = 4.0,
) -> list[tuple[float, float, float]]:
    """Detect "volume rides" — sections where the loudness changes
    significantly — and return them as (start_beat, end_beat, gain_db_offset)
    segments.

    Built so the builder can split a long clip into shorter clips and
    set each clip's `gain_db` to match. This is the workaround for
    Tracktion's volume automation being unreliable in our pipeline.
    """
    try:
        import numpy as np
    except ImportError:
        return []

    if sample_rate <= 0 or bpm <= 0:
        return []
    a = np.asarray(audio, dtype=np.float32)
    if a.ndim > 1:
        a = a.mean(axis=tuple(range(a.ndim - 1)))
    if a.size < sample_rate * window_seconds * 2:
        return []

    win = int(window_seconds * sample_rate)
    n_frames = a.shape[0] // win
    if n_frames < 2:
        return []

    rms = np.sqrt(np.mean(a[: n_frames * win].reshape(n_frames, win) ** 2, axis=1) + 1e-12)
    rms_db = 20.0 * np.log10(rms + 1e-12)

    # Reference: median RMS over the whole track, mapped to "should be
    # at target_lufs". Each window's offset = target - (its dB - median).
    median_db = float(np.median(rms_db))
    # Avoid swinging huge gains for quiet/silent segments (intro/outro)
    offsets = np.clip(rms_db - median_db, -8.0, 8.0)

    # Bin into segments where offset is roughly constant (±1.5 dB)
    segments: list[tuple[float, float, float]] = []
    seg_start_frame = 0
    seg_value = float(offsets[0])
    for i in range(1, n_frames):
        if abs(float(offsets[i]) - seg_value) > 1.5:
            # Close the current segment
            seg_start_beat = (seg_start_frame * win / sample_rate) * (bpm / 60.0)
            seg_end_beat = (i * win / sample_rate) * (bpm / 60.0)
            if seg_end_beat - seg_start_beat >= min_segment_beats:
                segments.append((round(seg_start_beat, 3),
                                  round(seg_end_beat, 3),
                                  round(seg_value, 2)))
            seg_start_frame = i
            seg_value = float(offsets[i])
    # Final segment
    seg_start_beat = (seg_start_frame * win / sample_rate) * (bpm / 60.0)
    seg_end_beat = (n_frames * win / sample_rate) * (bpm / 60.0)
    if seg_end_beat - seg_start_beat >= min_segment_beats:
        segments.append((round(seg_start_beat, 3), round(seg_end_beat, 3),
                          round(seg_value, 2)))
    return segments


# --------------------------------------------------------------------------- #
# Filter sweeps from spectral centroid                                        #
# --------------------------------------------------------------------------- #


def filter_cutoff_curve(
    audio: "np.ndarray",
    sample_rate: int,
    bpm: float,
    *,
    window_seconds: float = 0.5,
    cutoff_min_hz: float = 200.0,
    cutoff_max_hz: float = 12000.0,
) -> list[tuple[float, float]]:
    """Map per-window spectral centroid to a filter-cutoff time series.
    Useful when the reference song has a long filter sweep we want to
    reproduce on a synth track.

    Returns (beat, cutoff_hz) breakpoints. cutoff_hz is clamped to
    [cutoff_min_hz, cutoff_max_hz].
    """
    try:
        import numpy as np
    except ImportError:
        return []

    if sample_rate <= 0 or bpm <= 0:
        return []
    a = np.asarray(audio, dtype=np.float32)
    if a.ndim > 1:
        a = a.mean(axis=tuple(range(a.ndim - 1)))
    if a.size < sample_rate * window_seconds * 2:
        return []

    win = int(window_seconds * sample_rate)
    n_frames = a.shape[0] // win
    if n_frames < 2:
        return []

    breakpoints: list[tuple[float, float]] = []
    for i in range(n_frames):
        start = i * win
        end = start + win
        chunk = a[start:end]
        if chunk.size == 0 or float(np.sum(chunk ** 2)) < 1e-8:
            continue
        # FFT and centroid
        fft = np.fft.rfft(chunk * np.hanning(chunk.size))
        mag = np.abs(fft) + 1e-12
        freqs = np.fft.rfftfreq(chunk.size, d=1.0 / sample_rate)
        centroid = float(np.sum(freqs * mag) / np.sum(mag))
        cutoff = max(cutoff_min_hz, min(cutoff_max_hz, centroid))
        beat = (start / sample_rate) * (bpm / 60.0)
        breakpoints.append((round(beat, 3), round(cutoff, 1)))
    return breakpoints
