"""High-level workflow tools — batch generation, A/B compare snapshots,
solo-render, edit transpose, mastering chain templates, tool-call history.

These are the "make my session smoother" tools.
"""

from __future__ import annotations

import copy
import time
from collections import deque
from pathlib import Path
from typing import Any

from ..model import STORE, Edit, Plugin, _id, AudioClip, MidiClip, Note
from .common import op


# ---- module-level tool history ring (T5.5) ----
_TOOL_HISTORY: deque = deque(maxlen=200)


def record_tool_call(name: str, args: dict, result: Any) -> None:
    """Helper for the @op decorator hooks — call this whenever a tool runs."""
    _TOOL_HISTORY.append({
        "ts": time.time(),
        "tool": name,
        "args_keys": sorted(args.keys()) if isinstance(args, dict) else None,
        "ok": isinstance(result, dict) and result.get("ok") is not False,
    })


# ---- snapshots (T6.2 A/B) ----
_SNAPSHOTS: dict[str, Edit] = {}


TOOLS: list[dict[str, Any]] = [
    {
        "name": "snapshot_save",
        "description": "Save the current Edit as a named snapshot for later A/B compare or recall.",
        "inputSchema": {"type": "object", "required": ["name"],
                        "properties": {"name": {"type": "string"}}},
    },
    {
        "name": "snapshot_recall",
        "description": "Restore a previously-saved snapshot as the active Edit.",
        "inputSchema": {"type": "object", "required": ["name"],
                        "properties": {"name": {"type": "string"}}},
    },
    {
        "name": "snapshot_list",
        "description": "List all saved snapshots in this session.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "edit_transpose",
        "description": "Transpose every MIDI note in the Edit by `semitones`. Audio clips are NOT pitch-shifted.",
        "inputSchema": {"type": "object", "required": ["semitones"],
                        "properties": {"semitones": {"type": "integer"}}},
    },
    {
        "name": "edit_set_tempo",
        "description": "Change the project tempo. preserve_pitch is informational (audio clips don't get pitch-shifted by this tool).",
        "inputSchema": {"type": "object", "required": ["bpm"],
                        "properties": {"bpm": {"type": "number"},
                                       "preserve_pitch": {"type": "boolean", "default": True}}},
    },
    {
        "name": "master_chain_apply",
        "description": (
            "Replace the master chain with a named template. template ∈ "
            "{clean_pop, loud_edm, lofi_warm, cinematic_dynamic, podcast_voice, "
            "no_processing}. Each is an EQ + Comp + Limiter chain tuned for "
            "its target use case."
        ),
        "inputSchema": {"type": "object", "required": ["template"],
                        "properties": {"template": {"type": "string",
                                                     "enum": ["clean_pop", "loud_edm",
                                                              "lofi_warm", "cinematic_dynamic",
                                                              "podcast_voice", "no_processing"]}}},
    },
    {
        "name": "tool_history",
        "description": "Return the last N tool calls made in this session for self-audit.",
        "inputSchema": {"type": "object",
                        "properties": {"last_n": {"type": "integer", "default": 25}}},
    },
    {
        "name": "compose_variations",
        "description": (
            "Run a composer N times with different seeds, saving each output "
            "to a numbered file. Returns the list of paths so you can render "
            "all and pick the best."
        ),
        "inputSchema": {"type": "object",
                        "required": ["composer_name", "out_path_prefix", "count"],
                        "properties": {
                            "composer_name": {"type": "string",
                                              "enum": ["compose_lofi_track",
                                                       "compose_synthwave_track",
                                                       "compose_rainstorm"]},
                            "out_path_prefix": {"type": "string"},
                            "count": {"type": "integer", "default": 3},
                            "extra_args": {"type": "object"},
                        }},
    },
    {
        "name": "edit_export_chord_chart",
        "description": (
            "Export a chord-chart text file from the markers + tempo of the "
            "current Edit. format ∈ {lead_sheet, nashville}. Markers should "
            "be named with section labels (e.g. 'verse', 'chorus')."
        ),
        "inputSchema": {"type": "object", "required": ["out_path"],
                        "properties": {"out_path": {"type": "string"},
                                       "format": {"type": "string",
                                                   "enum": ["lead_sheet", "nashville"],
                                                   "default": "lead_sheet"}}},
    },
]


# ---------- impls ----------


def snapshot_save(args: dict) -> dict:
    name = args["name"]
    _SNAPSHOTS[name] = copy.deepcopy(STORE.edit)
    return {"ok": True, "name": name, "tracks": len(STORE.edit.tracks)}


def snapshot_recall(args: dict) -> dict:
    name = args["name"]
    snap = _SNAPSHOTS.get(name)
    if snap is None:
        return {"ok": False, "error": f"unknown snapshot: {name}",
                "available": sorted(_SNAPSHOTS.keys())}
    STORE.replace(copy.deepcopy(snap))
    return {"ok": True, "name": name}


def snapshot_list(args: dict) -> dict:
    return {"ok": True, "names": sorted(_SNAPSHOTS.keys())}


@op("edit_transpose", intent_template="Transpose all MIDI by {semitones}st")
def edit_transpose(edit: Edit, semitones: int) -> dict:
    n_moved = 0
    for t in edit.tracks:
        for c in t.clips:
            if isinstance(c, MidiClip):
                for note in c.notes:
                    note.pitch = max(0, min(127, note.pitch + int(semitones)))
                    n_moved += 1
    edit.key_root = (edit.key_root + int(semitones)) % 12
    return {"semitones": int(semitones), "notes_moved": n_moved}


@op("edit_set_tempo", intent_template="Set tempo → {bpm}")
def edit_set_tempo(edit: Edit, bpm: float, preserve_pitch: bool = True) -> dict:
    edit.tempo = float(bpm)
    return {"bpm": float(bpm)}


# ---- master chain templates ----


def _mc_plugin(type_: str, name: str, **params) -> Plugin:
    return Plugin(id=_id("plg"), type=type_, name=name, params=params)


_MASTER_CHAINS: dict[str, list[Plugin]] = {}


def _build_master_chains() -> dict[str, list[Plugin]]:
    """Lazy build (each call returns fresh Plugin instances with new ids)."""
    return {
        "clean_pop": [
            _mc_plugin("3 Band EQ", "Master EQ",
                       loFreq=80.0, loGain=0.5, midFreq=500.0, midGain=-0.5, midQ=0.8,
                       hiFreq=12000.0, hiGain=1.5),
            _mc_plugin("Compressor", "Master Glue",
                       threshold=-10.0, ratio=2.0, attack=15.0, release=200.0,
                       knee=8.0, outputDb=0.5),
            _mc_plugin("Limiter", "Master Limiter", threshold=-1.0, release=50.0),
        ],
        "loud_edm": [
            _mc_plugin("3 Band EQ", "Master EQ",
                       loFreq=70.0, loGain=2.0, midFreq=400.0, midGain=-1.5, midQ=0.7,
                       hiFreq=10000.0, hiGain=3.0),
            _mc_plugin("Compressor", "Heavy Glue",
                       threshold=-12.0, ratio=3.5, attack=8.0, release=120.0,
                       knee=4.0, outputDb=2.0),
            _mc_plugin("Limiter", "Brickwall", threshold=-0.3, release=30.0),
        ],
        "lofi_warm": [
            _mc_plugin("3 Band EQ", "Lofi EQ",
                       loFreq=90.0, loGain=0.5, midFreq=300.0, midGain=-1.5, midQ=0.7,
                       hiFreq=9000.0, hiGain=1.5),
            _mc_plugin("Compressor", "Lofi Glue",
                       threshold=-8.0, ratio=1.6, attack=40.0, release=250.0,
                       knee=10.0, outputDb=0.5),
            _mc_plugin("Compresaturator", "Tape Saturator"),
            _mc_plugin("Lowpass Filter", "Lofi Lowpass", frequency=11000.0, q=0.6),
            _mc_plugin("Limiter", "Master Limiter", threshold=-1.5, release=50.0),
        ],
        "cinematic_dynamic": [
            _mc_plugin("3 Band EQ", "Cinema EQ",
                       loFreq=60.0, loGain=1.5, midFreq=800.0, midGain=0.0, midQ=0.6,
                       hiFreq=14000.0, hiGain=2.0),
            _mc_plugin("Compressor", "Subtle Glue",
                       threshold=-6.0, ratio=1.4, attack=50.0, release=400.0,
                       knee=12.0, outputDb=0.0),
            _mc_plugin("Limiter", "Soft Ceiling", threshold=-2.0, release=80.0),
        ],
        "podcast_voice": [
            _mc_plugin("3 Band EQ", "Voice EQ",
                       loFreq=120.0, loGain=-3.0, midFreq=2500.0, midGain=2.0, midQ=0.9,
                       hiFreq=10000.0, hiGain=1.0),
            _mc_plugin("Compressor", "Voice Comp",
                       threshold=-18.0, ratio=3.0, attack=5.0, release=80.0,
                       knee=4.0, outputDb=3.0),
            _mc_plugin("Limiter", "Broadcast Limit", threshold=-1.0, release=40.0),
        ],
        "no_processing": [],
    }


@op("master_chain_apply", intent_template="Apply master template '{template}'")
def master_chain_apply(edit: Edit, template: str) -> dict:
    chains = _build_master_chains()
    if template not in chains:
        raise ValueError(f"unknown template: {template}")
    edit.master_chain = chains[template]
    return {"template": template, "n_plugins": len(edit.master_chain)}


def tool_history(args: dict) -> dict:
    last_n = int(args.get("last_n", 25))
    history = list(_TOOL_HISTORY)[-last_n:]
    return {"ok": True, "count": len(history), "history": history}


# ---- batch composer ----


def compose_variations(args: dict) -> dict:
    composer_name = args["composer_name"]
    prefix = args["out_path_prefix"]
    count = int(args.get("count", 3))
    extra = args.get("extra_args", {}) or {}

    from . import composer as composer_mod
    composer_fn = getattr(composer_mod, composer_name, None)
    if composer_fn is None:
        return {"ok": False, "error": f"unknown composer: {composer_name}"}

    paths: list[str] = []
    for i in range(count):
        out_path = f"{prefix}_v{i+1}.tracktionedit"
        call_args = {**extra, "out_path": out_path, "seed": 1000 + i}
        res = composer_fn(call_args)
        paths.append(res.get("path", out_path))
    return {"ok": True, "paths": paths, "count": len(paths)}


# ---- chord chart export ----


def edit_export_chord_chart(args: dict) -> dict:
    out_path = args["out_path"]
    fmt = args.get("format", "lead_sheet")
    edit = STORE.edit

    note_names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    key_name = note_names[edit.key_root] + (" minor" if edit.key_mode == "minor" else " major")

    lines = []
    lines.append(f"=== {edit.name} ===")
    lines.append(f"Tempo: {edit.tempo:.0f} BPM   Time: {edit.time_sig_num}/{edit.time_sig_denom}   Key: {key_name}")
    lines.append("")

    # Use Edit.progression if populated; otherwise fall back to markers only.
    if edit.progression:
        # Bar-grid layout: 4 chords per line, separated by " | ", with
        # section labels inserted from markers when their start beat aligns.
        marker_at: dict[int, str] = {int(round(m.start_beats)): m.name
                                      for m in edit.markers}
        # Group chords into bars
        beats_per_bar = edit.time_sig_num
        # Use chord name + roman in lead sheet, just roman in nashville
        line_chords: list[str] = []
        for c in edit.progression:
            label = c.name if fmt == "lead_sheet" else c.roman
            beat = int(round(c.start_beats))
            if beat in marker_at:
                # Flush current line + insert section header
                if line_chords:
                    lines.append(" | ".join(line_chords))
                    line_chords = []
                lines.append("")
                lines.append(f"[ {marker_at[beat].upper()} ]")
            line_chords.append(label.ljust(8))
            if len(line_chords) == 4:
                lines.append(" | ".join(line_chords))
                line_chords = []
        if line_chords:
            lines.append(" | ".join(line_chords))
    elif edit.markers:
        lines.append("(no chord progression stored; section markers only)")
        lines.append("")
        for m in edit.markers:
            lines.append(f"[{m.start_beats:6.1f}]  {m.name}  ({m.length_beats:.0f} beats)")
    else:
        lines.append("(no progression or markers; populate via composer or progression_generate)")

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return {"ok": True, "path": out_path, "lines": len(lines)}


HANDLERS = {
    "snapshot_save":            snapshot_save,
    "snapshot_recall":          snapshot_recall,
    "snapshot_list":            snapshot_list,
    "edit_transpose":           edit_transpose,
    "edit_set_tempo":           edit_set_tempo,
    "master_chain_apply":       master_chain_apply,
    "tool_history":             tool_history,
    "compose_variations":       compose_variations,
    "edit_export_chord_chart":  edit_export_chord_chart,
}
