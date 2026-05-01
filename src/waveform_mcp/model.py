"""In-memory Edit model.

Every mutation flows through `EditStore.apply(op)`. The store snapshots before/after
state and emits a diff event so subscribers (preview UI, JSONL log, host narration)
stay in sync without each tool implementation having to remember to publish.

This is the single source of truth while a session is running. Serialization to
.tracktionedit XML happens in xml_writer.py; round-tripping back happens in
xml_reader.py. The model is deliberately *not* a faithful 1:1 of the Tracktion
schema — it's the shape the LLM tools want to work with, projected onto the XML
on save.
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


# ---------- leaf types ----------


@dataclass
class Note:
    """A MIDI note in a clip.

    Schema confirmed from tracktion_engine source (tracktion_MidiList.cpp):
    XML attrs are p (pitch), b (beat), l (length-in-beats), v (velocity),
    c (colour — NOT channel; channel lives on the parent <SEQUENCE>).
    Optional: lift (note-off velocity), pres (pressure), bend (pitch bend).
    """
    pitch: int                  # MIDI 0-127
    start_beats: float
    length_beats: float
    velocity: int = 100         # 0-127
    colour: int = 0             # display tint, 0..N
    lift: int | None = None     # note-off velocity (0-127), optional
    pres: int | None = None     # pressure, optional
    bend: float | None = None   # pitch bend, optional


@dataclass
class Plugin:
    id: str
    type: str                   # "4OSC" | "Wavetable" | "MicroDrumSampler" | "Multisampler" | vst3-uid
    name: str
    params: dict[str, Any] = field(default_factory=dict)
    # Mod matrix items: list of {modParam, modItem, modDepth}. Captured from
    # real .trkpreset files; empty by default. The writer emits a populated
    # <MODMATRIX> when this is non-empty, otherwise <MODMATRIX/>.
    modmatrix: list[dict[str, Any]] = field(default_factory=list)
    # Sampler SOUND children. Each entry maps an audio file to a MIDI key range:
    #   {source, name?, keyNote, minNote, maxNote, gainDb?, pan?, startTime?,
    #    length?, openEnded?}
    # Schema captured from tracktion_engine/plugins/effects/tracktion_SamplerPlugin.cpp
    sounds: list[dict[str, Any]] = field(default_factory=list)
    preset_path: str | None = None
    enabled: bool = True
    # Track-level Modifier assignments routed to this plugin's parameters.
    modifier_assignments: list[Any] = field(default_factory=list)   # list[ModifierAssignment]
    # Macro parameters exposed by this plugin.
    macros: list[Any] = field(default_factory=list)                 # list[Macro]


@dataclass
class Send:
    dest_track_id: str
    amount_db: float = 0.0
    pre_post: Literal["pre", "post"] = "post"


@dataclass
class Marker:
    name: str
    start_beats: float
    length_beats: float = 0.0


@dataclass
class Modifier:
    """A track-level modulation source — LFO, envelope, random, etc.
    Lives under <TRACK>/<MODIFIERS> in the .tracktionedit XML. Plugin
    parameters reference these via ModifierAssignment.

    Schema captured from tracktion_engine OldEditConversion.h:
      <LFO id syncType depth phase rateType rate/>
    """
    id: str
    type: Literal["lfo", "envelope", "random", "step_lfo", "midi_tracker"] = "lfo"
    depth: float = 0.5            # 0..1
    rate: float = 1.0             # Hz when rateType=0, beat multiplier when rateType=1
    rate_type: int = 1            # 0=time-based (Hz), 1=beat-synced
    sync_type: int = 1            # 1=transport sync
    phase: float = 0.0            # 0..1 (normalized phase offset)
    shape: str = "sine"           # for LFO: sine, triangle, square, saw


@dataclass
class ModifierAssignment:
    """Links a parameter on a plugin to a track-level Modifier.
    Lives under <PLUGIN>/<MODIFIERASSIGNMENTS> in the XML.

      <LFO source="<modifier-id>" paramID="filterFreq" value="1.0"/>
    """
    modifier_id: str       # references Track.modifiers[N].id
    param_id: str          # the plugin's param name (e.g. "filterFreq")
    value: float = 1.0     # depth scaling; typically 1.0
    modifier_type: Literal["lfo", "envelope", "random", "step_lfo", "midi_tracker"] = "lfo"


@dataclass
class Macro:
    """A macro parameter on a plugin — single-knob control mapped to one
    or more underlying params. Lives under
    <PLUGIN>/<MACROPARAMETERS>/<MACROPARAMETER>.
    """
    id: str
    name: str = "Macro"
    value: float = 0.0           # current 0..1 value
    default_value: float = 0.0


@dataclass
class StepClipChannel:
    """One drum row in a StepClip. (channel, note) define what plays."""
    channel: int = 1
    note: int = 60               # MIDI pitch this row triggers
    velocity: int = 127          # default velocity for this row
    name: str = "C4"             # display name


@dataclass
class StepClipPattern:
    """One programmed pattern. Each row in `rows` is for the corresponding
    StepClipChannel — a string of '0'/'1' per step + a list of velocities
    per step. numNotes = total step count across all rows; noteLength is
    the duration of each step in beats.
    """
    note_length: float = 1.0    # beats per step (0.25 = 16ths)
    rows: list[dict] = field(default_factory=list)
    # Each row dict: {pattern: "10101010...", velocities: [127, 127, ...]}


@dataclass
class StepClip:
    """A drum-machine step clip — alternative to MidiClip for grid-based
    drum programming. Schema captured from a real Waveform fixture.
    """
    id: str
    name: str
    start_beats: float
    length_beats: float
    channels: list[StepClipChannel] = field(default_factory=list)
    patterns: list[StepClipPattern] = field(default_factory=list)
    repeat_sequence: bool = True
    sequence_index: int = 0
    kind: Literal["step"] = "step"


@dataclass
class ChordSymbol:
    """A chord placed on the timeline. Used by chord-chart export and
    written by composers that know what chord they're playing.
    """
    name: str               # 'Am', 'Cmaj7', 'F/A', etc.
    roman: str              # 'i', 'IV', 'bVI', 'V7'
    start_beats: float
    length_beats: float
    root_midi: int = 0      # informational


# ---------- automation ----------


@dataclass
class AutomationPoint:
    """One point on an automation curve.

    `value` is in the param's natural unit (dB for volume, -1..1 for pan,
    0..1 for plugin params). `curve` shapes the segment leading into this
    point: 0 = linear, ±1 = exponential bend.
    """
    beat: float
    value: float
    curve: float = 0.0


@dataclass
class AutomationLane:
    """A single automation curve attached to a track.

    `target` selects what's being automated:
      - "volume" — track volume in dB (the implicit volume plugin)
      - "pan"    — track pan -1..1 (same plugin)
      - "plugin/<plugin_id>/<param>" — any param on a track plugin
    """
    target: str
    points: list[AutomationPoint] = field(default_factory=list)


# ---------- clips ----------


@dataclass
class MidiClip:
    id: str
    name: str
    start_beats: float
    length_beats: float
    notes: list[Note] = field(default_factory=list)
    kind: Literal["midi"] = "midi"
    # Optional clip-level mix + fades. Tracktion ramps note velocities
    # across MIDI clip fade regions.
    gain_db: float = 0.0
    fade_in_beats: float = 0.0
    fade_out_beats: float = 0.0


@dataclass
class AudioClip:
    id: str
    name: str
    start_beats: float
    length_beats: float
    file_path: str
    warp_mode: Literal["none", "stretch", "warp"] = "none"
    kind: Literal["audio"] = "audio"
    # Per-clip mix and edit fields. All optional, default to no-effect values.
    gain_db: float = 0.0                # clip-level gain, on top of track volume
    fade_in_beats: float = 0.0          # linear fade-in duration
    fade_out_beats: float = 0.0         # linear fade-out duration
    offset_in_source_beats: float = 0.0 # skip this many beats into the source file


Clip = MidiClip | AudioClip | StepClip


# ---------- tracks ----------


@dataclass
class Track:
    id: str
    name: str
    kind: Literal["audio", "midi", "folder", "submix", "automation"]
    volume_db: float = 0.0
    pan: float = 0.0            # -1..1
    mute: bool = False
    solo: bool = False
    plugins: list[Plugin] = field(default_factory=list)
    clips: list[Clip] = field(default_factory=list)
    sends: list[Send] = field(default_factory=list)
    output: str = "master"      # track id or "master"
    automation: list[AutomationLane] = field(default_factory=list)
    modifiers: list[Modifier] = field(default_factory=list)


# ---------- root ----------


@dataclass
class Edit:
    id: str = field(default_factory=lambda: _id("edit"))
    name: str = "Untitled"
    tempo: float = 120.0
    time_sig_num: int = 4
    time_sig_denom: int = 4
    key_root: int = 0           # 0=C, 1=C#, ..., 11=B
    key_mode: Literal["major", "minor"] = "major"
    length_beats: float = 64.0
    # Master volume in dB. 0 = unity. Positive = louder, negative = softer.
    master_volume_db: float = 0.0
    # Optional additional tempo automation points: list of (start_beat, bpm).
    # Used to bump tempo at a climax section or insert a ritard at outro.
    tempo_changes: list[tuple[float, float]] = field(default_factory=list)
    tracks: list[Track] = field(default_factory=list)
    markers: list[Marker] = field(default_factory=list)
    master_chain: list[Plugin] = field(default_factory=list)
    progression: list[ChordSymbol] = field(default_factory=list)

    # ---- lookup helpers (read-only) ----

    def track(self, track_id: str) -> Track:
        for t in self.tracks:
            if t.id == track_id:
                return t
        raise KeyError(f"track not found: {track_id}")

    def clip(self, clip_id: str) -> Clip:
        for t in self.tracks:
            for c in t.clips:
                if c.id == clip_id:
                    return c
        raise KeyError(f"clip not found: {clip_id}")

    def summary(self) -> dict[str, Any]:
        """JSON-serializable overview for the LLM."""
        return {
            "id": self.id,
            "name": self.name,
            "tempo": self.tempo,
            "time_signature": [self.time_sig_num, self.time_sig_denom],
            "key": [self.key_root, self.key_mode],
            "length_beats": self.length_beats,
            "tracks": [
                {
                    "id": t.id,
                    "name": t.name,
                    "kind": t.kind,
                    "volume_db": t.volume_db,
                    "pan": t.pan,
                    "mute": t.mute,
                    "solo": t.solo,
                    "plugins": [{"id": p.id, "type": p.type, "name": p.name} for p in t.plugins],
                    "clips": [
                        {
                            "id": c.id,
                            "kind": c.kind,
                            "name": c.name,
                            "start": c.start_beats,
                            "length": c.length_beats,
                            "note_count": len(c.notes) if isinstance(c, MidiClip) else None,
                        }
                        for c in t.clips
                    ],
                }
                for t in self.tracks
            ],
            "markers": [
                {"name": m.name, "start": m.start_beats, "length": m.length_beats}
                for m in self.markers
            ],
        }


# ---------- mutation store ----------


class EditStore:
    """Holds the current Edit, a stack of snapshots for undo, and a list of
    subscribers that get called with (op_name, args, before, after) on every
    successful apply.
    """

    def __init__(self) -> None:
        self.edit: Edit = Edit()
        self._undo: list[Edit] = []
        self._subscribers: list[Any] = []  # callables: (op, args, before, after) -> None

    def subscribe(self, fn) -> None:
        self._subscribers.append(fn)

    def apply(self, op: str, args: dict, fn) -> Any:
        """Run `fn(self.edit, **args)`, snapshot before/after, notify subscribers.

        The op function is responsible for mutation. It returns whatever the tool
        wants to surface to the LLM (often the new id of a created entity).
        """
        before = copy.deepcopy(self.edit)
        try:
            result = fn(self.edit, **args)
        except Exception:
            self.edit = before  # roll back any partial mutation
            raise
        after_snapshot = copy.deepcopy(self.edit)
        self._undo.append(before)
        if len(self._undo) > 200:
            self._undo.pop(0)
        for sub in self._subscribers:
            try:
                sub(op, args, before, after_snapshot)
            except Exception:
                # subscribers must not break mutation flow
                pass
        return result

    def undo(self) -> bool:
        if not self._undo:
            return False
        self.edit = self._undo.pop()
        return True

    def replace(self, edit: Edit) -> None:
        """Used when loading from disk — bypasses undo stack intentionally."""
        self.edit = edit
        self._undo.clear()


# Singleton — the MCP server is a single-session process.
STORE = EditStore()
