"""Audio measurement & comparison — LUFS, true peak, spectral balance.

Uses Waveform's bundled ffmpeg (loudnorm filter for LUFS, astats for peak,
showspectrumpic + Python parsing for spectrum bands).

Tools:
  - audio_loudness_lufs : integrated LUFS, true peak, dynamic range
  - audio_spectrum      : N-band frequency-balance histogram (RMS per band)
  - audio_compare       : diff two files' loudness + spectrum
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any


TOOLS: list[dict[str, Any]] = [
    {
        "name": "audio_loudness_lufs",
        "description": (
            "Measure integrated LUFS, true peak (dBTP), and dynamic range "
            "(LRA) for an audio file using ffmpeg's loudnorm filter. "
            "Reference targets: Spotify -14 LUFS, Apple -16, broadcast -23. "
            "True peak should stay under -1.0 dBTP."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["file_path"],
            "properties": {"file_path": {"type": "string"}},
        },
    },
    {
        "name": "audio_spectrum",
        "description": (
            "Return RMS energy per frequency band. bands defaults to a "
            "10-band log-spaced layout (sub, low, low-mid, mid, hi-mid, "
            "presence, brilliance, air). Useful for 'compared to my "
            "reference, this song is 4 dB heavier in the low-mid region.'"
        ),
        "inputSchema": {
            "type": "object",
            "required": ["file_path"],
            "properties": {
                "file_path": {"type": "string"},
                "bands": {"type": "integer", "default": 10},
            },
        },
    },
    {
        "name": "audio_compare",
        "description": (
            "Compare two audio files: returns LUFS / peak / DR diffs and a "
            "per-band spectrum diff in dB. Positive numbers mean file_a is "
            "louder/brighter than file_b in that dimension."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["file_a", "file_b"],
            "properties": {
                "file_a": {"type": "string"},
                "file_b": {"type": "string"},
                "bands": {"type": "integer", "default": 10},
            },
        },
    },
]


def _ffmpeg_path() -> str:
    from ..safety import find_bundled_tool

    return find_bundled_tool("ffmpeg") or "ffmpeg"


def _ffprobe_path() -> str:
    from ..safety import find_bundled_tool

    return find_bundled_tool("ffprobe") or "ffprobe"


def audio_loudness_lufs(args: dict) -> dict:
    path = args["file_path"]
    if not Path(path).exists():
        return {"ok": False, "error": f"file not found: {path}"}
    ff = _ffmpeg_path()
    # ffmpeg loudnorm filter prints a JSON summary on a 2nd pass; here we use
    # ebur128 filter which gives integrated LUFS + LRA + true peak in one pass.
    res = subprocess.run(
        [ff, "-nostats", "-i", path, "-filter_complex", "ebur128=peak=true",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    out = res.stderr  # ffmpeg writes filter output to stderr
    # ebur128 emits TWO Summary blocks: an initial one at frame-0 (-70 LUFS,
    # nothing processed yet) and the real one at end-of-stream. Take the LAST
    # match of each pattern.
    def _last(pattern: str) -> float | None:
        ms = list(re.finditer(pattern, out))
        return float(ms[-1].group(1)) if ms else None

    integrated = _last(r"Integrated loudness:\s*\n\s*I:\s*(-?\d+(?:\.\d+)?)\s*LUFS")
    lra        = _last(r"Loudness range:\s*\n\s*LRA:\s*(-?\d+(?:\.\d+)?)\s*LU")
    peak       = _last(r"True peak:\s*\n\s*Peak:\s*(-?\d+(?:\.\d+)?)\s*dBFS")

    return {
        "ok": True,
        "file": path,
        "integrated_lufs": integrated,
        "lra": lra,
        "true_peak_dbfs": peak,
    }


def audio_spectrum(args: dict) -> dict:
    """Log-spaced RMS-per-band frequency balance.

    Implementation: single ffmpeg call to decode to mono PCM at 22050 Hz, then
    one numpy rfft per windowed frame, accumulate magnitude into log-spaced
    bands. ~50× faster than the previous one-ffmpeg-per-band approach.
    """
    import math
    import wave
    path = args["file_path"]
    if not Path(path).exists():
        return {"ok": False, "error": f"file not found: {path}"}
    n_bands = int(args.get("bands", 10))

    try:
        import numpy as np  # type: ignore
    except ImportError:
        return {"ok": False, "error": "numpy required for audio_spectrum"}

    # Decode to mono 16-bit PCM @ 22050 Hz
    ff = _ffmpeg_path()
    out_wav = Path(path).with_suffix(".__spec.wav")
    try:
        subprocess.run(
            [ff, "-y", "-i", path, "-ac", "1", "-ar", "22050",
             "-c:a", "pcm_s16le", str(out_wav)],
            capture_output=True, check=True,
        )
        with wave.open(str(out_wav), "rb") as w:
            n = w.getnframes()
            sw = w.getsampwidth()
            sr = w.getframerate()
            raw = w.readframes(n)
    finally:
        out_wav.unlink(missing_ok=True)
    samples = np.frombuffer(raw[:n*sw], dtype=np.int16).astype(np.float32) / 32768.0

    # Log-spaced band centers + edges
    f_min, f_max = 60.0, 16000.0
    centers = [f_min * (f_max / f_min) ** (i / (n_bands - 1)) for i in range(n_bands)]
    edges = [centers[0] * 0.7] + [(centers[i] * centers[i+1]) ** 0.5 for i in range(n_bands - 1)] + [centers[-1] * 1.4]

    fft_size = 4096
    hop = 2048
    window = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(fft_size) / fft_size)

    # Accumulator per band
    band_energy = [0.0] * n_bands
    band_count = 0
    for start in range(0, len(samples) - fft_size, hop):
        frame = samples[start:start + fft_size] * window
        spec = np.abs(np.fft.rfft(frame))   # shape (fft_size/2+1,)
        # Bin frequencies
        for b in range(n_bands):
            lo, hi = edges[b], edges[b + 1]
            lo_bin = int(lo * fft_size / sr)
            hi_bin = int(hi * fft_size / sr)
            if hi_bin > lo_bin:
                band_energy[b] += float(np.sum(spec[lo_bin:hi_bin] ** 2))
        band_count += 1

    rms_db: list[float] = []
    for e in band_energy:
        rms = (e / max(1, band_count)) ** 0.5
        if rms <= 0:
            rms_db.append(float("-inf"))
        else:
            rms_db.append(20.0 * math.log10(rms / 32768.0))

    return {
        "ok": True,
        "file": path,
        "centers_hz": [round(c, 1) for c in centers],
        "rms_db": rms_db,
    }


def audio_compare(args: dict) -> dict:
    a = audio_loudness_lufs({"file_path": args["file_a"]})
    b = audio_loudness_lufs({"file_path": args["file_b"]})
    if not a.get("ok") or not b.get("ok"):
        return {"ok": False, "error": "loudness measurement failed",
                "a_error": a.get("error"), "b_error": b.get("error")}
    sa = audio_spectrum({"file_path": args["file_a"], "bands": args.get("bands", 10)})
    sb = audio_spectrum({"file_path": args["file_b"], "bands": args.get("bands", 10)})

    diff_band = []
    if sa.get("ok") and sb.get("ok"):
        for ar, br, c in zip(sa["rms_db"], sb["rms_db"], sa["centers_hz"]):
            d = ar - br if not (ar == float("-inf") or br == float("-inf")) else None
            diff_band.append({"hz": c, "diff_db": d})

    return {
        "ok": True,
        "file_a": args["file_a"], "file_b": args["file_b"],
        "lufs_diff": (a["integrated_lufs"] or 0) - (b["integrated_lufs"] or 0)
                     if a["integrated_lufs"] is not None and b["integrated_lufs"] is not None else None,
        "true_peak_diff": (a["true_peak_dbfs"] or 0) - (b["true_peak_dbfs"] or 0)
                          if a["true_peak_dbfs"] is not None and b["true_peak_dbfs"] is not None else None,
        "lra_diff": (a["lra"] or 0) - (b["lra"] or 0)
                    if a["lra"] is not None and b["lra"] is not None else None,
        "spectrum_diff": diff_band,
    }


HANDLERS = {
    "audio_loudness_lufs": audio_loudness_lufs,
    "audio_spectrum":      audio_spectrum,
    "audio_compare":       audio_compare,
}
