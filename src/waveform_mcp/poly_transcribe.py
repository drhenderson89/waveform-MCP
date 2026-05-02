"""Polyphonic audio → MIDI transcription via Spotify BasicPitch.

BasicPitch is a small ML model (~17 MB) that transcribes any audio
(monophonic or polyphonic) to MIDI notes. We invoke it as a subprocess
into a Python interpreter that has it installed — typically the same
venv that hosts music-analyzer, since both are heavy ML deps.

This module is graceful: if BasicPitch isn't installed, transcription
returns an empty list with a clear warning. The reference_adapter falls
back to its chord-tone placeholder in that case, so the pipeline keeps
working — just with lower fidelity.

To install:
  <music-analyzer-venv>/Scripts/pip install basic-pitch

Public API:
  is_available() -> bool
  transcribe(audio_path, *, options=None) -> TranscriptionResult
  filter_by_role(notes, role) -> list[NoteEvent]   # split into bass/lead etc
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .composition_spec import NoteEvent

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Locator                                                                     #
# --------------------------------------------------------------------------- #


def _find_python_with_basic_pitch() -> str | None:
    """Return path to a Python interpreter that has basic-pitch importable.

    Checks (in order):
      1. MCP_BASIC_PITCH_PY env var
      2. The current interpreter
      3. music-analyzer's venv at the standard location
    Returns None if none found.
    """
    env_py = os.environ.get("MCP_BASIC_PITCH_PY")
    if env_py and Path(env_py).exists() and _has_module(env_py, "basic_pitch"):
        return env_py

    # Try current interpreter
    if _has_module(sys.executable, "basic_pitch"):
        return sys.executable

    # Try music-analyzer venv (same heuristic as import_song)
    candidates = [
        Path(r"C:\Users\jarms\repos\music-analyzer\venv\Scripts\python.exe"),
        Path(r"C:\Users\jarms\repos\music-analyzer\venv314\Scripts\python.exe"),
    ]
    for py in candidates:
        if py.exists() and _has_module(str(py), "basic_pitch"):
            return str(py)
    return None


def _has_module(python_exe: str, module: str) -> bool:
    """Quick check: can `python_exe -c 'import module'` succeed?"""
    try:
        proc = subprocess.run(
            [python_exe, "-c", f"import {module}"],
            capture_output=True, timeout=10,
        )
        return proc.returncode == 0
    except Exception:
        return False


# Cache: detection is slow (subprocess); only do it once per process.
_AVAILABLE_PY: str | None | object = object()  # sentinel = not yet checked


def is_available() -> bool:
    """Return True if a Python with basic-pitch is locatable."""
    global _AVAILABLE_PY
    if _AVAILABLE_PY is None:
        return False
    if isinstance(_AVAILABLE_PY, str):
        return True
    # First call: detect
    py = _find_python_with_basic_pitch()
    _AVAILABLE_PY = py
    return py is not None


def _python_for_basic_pitch() -> str | None:
    if is_available():
        return _AVAILABLE_PY  # type: ignore[return-value]
    return None


# --------------------------------------------------------------------------- #
# Transcription                                                               #
# --------------------------------------------------------------------------- #


@dataclass
class TranscriptionOptions:
    """Knobs for basic-pitch."""
    onset_threshold: float = 0.5      # 0.0..1.0; lower = more notes
    frame_threshold: float = 0.3
    minimum_note_length_ms: float = 60.0
    minimum_frequency: float | None = None   # Hz; None = no floor
    maximum_frequency: float | None = None
    multiple_pitch_bends: bool = False
    melodia_trick: bool = True
    timeout_seconds: float = 600.0


@dataclass
class TranscriptionResult:
    """Full result of a transcription call."""
    ok: bool
    notes: list[NoteEvent] = field(default_factory=list)
    bpm: float | None = None
    error: str = ""
    midi_path: str | None = None       # may be None if we transcribed in-mem


# Subprocess driver — runs in the venv that has basic_pitch
_DRIVER = r'''
import json, sys, os
audio_path = sys.argv[1]
opts = json.loads(sys.argv[2])

from basic_pitch.inference import predict
from basic_pitch import ICASSP_2022_MODEL_PATH

model_output, midi_data, note_events = predict(
    audio_path,
    model_or_model_path=ICASSP_2022_MODEL_PATH,
    onset_threshold=opts.get("onset_threshold", 0.5),
    frame_threshold=opts.get("frame_threshold", 0.3),
    minimum_note_length=opts.get("minimum_note_length_ms", 60.0),
    minimum_frequency=opts.get("minimum_frequency"),
    maximum_frequency=opts.get("maximum_frequency"),
    multiple_pitch_bends=opts.get("multiple_pitch_bends", False),
    melodia_trick=opts.get("melodia_trick", True),
)

# note_events: list of (start_sec, end_sec, pitch_midi, amplitude, [pitch_bends])
# Convert to a JSON-friendly shape; caller turns these into NoteEvents
# and converts seconds to beats using the tempo it passes in.
out = []
for ne in note_events:
    if len(ne) >= 4:
        s, e, pitch, amp = ne[0], ne[1], int(ne[2]), float(ne[3])
        out.append({
            "start_sec": float(s),
            "end_sec": float(e),
            "pitch": pitch,
            "amplitude": amp,
        })

# Extract tempo if midi_data has it
bpm = None
try:
    if midi_data.tempo_changes and midi_data.tempo_changes[0].tempo:
        bpm = float(midi_data.tempo_changes[0].tempo)
except Exception:
    pass

print(json.dumps({"notes": out, "bpm": bpm}))
'''


def transcribe(
    audio_path: str,
    *,
    bpm: float = 120.0,
    options: TranscriptionOptions | None = None,
) -> TranscriptionResult:
    """Run BasicPitch on `audio_path` and return note events in beats.

    `bpm` is used to convert basic-pitch's seconds-positioned events to
    beats. Pass the tempo you got from the analyzer for accurate timing.

    Returns a TranscriptionResult with `ok=False` and an `error` message
    on failure (missing dependency, file not found, subprocess crash).
    """
    opts = options or TranscriptionOptions()

    if not Path(audio_path).exists():
        return TranscriptionResult(ok=False, error=f"audio not found: {audio_path}")

    py = _python_for_basic_pitch()
    if py is None:
        return TranscriptionResult(
            ok=False,
            error=("basic-pitch not installed. Run "
                   "<music-analyzer-venv>/Scripts/pip install basic-pitch, "
                   "or set MCP_BASIC_PITCH_PY to a Python with it installed."),
        )

    try:
        proc = subprocess.run(
            [py, "-c", _DRIVER, str(audio_path),
             json.dumps({
                 "onset_threshold": opts.onset_threshold,
                 "frame_threshold": opts.frame_threshold,
                 "minimum_note_length_ms": opts.minimum_note_length_ms,
                 "minimum_frequency": opts.minimum_frequency,
                 "maximum_frequency": opts.maximum_frequency,
                 "multiple_pitch_bends": opts.multiple_pitch_bends,
                 "melodia_trick": opts.melodia_trick,
             })],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=opts.timeout_seconds,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
        )
    except subprocess.TimeoutExpired:
        return TranscriptionResult(
            ok=False, error=f"basic-pitch timed out after {opts.timeout_seconds}s")
    except Exception as e:
        return TranscriptionResult(ok=False, error=f"subprocess failed: {e}")

    if proc.returncode != 0:
        return TranscriptionResult(
            ok=False,
            error=f"basic-pitch exited {proc.returncode}: {(proc.stderr or '')[-1500:]}",
        )

    try:
        # Driver prints exactly one JSON line as the last line of stdout
        last_line = (proc.stdout or "").strip().splitlines()[-1]
        payload = json.loads(last_line)
    except Exception as e:
        return TranscriptionResult(
            ok=False, error=f"could not parse basic-pitch output: {e}")

    detected_bpm = payload.get("bpm")
    use_bpm = float(detected_bpm) if detected_bpm else float(bpm)
    if use_bpm <= 0:
        use_bpm = float(bpm) or 120.0

    notes: list[NoteEvent] = []
    for ne in payload.get("notes", []):
        start_sec = float(ne["start_sec"])
        end_sec = float(ne["end_sec"])
        if end_sec <= start_sec:
            continue
        # Map amplitude (0..1) to MIDI velocity (1..127)
        amp = float(ne.get("amplitude", 0.7))
        velocity = max(1, min(127, int(round(amp * 127))))
        notes.append(NoteEvent(
            start_beat=start_sec * use_bpm / 60.0,
            length_beats=(end_sec - start_sec) * use_bpm / 60.0,
            pitch=int(ne["pitch"]),
            velocity=velocity,
        ))

    return TranscriptionResult(ok=True, notes=notes, bpm=detected_bpm)


# --------------------------------------------------------------------------- #
# Role splitting                                                              #
# --------------------------------------------------------------------------- #


# Heuristic pitch-range thresholds for splitting a transcription into
# per-role tracks. These are meant for "the song has been demucs'd to
# 'other' and we got back N notes" type situations — pick out which
# notes are bass-line versus melody.
_ROLE_PITCH_RANGES = {
    "bass":           (24, 50),     # E1 .. D3
    "sub_bass":       (16, 36),
    "lead":           (60, 96),     # C4 .. C7
    "melody":         (55, 88),     # G3 .. E6
    "rhythm_guitar":  (40, 67),     # E2 .. G4
    "keys":           (48, 84),     # C3 .. C6
    "pad":            (36, 72),     # C2 .. C5
}


def filter_by_role(notes: list[NoteEvent], role: str) -> list[NoteEvent]:
    """Return only notes whose pitch sits in role's typical range.
    Unknown roles return all notes."""
    rng = _ROLE_PITCH_RANGES.get(role)
    if rng is None:
        return list(notes)
    lo, hi = rng
    return [n for n in notes if lo <= n.pitch <= hi]


# --------------------------------------------------------------------------- #
# Adapter helper — populate spec tracks from a transcription                  #
# --------------------------------------------------------------------------- #


def populate_spec_tracks_from_transcription(
    spec_tracks: list,                    # list[TrackSpec]
    transcription: list[NoteEvent],
    *,
    overwrite: bool = False,
) -> dict[str, int]:
    """Distribute transcribed notes across roles in `spec_tracks` using
    pitch-range heuristics. Mutates the tracks in place. Returns a count
    of notes assigned per role. Does nothing for tracks that already
    have notes unless `overwrite=True`.

    Drum tracks are never touched (notes go via drum_pattern).
    """
    counts: dict[str, int] = {}
    for t in spec_tracks:
        if getattr(t, "role", None) == "drums":
            continue
        if t.notes and not overwrite:
            continue
        role = t.role
        notes_for_role = filter_by_role(transcription, role)
        if not notes_for_role:
            continue
        if overwrite:
            t.notes = list(notes_for_role)
        else:
            t.notes.extend(notes_for_role)
        counts[role] = len(notes_for_role)
    return counts
