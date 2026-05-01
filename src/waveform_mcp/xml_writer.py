"""Edit -> .tracktionedit XML serializer (v0.2 — real-schema compatible).

Schema reverse-engineered from a Waveform 13.5.25 hand-saved fixture. Captured
via the win_input UIA tools + waveform_workflows.waveform_save. Reference file
at tests/fixtures/ref.tracktionedit.

Key invariants observed from the real format:
  - Element order matters. JUCE's ValueTree XML is deserialized positionally
    in some places.
  - IDs are sequential integer strings starting at 1001. We allocate per-Edit
    via _IdAllocator.
  - Volume / pan / mute live as <PLUGIN type="volume"|"level"> children of
    each <TRACK>, NOT as track attributes.
  - Tracks are direct children of <EDIT>, alongside MASTERTRACK / MARKERTRACK /
    etc. — no <TRACKS> wrapper.
  - The skeleton has many "always present, often empty" elements
    (TRACKCOMPS, RACKS, MASTERPLUGINS, EDITMIXGROUPS, SCENES, AUDIOEDITING,
    AUTOMAPXML, VIDEO). Omitting them risks Waveform refusing to load.

The writer keeps a string-template feel for the skeleton so the schema is
readable here. Track / clip / note pieces are functions so they're easy to
extend as we capture more fixtures (MIDI clips, audio clips, plugins).
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from typing import Any

from .model import (
    AudioClip,
    Edit,
    MidiClip,
    Note,
    Plugin,
    Send,
    StepClip,
    Track,
)


# ---------- helpers ----------


def _b(v: bool) -> str:
    return "1" if v else "0"


def _f(v: float) -> str:
    return f"{v:.6f}".rstrip("0").rstrip(".") or "0"


def _attr(elem: ET.Element, **kwargs) -> ET.Element:
    for k, v in kwargs.items():
        if v is None:
            continue
        if isinstance(v, bool):
            elem.set(k, _b(v))
        elif isinstance(v, float):
            elem.set(k, _f(v))
        else:
            elem.set(k, str(v))
    return elem


class _IdAllocator:
    """Sequential integer-string IDs starting at 1001 — matches Waveform's
    pattern. Each Edit gets a fresh allocator so IDs are stable per save."""

    def __init__(self, start: int = 1001) -> None:
        self._next = start

    def next(self) -> str:
        i = self._next
        self._next += 1
        return str(i)


# ---------- track palette colours (matches Waveform's default 8-track set) ----------

_TRACK_COLOURS = [
    "ffff0000",  # red
    "ffffaa00",  # orange
    "ffaaff00",  # yellow-green
    "ff00ff00",  # green
    "ff00ffaa",  # teal
    "ff00aaff",  # cyan
    "ff0000ff",  # blue
    "ffaa00ff",  # magenta
]


def _track_colour(index: int) -> str:
    return _TRACK_COLOURS[index % len(_TRACK_COLOURS)]


# ---------- per-element builders ----------


def _build_transport(parent: ET.Element) -> None:
    _attr(
        ET.SubElement(parent, "TRANSPORT"),
        endToEnd=True,
        position=0.0,
        scrubInterval=0.74,
    )


def _build_pitch_sequence(parent: ET.Element, edit: Edit) -> None:
    seq = ET.SubElement(parent, "PITCHSEQUENCE")
    # Real format uses MIDI note number; key_root is 0..11 → middle-octave (C4=60)
    midi = 60 + (edit.key_root % 12)
    _attr(ET.SubElement(seq, "PITCH"), startBeat=0.0, pitch=midi)


def _build_tempo_sequence(parent: ET.Element, edit: Edit) -> None:
    seq = ET.SubElement(parent, "TEMPOSEQUENCE")
    # Initial tempo
    _attr(ET.SubElement(seq, "TEMPO"), startBeat=0.0, bpm=edit.tempo, curve=1.0)
    # Additional tempo points (e.g. chorus_b climax bump, outro ritard)
    # Stored on the Edit as tempo_changes: list[(start_beat, bpm)].
    for sb, bpm in getattr(edit, "tempo_changes", []) or []:
        _attr(ET.SubElement(seq, "TEMPO"), startBeat=float(sb), bpm=float(bpm), curve=1.0)
    _attr(
        ET.SubElement(seq, "TIMESIG"),
        numerator=edit.time_sig_num,
        denominator=edit.time_sig_denom,
        startBeat=0.0,
    )


def _build_master_volume(parent: ET.Element, ids: _IdAllocator, db: float = 0.0) -> None:
    mv = ET.SubElement(parent, "MASTERVOLUME")
    plugin = ET.SubElement(mv, "PLUGIN")
    _attr(plugin, type="volume", volume=_db_to_norm(db), id=ids.next(), enabled=True)
    ET.SubElement(plugin, "MODIFIERASSIGNMENTS")


def _build_input_devices(parent: ET.Element) -> None:
    inputs = ET.SubElement(parent, "INPUTDEVICES")
    _attr(
        ET.SubElement(inputs, "INPUTDEVICE"),
        deviceID="all_midi_in",
        name="All MIDI Ins",
    )


def _build_marker_track(parent: ET.Element, edit: Edit, ids: _IdAllocator) -> None:
    mt = ET.SubElement(parent, "MARKERTRACK")
    _attr(mt, id=ids.next(), name="Marker")
    for m in edit.markers:
        _attr(
            ET.SubElement(mt, "MARKER"),
            name=m.name,
            start=m.start_beats,
            length=m.length_beats,
        )


def _build_master_track(parent: ET.Element, edit: Edit, ids: _IdAllocator) -> None:
    mt = ET.SubElement(parent, "MASTERTRACK")
    _attr(mt, name="Master", id=ids.next())
    ET.SubElement(mt, "MODIFIERS")


def _build_track(parent: ET.Element, t: Track, index: int, ids: _IdAllocator) -> None:
    # Submix / folder tracks emit a FOLDERTRACK element (captured fixture:
    # tests/fixtures/BEFORE_baseline → after Track → Add submix track).
    tag = "FOLDERTRACK" if t.kind in ("submix", "folder") else "TRACK"
    el = ET.SubElement(parent, tag)
    track_id = ids.next()
    _attr(el, id=track_id, name=t.name, colour=_track_colour(index),
          mute=t.mute if t.mute else None,
          solo=t.solo if t.solo else None)
    # Track-level modifiers (LFO / envelope / random / step_lfo / midi_tracker).
    # Schema from tracktion_engine: <LFO id syncType depth phase rateType rate/>
    # The element TAG matches the modifier type (uppercase in real Waveform).
    modifiers_el = ET.SubElement(el, "MODIFIERS")
    for mod in (t.modifiers or []):
        mod_tag = {"lfo":"LFO", "envelope":"ENVELOPEFOLLOWER",
                   "random":"RANDOM", "step_lfo":"STEPLFO",
                   "midi_tracker":"MIDITRACKER"}.get(mod.type, "LFO")
        _attr(ET.SubElement(modifiers_el, mod_tag),
              id=mod.id,
              syncType=mod.sync_type,
              depth=float(mod.depth),
              phase=float(mod.phase),
              rateType=mod.rate_type,
              rate=float(mod.rate),
              shape=mod.shape if mod.type == "lfo" else None)

    # CLIPSLOTS holds the user-assigned clip slots (for the v13 Clip Launcher).
    # Empty for now; extend when we model clip slots.
    ET.SubElement(el, "CLIPSLOTS")

    # User clips appear here, between CLIPSLOTS and the implicit volume/level
    # plugins. Captured from a fresh "Insert new MIDI clip" fixture.
    for c in t.clips:
        if isinstance(c, MidiClip):
            _build_midi_clip(el, c, index, ids)
        elif isinstance(c, AudioClip):
            _build_audio_clip(el, c, ids)
        elif isinstance(c, StepClip):
            _build_step_clip(el, c, index, ids)

    # User-added plugins (instruments, FX) before the implicit volume/level
    # We track the rendered plugin element by source id so automation lanes
    # targeting "plugin/<id>/<param>" can attach AUTOMATIONCURVES on it.
    plugin_elements: dict[str, ET.Element] = {}
    for p in t.plugins:
        plugin_el = _build_plugin(el, p, ids)
        plugin_elements[p.id] = plugin_el

    # Implicit per-track plugins: a volume plugin and a level meter.
    # Real Waveform always emits these; we encode user volume_db / pan into
    # the volume plugin so the round-trip preserves mix state.
    vol_plugin = ET.SubElement(el, "PLUGIN")
    _attr(vol_plugin, type="volume", id=ids.next(), enabled=True)
    if t.volume_db != 0.0 or t.pan != 0.0:
        _attr(vol_plugin, volume=_db_to_norm(t.volume_db), pan=t.pan)
    ET.SubElement(vol_plugin, "MODIFIERASSIGNMENTS")
    plugin_elements["__volume__"] = vol_plugin

    # Attach automation lanes to the right plugin element.
    for lane in getattr(t, "automation", []) or []:
        _build_automation_lane(plugin_elements, lane)

    level_plugin = ET.SubElement(el, "PLUGIN")
    _attr(level_plugin, type="level", id=ids.next(), enabled=True)

    # OUTPUTDEVICES comes AFTER the level plugin in tracks that have clips —
    # captured this in the clip-insert fixture diff. For tracks without clips,
    # real Waveform puts it before plugins (see ref.tracktionedit). The
    # ordering rule appears to be: empty track → before plugins; track with
    # clips → after plugins. We always emit it after to match the with-clips
    # case which is more common in user-driven edits.
    od = ET.SubElement(el, "OUTPUTDEVICES")
    _attr(ET.SubElement(od, "DEVICE"), name="(default audio output)")

    # Sends — placement and exact element name in real format TBD; capture
    # this in the next fixture pass.
    for s in t.sends:
        _build_send(el, s)


def _db_to_norm(db: float) -> float:
    """Waveform's volume plugin stores normalized 0..1, with 0.6376281... ≈ unity (0 dB).
    The exact mapping isn't documented; this is a placeholder that round-trips
    0 dB to the observed unity value. Refine when we capture a fixture with a
    non-default volume."""
    if db == 0.0:
        return 0.6376281380653381
    # Linear approximation around unity until we have more data points.
    import math
    return 0.6376281380653381 * (10.0 ** (db / 40.0))


def _build_midi_clip(parent: ET.Element, c: MidiClip, track_index: int, ids: _IdAllocator) -> None:
    """Schema captured from a real "Insert new MIDI clip" fixture (Waveform 13.5)."""
    el = ET.SubElement(parent, "MIDICLIP")
    _attr(
        el,
        name=c.name,
        start=c.start_beats,
        length=c.length_beats,
        offset=0.0,
        id=ids.next(),
        sync=0,
        currentTake=0,
        speed=1.0,
        colour=_track_colour(track_index),
        fadeIn=c.fade_in_beats if c.fade_in_beats > 0.0 else None,
        fadeOut=c.fade_out_beats if c.fade_out_beats > 0.0 else None,
    )
    ET.SubElement(el, "AUTOMATIONCURVES")
    ET.SubElement(el, "QUANTISATION")
    ET.SubElement(el, "GROOVE")
    seq = ET.SubElement(el, "SEQUENCE")
    _attr(seq, ver=1, channelNumber=1)
    for n in c.notes:
        # NOTE schema from tracktion_engine/midi/tracktion_MidiList.cpp:
        # IDs::p=pitch, IDs::b=beat, IDs::l=length, IDs::v=velocity, IDs::c=colour.
        # Channel is per-clip on <SEQUENCE channelNumber=...>, not per-note.
        note_el = ET.SubElement(seq, "NOTE")
        _attr(
            note_el,
            p=n.pitch,
            b=n.start_beats,
            l=n.length_beats,
            v=n.velocity,
            c=n.colour,
        )
        if n.lift is not None:
            _attr(note_el, lift=n.lift)
        if n.pres is not None:
            _attr(note_el, pres=n.pres)
        if n.bend is not None:
            _attr(note_el, bend=n.bend)
    pg = ET.SubElement(el, "PATTERNGENERATOR")
    ET.SubElement(pg, "PROGRESSION")


def _build_audio_clip(parent: ET.Element, c: AudioClip, ids: _IdAllocator) -> None:
    """Schema captured from a real "Insert new Audio clip" fixture (Waveform 13.5)."""
    el = ET.SubElement(parent, "AUDIOCLIP")
    # Tracktion stores clip gain as linear amplitude, not dB.
    gain_lin = 10.0 ** (c.gain_db / 20.0) if c.gain_db != 0.0 else 1.0
    _attr(
        el,
        name=c.name,
        start=c.start_beats,
        length=c.length_beats,
        # `offset` on AUDIOCLIP = how many beats to skip into the source file
        # (i.e. the inverse of "trim from start"). Captured from a hand-edited
        # fixture where dragging the left edge of an audio clip increased this.
        offset=c.offset_in_source_beats if c.offset_in_source_beats != 0.0 else 0.0,
        id=ids.next(),
        sync=0,
        elastiqueMode=0 if c.warp_mode == "none" else 1,
        pan=0.0,
        colour="ffff0000",
        proxyAllowed=True,
        resamplingQuality="lagrange",
        gain=gain_lin if gain_lin != 1.0 else None,
        fadeIn=c.fade_in_beats if c.fade_in_beats > 0.0 else None,
        fadeOut=c.fade_out_beats if c.fade_out_beats > 0.0 else None,
    )
    if c.file_path:
        # Real fixture didn't capture how the source path is stored — likely
        # 'source' or referenced by a project audio file ID. Capturing this
        # in the next fixture pass (drag a factory loop onto a track).
        _attr(el, source=c.file_path)
    ET.SubElement(el, "AUTOMATIONCURVES")
    _attr(
        ET.SubElement(el, "LOOPINFO"),
        numBeats=0.0,
        denominator=4,
        numerator=4,
        oneShot=False,
        bpm=0,
        rootNote=-1,
        inMarker=0,
        outMarker=-1,
    )


def _build_step_clip(parent: ET.Element, c: StepClip, track_index: int, ids: _IdAllocator) -> None:
    """Schema captured from a real Waveform fixture (Convert to Step clip)."""
    el = ET.SubElement(parent, "STEPCLIP")
    _attr(el,
          name=c.name, start=c.start_beats, length=c.length_beats, offset=0.0,
          id=ids.next(), sequence=c.sequence_index,
          colour=_track_colour(track_index),
          repeatSequence=c.repeat_sequence)
    ET.SubElement(el, "AUTOMATIONCURVES")
    channels_el = ET.SubElement(el, "CHANNELS")
    for ch in c.channels:
        _attr(ET.SubElement(channels_el, "CHANNEL"),
              channel=ch.channel, note=ch.note,
              velocity=ch.velocity, name=ch.name)
    patterns_el = ET.SubElement(el, "PATTERNS")
    for pat in c.patterns:
        # numNotes = sum of '1's across all rows; noteLength in beats per step
        total_ones = sum(row.get("pattern", "").count("1") for row in pat.rows)
        pat_el = ET.SubElement(patterns_el, "PATTERN")
        _attr(pat_el, numNotes=total_ones, noteLength=pat.note_length)
        for row in pat.rows:
            ch_el = ET.SubElement(pat_el, "CHANNEL")
            vels = " ".join(str(int(v)) for v in row.get("velocities", []))
            _attr(ch_el, pattern=row.get("pattern", ""), velocities=vels)


def _build_plugin(parent: ET.Element, p: Plugin, ids: _IdAllocator) -> ET.Element:
    """Plugin schema captured from .trkpreset factory presets.

    Plugin parameters are stored as DIRECT ATTRIBUTES of <PLUGIN>, not as
    nested <PARAM> elements. Type codes are short lowercase strings — see
    BUILTIN_PLUGIN_TYPES in tools/preset_library.py for the friendly-name
    map. Common children: MACROPARAMETERS, MODIFIERASSIGNMENTS, FACEPLATE,
    MODMATRIX (for instruments).
    """
    el = ET.SubElement(parent, "PLUGIN")
    plugin_id = ids.next()
    # Resolve friendly names (e.g. "4OSC") to type codes (e.g. "4osc") if
    # the model holds a friendly name. Pass-through if already a code.
    from .tools.preset_library import BUILTIN_PLUGIN_TYPES
    type_code = BUILTIN_PLUGIN_TYPES.get(p.type, p.type)
    _attr(el, type=type_code, id=plugin_id, enabled=p.enabled)
    # `frozen="0"` is on every plugin in real Waveform output; omit on the
    # implicit volume/level which we emit elsewhere without it for backwards
    # compat with our existing reference fixture.
    if type_code not in ("volume", "level"):
        _attr(el, frozen=False)

    # Plugin params as direct attributes
    for k, v in p.params.items():
        _attr(el, **{k: v})

    if p.preset_path:
        _attr(el, presetPath=p.preset_path)

    # MACROPARAMETERS — emit children if any macros are defined.
    macros_el = ET.SubElement(el, "MACROPARAMETERS")
    _attr(macros_el, id=plugin_id)
    for m in p.macros:
        _attr(ET.SubElement(macros_el, "MACROPARAMETER"),
              id=m.id, name=m.name,
              value=float(m.value), defaultValue=float(m.default_value))
    # MODIFIERASSIGNMENTS — links to track-level Modifiers per parameter.
    # Schema: <LFO source="<modifier-id>" paramID="<param>" value="1.0"/>
    mod_assign_el = ET.SubElement(el, "MODIFIERASSIGNMENTS")
    for ma in p.modifier_assignments:
        ma_tag = {"lfo":"LFO", "envelope":"ENVELOPEFOLLOWER",
                  "random":"RANDOM", "step_lfo":"STEPLFO",
                  "midi_tracker":"MIDITRACKER"}.get(ma.modifier_type, "LFO")
        _attr(ET.SubElement(mod_assign_el, ma_tag),
              source=ma.modifier_id,
              paramID=ma.param_id,
              value=float(ma.value))
    # Instruments also need FACEPLATE + MODMATRIX.
    if type_code in ("4osc", "wavetable", "microDrumSampler", "multiSampler"):
        _attr(ET.SubElement(el, "FACEPLATE"), width=8, height=4, autoSize=True)
        modmatrix = ET.SubElement(el, "MODMATRIX")
        for item in p.modmatrix:
            _attr(ET.SubElement(modmatrix, "MODMATRIXITEM"), **item)

    # Sampler plugin: emit <SOUND> children (schema from tracktion_engine
    # SamplerPlugin.cpp). One SOUND per sample-to-key-range mapping.
    # Sampler doesn't decode MP3 — auto-convert source files to a cached WAV.
    if type_code == "sampler":
        from .audio_convert import ensure_wav
        for s in p.sounds:
            sound = dict(s)
            src = sound.get("source")
            if src:
                try:
                    sound["source"] = ensure_wav(src)
                except Exception:
                    # If conversion fails (no ffmpeg, bad file), pass through —
                    # the user can see the path in the edit and diagnose.
                    pass
            _attr(ET.SubElement(el, "SOUND"), **sound)
    return el


def _build_automation_lane(plugin_elements: dict, lane) -> None:
    """Attach an <AUTOMATIONCURVE> to the right plugin element.

    target conventions:
      "volume"                       -> DISABLED (silences track in Waveform)
      "pan"                          -> implicit volume plugin, paramID "pan"
      "plugin/<plugin_id>/<param>"   -> named user plugin, given paramID

    Tracktion's curve XML (captured from a hand-edited fixture):
      <AUTOMATIONCURVE paramID="pan">
        <POINT b="0" v="-0.5" c="0"/>
        <POINT b="4" v="0.5" c="0"/>
      </AUTOMATIONCURVE>

    Volume curves are skipped here as a defensive layer — even though the
    high-level tools refuse them, an in-memory Edit loaded from disk could
    still contain one. We don't have a verified curve schema for the
    volume plugin yet (every attempt silences the track).
    """
    target = lane.target
    if target == "volume":
        return  # see comment above
    if target == "pan":
        plugin_el = plugin_elements.get("__volume__")
        param_id = target
    elif target.startswith("plugin/"):
        # plugin/<id>/<param>
        _, plugin_ref, param_id = target.split("/", 2)
        plugin_el = plugin_elements.get(plugin_ref)
    else:
        return  # unknown target — skip silently rather than corrupt the file
    if plugin_el is None:
        return
    curve = ET.SubElement(plugin_el, "AUTOMATIONCURVE")
    _attr(curve, paramID=param_id)
    for pt in lane.points:
        _attr(ET.SubElement(curve, "POINT"), b=float(pt.beat), v=float(pt.value), c=float(pt.curve))


def _build_send(parent: ET.Element, s: Send) -> None:
    # SPECULATIVE — exact element name pending fixture capture
    _attr(
        ET.SubElement(parent, "SEND"),
        dest=s.dest_track_id,
        amountDb=s.amount_db,
        prePost=s.pre_post,
    )


def _build_view_state(parent: ET.Element, edit: Edit) -> None:
    _attr(
        ET.SubElement(parent, "VIEWSTATE"),
        minimalTransportBar=True,
        hiddenClips="",
        lockedClips="",
        chordTrackVisible=False,
        arrangerTrackVisible=False,
        enabledTrackTags="",
        currentSidePanel="Search",
        browserSelection="result/all",
        viewleft=-1.0,
        viewright=float(edit.length_beats / 2.0),
    )


# ---------- top-level ----------


def to_xml(edit: Edit) -> ET.ElementTree:
    """Serialize the Edit. Element order matches a real Waveform 13.5 save."""
    ids = _IdAllocator()

    root = ET.Element("EDIT")
    now_ms = int(time.time() * 1000)
    _attr(
        root,
        appVersion="Waveform 13.5.25",
        projectID=edit.id,
        creationTime=now_ms,
        lastSignificantChange=hex(now_ms)[2:],  # Waveform uses a hex-ish id; placeholder
        modifiedBy="waveform-mcp",
    )

    # Order from real fixture:
    _build_transport(root)
    _build_pitch_sequence(root, edit)
    _build_tempo_sequence(root, edit)
    ET.SubElement(root, "VIDEO")
    _attr(ET.SubElement(root, "CLICKTRACK"), level=0.6)
    _build_master_volume(root, ids, db=getattr(edit, "master_volume_db", 0.0))
    ET.SubElement(root, "RACKS")
    master_plugins = ET.SubElement(root, "MASTERPLUGINS")
    for p in edit.master_chain:
        _build_plugin(master_plugins, p, ids)
    _build_input_devices(root)
    ET.SubElement(root, "TRACKCOMPS")

    # Skeleton tracks Waveform always emits
    _attr(ET.SubElement(root, "ARRANGERTRACK"), name="Arranger", id=ids.next())
    _attr(ET.SubElement(root, "CHORDTRACK"), name="Chord", id=ids.next())
    _build_marker_track(root, edit, ids)
    tempotrack = ET.SubElement(root, "TEMPOTRACK")
    _attr(tempotrack, name="Global", id=ids.next())
    ET.SubElement(tempotrack, "MODIFIERS")
    _build_master_track(root, edit, ids)

    # User tracks
    for i, t in enumerate(edit.tracks):
        _build_track(root, t, i, ids)

    # Trailers
    _build_view_state(root, edit)
    ET.SubElement(root, "EDITMIXGROUPS")
    ET.SubElement(root, "SCENES")
    ET.SubElement(root, "AUDIOEDITING")
    ET.SubElement(root, "AUTOMAPXML")

    return ET.ElementTree(root)


def write(edit: Edit, path: str) -> None:
    tree = to_xml(edit)
    ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def to_string(edit: Edit) -> str:
    tree = to_xml(edit)
    ET.indent(tree, space="  ")
    return ET.tostring(tree.getroot(), encoding="unicode")
