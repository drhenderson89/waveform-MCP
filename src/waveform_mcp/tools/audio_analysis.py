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
    candidates = [r"C:\Program Files\Tracktion\Waveform 13\ffmpeg.exe", "ffmpeg"]
    for c in candidates:
        try:
            subprocess.run([c, "-version"], capture_output=True, check=True, timeout=5)
            return c
        except Exception:
            continue
    return "ffmpeg"


def _ffprobe_path() -> str:
    candidates = [r"C:\Program Files\Tracktion\Waveform 13\ffprobe.exe", "ffprobe"]
    for c in candidates:
        try:
            subprocess.run([c, "-version"], capture_output=True, check=True, timeout=5)
            return c
        except Exception:
            continue
    return "ffprobe"


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
    # Parse final summary block
    integrated = re.search(r"Integrated loudness:\s*\n\s*I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", out)
    lra = re.search(r"Loudness range:\s*\n\s*LRA:\s*(-?\d+(?:\.\d+)?)\s*LU", out)
    peak = re.search(r"True peak:\s*\n\s*Peak:\s*(-?\d+(?:\.\d+)?)\s*dBFS", out)

    return {
        "ok": True,
        "file": path,
        "integrated_lufs": float(integrated.group(1)) if integrated else None,
        "lra": float(lra.group(1)) if lra else None,
        "true_peak_dbfs": float(peak.group(1)) if peak else None,
    }


def audio_spectrum(args: dict) -> dict:
    """RMS per band. Approach: use ffmpeg's `astats` per a `aresample → split
    → bandpass per-band → astats` chain, but that's complex. Simpler: use
    ebur128 + biquad bandpasses in a loop.

    For pragmatism, we use a single ffmpeg run with astats on each of N
    bandpass-filtered streams. Returns dB RMS per band.
    """
    path = args["file_path"]
    if not Path(path).exists():
        return {"ok": False, "error": f"file not found: {path}"}
    n_bands = int(args.get("bands", 10))
    ff = _ffmpeg_path()

    # Log-spaced band centers from 60 Hz to 16 kHz
    import math
    f_min, f_max = 60.0, 16000.0
    centers = [f_min * (f_max / f_min) ** (i / (n_bands - 1)) for i in range(n_bands)]
    # Q so adjacent bands meet at -3 dB ≈ 0.7 octave width per band
    q = 1.4

    # Build a filter graph: split into N copies, bandpass each, astats each.
    # We'll stitch the per-band RMS from stderr.
    # To keep parsing simple, run one band at a time.
    rms_db: list[float] = []
    for f in centers:
        res = subprocess.run(
            [ff, "-nostats", "-i", path,
             "-af", f"bandpass=f={f}:width_type=q:w={q},astats=metadata=1:reset=0",
             "-f", "null", "-"],
            capture_output=True, text=True,
        )
        # Look for "RMS level dB:" in the final summary
        m = re.findall(r"RMS level dB:\s*(-?\d+(?:\.\d+)?|-inf)", res.stderr)
        if m:
            val = m[-1]
            rms = float("-inf") if val == "-inf" else float(val)
        else:
            rms = float("-inf")
        rms_db.append(rms)

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
