"""Preset / factory-content library access.

Waveform ships factory presets as plain `.trkpreset` XML files in the user's
settings directory. They contain real `<PLUGIN>` schemas with full parameter
sets — invaluable for understanding what each plugin's state looks like and
for letting the agent instantiate plugins with known-good defaults.

This module exposes:
  - `waveform_preset_list(category?)` — list available factory preset folders
    or files under a specific plugin category
  - `waveform_preset_read(path)` — return the parsed XML of a preset
  - `waveform_plugin_types()` — return the type-code map (e.g. "4osc") for
    Tracktion's built-in plugins, captured from the factory presets

Also exposes `BUILTIN_PLUGIN_TYPES` for use elsewhere in the codebase.
"""

from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


# Map of friendly name → Tracktion type code, captured from .trkpreset files.
# Add to this list as new plugin schemas are captured.
BUILTIN_PLUGIN_TYPES: dict[str, str] = {
    # Instruments
    "4OSC": "4osc",
    "Sampler": "sampler",
    # EQs
    "1 Band EQ": "1bandEq",
    "3 Band EQ": "3bandEq",
    "4 Band EQ": "4bandEq",
    "8 Band EQ": "8bandEq",
    "Lowpass Filter": "lowpass",
    # Dynamics
    "Compressor": "comp",
    "Compressor (legacy)": "compressor",  # older code, still recognized
    "Limiter": "limiter",
    "Gate": "gate",
    # Time/space
    "Plate Reverb": "plateReverb",
    "Natural Reverb": "naturalReverb",
    "Non-linear Reverb": "nonLinearReverb",
    "Reverb (legacy)": "reverb",
    "Delay": "stereoDelay",
    "Delay (legacy)": "delay",
    "Chorus": "chorusEffect",
    "Chorus (legacy)": "chorus",
    "Phaser": "phaser2",
    "Phaser (legacy)": "phaser",
    "Pitch Shifter": "pitchShifter",
    # Saturation / character (lofi gold)
    "Compresaturator": "airwindows_compresaturator",
    # Cabinet sims
    "Guitar IR": "guitarIR",
    "Dual Guitar IR": "dualGuitarIR",
    # DJ / utility
    "DJ EQ": "djeq",
    "DJ Filter": "djfilter",
    "Crossfader": "crossfader",
    "Attenuator": "attenuator",
    "MIDI Patchbay": "midipatchbay",
    # Track-internal / structural
    "Volume": "volume",
    "Level Meter": "level",
    "Text": "text",
    "Rack": "rack",
    "VST": "vst",
}


def _factory_presets_dir() -> Path | None:
    """Return Waveform's Factory Presets directory, OS-aware."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", "")) / "Tracktion" / "Waveform" / "Factory Presets"
    elif sys.platform == "darwin":
        base = (
            Path.home()
            / "Library"
            / "Application Support"
            / "Tracktion"
            / "Waveform"
            / "Factory Presets"
        )
    else:
        base = Path.home() / ".config" / "Tracktion" / "Waveform" / "Factory Presets"
    return base if base.exists() else None


TOOLS: list[dict[str, Any]] = [
    {
        "name": "waveform_preset_list",
        "description": (
            "List available factory presets. Without args, returns the plugin category "
            "folders (4OSC, Compressor, etc.). With `category`, lists individual "
            ".trkpreset files in that category."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Plugin category folder name, e.g. '4OSC'",
                },
            },
        },
    },
    {
        "name": "waveform_preset_read",
        "description": (
            "Read a .trkpreset XML and return its <PLUGIN> element contents — the type, "
            "parameters, and any nested elements. Use this to grab a known-good "
            "instrument or effect configuration to drop into a generated edit."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the .trkpreset"},
            },
        },
    },
    {
        "name": "waveform_plugin_types",
        "description": (
            "Return the friendly-name → Tracktion-type-code map for built-in plugins. "
            "These codes are what the .tracktionedit XML expects in <PLUGIN type=\"...\"/>."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def waveform_preset_list(args: dict) -> dict:
    base = _factory_presets_dir()
    if base is None:
        return {"ok": False, "error": "Factory Presets directory not found"}
    category = args.get("category")
    if not category:
        return {
            "ok": True,
            "categories": sorted(
                [d.name for d in base.iterdir() if d.is_dir() and not d.name.startswith("__")]
            ),
            "directory": str(base),
        }
    cat_dir = base / category
    if not cat_dir.exists():
        return {"ok": False, "error": f"category not found: {category}"}
    return {
        "ok": True,
        "category": category,
        "presets": sorted(
            [p.name for p in cat_dir.glob("*.trkpreset")]
        ),
        "directory": str(cat_dir),
    }


def waveform_preset_read(args: dict) -> dict:
    path = Path(args["path"])
    if not path.exists():
        return {"ok": False, "error": f"file not found: {path}"}
    try:
        tree = ET.parse(path)
    except ET.ParseError as e:
        return {"ok": False, "error": f"XML parse error: {e}"}
    root = tree.getroot()
    plugin = root.find("PLUGIN")
    if plugin is None:
        return {"ok": False, "error": "no <PLUGIN> element in preset"}
    return {
        "ok": True,
        "preset_name": root.get("name", ""),
        "preset_type": root.get("type", ""),
        "preset_subtype": root.get("subtype", ""),
        "plugin": _plugin_to_dict(plugin),
    }


def _plugin_to_dict(elem: ET.Element) -> dict:
    return {
        "tag": elem.tag,
        "attrs": dict(elem.attrib),
        "children": [_plugin_to_dict(c) for c in elem],
    }


def waveform_plugin_types(args: dict) -> dict:
    return {"ok": True, "types": dict(BUILTIN_PLUGIN_TYPES)}


HANDLERS = {
    "waveform_preset_list": waveform_preset_list,
    "waveform_preset_read": waveform_preset_read,
    "waveform_plugin_types": waveform_plugin_types,
}
