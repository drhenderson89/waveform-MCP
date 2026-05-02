"""`.tracktionedit` -> Edit deserializer (v0.2 — real-schema compatible).

Permissive: tolerates unknown attributes and extra elements that real Waveform
saves include but we don't model yet (TRANSPORT, VIDEO, CLICKTRACK, RACKS,
MASTERPLUGINS, INPUTDEVICES, TRACKCOMPS, ARRANGERTRACK, CHORDTRACK, TEMPOTRACK,
VIEWSTATE, EDITMIXGROUPS, SCENES, AUDIOEDITING, AUTOMAPXML).

Reads Waveform 13.5 native files. Tracks are direct children of EDIT (no
TRACKS wrapper). Volume / pan are read out of the per-track volume PLUGIN.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from .model import (
    AudioClip,
    Edit,
    Marker,
    MidiClip,
    Note,
    Plugin,
    Send,
    Track,
)


def _f(s: str | None, default: float = 0.0) -> float:
    if s is None:
        return default
    try:
        return float(s)
    except ValueError:
        return default


def _i(s: str | None, default: int = 0) -> int:
    if s is None:
        return default
    try:
        return int(s)
    except ValueError:
        return default


def _b(s: str | None, default: bool = False) -> bool:
    if s is None:
        return default
    return s in ("1", "true", "True")


def _norm_to_db(norm: float) -> float:
    """Inverse of _db_to_norm in xml_writer. Approximate for now."""
    if norm == 0.6376281380653381:
        return 0.0
    if norm <= 0:
        return -60.0
    import math
    return 40.0 * math.log10(norm / 0.6376281380653381)


def _read_plugin(el: ET.Element) -> Plugin:
    # Real Waveform stores plugin params as direct attributes of <PLUGIN>,
    # not as nested <PARAM> elements. Skip the structural attrs we already
    # account for separately, treat the rest as params.
    SKIP = {"id", "type", "name", "enabled", "presetPath",
            "windowX", "windowY", "windowLocked", "frozen"}
    params: dict = {}
    for k, v in el.attrib.items():
        if k in SKIP:
            continue
        try:
            if "." in v or "e" in v.lower():
                params[k] = float(v)
            else:
                params[k] = int(v)
        except (ValueError, TypeError):
            params[k] = v
    # Legacy <PARAM> form — keep for compatibility
    for p in el.findall("PARAM"):
        name = p.get("name", "")
        val = p.get("value", "")
        try:
            params[name] = float(val)
        except (ValueError, TypeError):
            params[name] = val
    return Plugin(
        id=el.get("id", ""),
        type=el.get("type", ""),
        name=el.get("name") or el.get("type", ""),
        enabled=_b(el.get("enabled"), True),
        preset_path=el.get("presetPath"),
        params=params,
    )


def _read_note(el: ET.Element) -> Note:
    n = Note(
        pitch=_i(el.get("p")),
        start_beats=_f(el.get("b")),
        length_beats=_f(el.get("l")),
        velocity=_i(el.get("v"), 100),
        colour=_i(el.get("c"), 0),
    )
    if el.get("lift") is not None:
        n.lift = _i(el.get("lift"))
    if el.get("pres") is not None:
        n.pres = _i(el.get("pres"))
    if el.get("bend") is not None:
        n.bend = _f(el.get("bend"))
    return n


def _read_midi_clip(el: ET.Element) -> MidiClip:
    notes: list[Note] = []
    # ElementTree's truthiness on Element is deprecated — use explicit
    # None checks. Both <SEQUENCE> and <MIDISEQUENCE> tags appear in
    # different .tracktionedit versions.
    seq = el.find("SEQUENCE")
    if seq is None:
        seq = el.find("MIDISEQUENCE")
    if seq is not None:
        for n in seq.findall("NOTE"):
            notes.append(_read_note(n))
    return MidiClip(
        id=el.get("id", ""),
        name=el.get("name", ""),
        start_beats=_f(el.get("start")),
        length_beats=_f(el.get("length")),
        notes=notes,
        fade_in_beats=_f(el.get("fadeIn"), 0.0),
        fade_out_beats=_f(el.get("fadeOut"), 0.0),
    )


def _read_audio_clip(el: ET.Element) -> AudioClip:
    import math
    gain_lin = _f(el.get("gain"), 1.0) or 1.0
    gain_db = 20.0 * math.log10(gain_lin) if gain_lin > 0 else 0.0
    return AudioClip(
        id=el.get("id", ""),
        name=el.get("name", ""),
        start_beats=_f(el.get("start")),
        length_beats=_f(el.get("length")),
        file_path=el.get("source", "") or el.get("file", ""),
        warp_mode=el.get("warp", "none"),  # type: ignore[arg-type]
        gain_db=gain_db,
        fade_in_beats=_f(el.get("fadeIn"), 0.0),
        fade_out_beats=_f(el.get("fadeOut"), 0.0),
        offset_in_source_beats=_f(el.get("offset"), 0.0),
    )


def _read_track(el: ET.Element) -> Track:
    plugins: list[Plugin] = []
    volume_db = 0.0
    pan = 0.0

    # Plugins: real format puts them as direct children of TRACK.
    # Distinguish: built-in volume/level plugins are 'volume' / 'level' types
    # — we extract their state into the Track's volume_db/pan, and skip them
    # from the user-visible plugin list.
    for p_el in el.findall("PLUGIN"):
        ptype = p_el.get("type", "")
        if ptype == "volume":
            v = _f(p_el.get("volume"), 0.6376281380653381)
            volume_db = _norm_to_db(v)
            pan = _f(p_el.get("pan"), 0.0)
            continue
        if ptype == "level":
            continue
        plugins.append(_read_plugin(p_el))

    # Older / our own writer might also wrap plugins in <PLUGINS>
    plugins_wrapper = el.find("PLUGINS")
    if plugins_wrapper is not None:
        for p_el in plugins_wrapper.findall("PLUGIN"):
            plugins.append(_read_plugin(p_el))

    sends: list[Send] = []
    sends_el = el.find("SENDS")
    if sends_el is not None:
        for s in sends_el.findall("SEND"):
            sends.append(
                Send(
                    dest_track_id=s.get("dest", ""),
                    amount_db=_f(s.get("amountDb")),
                    pre_post=s.get("prePost", "post"),  # type: ignore[arg-type]
                )
            )

    clips: list = []
    # Real format: clips live in CLIPSLOTS (Clip Launcher) or as direct
    # MIDICLIP / AUDIOCLIP children of TRACK in arrangement view.
    for c in el:
        if c.tag in ("MIDICLIP", "MIDI"):
            clips.append(_read_midi_clip(c))
        elif c.tag in ("AUDIOCLIP", "WAVECLIP"):
            clips.append(_read_audio_clip(c))
    # Older v0.1 wrapper
    clips_wrapper = el.find("CLIPS")
    if clips_wrapper is not None:
        for c in clips_wrapper:
            if c.tag == "MIDICLIP":
                clips.append(_read_midi_clip(c))
            elif c.tag == "AUDIOCLIP":
                clips.append(_read_audio_clip(c))

    return Track(
        id=el.get("id", ""),
        name=el.get("name", ""),
        kind=el.get("kind", "audio"),  # type: ignore[arg-type]
        volume_db=volume_db,
        pan=pan,
        mute=_b(el.get("mute")),
        solo=_b(el.get("solo")),
        output=el.get("output", "master"),
        plugins=plugins,
        clips=clips,
        sends=sends,
    )


def read(path: str) -> Edit:
    tree = ET.parse(path)
    return _from_root(tree.getroot())


def from_string(xml: str) -> Edit:
    return _from_root(ET.fromstring(xml))


def _from_root(root: ET.Element) -> Edit:
    edit = Edit()
    edit.id = root.get("projectID") or root.get("projectId", edit.id)
    edit.name = root.get("projectName", edit.name)

    tempo_el = root.find("TEMPOSEQUENCE/TEMPO")
    if tempo_el is not None:
        edit.tempo = _f(tempo_el.get("bpm"), 120.0)
    sig_el = root.find("TEMPOSEQUENCE/TIMESIG")
    if sig_el is not None:
        edit.time_sig_num = _i(sig_el.get("numerator"), 4)
        edit.time_sig_denom = _i(sig_el.get("denominator"), 4)
    pitch_el = root.find("PITCHSEQUENCE/PITCH")
    if pitch_el is not None:
        midi = _i(pitch_el.get("pitch"), 60)
        edit.key_root = midi % 12

    mt = root.find("MARKERTRACK")
    if mt is not None:
        for m in mt.findall("MARKER"):
            edit.markers.append(
                Marker(
                    name=m.get("name", ""),
                    start_beats=_f(m.get("start")),
                    length_beats=_f(m.get("length")),
                )
            )

    # Tracks: real format = direct children of EDIT. Skip framework tracks.
    framework_tags = {"ARRANGERTRACK", "CHORDTRACK", "MARKERTRACK", "TEMPOTRACK", "MASTERTRACK"}
    for t in root:
        if t.tag in framework_tags:
            continue
        if t.tag in ("TRACK", "FOLDERTRACK", "SUBMIXTRACK", "AUTOMATIONTRACK"):
            edit.tracks.append(_read_track(t))

    # Old v0.1 wrapper compat
    tracks_wrapper = root.find("TRACKS")
    if tracks_wrapper is not None:
        for tag in ("TRACK", "FOLDERTRACK", "SUBMIXTRACK", "AUTOMATIONTRACK"):
            for t in tracks_wrapper.findall(tag):
                edit.tracks.append(_read_track(t))

    # Master plugins live in MASTERPLUGINS in real format
    master = root.find("MASTERPLUGINS")
    if master is not None:
        edit.master_chain = [_read_plugin(p) for p in master.findall("PLUGIN")]
    # Old v0.1 fallback
    master_old = root.find("MASTERTRACK/PLUGINS")
    if master_old is not None and not edit.master_chain:
        edit.master_chain = [_read_plugin(p) for p in master_old.findall("PLUGIN")]

    return edit
