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
    # Articulation hint — let composers select the right voice for the
    # job. "pizz" for plucked, "bowed" for sustained strings, "staccato"
    # for short, "legato" for connected, None for "doesn't apply"
    # (synths, drums). Critical for picking the right SFZ variant
    # (e.g. ContrabassPizz vs ContrabassSusVB sound very different).
    articulation: str | None = None


# --------------------------------------------------------------------------- #
# SFZ → Sampler conversion                                                     #
# --------------------------------------------------------------------------- #


def _parse_sfz_regions(sfz_path: str | Path) -> list[dict]:
    """Parse an SFZ file into a list of region dicts with defaults from
    <control>, <global>, and <group> blocks merged in.

    SFZ section hierarchy (innermost wins):
        <control>   one per file, sets default_path / etc.
        <global>    one per file, opcodes inherited by all regions
        <group>     N per file, opcodes inherited by following regions
                    until the next <group>
        <region>    leaf — sample mapping

    Recognised opcodes: lokey, hikey, lovel, hivel, pitch_keycenter,
    sample, volume, pan, tune, default_path. Everything else is
    ignored at our fidelity.
    """
    p = Path(sfz_path)
    if not p.exists():
        return []
    text = p.read_text(encoding="utf-8", errors="ignore")

    control: dict[str, str] = {}
    global_d: dict[str, Any] = {}
    group_d: dict[str, Any] = {}
    regions: list[dict] = []
    current_section = None    # "control" / "global" / "group" / "region"
    region: dict | None = None

    INT_OPCODES = ("lokey", "hikey", "lovel", "hivel", "pitch_keycenter")
    FLOAT_OPCODES = ("volume", "pan", "tune")

    def _parse_kv(buf: dict, line: str):
        # Split on whitespace, keep "key=value" tokens
        for token in line.split():
            if "=" not in token:
                continue
            key, val = token.split("=", 1)
            key = key.strip().lower()
            val = val.strip()
            if key in INT_OPCODES:
                try:
                    buf[key] = _parse_midi_value(val)
                except Exception:
                    pass
            elif key == "sample":
                buf["sample"] = val
            elif key == "default_path":
                buf["default_path"] = val
            elif key in FLOAT_OPCODES:
                try:
                    buf[key] = float(val)
                except Exception:
                    pass

    def _flush_region():
        nonlocal region
        if region is None:
            return
        # Merge: control < global < group < region
        merged = {}
        merged.update({k: v for k, v in global_d.items()
                       if k != "default_path"})
        merged.update({k: v for k, v in group_d.items()
                       if k != "default_path"})
        merged.update(region)
        # Default pitch_keycenter to lokey if absent
        if "pitch_keycenter" not in merged or merged["pitch_keycenter"] is None:
            merged["pitch_keycenter"] = merged.get("lokey", 60)
        if "lokey" not in merged: merged["lokey"] = 0
        if "hikey" not in merged: merged["hikey"] = 127
        if "lovel" not in merged: merged["lovel"] = 0
        if "hivel" not in merged: merged["hivel"] = 127
        if merged.get("sample"):
            # Apply default_path if the sample is relative
            sample = merged["sample"]
            dp = control.get("default_path", "")
            if dp and not Path(sample).is_absolute():
                sample = str(Path(dp) / sample)
            merged["sample"] = sample
            regions.append(merged)
        region = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        # Strip line / inline comments (// ...)
        if "//" in line:
            line = line.split("//", 1)[0].strip()
            if not line:
                continue
        # Header detection — may have opcodes after the header on the same line
        for header in ("<control>", "<global>", "<group>", "<region>"):
            if line.startswith(header):
                _flush_region()
                tail = line[len(header):].strip()
                if header == "<control>":
                    current_section = "control"
                elif header == "<global>":
                    current_section = "global"
                elif header == "<group>":
                    current_section = "group"
                    group_d = {}
                elif header == "<region>":
                    current_section = "region"
                    region = {}
                line = tail
                break
        if not line:
            continue
        # Parse opcodes into the current section's dict
        if current_section == "control":
            _parse_kv(control, line)
        elif current_section == "global":
            _parse_kv(global_d, line)
        elif current_section == "group":
            _parse_kv(group_d, line)
        elif current_section == "region" and region is not None:
            _parse_kv(region, line)
    _flush_region()
    return regions


def _parse_midi_value(s: str) -> int:
    """SFZ accepts both numeric MIDI values (60) and note names (C4)."""
    s = s.strip()
    try:
        return int(s)
    except ValueError:
        pass
    # Note-name parser: C4 = 60, C-1 = 0
    name = s[0].upper()
    rest = s[1:]
    sharp_flat = 0
    if rest.startswith("#"):
        sharp_flat = 1
        rest = rest[1:]
    elif rest.startswith("b"):
        sharp_flat = -1
        rest = rest[1:]
    octave = int(rest)
    pcs = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
    return (octave + 1) * 12 + pcs[name] + sharp_flat


def _fuzzy_resolve_sample(sfz_dir: Path, sample_rel: str) -> Path | None:
    """Resolve a sample path with tolerance for VSCO-style folder-name typos.

    Walks the relative path component by component, backtracking when
    a chosen fuzzy match doesn't lead to the file. Tries: exact, case-
    insensitive exact, prefix match, contains match.
    """
    parts = [p for p in sample_rel.replace("\\", "/").split("/") if p]

    def _walk(cur: Path, idx: int) -> Path | None:
        if idx == len(parts):
            return cur if cur.is_file() else None
        part = parts[idx]
        is_last = (idx == len(parts) - 1)
        # Exact match first
        exact = cur / part
        if exact.exists():
            r = _walk(exact, idx + 1)
            if r is not None:
                return r
        if not cur.is_dir():
            return None
        try:
            siblings = list(cur.iterdir())
        except OSError:
            return None
        target = part.lower()
        candidates: list[Path] = []
        # case-insensitive exact
        candidates += [s for s in siblings if s.name.lower() == target and s != exact]
        if not is_last:
            candidates += [s for s in siblings
                           if s.is_dir() and s.name.lower().startswith(target)
                           and s not in candidates]
            candidates += [s for s in siblings
                           if s.is_dir() and target in s.name.lower()
                           and s not in candidates]
        else:
            stem = Path(part).stem.lower()
            ext = Path(part).suffix.lower()
            candidates += [s for s in siblings
                           if s.suffix.lower() == ext and s.stem.lower() == stem
                           and s not in candidates]
            # Last-resort: file may be one level deeper in a subfolder
            # (e.g., SFZ says Violin/foo.wav, disk has Violin/susVib/foo.wav)
            if not candidates:
                for sub in siblings:
                    if sub.is_dir():
                        try:
                            for sub_child in sub.iterdir():
                                if (sub_child.suffix.lower() == ext
                                        and sub_child.stem.lower() == stem):
                                    candidates.append(sub_child)
                                    break
                        except OSError:
                            pass
        for c in candidates:
            r = _walk(c, idx + 1)
            if r is not None:
                return r
        return None

    return _walk(sfz_dir, 0)


def _sfz_to_sampler_sounds(sfz_path: str | Path) -> list[dict]:
    """Convert an SFZ file into a list of Sampler SOUND entries.

    Picks one sample per (lokey, hikey, pitch_keycenter) tuple — the
    one closest to mid-velocity (around 80) — so velocity layers and
    round-robins collapse to a single representative sample per pitch
    range. This loses sample-library nuance but produces a working
    Sampler without requiring the user to manually load anything.
    """
    sfz_path = Path(sfz_path)
    regions = _parse_sfz_regions(sfz_path)
    if not regions:
        return []
    # Group by (lokey, hikey, pitch_keycenter)
    by_range: dict[tuple, list[dict]] = {}
    for r in regions:
        key = (int(r["lokey"]), int(r["hikey"]), int(r["pitch_keycenter"]))
        by_range.setdefault(key, []).append(r)

    sounds: list[dict] = []
    for (lo, hi, kc), regs in by_range.items():
        # Pick the region whose velocity range contains 80 (or closest)
        best = min(regs, key=lambda r: abs(((int(r["lovel"]) + int(r["hivel"])) // 2) - 80))
        sample_rel = best["sample"].replace("\\", "/")
        sample_abs = (sfz_path.parent / sample_rel).resolve()
        if not sample_abs.exists():
            sample_abs = _fuzzy_resolve_sample(sfz_path.parent, sample_rel)
            if sample_abs is None:
                continue
        # SFZ "volume" is in dB; map to gainDb
        gain_db = float(best.get("volume", 0))
        pan = float(best.get("pan", 0)) / 100.0   # SFZ pan is -100..100 → -1..1
        sounds.append({
            "source": str(sample_abs),
            "name": sample_abs.stem[:32],
            "keyNote": int(kc),
            "minNote": int(lo),
            "maxNote": int(hi),
            "startTime": 0.0,
            "gainDb": gain_db,
            "pan": pan,
            "openEnded": 1,
        })
    return sounds


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
        # Also index the preset under each of its declared roles so
        # `pick("keys")` finds e-pianos/organs/vibes that listed "keys"
        # as a role rather than falling through to the wrong fallback.
        for role in (preset.roles or ()):
            if role != category:
                R.setdefault(role, []).append(preset)

    # ── PIANO / E-PIANO ──
    # Salamander Grand Piano — Alexander Holm's free SFZ. The benchmark
    # free piano sound. ~466 MB samples, V3 retuned variant has the
    # better tuning. Drops in via sforzando.
    salamander_v3 = (_FACTORY_SFZ_DIR / "Salamander"
                     / "SalamanderGrandPianoV3_44.1khz16bit"
                     / "SalamanderGrandPianoV3.sfz")
    salamander_retuned = (_FACTORY_SFZ_DIR / "Salamander"
                          / "SalamanderGrandPianoV3_44.1khz16bit"
                          / "SalamanderGrandPianoV3Retuned.sfz")
    if salamander_retuned.exists():
        _add("piano", InstrumentPreset(
            name="Salamander Grand Piano (Retuned)",
            plugin_type="sforzando",
            sfz_path=str(salamander_retuned),
            roles=["piano", "keys"],
            pitch_range=(21, 108),
            output_gain_db=-3.0,
        ), exists=True)
    if salamander_v3.exists():
        _add("piano", InstrumentPreset(
            name="Salamander Grand Piano V3",
            plugin_type="sforzando",
            sfz_path=str(salamander_v3),
            roles=["piano", "keys"],
            pitch_range=(21, 108),
            output_gain_db=-3.0,
        ), exists=True)

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

    # ── VSCO Community Edition (real orchestra samples via SFZ) ──
    # 75 SFZ files, ~2 GB of WAV samples. Each instrument has multiple
    # articulations; we register the "sustained vibrato" or "sustained"
    # variants as the default voice for each role.
    vsco_root = _FACTORY_SFZ_DIR / "VSCO" / "VSCO-2-CE-1.1.0"
    if vsco_root.exists():
        # Map: (sfz_filename, friendly_name, category, roles, pitch_range)
        vsco_map = [
            # Pianos
            ("UprightPiano.sfz", "VSCO Upright Piano", "piano",
             ["piano", "keys"], (21, 108), -3.0),
            ("VSUpright1.sfz", "VSCO Upright 1", "piano",
             ["piano", "keys"], (21, 108), -3.0),
            # Strings — ensemble sustained vibrato for melodic use
            ("ViolinEnsSusVib.sfz", "VSCO Violin Ensemble", "strings",
             ["strings", "violin"], (55, 96), -3.0),
            ("ViolaEnsSusVib.sfz", "VSCO Viola Ensemble", "strings",
             ["strings"], (48, 88), -3.0),
            ("CelloEnsSusVib.sfz", "VSCO Cello Ensemble", "strings",
             ["strings"], (36, 76), -3.0),
            ("ContrabassSusVB.sfz", "VSCO Contrabass (Bowed)", "strings",
             ["strings"], (28, 60), -3.0, "bowed"),
            # PIZZICATO contrabass — plucked attack, short decay. THIS
            # is the proper jazz upright bass voice. Bowed sus version
            # was being incorrectly used as the bass and droned.
            ("ContrabassPizz.sfz", "VSCO Contrabass Pizz", "bass",
             ["bass", "upright_bass"], (28, 60), -3.0, "pizz"),
            # Brass — sustained
            ("FHornSus.sfz", "VSCO French Horn", "brass",
             ["brass"], (40, 77), -3.0),
            ("TrumpetSus.sfz", "VSCO Trumpet", "brass",
             ["brass"], (54, 84), -3.0),
            ("TromboneSus.sfz", "VSCO Trombone", "brass",
             ["brass"], (40, 72), -3.0),
            ("TubaSus.sfz", "VSCO Tuba", "brass",
             ["brass"], (28, 58), -3.0),
            # Woodwinds
            ("FluteSusVib.sfz", "VSCO Flute", "flute",
             ["flute", "lead"], (60, 96), -3.0),
            ("ClarinetSus.sfz", "VSCO Clarinet", "flute",
             ["clarinet", "flute", "lead"], (50, 84), -3.0),
            ("OboeSusVib.sfz", "VSCO Oboe", "flute",
             ["oboe", "lead"], (58, 87), -3.0),
            ("BassoonSus.sfz", "VSCO Bassoon", "brass",
             ["bassoon", "bass"], (34, 68), -3.0),
            # Percussion / mallet
            ("Glockenspiel.sfz", "VSCO Glockenspiel", "bells",
             ["bells", "vibes"], (72, 108), -3.0),
            ("TubularBells.sfz", "VSCO Tubular Bells", "bells",
             ["bells"], (60, 96), -3.0),
            ("Marimba.sfz", "VSCO Marimba", "vibes",
             ["vibes", "bells"], (48, 96), -3.0),
            ("Xylophone.sfz", "VSCO Xylophone", "vibes",
             ["vibes", "bells"], (60, 108), -3.0),
            # Other
            ("Harp.sfz", "VSCO Harp", "pluck",
             ["pluck", "vibes"], (24, 96), -3.0),
            ("OrganLoud.sfz", "VSCO Pipe Organ", "organ",
             ["organ", "pad"], (28, 96), -6.0),
        ]
        for entry in vsco_map:
            # Optional 7th element: articulation hint (pizz/bowed/staccato)
            if len(entry) == 7:
                fname, nm, cat, roles, prange, gain, articulation = entry
            else:
                fname, nm, cat, roles, prange, gain = entry
                articulation = None
            sfz = vsco_root / fname
            if sfz.exists():
                _add(cat, InstrumentPreset(
                    name=nm, plugin_type="sforzando",
                    sfz_path=str(sfz),
                    roles=roles,
                    pitch_range=prange,
                    output_gain_db=gain,
                    articulation=articulation,
                ), exists=True)

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


def _seed_chain_offsets() -> None:
    """Pre-register chain offsets for every known preset so the mix
    calibrator stops emitting `no match → -10 dB default` warnings.
    Called once at import after the registry is built. Existing entries
    in PLUGIN_CHAIN_OFFSETS_DB are NOT overwritten (manual tuning wins).
    """
    try:
        from .. import mix_calibration
    except Exception:
        return
    seen: set[int] = set()
    for presets in _REGISTRY.values():
        for p in presets:
            if id(p) in seen:
                continue
            seen.add(id(p))
            key = (p.name.lower(),)
            if key in mix_calibration.PLUGIN_CHAIN_OFFSETS_DB:
                continue
            # Sampler-loaded SFZ patches play at unity (0 dB matches the
            # 808 Kit reference). 4OSC presets play ~8 dB hotter than
            # reference because they're synth-generated, not sampled.
            default = -8.0 if p.plugin_type == "4OSC" else 0.0
            mix_calibration.PLUGIN_CHAIN_OFFSETS_DB[key] = default


_seed_chain_offsets()


# --------------------------------------------------------------------------- #
# Public API                                                                   #
# --------------------------------------------------------------------------- #


def list_categories() -> list[str]:
    return sorted(_REGISTRY.keys())


def list_in_category(category: str) -> list[InstrumentPreset]:
    return list(_REGISTRY.get(category, []))


# Categories that are TIMBRALLY INCOMPATIBLE — a fallback between them
# means the rendered song will sound nothing like the composer intended.
# Hit when "keys" was empty and fell through to "strings" → wall of synth
# strings instead of an electric piano. Lookups warn loudly when this
# happens so it's caught at compose time instead of after listening.
_INCOMPATIBLE_FALLBACKS: dict[str, set[str]] = {
    "keys":   {"strings", "brass", "drums"},
    "piano":  {"strings", "brass", "drums"},
    "bass":   {"lead", "pad", "drums", "vibes"},
    "drums":  {"piano", "keys", "strings", "brass", "lead"},
    "lead":   {"drums"},
    "violin": {"piano", "keys", "drums", "brass"},
    "guitar": {"strings", "drums"},
}


def pick(category: str, rng: random.Random | None = None,
         *, fallback: str | None = None,
         articulation: str | None = None) -> InstrumentPreset | None:
    """Pick a preset from `category` using the given RNG.

    If `articulation` is provided, only presets matching that articulation
    (or with no articulation declared) are eligible — lets callers ask
    for "bass with pizz articulation" and avoid landing on the bowed
    sustained variant.

    Returns None if the category is empty AND no fallback is provided.
    Warns loudly when the fallback would cross timbre families
    (e.g. "keys" → "strings" lands you on synth strings).
    """
    candidates = _REGISTRY.get(category, [])
    if articulation:
        candidates = [c for c in candidates
                      if c.articulation == articulation
                      or c.articulation is None]
    if candidates:
        rng = rng or random.Random()
        return rng.choice(candidates)
    if fallback:
        bad = _INCOMPATIBLE_FALLBACKS.get(category, set())
        if fallback in bad:
            logger.warning(
                "instruments.pick: category %r empty, falling back to %r — "
                "TIMBRALLY INCOMPATIBLE. Rendered song will not match "
                "composer intent. Consider populating %r or choosing a "
                "different fallback.",
                category, fallback, category,
            )
        return pick(fallback, rng, articulation=articulation)
    return None


def equip(track_id: str, category: str,
          rng: random.Random | None = None,
          *, fallback: str | None = None,
          articulation: str | None = None) -> InstrumentPreset | None:
    """Add the chosen preset's plugin to the track and load the preset.

    `articulation` filters by InstrumentPreset.articulation — pass
    "pizz" to specifically pick plucked strings, "bowed" for sustained,
    "staccato" for short. None means any articulation.

    Returns the InstrumentPreset that was equipped, or None on failure.
    """
    from . import plugins as plugin_tools
    from ..model import STORE

    preset = pick(category, rng, fallback=fallback, articulation=articulation)
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
        # Convert the SFZ to Sampler SOUND entries so the patch plays
        # without manual sforzando setup. SFZ regions → Sampler
        # multi-mapping. We lose sforzando-only features (velocity
        # crossfades, round-robins, full opcode set) but get an
        # auto-loading instrument that just works on render.
        sounds = _sfz_to_sampler_sounds(preset.sfz_path)
        if not sounds:
            logger.warning("instruments.equip: SFZ %s produced no sounds; "
                           "leaving as bare sforzando (will need manual load)",
                           preset.sfz_path)
            plugin_tools.plugin_add({"track_id": track_id, "type": "sforzando",
                                      "name": preset.name})
            track = STORE.edit.track(track_id)
            if track.plugins:
                track.plugins[-1].preset_path = preset.sfz_path
        else:
            plugin_tools.plugin_add({"track_id": track_id, "type": "Sampler",
                                      "name": preset.name})
            track = STORE.edit.track(track_id)
            if track.plugins:
                track.plugins[-1].sounds = sounds
                # Stash the SFZ path so the user knows where it came from
                track.plugins[-1].preset_path = preset.sfz_path
    elif preset.plugin_type == "Sampler":
        plugin_tools.plugin_add({"track_id": track_id, "type": "Sampler",
                                  "name": preset.name})
        track = STORE.edit.track(track_id)
        if track.plugins and preset.sample_map:
            track.plugins[-1].sounds = list(preset.sample_map)

    # Auto-register the chain offset so the calibrator stops defaulting
    # to -10 dB for every newly equipped SFZ/preset. Treat Samplers as
    # unity (matches 808 Kit reference); 4OSC as -8 (matches the rest
    # of the 4OSC presets in the calibration table).
    try:
        from .. import mix_calibration
        key = (preset.name.lower(),)
        if key not in mix_calibration.PLUGIN_CHAIN_OFFSETS_DB:
            default = -8.0 if preset.plugin_type == "4OSC" else 0.0
            mix_calibration.PLUGIN_CHAIN_OFFSETS_DB[key] = default
    except Exception:
        pass
    return preset


def equip_by_name(track_id: str, name_substring: str,
                   rng: random.Random | None = None,
                   *, fallback_category: str | None = None) -> InstrumentPreset | None:
    """Equip a preset whose name contains `name_substring` (case-insensitive).
    Searches all categories. If no match is found, falls back to picking
    randomly from `fallback_category`.

    This bypasses the rng-rolls-the-wrong-instrument problem: composers
    that need a SPECIFIC sound (Salamander Grand for the lead, Pizz
    contrabass for the upright) can pin it directly.
    """
    target = name_substring.lower()
    seen = set()
    matches: list[InstrumentPreset] = []
    for presets in _REGISTRY.values():
        for p in presets:
            if id(p) in seen:
                continue
            seen.add(id(p))
            if target in p.name.lower():
                matches.append(p)
    if matches:
        _REGISTRY.setdefault("__pinned__", []).clear()
        _REGISTRY["__pinned__"].append(matches[0])
        return equip(track_id, "__pinned__", rng,
                      fallback=fallback_category)
    if fallback_category:
        return equip(track_id, fallback_category, rng)
    logger.warning("equip_by_name: no preset matches %r", name_substring)
    return None


def get_sample_range(track_id: str) -> tuple[int, int] | None:
    """Inspect a track's loaded Sampler plugin and return (min_midi,
    max_midi) covered by its SOUND entries. Returns None if the track
    has no Sampler or it has no SOUND mappings.

    Useful for clamping generated note pitches into the actual sample
    range — playing a note above the highest sample causes the Sampler
    to pitch-shift up, which sounds buzzy and out of tune.
    """
    from ..model import STORE
    try:
        track = STORE.edit.track(track_id)
    except Exception:
        return None
    for plug in track.plugins:
        if plug.type and plug.type.lower() == "sampler" and plug.sounds:
            mn = min(int(s.get("minNote", 0)) for s in plug.sounds)
            mx = max(int(s.get("maxNote", 127)) for s in plug.sounds)
            return (mn, mx)
    return None


def pitch_in_range(pitch: int, lo: int, hi: int) -> int:
    """Octave-shift `pitch` so it falls within [lo, hi] while preserving
    pitch class. Returns the closest in-range octave equivalent.

    Use this on every note bound for a Sampler track to avoid
    pitch-shifted-resampling artifacts (the "sounds out of tune"
    problem when bass notes go above the upright sample range).
    """
    while pitch > hi:
        pitch -= 12
    while pitch < lo:
        pitch += 12
    return pitch


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
