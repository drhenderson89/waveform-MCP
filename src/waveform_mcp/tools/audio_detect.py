"""Audio file analysis — detect tempo and key from a WAV/MP3 file.

Tempo detection: ffmpeg's loudness onset times → autocorrelation across
common BPM range. Crude but works for ~70% of pop/electronic loops with
clear downbeats.

Key detection: chromagram via STFT → match against major/minor profile
templates (Krumhansl-Schmuckler). Decent on tonal music, less reliable on
pure beats.

Tools:
  - audio_detect_tempo : returns BPM with confidence
  - audio_detect_key   : returns root + mode with confidence
"""

from __future__ import annotations

import math
import re
import subprocess
import wave
from pathlib import Path
from typing import Any


TOOLS: list[dict[str, Any]] = [
    {
        "name": "audio_detect_tempo",
        "description": (
            "Detect the BPM of an audio file via onset autocorrelation. "
            "bpm_range bounds the search (default 60..180). Returns a single "
            "BPM with confidence 0..1. Works well on EDM/pop loops with a "
            "kick on every beat; less reliable on free-tempo or sparse music."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["file_path"],
            "properties": {
                "file_path": {"type": "string"},
                "bpm_min": {"type": "number", "default": 60},
                "bpm_max": {"type": "number", "default": 180},
            },
        },
    },
    {
        "name": "audio_detect_key",
        "description": (
            "Detect the musical key of an audio file. Returns root note + "
            "mode (major/minor) with confidence 0..1. Uses chromagram + "
            "Krumhansl-Schmuckler key profiles. Reliable on tonal pop/jazz; "
            "less reliable on percussion-heavy or atonal material."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["file_path"],
            "properties": {"file_path": {"type": "string"}},
        },
    },
]


def _ff_path(name: str = "ffmpeg") -> str:
    candidates = [rf"C:\Program Files\Tracktion\Waveform 13\{name}.exe", name]
    for c in candidates:
        try:
            subprocess.run([c, "-version"], capture_output=True, check=True, timeout=5)
            return c
        except Exception:
            continue
    return name


def _decode_to_mono_pcm(path: str, sample_rate: int = 22050) -> tuple[list[float], int]:
    """ffmpeg-decode any audio file to mono float samples at sample_rate."""
    ff = _ff_path("ffmpeg")
    out_wav = Path(path).with_suffix(".__decoded.wav")
    subprocess.run(
        [ff, "-y", "-i", path, "-ac", "1", "-ar", str(sample_rate),
         "-c:a", "pcm_s16le", str(out_wav)],
        capture_output=True, check=True,
    )
    with wave.open(str(out_wav), "rb") as w:
        n = w.getnframes()
        sw = w.getsampwidth()
        raw = w.readframes(n)
    out_wav.unlink(missing_ok=True)
    # Convert int16 → float [-1, 1]
    import struct
    samples = struct.unpack(f"{n}h", raw[:n*sw])
    return ([s / 32768.0 for s in samples], sample_rate)


def audio_detect_tempo(args: dict) -> dict:
    path = args["file_path"]
    if not Path(path).exists():
        return {"ok": False, "error": f"file not found: {path}"}
    bpm_min = float(args.get("bpm_min", 60))
    bpm_max = float(args.get("bpm_max", 180))

    try:
        samples, sr = _decode_to_mono_pcm(path, sample_rate=22050)
    except Exception as e:
        return {"ok": False, "error": f"decode failed: {e}"}

    # Simple onset envelope: |sample| smoothed at 100 Hz frame rate
    frame_size = int(sr / 100)   # 10 ms frames
    n_frames = len(samples) // frame_size
    env = []
    for f in range(n_frames):
        chunk = samples[f * frame_size:(f + 1) * frame_size]
        env.append(sum(abs(s) for s in chunk) / len(chunk) if chunk else 0.0)

    # Onset by half-wave-rectified diff
    onsets = [max(0.0, env[i] - env[i - 1]) for i in range(1, len(env))]

    # Autocorrelation across lags 1..N
    # frame rate = 100/sec → BPM B has period 60/B seconds = 6000/B frames
    if not onsets:
        return {"ok": False, "error": "no audio energy"}
    best_bpm = 0.0
    best_score = -1e9
    for bpm in range(int(bpm_min), int(bpm_max) + 1):
        lag = int(round(6000.0 / bpm))
        if lag <= 0 or lag >= len(onsets):
            continue
        # Sum onset[i] * onset[i+lag]
        n = len(onsets) - lag
        s = sum(onsets[i] * onsets[i + lag] for i in range(0, n, 4))  # decimate for speed
        if s > best_score:
            best_score = s
            best_bpm = float(bpm)

    # Confidence: relative to score range
    return {"ok": True, "file": path, "bpm": best_bpm,
            "confidence": min(1.0, best_score / max(1e-9, sum(onsets) ** 2 / len(onsets) / 4))}


# Krumhansl-Schmuckler key profiles (correlation against pitch-class histogram)
_KS_MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
_KS_MINOR = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def audio_detect_key(args: dict) -> dict:
    path = args["file_path"]
    if not Path(path).exists():
        return {"ok": False, "error": f"file not found: {path}"}

    try:
        samples, sr = _decode_to_mono_pcm(path, sample_rate=22050)
    except Exception as e:
        return {"ok": False, "error": f"decode failed: {e}"}

    # Naive chromagram: FFT in overlapping windows, fold magnitudes into 12 PCs
    fft_size = 4096
    hop = 2048
    pc_hist = [0.0] * 12
    # Manual DFT with cmath would be slow; use the simplest possible approach:
    # bin -> freq -> pitch class. We'll do this with `numpy` if available,
    # else fall back to a slower pure-Python loop.
    try:
        import numpy as np  # type: ignore
    except ImportError:
        return {"ok": False, "error": "numpy required for key detection"}

    arr = np.array(samples, dtype=np.float32)
    # Hann window
    window = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(fft_size) / fft_size)

    for start in range(0, len(arr) - fft_size, hop):
        frame = arr[start:start + fft_size] * window
        spec = np.abs(np.fft.rfft(frame))
        # bin idx → freq → pitch class
        for k in range(1, len(spec)):
            f = k * sr / fft_size
            if f < 60 or f > 5000:
                continue
            # MIDI pitch from freq: 69 + 12*log2(f/440)
            midi = 69.0 + 12.0 * math.log2(f / 440.0)
            pc = int(round(midi)) % 12
            pc_hist[pc] += float(spec[k])

    # Normalize
    total = sum(pc_hist) or 1.0
    pc_hist = [v / total for v in pc_hist]

    # Correlate with all 24 keys
    def corr(profile, hist):
        mean_p = sum(profile) / 12
        mean_h = sum(hist) / 12
        num = sum((profile[i] - mean_p) * (hist[i] - mean_h) for i in range(12))
        denom = math.sqrt(sum((p - mean_p) ** 2 for p in profile) * sum((h - mean_h) ** 2 for h in hist))
        return num / denom if denom > 0 else 0.0

    best = (-1e9, "C", "major")
    for tonic in range(12):
        rotated = pc_hist[tonic:] + pc_hist[:tonic]
        cm = corr(_KS_MAJOR, rotated)
        cn = corr(_KS_MINOR, rotated)
        if cm > best[0]:
            best = (cm, _NOTE_NAMES[tonic], "major")
        if cn > best[0]:
            best = (cn, _NOTE_NAMES[tonic], "minor")

    return {"ok": True, "file": path,
            "root": best[1], "mode": best[2], "confidence": float(best[0])}


HANDLERS = {
    "audio_detect_tempo": audio_detect_tempo,
    "audio_detect_key":   audio_detect_key,
}
