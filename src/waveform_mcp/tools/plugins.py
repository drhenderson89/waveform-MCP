"""Plugin tools — built-in instruments and effects first.

Tracktion's built-in instruments have readable parameter trees in the
.tracktionedit XML, so they're safe to instantiate from scratch. External
VSTs have opaque base64 state blobs and must be addressed by preset path.
"""

from __future__ import annotations

from typing import Any

from ..model import Edit, Plugin, _id
from .common import op
from .preset_library import BUILTIN_PLUGIN_TYPES

# Curated list of built-ins we know about (research notes from Waveform 13).
BUILTIN_INSTRUMENTS = ["4OSC", "Wavetable", "MicroDrumSampler", "Multisampler", "Collective"]
BUILTIN_FX = [
    "Compressor",
    "Equalizer",
    "Reverb",
    "Delay",
    "Chorus",
    "Phaser",
    "AutoPan",
    "PitchShifter",
    "MidiModifier",
]


TOOLS: list[dict[str, Any]] = [
    {
        "name": "plugin_list",
        "description": "Return the curated list of built-in instruments and effects available by name.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "plugin_add",
        "description": (
            "Add a plugin to a track's chain. For built-ins, pass the type name "
            "(e.g. '4OSC', 'Wavetable'). For external VSTs, also pass preset_path."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["track_id", "type"],
            "properties": {
                "track_id": {"type": "string"},
                "type": {"type": "string", "description": "Built-in name or VST3 unique id"},
                "name": {"type": "string"},
                "preset_path": {"type": "string"},
            },
        },
    },
    {
        "name": "plugin_set_param",
        "description": "Set a named parameter on a plugin in a track's chain.",
        "inputSchema": {
            "type": "object",
            "required": ["track_id", "plugin_id", "param", "value"],
            "properties": {
                "track_id": {"type": "string"},
                "plugin_id": {"type": "string"},
                "param": {"type": "string"},
                "value": {},
            },
        },
    },
    {
        "name": "plugin_remove",
        "description": "Remove a plugin from a track's chain by id.",
        "inputSchema": {
            "type": "object",
            "required": ["track_id", "plugin_id"],
            "properties": {
                "track_id": {"type": "string"},
                "plugin_id": {"type": "string"},
            },
        },
    },
    {
        "name": "plugin_discover",
        "description": (
            "List third-party VST/AU plugins Waveform has scanned. Optional "
            "filters: kind ('instrument'|'effect'), format ('VST3'|'VST'|'AU'), "
            "manufacturer (substring), name (substring). Use the returned "
            "uniqueId as the `type` arg to plugin_add for VST3 instantiation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["instrument", "effect"]},
                "format": {"type": "string"},
                "manufacturer": {"type": "string"},
                "name": {"type": "string"},
            },
        },
    },
    {
        "name": "eq_high_pass",
        "description": "Add a high-pass filter at `freq` Hz to a track. `slope` is dB/oct (12, 18, 24).",
        "inputSchema": {"type": "object", "required": ["track_id"],
                        "properties": {"track_id": {"type": "string"},
                                       "freq": {"type": "number", "default": 100.0},
                                       "slope": {"type": "integer", "default": 24}}},
    },
    {
        "name": "eq_low_pass",
        "description": "Add a lowpass filter at `freq` Hz to a track for darkening/muffling.",
        "inputSchema": {"type": "object", "required": ["track_id"],
                        "properties": {"track_id": {"type": "string"},
                                       "freq": {"type": "number", "default": 8000.0},
                                       "q": {"type": "number", "default": 0.7}}},
    },
    {
        "name": "eq_tilt",
        "description": "Single-knob bright/dark EQ. brightness -1=dark, 0=neutral, +1=bright.",
        "inputSchema": {"type": "object", "required": ["track_id"],
                        "properties": {"track_id": {"type": "string"},
                                       "brightness": {"type": "number", "default": 0.0}}},
    },
    {
        "name": "compress_glue",
        "description": "Gentle bus 'glue' compressor. amount 0..1 (0=barely-there, 1=noticeable).",
        "inputSchema": {"type": "object", "required": ["track_id"],
                        "properties": {"track_id": {"type": "string"},
                                       "amount": {"type": "number", "default": 0.5}}},
    },
    {
        "name": "compress_smash",
        "description": "Aggressive parallel-style compressor for drum-bus / NY-style smash. No params — preset values.",
        "inputSchema": {"type": "object", "required": ["track_id"],
                        "properties": {"track_id": {"type": "string"}}},
    },
    {
        "name": "plugin_add_drum_kit",
        "description": (
            "Add a Sampler-backed drum kit to a track. `pads` is a list of "
            "{midi_note, sample_path, name?, gain_db?, pan?, decay?}. Each pad "
            "becomes one SOUND mapped to a single MIDI note. Replaces the "
            "manual Sampler+sounds boilerplate in compose_lofi_track."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["track_id", "pads"],
            "properties": {
                "track_id": {"type": "string"},
                "name": {"type": "string", "default": "Drum Kit"},
                "pads": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["midi_note", "sample_path"],
                        "properties": {
                            "midi_note": {"type": "integer"},
                            "sample_path": {"type": "string"},
                            "name": {"type": "string"},
                            "gain_db": {"type": "number"},
                            "pan": {"type": "number"},
                            "decay": {"type": "number"},
                        },
                    },
                },
            },
        },
    },
    {
        "name": "plugin_add_modifier",
        "description": (
            "Add a TRACK-LEVEL modifier (LFO/envelope/random/step_lfo) and "
            "assign it to a plugin parameter. Real Tracktion schema — wires "
            "both <MODIFIERS> on the track AND <MODIFIERASSIGNMENTS> on the "
            "plugin. depth: 0..1 (modulation amount). rate: Hz when "
            "rate_type=0, beat multiplier when rate_type=1 (default = beat-"
            "synced). shape ∈ {sine, triangle, square, saw}."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["track_id", "target_plugin_id", "target_param"],
            "properties": {
                "track_id":         {"type": "string"},
                "target_plugin_id": {"type": "string"},
                "target_param":     {"type": "string"},
                "modifier_type":    {"type": "string",
                                      "enum": ["lfo", "envelope", "random", "step_lfo"],
                                      "default": "lfo"},
                "depth":     {"type": "number", "default": 0.5},
                "rate":      {"type": "number", "default": 1.0},
                "rate_type": {"type": "integer", "default": 1,
                              "description": "0 = Hz, 1 = beat-synced"},
                "shape":     {"type": "string",
                              "enum": ["sine", "triangle", "square", "saw"],
                              "default": "sine"},
                "phase":     {"type": "number", "default": 0.0},
            },
        },
    },
    {
        "name": "plugin_state_capture",
        "description": (
            "Read a saved .tracktionedit file and extract a VST plugin's "
            "base64 state blob by plugin_name (substring match) or unique_id. "
            "Used to copy a manually-loaded plugin's configuration (e.g. an "
            "SFZ loaded into sforzando) for re-use programmatically."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "edit_file_path": {"type": "string"},
                "plugin_name":    {"type": "string"},
                "unique_id":      {"type": "string"},
            },
        },
    },
    {
        "name": "plugin_set_vst_state",
        "description": (
            "Apply a base64 VST state blob (from plugin_state_capture) to an "
            "existing plugin in the in-memory Edit. Lets you copy one "
            "configured VST onto every other instance."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["track_id", "plugin_id", "state"],
            "properties": {
                "track_id":  {"type": "string"},
                "plugin_id": {"type": "string"},
                "state":     {"type": "string"},
            },
        },
    },
    {
        "name": "plugin_add_macro",
        "description": (
            "Add a MacroParameter (single 0..1 knob) to a plugin. Macros are "
            "the canonical Tracktion way to expose a single control that "
            "drives multiple underlying params via assignments."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["track_id", "plugin_id", "name"],
            "properties": {
                "track_id":      {"type": "string"},
                "plugin_id":     {"type": "string"},
                "name":          {"type": "string"},
                "value":         {"type": "number", "default": 0.0},
                "default_value": {"type": "number", "default": 0.0},
            },
        },
    },
    {
        "name": "plugin_add_reverb",
        "description": (
            "Add a built-in reverb to a track. type ∈ {plate, natural, non_linear}. "
            "All other params are normalized 0..1: size, decay, mix (wet amount), "
            "pre_delay, diffusion, definition, high_damp, low_damp, high_cut. "
            "Defaults to a tasteful 25% wet plate."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["track_id"],
            "properties": {
                "track_id": {"type": "string"},
                "type": {"type": "string", "enum": ["plate", "natural", "non_linear"], "default": "plate"},
                "size": {"type": "number", "default": 0.5},
                "decay": {"type": "number", "default": 0.5},
                "mix": {"type": "number", "default": 0.25},
                "pre_delay": {"type": "number", "default": 0.0},
                "diffusion": {"type": "number", "default": 0.6},
                "definition": {"type": "number", "default": 0.5},
                "high_damp": {"type": "number", "default": 0.7},
                "low_damp": {"type": "number", "default": 0.0},
                "high_cut": {"type": "number", "default": 1.0},
                "name": {"type": "string"},
            },
        },
    },
]


# ---------- impls ----------


@op("plugin_add", intent_template="Add {type} to {track_id}")
def plugin_add(
    edit: Edit,
    track_id: str,
    type: str,
    name: str | None = None,
    preset_path: str | None = None,
) -> dict:
    """Add a plugin to a track. `type` accepts:
      - Built-in plugin name ('4OSC', 'Sampler', '3 Band EQ', etc.)
      - VST3 uniqueId or VST plugin name — auto-resolved against the
        knownPluginList64.settings cache so the proper <PLUGIN type="vst"
        ... filename uniqueId uid name manufacturer/> XML is emitted.
    """
    t = edit.track(track_id)

    # Try to resolve external VSTs from the scanned plugin list.
    vst_match = None
    type_lower = type.lower()
    if type not in BUILTIN_PLUGIN_TYPES and type_lower not in (
        c.lower() for c in BUILTIN_PLUGIN_TYPES.values()
    ):
        scanned = _read_vst_list()
        # Match by uniqueId, uid, filename basename, or name
        for plug in scanned:
            if (plug["uniqueId"].lower() == type_lower or
                plug["uid"].lower() == type_lower or
                plug["name"].lower() == type_lower):
                vst_match = plug
                break

    if vst_match:
        p = Plugin(
            id=_id("plg"),
            type="vst",
            name=name or vst_match["name"],
            preset_path=preset_path,
            vst_filename=vst_match["file"],
            vst_unique_id=vst_match["uniqueId"],
            vst_uid=vst_match["uid"],
            vst_manufacturer=vst_match["manufacturer"],
        )
    else:
        p = Plugin(id=_id("plg"), type=type, name=name or type, preset_path=preset_path)
    t.plugins.append(p)
    return {"track_id": track_id, "plugin_id": p.id,
            "resolved_as": "vst" if vst_match else "builtin",
            "filename": vst_match["file"] if vst_match else None}


@op("plugin_set_param", intent_template="Set {param} on {plugin_id}")
def plugin_set_param(edit: Edit, track_id: str, plugin_id: str, param: str, value) -> dict:
    t = edit.track(track_id)
    for p in t.plugins:
        if p.id == plugin_id:
            p.params[param] = value
            return {"plugin_id": plugin_id, "param": param}
    raise KeyError(f"plugin {plugin_id} not found on track {track_id}")


@op("plugin_remove", intent_template="Remove plugin {plugin_id}")
def plugin_remove(edit: Edit, track_id: str, plugin_id: str) -> dict:
    t = edit.track(track_id)
    t.plugins = [p for p in t.plugins if p.id != plugin_id]
    return {"plugin_id": plugin_id}


# ---------- reverb helper ----------
# All three Tracktion reverbs share the same param surface (0..1 normalized).
# Captured from factory presets — see preset_library.py for source paths.
_REVERB_TYPE_CODES = {
    "plate": "plateReverb",
    "natural": "naturalReverb",
    "non_linear": "nonLinearReverb",
    "nonlinear": "nonLinearReverb",
}


@op("plugin_add_reverb", intent_template="Add {type} reverb to {track_id}")
def plugin_add_reverb(
    edit: Edit,
    track_id: str,
    type: str = "plate",
    size: float = 0.5,
    decay: float = 0.5,
    mix: float = 0.25,
    pre_delay: float = 0.0,
    diffusion: float = 0.6,
    definition: float = 0.5,
    high_damp: float = 0.7,
    low_damp: float = 0.0,
    high_cut: float = 1.0,
    name: str | None = None,
) -> dict:
    """Add a built-in reverb with normalized (0..1) parameters.

    Sensible defaults for `mix=0.25` give ~25% wet which is typical for
    insert use; drop to 0.05 for a subtle ambience or push to 0.5+ for
    bigger spaces. Plate is bright + tight, Natural is a real room sim,
    Non-linear is gated/reverse style for percussive elements.
    """
    type_code = _REVERB_TYPE_CODES.get(type.lower())
    if not type_code:
        raise ValueError(f"unknown reverb type {type!r}; use plate, natural, or non_linear")
    t = edit.track(track_id)
    p = Plugin(
        id=_id("plg"),
        type=type_code,
        name=name or f"{type.title()} Reverb",
        params={
            "size": float(size),
            "decay": float(decay),
            "mix": float(mix),
            "preDelay": float(pre_delay),
            "diffusion": float(diffusion),
            "definition": float(definition),
            "highDamp": float(high_damp),
            "lowDamp": float(low_damp),
            "highCut": float(high_cut),
        },
    )
    t.plugins.append(p)
    return {"track_id": track_id, "plugin_id": p.id, "type": type_code}


# ---------- drum-kit helper ----------
# Convenience: builds a Sampler plugin with one SOUND per pad, each mapped to
# a single MIDI note. Functionally equivalent to a Drum Sampler for our
# composers; fully-fledged Drum Sampler emits a different element shape that
# we don't have a tracktionedit fixture for yet.


@op("plugin_add_drum_kit", intent_template="Add drum kit to {track_id}")
def plugin_add_drum_kit(
    edit: Edit,
    track_id: str,
    pads: list[dict],
    name: str = "Drum Kit",
) -> dict:
    """Add a Sampler-backed drum kit. `pads` is a list of:
        {midi_note: int, sample_path: str, name?: str,
         gain_db?: float, pan?: float, decay?: float}
    Each pad becomes one SOUND mapped to a single key (midi_note).
    """
    t = edit.track(track_id)
    sounds = []
    for pad in pads:
        note = int(pad["midi_note"])
        sounds.append({
            "source": pad["sample_path"],
            "name": pad.get("name", f"Pad {note}"),
            "keyNote": note,
            "minNote": note,
            "maxNote": note,
            "startTime": 0.0,
            "gainDb": float(pad.get("gain_db", 0.0)),
            "pan": float(pad.get("pan", 0.0)),
            "openEnded": 1,
        })
    p = Plugin(id=_id("plg"), type="sampler", name=name, sounds=sounds)
    t.plugins.append(p)
    return {"track_id": track_id, "plugin_id": p.id, "pad_count": len(sounds)}


# ---------- modifier helper ----------
# Modifiers (LFOs, envelopes, sidechain followers) attach inside a plugin's
# MODIFIERASSIGNMENTS. The exact XML shape we capture here is conservative —
# enough to sketch the modulation but it might not animate the param until
# Waveform processes the assignment fully. Track this with a fixture pass.


# ---------- EQ + compressor convenience helpers ----------


@op("eq_high_pass", intent_template="HP at {freq}Hz on {track_id}")
def eq_high_pass(edit: Edit, track_id: str, freq: float = 100.0, slope: int = 24) -> dict:
    """Add a 3 Band EQ configured as a high-pass at the given frequency.
    `slope` is dB/oct (12, 18, or 24). Implemented via aggressive low-band cut.
    """
    t = edit.track(track_id)
    p = Plugin(id=_id("plg"), type="3 Band EQ", name=f"HP {freq:.0f}",
               params={"loFreq": float(freq), "loGain": -float(slope),
                       "midFreq": 1000.0, "midGain": 0.0, "midQ": 0.8,
                       "hiFreq": 8000.0, "hiGain": 0.0})
    t.plugins.append(p)
    return {"track_id": track_id, "plugin_id": p.id}


@op("eq_low_pass", intent_template="LP at {freq}Hz on {track_id}")
def eq_low_pass(edit: Edit, track_id: str, freq: float = 8000.0, q: float = 0.7) -> dict:
    """Lowpass filter for darkening / muffling effects."""
    t = edit.track(track_id)
    p = Plugin(id=_id("plg"), type="Lowpass Filter", name=f"LP {freq:.0f}",
               params={"frequency": float(freq), "q": float(q)})
    t.plugins.append(p)
    return {"track_id": track_id, "plugin_id": p.id}


@op("eq_tilt", intent_template="Tilt EQ on {track_id}")
def eq_tilt(edit: Edit, track_id: str, brightness: float = 0.0) -> dict:
    """Single-knob bright/dark via 3 Band EQ. brightness ∈ -1..1.
    Negative tilts darker (boost lows, cut highs), positive tilts brighter."""
    t = edit.track(track_id)
    amount = max(-6.0, min(6.0, float(brightness) * 4.0))
    p = Plugin(id=_id("plg"), type="3 Band EQ", name=f"Tilt {brightness:+.1f}",
               params={"loFreq": 200.0, "loGain": -amount,
                       "midFreq": 1000.0, "midGain": 0.0, "midQ": 0.8,
                       "hiFreq": 6000.0, "hiGain": amount})
    t.plugins.append(p)
    return {"track_id": track_id, "plugin_id": p.id}


@op("compress_glue", intent_template="Glue comp on {track_id}")
def compress_glue(edit: Edit, track_id: str, amount: float = 0.5) -> dict:
    """Single-knob gentle bus compressor. amount ∈ 0..1 → ratio 1.5..4, threshold -8..-12."""
    a = max(0.0, min(1.0, float(amount)))
    t = edit.track(track_id)
    p = Plugin(id=_id("plg"), type="Compressor", name=f"Glue {a:.1f}",
               params={"threshold": -8.0 - a * 4.0,
                       "ratio": 1.5 + a * 2.5,
                       "attack": 25.0, "release": 200.0,
                       "knee": 8.0, "outputDb": a * 1.5})
    t.plugins.append(p)
    return {"track_id": track_id, "plugin_id": p.id}


@op("compress_smash", intent_template="Smash comp on {track_id}")
def compress_smash(edit: Edit, track_id: str) -> dict:
    """Aggressive parallel-compression-style: -20 threshold, 8:1, fast attack.
    Use on a duplicate drum bus + blend with the dry."""
    t = edit.track(track_id)
    p = Plugin(id=_id("plg"), type="Compressor", name="Smash",
               params={"threshold": -20.0, "ratio": 8.0,
                       "attack": 1.0, "release": 80.0,
                       "knee": 2.0, "outputDb": 4.0})
    t.plugins.append(p)
    return {"track_id": track_id, "plugin_id": p.id}


@op("plugin_add_modifier", intent_template="Add {modifier_type} on {track_id} → {target_plugin_id}/{target_param}")
def plugin_add_modifier(
    edit: Edit,
    track_id: str,
    target_plugin_id: str,
    target_param: str,
    modifier_type: str = "lfo",        # "lfo" | "envelope" | "random" | "step_lfo"
    depth: float = 0.5,
    rate: float = 1.0,
    rate_type: int = 1,                # 0=Hz, 1=beat-synced
    shape: str = "sine",
    phase: float = 0.0,
) -> dict:
    """Add a TRACK-LEVEL modifier and assign it to a plugin parameter.

    Real schema (captured from tracktion_engine source):
      Track has <MODIFIERS><LFO id depth rate ...></MODIFIERS>
      Plugin has <MODIFIERASSIGNMENTS><LFO source="<id>" paramID="..." value="1.0"/></MODIFIERASSIGNMENTS>

    This wires both ends. depth/rate/shape/phase configure the modulation
    source; the assignment scales it by `value`=1.0 onto the target param.
    """
    from ..model import Modifier, ModifierAssignment
    t = edit.track(track_id)
    target = next((p for p in t.plugins if p.id == target_plugin_id), None)
    if target is None:
        raise KeyError(f"plugin {target_plugin_id} not found on track {track_id}")
    mod = Modifier(
        id=_id("mod"),
        type=modifier_type,  # type: ignore[arg-type]
        depth=float(depth),
        rate=float(rate),
        rate_type=int(rate_type),
        shape=shape,
        phase=float(phase),
    )
    t.modifiers.append(mod)
    target.modifier_assignments.append(ModifierAssignment(
        modifier_id=mod.id,
        param_id=target_param,
        modifier_type=modifier_type,  # type: ignore[arg-type]
        value=1.0,
    ))
    return {
        "track_id": track_id,
        "modifier_id": mod.id,
        "target_plugin_id": target_plugin_id,
        "target_param": target_param,
        "modifier_type": modifier_type,
    }


def plugin_state_capture(args: dict) -> dict:
    """Read the current Edit XML and find a VST plugin's state by name.
    Returns the base64 state blob, useful for copying a manually-loaded
    plugin's configuration (e.g. an SFZ loaded into sforzando) into other
    plugins of the same type programmatically.
    """
    import re
    edit_path = args.get("edit_file_path",
        r"C:\Users\jarms\OneDrive\Documents\MCP-Validate\MCP-Validate Edit 1.tracktionedit")
    target_name = args.get("plugin_name", "").lower()
    target_unique_id = args.get("unique_id", "").lower()
    if not (target_name or target_unique_id):
        return {"ok": False, "error": "pass plugin_name or unique_id"}
    try:
        xml = open(edit_path, encoding="utf-8").read()
    except FileNotFoundError:
        return {"ok": False, "error": f"file not found: {edit_path}"}

    # Plugins can span multiple lines; capture the full open tag including state.
    pattern = re.compile(r'<PLUGIN\b[^/>]*?type="vst"[^/>]*?(?:/>|>)', re.DOTALL)
    for m in pattern.finditer(xml):
        block = m.group(0)
        name_m = re.search(r'name="([^"]+)"', block)
        uid_m = re.search(r'uniqueId="([^"]+)"', block)
        if (target_name and name_m and target_name in name_m.group(1).lower()) or \
           (target_unique_id and uid_m and target_unique_id == uid_m.group(1).lower()):
            state_m = re.search(r'state="([^"]*)"', block)
            return {
                "ok": True,
                "name": name_m.group(1) if name_m else None,
                "unique_id": uid_m.group(1) if uid_m else None,
                "state": state_m.group(1) if state_m else None,
                "has_state": bool(state_m),
            }
    return {"ok": False, "error": "no matching VST plugin found"}


@op("plugin_set_vst_state", intent_template="Set VST state on {plugin_id}")
def plugin_set_vst_state(edit: Edit, track_id: str, plugin_id: str, state: str) -> dict:
    """Apply a captured VST state blob to an existing plugin in the model.
    Pair with plugin_state_capture to copy a manually-loaded plugin's state
    (like an SFZ loaded into sforzando) onto every other instance.
    """
    t = edit.track(track_id)
    plug = next((p for p in t.plugins if p.id == plugin_id), None)
    if plug is None:
        raise KeyError(f"plugin {plugin_id} not found")
    plug.vst_state = state
    return {"plugin_id": plugin_id, "state_size": len(state) if state else 0}


@op("plugin_add_macro", intent_template="Add macro '{name}' on {plugin_id}")
def plugin_add_macro(
    edit: Edit,
    track_id: str,
    plugin_id: str,
    name: str,
    value: float = 0.0,
    default_value: float = 0.0,
) -> dict:
    """Add a MacroParameter to a plugin. Macros expose a single 0..1 knob
    that other automation/modifiers can target as a single control point.
    """
    from ..model import Macro
    t = edit.track(track_id)
    plug = next((p for p in t.plugins if p.id == plugin_id), None)
    if plug is None:
        raise KeyError(f"plugin {plugin_id} not found")
    macro = Macro(id=_id("macro"), name=name, value=float(value),
                  default_value=float(default_value))
    plug.macros.append(macro)
    return {"plugin_id": plugin_id, "macro_id": macro.id, "name": name}


def plugin_list(args: dict) -> dict:
    return {
        "ok": True,
        "instruments": BUILTIN_INSTRUMENTS,
        "effects": BUILTIN_FX,
    }


_VST_CACHE: dict | None = None


def _read_vst_list() -> list[dict]:
    """Parse knownPluginList64.settings into a list of plugin info dicts.
    Cached per-process. Returns empty list if file is missing.
    """
    global _VST_CACHE
    if _VST_CACHE is not None:
        return _VST_CACHE["plugins"]
    import os, sys, xml.etree.ElementTree as ET
    if sys.platform == "win32":
        path = os.path.join(os.environ.get("APPDATA", ""), "Tracktion", "Waveform", "knownPluginList64.settings")
    elif sys.platform == "darwin":
        path = os.path.expanduser("~/Library/Application Support/Tracktion/Waveform/knownPluginList64.settings")
    else:
        path = os.path.expanduser("~/.config/Tracktion/Waveform/knownPluginList64.settings")
    plugins: list[dict] = []
    try:
        root = ET.parse(path).getroot()
        for el in root.findall("PLUGIN"):
            plugins.append({
                "name": el.get("name", ""),
                "format": el.get("format", ""),
                "category": el.get("category", ""),
                "manufacturer": el.get("manufacturer", ""),
                "version": el.get("version", ""),
                "file": el.get("file", ""),
                "uid": el.get("uid", ""),
                "uniqueId": el.get("uniqueId", ""),
                "is_instrument": el.get("isInstrument", "0") == "1",
                "is_shell": el.get("isShell", "0") == "1",
                "num_inputs": int(el.get("numInputs", 0) or 0),
                "num_outputs": int(el.get("numOutputs", 0) or 0),
            })
    except FileNotFoundError:
        pass
    except Exception:
        pass
    _VST_CACHE = {"plugins": plugins, "path": path}
    return plugins


def plugin_discover(args: dict) -> dict:
    """List installed third-party VST/AU plugins from Waveform's scanned cache.
    Optional filters: kind ('instrument' | 'effect'), format ('VST3' | 'VST' | 'AU'),
    manufacturer (substring match), name (substring match).
    """
    plugins = _read_vst_list()
    kind = args.get("kind")
    fmt = args.get("format")
    mfr = (args.get("manufacturer") or "").lower()
    name_q = (args.get("name") or "").lower()
    out = []
    for p in plugins:
        if kind == "instrument" and not p["is_instrument"]:
            continue
        if kind == "effect" and p["is_instrument"]:
            continue
        if fmt and p["format"].upper() != fmt.upper():
            continue
        if mfr and mfr not in p["manufacturer"].lower():
            continue
        if name_q and name_q not in p["name"].lower():
            continue
        out.append(p)
    return {"ok": True, "count": len(out), "plugins": out}


HANDLERS = {
    "plugin_list": plugin_list,
    "plugin_add": plugin_add,
    "plugin_set_param": plugin_set_param,
    "plugin_remove": plugin_remove,
    "plugin_add_reverb": plugin_add_reverb,
    "plugin_add_drum_kit": plugin_add_drum_kit,
    "plugin_add_modifier": plugin_add_modifier,
    "plugin_add_macro": plugin_add_macro,
    "plugin_state_capture": plugin_state_capture,
    "plugin_set_vst_state": plugin_set_vst_state,
    "plugin_discover": plugin_discover,
    "eq_high_pass": eq_high_pass,
    "eq_low_pass": eq_low_pass,
    "eq_tilt": eq_tilt,
    "compress_glue": compress_glue,
    "compress_smash": compress_smash,
}
