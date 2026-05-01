"""Audio file conversion helpers.

Waveform's built-in Sampler doesn't decode MP3 source files — they load
silently. This module provides `ensure_wav(path)` which returns a WAV path
unchanged, and converts an MP3 to a sibling .wav (cached) on demand using
Waveform's bundled ffmpeg.

Cache strategy: a converted file lands next to the source as
`<basename>.converted.wav`. We re-use it if the source's mtime hasn't
changed since the converted file was written.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def _find_ffmpeg() -> str | None:
    """Locate ffmpeg.exe — prefer Waveform's bundled copy, fall back to PATH."""
    candidates = [
        Path(r"C:\Program Files\Tracktion\Waveform 13\ffmpeg.exe"),
        Path(r"C:\Program Files\Tracktion\Waveform 12\ffmpeg.exe"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    on_path = shutil.which("ffmpeg")
    return on_path


def ensure_wav(source: str | Path) -> str:
    """Return a WAV path. If `source` is already WAV (or a non-MP3 we don't
    handle), return it as-is. If it's MP3, convert to a cached sibling WAV.

    Raises if ffmpeg isn't available AND the source needs conversion.
    """
    p = Path(source)
    if not p.exists():
        # Don't try to convert; let the caller hit the missing-file error
        return str(p)
    if p.suffix.lower() in (".wav", ".aif", ".aiff", ".flac"):
        return str(p)
    if p.suffix.lower() != ".mp3":
        # Unknown — return unchanged. Sampler may still fail; surfacing here
        # would mask other formats Waveform might handle.
        return str(p)

    cached = p.with_suffix(".converted.wav")
    if cached.exists() and cached.stat().st_mtime >= p.stat().st_mtime:
        return str(cached)

    ffmpeg = _find_ffmpeg()
    if ffmpeg is None:
        raise RuntimeError(
            f"ffmpeg not found; cannot convert {p.name} to WAV. Sampler will not load MP3 sources."
        )
    # 16-bit PCM WAV at the source's native sample rate. -y to overwrite stale cache.
    subprocess.run(
        [ffmpeg, "-y", "-i", str(p), "-c:a", "pcm_s16le", str(cached)],
        capture_output=True,
        check=True,
    )
    return str(cached)
