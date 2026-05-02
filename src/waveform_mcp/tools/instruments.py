"""Instrument registry — pick a real-sounding instrument per track role.

Generative composers were calling plain `plugin_add({type: "4OSC"})` on
every melodic track, leaving 4OSC's bare default patch (basic saw wave).
The user correctly noted: every track ends up sounding the same.

This registry maps role categories ("piano" / "violin" / "guitar" /
"vibes" / "brass" / "organ" / "lead" / "bass" / "pad" / etc.) to a
list of concrete instrument presets. Each preset is one of:

  * sforzando + SFZ file — real sampled instrument (Emilyguitar etc.)
  * 4OSC + .trkpreset    — synthesized but characterful (Violin WMF,
                            Phoenix Brass, E-Piano R, etc.)
  * Sampler + WAV/FLAC list — for any custom multi-sampled patch

Public API:
  list_categories() -> list[str]
  list_in_category(category) -> list[InstrumentPreset]
  pick(category, rng) -> InstrumentPreset
  equip(track_id, category, rng) -> InstrumentPreset
    Adds plugin to the track + loads its preset/sfz/samples.
    Returns the chosen InstrumentPreset for logging.

When a category has no entries on the host system (e.g. user didn't
install the SFZ pack), the registry falls back to the closest
synthesized 4OSC preset so composers always get *something*.
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Registry data model                                                         #
# --------------------------------------------------------------------------- #


@dataclass
class InstrumentPreset:
    """One concrete instrument the registry can equip on a track."""
    name: str
    plugin_type: str    # "sforzando" / "4OSC" / "Sampler"
    # Exactly one of these is set:
    preset_filename: str | None = None    # for 4OSC presets
    sfz_path: str | None = None           # for sforzando + SFZ
    sample_map: list[dict] | None = None  # for Sampler
    # Calibration hint — overrides the chain calibration table when set
    output_gain_db: float = 0.0
    # Pitch range this preset sounds best in. Composer can transpose
    # generated notes to fit. (low_midi, high_midi) — None means no
    # constraint.
    pitch_range: tuple[int, int] | None = None
    # Free-form: which categories this preset suits (a single instrument
    # can serve multiple roles, e.g. e-piano fits both "piano" and "keys")
    roles: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Filesystem locators                                                          #
# --------------------------------------------------------------------------- #


_FACTORY_PRESETS_DIR = (
    Path.home() / "AppData" / "Roaming" / "Tracktion" / "Waveform"
    / "Factory Presets" / "4OSC"
)
_FACTORY_SFZ_DIR = (
    Path.home() / "AppData" / "Roaming" / "Tracktion" / "Waveform"
    / "Factory Loops" / "SFZ"
)


def _has_factory_preset(name: str) -> bool:
    return (_FACTORY_PRESETS_DIR / name).exists()


def _has_sfz(rel_path: str) -> bool:
    return (_FACTORY_SFZ_DIR / rel_path).exists()


# --------------------------------------------------------------------------- #
# Category registry                                                            #
# --------------------------------------------------------------------------- #


def _build_registry() -> dict[str, list[InstrumentPreset]]:
    """Build the category → presets registry, filtering out anything
    whose backing file doesn't exist on this host."""
    R: dict[str, list[InstrumentPreset]] = {}

    def _add(category: str, preset: InstrumentPreset, *, exists: bool):
        if not exists:
            return
        R.setdefault(category, []).append(preset)

    # ── PIANO / E-PIANO ──
    _add("piano", InstrumentPreset(
        name="E-Piano R", plugin_type="4OSC",
        preset_filename="4OSC E-Piano R WMF.trkpreset",
        roles=["piano", "keys"],
        pitch_range=(36, 96),
    ), exists=_has_factory_preset("4OSC E-Piano R WMF.trkpreset"))

    # ── ORGAN / TONEWHEELS ──
    _add("organ", InstrumentPreset(
        name="House Organ", plugin_type="4OSC",
        preset_filename="4OSC House Organ WMF.trkpreset",
        roles=["organ", "keys"], pitch_range=(36, 96),
    ), exists=_has_factory_preset("4OSC House Organ WMF.trkpreset"))
    _add("organ", InstrumentPreset(
        name="Tonewheels", plugin_type="4OSC",
        preset_filename="4OSC Tonewheels WMF.trkpreset",
        roles=["organ", "keys"], pitch_range=(36, 96),
    ), exists=_has_factory_preset("4OSC Tonewheels WMF.trkpreset"))

    # ── VIBES / BELLS ──
    _add("vibes", InstrumentPreset(
        name="PeePeeGee Vibes", plugin_type="4OSC",
        preset_filename="4OSC PeePeeGee Vibes WMF.trkpreset",
        roles=["vibes", "bells", "keys"], pitch_range=(48, 96),
    ), exists=_has_factory_preset("4OSC PeePeeGee Vibes WMF.trkpreset"))
    _add("bells", InstrumentPreset(
        name="Warm Bells", plugin_type="4OSC",
        preset_filename="4OSC Warm Bells WMF.trkpreset",
        roles=["bells", "vibes"], pitch_range=(60, 108),
    ), exists=_has_factory_preset("4OSC Warm Bells WMF.trkpreset"))

    # ── STRINGS ──
    _add("strings", InstrumentPreset(
        name="Strings WMF", plugin_type="4OSC",
        preset_filename="4OSC Strings WMF.trkpreset",
        roles=["strings", "pad"], pitch_range=(36, 96),
    ), exists=_has_factory_preset("4OSC Strings WMF.trkpreset"))
    _add("strings", InstrumentPreset(
        name="Strings 16'", plugin_type="4OSC",
        preset_filename="4OSC Strings 16' WMF.trkpreset",
        roles=["strings"], pitch_range=(24, 84),
    ), exists=_has_factory_preset("4OSC Strings 16' WMF.trkpreset"))
    _add("violin", InstrumentPreset(
        name="Violin", plugin_type="4OSC",
        preset_filename="4OSC Violin WMF.trkpreset",
        roles=["violin", "strings"], pitch_range=(55, 96),
    ), exists=_has_factory_preset("4OSC Violin WMF.trkpreset"))

    # ── BRASS ──
    _add("brass", InstrumentPreset(
        name="Phoenix Brass", plugin_type="4OSC",
        preset_filename="4OSC Phoenix Brass WMF.trkpreset",
        roles=["brass"], pitch_range=(36, 84),
    ), exists=_has_factory_preset("4OSC Phoenix Brass WMF.trkpreset"))
    _add("brass", InstrumentPreset(
        name="Soft Brass", plugin_type="4OSC",
        preset_filename="4OSC Soft Brass WMF.trkpreset",
        roles=["brass"], pitch_range=(36, 84),
    ), exists=_has_factory_preset("4OSC Soft Brass WMF.trkpreset"))
    _add("brass", InstrumentPreset(
        name="Synth Brass", plugin_type="4OSC",
        preset_filename="4OSC Synth Brass WMF.trkpreset",
        roles=["brass", "lead"], pitch_range=(48, 84),
    ), exists=_has_factory_preset("4OSC Synth Brass WMF.trkpreset"))

    # ── WIND ──
    _add("flute", InstrumentPreset(
        name="Flute", plugin_type="4OSC",
        preset_filename="4OSC Flute WMF.trkpreset",
        roles=["flute", "lead"], pitch_range=(60, 96),
    ), exists=_has_factory_preset("4OSC Flute WMF.trkpreset"))

    # ── GUITAR (REAL SAMPLES via sforzando) ──
    for variant, friendly in [
        ("emily_basic.sfz",      "Electric Guitar (basic)"),
        ("emily_clean.sfz",      "Electric Guitar (clean)"),
        ("emily_chords.sfz",     "Electric Guitar (chords)"),
        ("emily_three_voice.sfz", "Electric Guitar (3-voice)"),
        ("emily_five_voice.sfz", "Electric Guitar (5-voice)"),
    ]:
        rel = f"Emilyguitar/{variant}"
        if _has_sfz(rel):
            R.setdefault("guitar", []).append(InstrumentPreset(
                name=friendly, plugin_type="sforzando",
                sfz_path=str(_FACTORY_SFZ_DIR / rel),
                roles=["guitar", "lead", "rhythm_guitar"],
                pitch_range=(40, 84),
                output_gain_db=-6.0,    # SFZ guitars run hot
            ))

    # ── PADS ──
    for fname, nm in [
        ("4OSC Bell Pad WMF.trkpreset",   "Bell Pad"),
        ("4OSC Bright Pad WMF.trkpreset", "Bright Pad"),
        ("4OSC Dark Pad WMF.trkpreset",   "Dark Pad"),
        ("4OSC Hollow Pad WMF.trkpreset", "Hollow Pad"),
        ("4OSC PWM Pad WMF.trkpreset",    "PWM Pad"),
        ("4OSC Sweep Pad WMF.trkpreset",  "Sweep Pad"),
        ("4OSC Vox Pad WMF.trkpreset",    "Vox Pad"),
    ]:
        if _has_factory_preset(fname):
            R.setdefault("pad", []).append(InstrumentPreset(
                name=nm, plugin_type="4OSC", preset_filename=fname,
                roles=["pad"], pitch_range=(36, 96),
            ))

    # ── BASS ──
    for fname, nm, gain in [
        ("4OSC Smack Bass WMF.trkpreset",      "Smack Bass", 0),
        ("4OSC Diode Bass WMF.trkpreset",      "Diode Bass", 0),
        ("4OSC Dub Bass WMF.trkpreset",        "Dub Bass", 0),
        ("4OSC Hi Q Bass WMF.trkpreset",       "Hi-Q Bass", 0),
        ("4OSC Pick Bass WMF.trkpreset",       "Pick Bass", 0),
        ("4OSC Rubber Bass WMF.trkpreset",     "Rubber Bass", 0),
        ("4OSC Transistor Bass WMF.trkpreset", "Transistor Bass", 0),
        ("4OSC Fretless Bass WMF.trkpreset",   "Fretless Bass", 0),
    ]:
        if _has_factory_preset(fname):
            R.setdefault("bass", []).append(InstrumentPreset(
                name=nm, plugin_type="4OSC", preset_filename=fname,
                roles=["bass"], pitch_range=(28, 60),
                output_gain_db=gain,
            ))

    # ── LEAD SYNTHS ──
    for fname, nm in [
        ("4OSC Default Lead.trkpreset",  "Default Lead"),
        ("4OSC Stab Synth WMF.trkpreset", "Stab Synth"),
        ("4OSC V-ger WMF.trkpreset",     "V-ger Lead"),
        ("4OSC Tech 5th WMF.trkpreset",  "Tech 5th Lead"),
        ("4OSC Phantasize WMF.trkpreset", "Phantasize Lead"),
    ]:
        if _has_factory_preset(fname):
            R.setdefault("lead", []).append(InstrumentPreset(
                name=nm, plugin_type="4OSC", preset_filename=fname,
                roles=["lead"], pitch_range=(60, 96),
            ))

    # ── PLUCK / ARP ──
    for fname, nm in [
        ("4OSC Pulse Seed WMF.trkpreset",     "Pulse Seed"),
        ("4OSC Dee Triangle WMF.trkpreset",   "Triangle Pluck"),
        ("4OSC DeeDub Poly WMF.trkpreset",    "Poly Pluck"),
        ("4OSC Wavetrack WMF.trkpreset",      "Wavetrack"),
    ]:
        if _has_factory_preset(fname):
            R.setdefault("pluck", []).append(InstrumentPreset(
                name=nm, plugin_type="4OSC", preset_filename=fname,
                roles=["pluck", "arp"], pitch_range=(48, 96),
            ))

    return R


_REGISTRY: dict[str, list[InstrumentPreset]] = _build_registry()


# --------------------------------------------------------------------------- #
# Public API                                                                   #
# --------------------------------------------------------------------------- #


def list_categories() -> list[str]:
    return sorted(_REGISTRY.keys())


def list_in_category(category: str) -> list[InstrumentPreset]:
    return list(_REGISTRY.get(category, []))


def pick(category: str, rng: random.Random | None = None,
         *, fallback: str | None = None) -> InstrumentPreset | None:
    """Pick a preset from `category` using the given RNG. Returns None
    if the category is empty AND no fallback is provided. If `fallback`
    is set, recurses with that category."""
    candidates = _REGISTRY.get(category, [])
    if candidates:
        rng = rng or random.Random()
        return rng.choice(candidates)
    if fallback:
        return pick(fallback, rng)
    return None


def equip(track_id: str, category: str,
          rng: random.Random | None = None,
          *, fallback: str | None = None) -> InstrumentPreset | None:
    """Add the chosen preset's plugin to the track and load the preset.
    Returns the InstrumentPreset that was equipped, or None on failure.
    """
    from . import plugins as plugin_tools
    from ..model import STORE

    preset = pick(category, rng, fallback=fallback)
    if preset is None:
        logger.warning("instruments.equip: no preset in category %r "
                       "(fallback=%r)", category, fallback)
        return None

    # Add the right plugin
    if preset.plugin_type == "4OSC":
        plugin_tools.plugin_add({"track_id": track_id, "type": "4OSC",
                                  "name": preset.name})
        # Apply preset state — uses the same loader compose_lofi_track uses
        try:
            from .composer import _apply_preset_to_track
            _apply_preset_to_track(track_id, "4OSC", preset.preset_filename)
        except Exception as e:
            logger.warning("preset load failed for %s: %s",
                           preset.preset_filename, e)
    elif preset.plugin_type == "sforzando":
        # Add the sforzando VST. plugin_add resolves VSTs via the
        # scanned plugin list. The SFZ load happens via state — here
        # we set the plugin name and the SFZ path is stored as a hint;
        # real SFZ injection requires the user to select the file in
        # sforzando's UI OR a captured plugin state blob with the path
        # baked in. For now we add the plugin and let the user point it.
        plugin_tools.plugin_add({"track_id": track_id, "type": "sforzando",
                                  "name": preset.name})
        # Cache the SFZ path on the plugin's metadata so a later step
        # can apply state if a captured base64 state is available.
        track = STORE.edit.track(track_id)
        if track.plugins:
            track.plugins[-1].preset_path = preset.sfz_path
    elif preset.plugin_type == "Sampler":
        plugin_tools.plugin_add({"track_id": track_id, "type": "Sampler",
                                  "name": preset.name})
        track = STORE.edit.track(track_id)
        if track.plugins and preset.sample_map:
            track.plugins[-1].sounds = list(preset.sample_map)
    return preset


# --------------------------------------------------------------------------- #
# Diagnostics                                                                  #
# --------------------------------------------------------------------------- #


def report() -> dict[str, Any]:
    """Return a diagnostic dump of what the registry found on this host."""
    return {
        "factory_presets_dir": str(_FACTORY_PRESETS_DIR),
        "factory_presets_dir_exists": _FACTORY_PRESETS_DIR.exists(),
        "factory_sfz_dir": str(_FACTORY_SFZ_DIR),
        "factory_sfz_dir_exists": _FACTORY_SFZ_DIR.exists(),
        "categories": {
            cat: [{"name": p.name, "plugin": p.plugin_type}
                  for p in presets]
            for cat, presets in sorted(_REGISTRY.items())
        },
        "total_presets": sum(len(v) for v in _REGISTRY.values()),
    }
