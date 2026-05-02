"""Public MCP tools for analyzing a reference song and composing from it.

Three tools:

  analyze_to_spec({audio_path, [options]}) -> {ok, spec, json_path}
      Runs music-analyzer on the audio file (subprocess into its venv),
      converts the resulting analyzer JSON into a CompositionSpec, and
      returns the spec dict. No edit is built. Use this to inspect /
      edit the spec before composing.

  compose_from_reference({audio_path, out_path, [options]}) -> {ok, edit_path, ...}
      Full pipeline: analyze → adapt → build → save. Single shot.

  compose_from_spec_json({spec_json, out_path}) -> {ok, edit_path, ...}
      Build directly from a hand-edited spec JSON. Useful when the user
      tweaked the analyzer output before composition.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..composition_spec import CompositionSpec
from ..reference_adapter import AdapterOptions, spec_from_analyzer_json
from .. import poly_transcribe
from .compose_from_spec import compose_from_spec

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Locating the analyzer                                                       #
# --------------------------------------------------------------------------- #


def _default_analyzer_paths() -> tuple[str | None, str | None]:
    """Return (python_exe, analyzer_dir) for the bundled music-analyzer.

    The analyzer lives at C:\\Users\\jarms\\repos\\music-analyzer (per the
    user's setup) with its own venv. We check the standard location first
    and fall back to env-var overrides:

      MCP_MUSIC_ANALYZER_DIR  — root of the analyzer repo
      MCP_MUSIC_ANALYZER_PY   — python.exe to use
    """
    env_dir = os.environ.get("MCP_MUSIC_ANALYZER_DIR")
    env_py = os.environ.get("MCP_MUSIC_ANALYZER_PY")
    if env_dir and env_py and Path(env_py).exists():
        return env_py, env_dir

    candidates = [
        Path(r"C:\Users\jarms\repos\music-analyzer"),
    ]
    for d in candidates:
        if not d.exists():
            continue
        for venv_py in (
            d / "venv" / "Scripts" / "python.exe",
            d / "venv314" / "Scripts" / "python.exe",
            d / ".venv" / "Scripts" / "python.exe",
        ):
            if venv_py.exists():
                return str(venv_py), str(d)
    return None, None


# --------------------------------------------------------------------------- #
# Analyzer driver                                                             #
# --------------------------------------------------------------------------- #


_ANALYZER_DRIVER = r'''
"""Non-interactive analyzer driver. Args: <audio_path>. Stdout: JSON path."""
import json, os, sys
sys.path.insert(0, sys.argv[2])  # analyzer dir

from analyzers.core import AudioData
from analyzers.rhythm import analyze_rhythm
from analyzers.harmony import analyze_harmony
from analyzers.timbre import analyze_timbre
from analyzers.melody import analyze_melody
from analyzers.stereo import analyze_stereo
from analyzers.structure import analyze_structure

audio_path = sys.argv[1]
audio = AudioData(audio_path, artist="")

rhythm     = analyze_rhythm(audio)
harmony    = analyze_harmony(audio)
timbre_r   = analyze_timbre(audio)
melody_r   = analyze_melody(audio)
stereo_r   = analyze_stereo(audio)
structure_r= analyze_structure(audio)

# Mirror analyzer_v4.save_results' JSON shape
import numpy as np

def safe(v):
    if isinstance(v, (np.floating, np.integer)):
        return float(v)
    if isinstance(v, np.ndarray):
        return v.tolist()
    return v

data = {
    "track_name": audio.track_name,
    "artist": "",
    "duration": audio.duration,
    "sample_rate": audio.sr,
    "is_stereo": audio.is_stereo,
    "rhythm": {
        "global_bpm": safe(rhythm["global_bpm"]),
        "tempo_range": [safe(rhythm["tempo_min"]), safe(rhythm["tempo_max"])],
        "beat_regularity": safe(rhythm["beat_regularity"]),
        "syncopation": safe(rhythm["syncopation"]),
        "avg_density": safe(rhythm["avg_density"]),
        "meter_candidates": rhythm.get("meter_candidates", []),
    },
    "harmony": {
        "overall_key": f"{harmony['overall_key']['key']} {harmony['overall_key']['mode']}",
        "key_confidence": safe(harmony["overall_key"]["confidence"]),
        "chords": [
            {"chord": c["chord"], "start": safe(c["start"]), "end": safe(c["end"])}
            for c in harmony.get("chords", [])
        ],
        "avg_chord_duration": safe(harmony.get("avg_chord_duration", 0)),
    },
    "timbre": {
        "spectral_centroid_mean": safe(timbre_r.get("spectral_centroid_mean", 0)),
        "brightness_description": timbre_r.get("brightness_description", ""),
        "tonality_description": timbre_r.get("tonality_description", ""),
        "harmonic_pct": safe(timbre_r.get("harmonic_pct", 50)),
        "percussive_pct": safe(timbre_r.get("percussive_pct", 50)),
        "dynamic_range": safe(timbre_r.get("dynamic_range", 0)),
        "band_energy_pct": {k: safe(v) for k, v in
                            timbre_r.get("band_energy_pct", {}).items()},
    },
    "melody": {
        "has_melody": melody_r.get("has_melody", False),
        "pitch_range": f"{melody_r.get('pitch_min_note','?')} - {melody_r.get('pitch_max_note','?')}",
        "range_semitones": safe(melody_r.get("range_semitones", 0)),
        "stepwise_pct": safe(melody_r.get("stepwise_pct", 0)),
        "leap_pct": safe(melody_r.get("leap_pct", 0)),
    },
    "stereo": {
        "is_stereo": stereo_r.get("is_stereo", False),
        "width_description": stereo_r.get("width_description", "N/A"),
        "mid_pct": safe(stereo_r.get("mid_pct", 0)),
        "side_pct": safe(stereo_r.get("side_pct", 0)),
    },
    "structure": [
        {"type": s["type"], "start": safe(s["start"]), "end": safe(s["end"]),
         "energy_ratio": safe(s.get("energy_ratio", 0)),
         "onset_density": safe(s.get("onset_density", 0))}
        for s in structure_r.get("sections", [])
    ],
}
out_json = audio_path.rsplit(".", 1)[0] + "_full_analysis.json"
with open(out_json, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
print(out_json)
'''


def _run_analyzer(audio_path: str, *, timeout: float = 600.0) -> tuple[dict[str, Any] | None, str]:
    """Run music-analyzer subprocess. Returns (analysis_dict, json_path | error_msg)."""
    py, analyzer_dir = _default_analyzer_paths()
    if not py or not analyzer_dir:
        return None, ("music-analyzer not found. Set MCP_MUSIC_ANALYZER_DIR + "
                      "MCP_MUSIC_ANALYZER_PY env vars, or install at "
                      "C:\\Users\\jarms\\repos\\music-analyzer with a venv.")
    if not Path(audio_path).exists():
        return None, f"audio file not found: {audio_path}"

    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    try:
        proc = subprocess.run(
            [py, "-c", _ANALYZER_DRIVER, audio_path, analyzer_dir],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None, f"analyzer timed out after {timeout}s"
    except Exception as e:
        return None, f"analyzer subprocess failed to launch: {e}"

    if proc.returncode != 0:
        tail = (proc.stderr or "")[-1500:]
        return None, f"analyzer exited {proc.returncode}: {tail}"

    json_path = (proc.stdout or "").strip().splitlines()[-1] if proc.stdout else ""
    if not json_path or not Path(json_path).exists():
        return None, f"analyzer didn't produce a JSON file (stdout={proc.stdout!r})"

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return None, f"failed to read analyzer JSON {json_path}: {e}"

    return data, json_path


# --------------------------------------------------------------------------- #
# Public tools                                                                #
# --------------------------------------------------------------------------- #


def analyze_to_spec(args: dict[str, Any]) -> dict[str, Any]:
    """Analyze an audio file and return a CompositionSpec dict.

    Args:
      audio_path: required. WAV/MP3/FLAC. Anything librosa can read.
      force_genre: optional. Override the heuristic genre classification.
      bars_override: optional int. Truncate/pad spec to N bars.
      skip_pad: optional bool. Drop pad role from instrumentation.
      timeout_seconds: max subprocess wall time. Default 600.
    """
    audio_path = args.get("audio_path")
    if not audio_path:
        return {"ok": False, "error": "missing 'audio_path'"}

    timeout = float(args.get("timeout_seconds", 600))
    analysis, info = _run_analyzer(str(audio_path), timeout=timeout)
    if analysis is None:
        return {"ok": False, "error": info}

    json_path = info
    options = AdapterOptions(
        force_genre=args.get("force_genre"),
        bars_override=args.get("bars_override"),
        skip_pad=bool(args.get("skip_pad", False)),
    )
    try:
        spec = spec_from_analyzer_json(analysis, options=options)
    except Exception as e:
        logger.exception("spec_from_analyzer_json failed")
        return {"ok": False, "error": f"adapter failed: {e}",
                "analyzer_json": json_path}

    # ── Phase B1: BasicPitch poly transcription ──
    # If basic-pitch is available, transcribe the audio file and overwrite
    # the chord-tone placeholders on melodic tracks with real notes.
    transcription_info: dict[str, Any] = {"used": False}
    if bool(args.get("transcribe", True)):
        if poly_transcribe.is_available():
            try:
                tr = poly_transcribe.transcribe(
                    str(audio_path), bpm=spec.tempo_bpm,
                )
                if tr.ok and tr.notes:
                    # Quantize raw transcription to a 16th-note grid and
                    # collapse overlapping micro-notes. Without this the
                    # transcribed notes drift between Waveform's strict
                    # bar-aligned drum loop and never feel "in time" with
                    # everything else.
                    from ..reference_adapter import _quantize_notes, _dedupe_overlapping
                    quantized = _quantize_notes(tr.notes, grid_beats=0.25,
                                                 min_length_beats=0.125)
                    quantized = _dedupe_overlapping(quantized, min_gap_beats=0.06)
                    counts = poly_transcribe.populate_spec_tracks_from_transcription(
                        spec.tracks, quantized, overwrite=True,
                    )
                    transcription_info = {
                        "used": True, "note_count_raw": len(tr.notes),
                        "note_count_quantized": len(quantized),
                        "per_role": counts,
                        "grid": "16th",
                    }
                    spec.provenance["basic_pitch"] = transcription_info
                else:
                    transcription_info = {"used": False, "reason": tr.error}
                    spec.provenance.setdefault("warnings", []).append(
                        f"basic-pitch failed: {tr.error}")
            except Exception as e:
                logger.exception("basic-pitch transcription failed")
                transcription_info = {"used": False, "reason": str(e)}
        else:
            transcription_info = {"used": False, "reason": "basic-pitch not installed"}

    return {
        "ok": True,
        "spec": spec.to_dict(),
        "analyzer_json": json_path,
        "summary": {
            "tempo": spec.tempo_bpm,
            "key": f"{['C','C#','D','D#','E','F','F#','G','G#','A','A#','B'][spec.key_root_pc]} {spec.key_mode}",
            "genre": spec.genre,
            "section_count": len(spec.sections),
            "chord_count": len(spec.chord_track),
            "track_count": len(spec.tracks),
            "total_bars": spec.total_length_bars,
        },
        "fidelity_caveats": spec.provenance.get("fidelity_caveats", []),
        "transcription": transcription_info,
    }


def compose_from_reference(args: dict[str, Any]) -> dict[str, Any]:
    """Full pipeline: analyze a reference audio file and write a
    .tracktionedit modeled after it.

    Args:
      audio_path: required.
      out_path: required. Where to write the .tracktionedit.
      force_genre, bars_override, skip_pad: as in analyze_to_spec.
      calibrate: bool, default True. Use plugin-aware fader calibration.
    """
    audio_path = args.get("audio_path")
    out_path = args.get("out_path")
    if not audio_path:
        return {"ok": False, "error": "missing 'audio_path'"}
    if not out_path:
        return {"ok": False, "error": "missing 'out_path'"}

    spec_res = analyze_to_spec(args)
    if not spec_res.get("ok"):
        return spec_res

    build_res = compose_from_spec({
        "spec": spec_res["spec"],
        "out_path": out_path,
        "calibrate": bool(args.get("calibrate", True)),
    })
    if not build_res.get("ok"):
        return {**build_res, "analyzer_json": spec_res.get("analyzer_json")}

    return {
        **build_res,
        "analyzer_json": spec_res["analyzer_json"],
        "summary": spec_res["summary"],
        "fidelity_caveats": spec_res["fidelity_caveats"],
        "transcription": spec_res.get("transcription"),
    }


def compose_from_spec_json(args: dict[str, Any]) -> dict[str, Any]:
    """Build an edit from an inline spec dict OR a path to a spec JSON.

    Args:
      spec: dict (CompositionSpec), OR
      spec_path: str pointing at a JSON file
      out_path: str
      calibrate: bool
    """
    spec_data = args.get("spec")
    spec_path = args.get("spec_path")
    out_path = args.get("out_path")

    if spec_data is None and spec_path:
        try:
            with open(spec_path, "r", encoding="utf-8") as f:
                spec_data = json.load(f)
        except Exception as e:
            return {"ok": False, "error": f"failed to read spec_path: {e}"}

    if spec_data is None:
        return {"ok": False, "error": "missing 'spec' or 'spec_path'"}
    if not out_path:
        return {"ok": False, "error": "missing 'out_path'"}

    return compose_from_spec({
        "spec": spec_data,
        "out_path": out_path,
        "calibrate": bool(args.get("calibrate", True)),
    })


# --------------------------------------------------------------------------- #
# MCP registration                                                            #
# --------------------------------------------------------------------------- #


HANDLERS = {
    "analyze_to_spec": analyze_to_spec,
    "compose_from_reference": compose_from_reference,
    "compose_from_spec_json": compose_from_spec_json,
}

TOOLS = [
    {
        "name": "analyze_to_spec",
        "description": (
            "Analyze a reference audio file (WAV/MP3/FLAC) and return a "
            "CompositionSpec dict suitable for compose_from_spec. Drives "
            "music-analyzer (rhythm, harmony, timbre, melody, structure) "
            "and the heuristic genre classifier. Doesn't write any edit."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "audio_path": {"type": "string"},
                "force_genre": {"type": "string"},
                "bars_override": {"type": "integer"},
                "skip_pad": {"type": "boolean"},
                "transcribe": {"type": "boolean", "default": True,
                                "description": "Use basic-pitch to transcribe melodic content if installed"},
                "timeout_seconds": {"type": "number", "default": 600},
            },
            "required": ["audio_path"],
        },
    },
    {
        "name": "compose_from_reference",
        "description": (
            "Analyze a reference audio file and produce a .tracktionedit "
            "modeled after its tempo, key, structure, chord progression, "
            "and inferred instrumentation. One-shot pipeline; mix is "
            "plugin-aware calibrated. Returns fidelity_caveats listing "
            "anything that's approximated rather than reproduced exactly."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "audio_path": {"type": "string"},
                "out_path":   {"type": "string"},
                "force_genre": {"type": "string"},
                "bars_override": {"type": "integer"},
                "skip_pad": {"type": "boolean"},
                "calibrate": {"type": "boolean", "default": True},
                "timeout_seconds": {"type": "number", "default": 600},
            },
            "required": ["audio_path", "out_path"],
        },
    },
    {
        "name": "compose_from_spec_json",
        "description": (
            "Build a .tracktionedit from an inline CompositionSpec dict or a "
            "JSON file. Useful for hand-edited specs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "spec":      {"type": "object"},
                "spec_path": {"type": "string"},
                "out_path":  {"type": "string"},
                "calibrate": {"type": "boolean", "default": True},
            },
            "required": ["out_path"],
        },
    },
]
