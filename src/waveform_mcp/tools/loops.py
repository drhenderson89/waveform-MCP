"""Loop library tools — search Waveform's `LoopLibrary.xml` and drop loops
onto tracks at song tempo.

Waveform indexes loop files by name + size in `LoopLibrary.xml`. Tempo and
metadata are encoded in the filename (e.g. `140 BPM Alex Sanfilippo Fill 2  4 bars.wav`).
We parse those conventions so the LLM can ask for "a 90 BPM loop" without
knowing the file path.

`loop_drop` wraps `audio_clip_import` and uses ffprobe (Waveform's bundled
ffmpeg) to compute the natural length in beats from the file duration and
song tempo. mode='fit' enables warp so the loop time-stretches to song
tempo; mode='original' keeps the file's natural tempo.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from ..model import STORE
from . import audio as audio_tools


def _loop_library_path() -> Path | None:
    if sys.platform == "win32":
        p = Path(os.environ.get("APPDATA", "")) / "Tracktion" / "Waveform" / "LoopLibrary.xml"
    elif sys.platform == "darwin":
        p = Path.home() / "Library" / "Application Support" / "Tracktion" / "Waveform" / "LoopLibrary.xml"
    else:
        p = Path.home() / ".config" / "Tracktion" / "Waveform" / "LoopLibrary.xml"
    return p if p.exists() else None


_BPM_RE = re.compile(r"(\d{2,3})\s*BPM", re.IGNORECASE)
_BARS_RE = re.compile(r"(\d+)\s*bars?", re.IGNORECASE)


def _parse_loop_meta(name: str) -> dict[str, Any]:
    """Extract tempo / bar-count / tags from a loop filename."""
    bpm_m = _BPM_RE.search(name)
    bars_m = _BARS_RE.search(name)
    return {
        "bpm": int(bpm_m.group(1)) if bpm_m else None,
        "bars": int(bars_m.group(1)) if bars_m else None,
    }


def _read_library() -> list[dict]:
    path = _loop_library_path()
    if path is None:
        return []
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return []
    out: list[dict] = []
    for el in root.findall("LOOP"):
        fp = el.get("path", "")
        nm = el.get("name", os.path.basename(fp))
        meta = _parse_loop_meta(nm)
        out.append({
            "path": fp,
            "name": nm,
            "size": int(el.get("size", 0) or 0),
            "bpm": meta["bpm"],
            "bars": meta["bars"],
        })
    return out


def _ffprobe_duration_seconds(path: str) -> float | None:
    candidates = [r"C:\Program Files\Tracktion\Waveform 13\ffprobe.exe", "ffprobe"]
    for ff in candidates:
        try:
            res = subprocess.run(
                [ff, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", path],
                capture_output=True, text=True, check=True,
            )
            return float(res.stdout.strip())
        except Exception:
            continue
    return None


TOOLS: list[dict[str, Any]] = [
    {
        "name": "loop_search",
        "description": (
            "Search Waveform's loop library. Optional filters: tempo (BPM, "
            "with tolerance), name (substring), bars (exact), min_bars, max_bars."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tempo": {"type": "number"},
                "tempo_tolerance": {"type": "number", "default": 5.0},
                "name": {"type": "string"},
                "bars": {"type": "integer"},
                "min_bars": {"type": "integer"},
                "max_bars": {"type": "integer"},
            },
        },
    },
    {
        "name": "loop_drop",
        "description": (
            "Drop a loop onto a track. mode='fit' time-stretches to song tempo, "
            "'original' uses natural tempo. start_beats defaults to end of "
            "existing clips on that track. Auto-detects file duration."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["track_id", "loop_path"],
            "properties": {
                "track_id": {"type": "string"},
                "loop_path": {"type": "string"},
                "start_beats": {"type": "number"},
                "mode": {"type": "string", "enum": ["fit", "original"], "default": "fit"},
            },
        },
    },
]


def loop_search(args: dict) -> dict:
    items = _read_library()
    tempo = args.get("tempo")
    tol = float(args.get("tempo_tolerance", 5.0))
    name_q = (args.get("name") or "").lower()
    bars = args.get("bars")
    min_bars = args.get("min_bars")
    max_bars = args.get("max_bars")
    out = []
    for it in items:
        if tempo is not None and it["bpm"] is not None:
            if abs(it["bpm"] - float(tempo)) > tol:
                continue
        if tempo is not None and it["bpm"] is None:
            continue  # filter strict — drop loops with no tempo metadata
        if name_q and name_q not in it["name"].lower():
            continue
        if bars is not None and it["bars"] != bars:
            continue
        if min_bars is not None and (it["bars"] is None or it["bars"] < min_bars):
            continue
        if max_bars is not None and (it["bars"] is None or it["bars"] > max_bars):
            continue
        out.append(it)
    return {"ok": True, "count": len(out), "loops": out}


def loop_drop(args: dict) -> dict:
    track_id = args["track_id"]
    loop_path = args["loop_path"]
    mode = args.get("mode", "fit")
    if not Path(loop_path).exists():
        return {"ok": False, "error": f"file not found: {loop_path}"}

    edit = STORE.edit
    track = edit.track(track_id)

    # Default start: end of last existing clip on the track
    start_beats = args.get("start_beats")
    if start_beats is None:
        if track.clips:
            last = max(c.start_beats + c.length_beats for c in track.clips)
            start_beats = last
        else:
            start_beats = 0.0
    start_beats = float(start_beats)

    # Compute length in beats from file duration + song tempo
    dur_s = _ffprobe_duration_seconds(loop_path)
    if dur_s is None:
        return {"ok": False, "error": "ffprobe unavailable; cannot determine loop duration"}

    meta = _parse_loop_meta(Path(loop_path).name)
    if mode == "fit" and meta["bars"]:
        # Use the bars from filename — fit to song tempo
        length_beats = meta["bars"] * float(edit.time_sig_num)
        warp = "stretch"
    elif mode == "fit" and meta["bpm"]:
        # Length in beats at file's natural tempo, but warp to song tempo
        length_beats = (dur_s / 60.0) * float(meta["bpm"])
        warp = "stretch"
    else:
        # Original: length in beats at SONG tempo (file plays at its native tempo;
        # the clip duration on the timeline reflects how many beats it spans).
        length_beats = (dur_s / 60.0) * float(edit.tempo)
        warp = "none"

    res = audio_tools.audio_clip_import({
        "track_id": track_id,
        "file_path": loop_path,
        "start_beats": start_beats,
        "length_beats": length_beats,
        "name": Path(loop_path).stem,
        "warp_mode": warp,
    })
    return {**res, "start_beats": start_beats, "length_beats": length_beats, "mode": mode}


HANDLERS = {
    "loop_search": loop_search,
    "loop_drop": loop_drop,
}
