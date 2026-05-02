"""CompositionSpec — pure data model for an analysis-driven composition.

The spec sits between the music analyzer (which observes a reference song)
and the Waveform builder (which constructs a .tracktionedit). It carries
*everything* the builder needs to assemble a balanced, structurally-faithful
edit, with no DAW or analyzer dependencies — so it's:

  * trivial to construct from analyzer JSON
  * trivial to hand-edit / serialise / round-trip
  * fully unit-testable in isolation

Design rules:

  * No DAW imports here. Pure dataclasses + serialisation only.
  * No analyzer imports here. Adapter lives elsewhere; this module just
    knows what it needs.
  * Every field has a sensible default so partial specs are usable.
  * `validate()` returns problems instead of raising — the builder can
    decide whether to bail or paper over.
  * `to_dict()` / `from_dict()` are lossless — serialisation is for
    persistence + inspection, not lossy summary.

Beat units throughout: positions and lengths are in *quarter-note beats*
unless explicitly suffixed (e.g. `length_bars`). 1 bar in 4/4 = 4 beats.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Sub-models                                                                  #
# --------------------------------------------------------------------------- #


SectionType = Literal[
    "intro",
    "verse",
    "pre_chorus",
    "chorus",
    "bridge",
    "breakdown",
    "build",
    "drop",
    "solo",
    "outro",
    "transition",
    # Catch-alls for analyzer outputs we don't have a clean mapping for
    "unknown",
]


# Roles we balance in MIX_BALANCE. Adding a role here means it can be
# routed through the builder; balance values come from music_theory_data.
TrackRole = Literal[
    "drums",
    "bass",
    "sub_bass",
    "rhythm_guitar",
    "rhythm_guitar_l",
    "rhythm_guitar_r",
    "lead",
    "melody",
    "counter",
    "pad",
    "keys",
    "arp",
    "ambience",
    "fx",
]


@dataclass
class SectionSpec:
    """One musical section in the form (intro, verse, chorus, ...).

    `start_beat` and `length_beats` are absolute beats from song start.
    `density` is a 0..1 hint of activity (used by the builder to decide
    whether to lay drums sparsely vs dense).
    `instrumentation` is the *subset* of TrackRoles that are active in
    this section — empty list means "all roles play". This is how we
    express "verse drops the lead" or "intro is just pad + drums".
    """

    type: SectionType
    start_beat: float
    length_beats: float
    density: float = 0.5            # 0=quiet/sparse, 1=peak
    brightness: float = 0.5         # 0=dark, 1=bright (drives preset choice)
    instrumentation: list[TrackRole] = field(default_factory=list)
    # Free-text label from analyzer (e.g., "Chorus (peak)")
    label: str = ""


@dataclass
class ChordEvent:
    """A chord placement on the timeline. `root_pc` is pitch class 0..11
    (0=C). `quality` is short label: "maj", "min", "7", "min7", "maj7",
    "dim", "sus4", etc.
    """

    start_beat: float
    length_beats: float
    root_pc: int                     # 0..11
    quality: str = "maj"
    # Optional extension data — bass note (slash chord), inversion
    bass_pc: int | None = None       # if set, voice with this pitch class on bottom
    inversion: int = 0               # 0=root position, 1=first, 2=second


@dataclass
class NoteEvent:
    """A single MIDI note on a track. `pitch` is MIDI note number (60=C4)."""

    start_beat: float
    length_beats: float
    pitch: int                       # MIDI 0..127
    velocity: int = 100              # 1..127
    # Optional channel / track-local id, ignored by basic builder
    channel: int = 0


@dataclass
class DrumPatternSpec:
    """A drum pattern is just a list of NoteEvents using General MIDI
    drum keys (kick=36, snare=38, closed-hat=42, open-hat=46, crash=49,
    ride=51, etc.). The builder loops it through the section."""

    notes: list[NoteEvent] = field(default_factory=list)
    loop_length_beats: float = 4.0   # one bar in 4/4
    # Optional per-section overrides keyed by section index in
    # CompositionSpec.sections. If unset, the same pattern applies
    # everywhere this DrumPatternSpec is referenced.
    section_variants: dict[int, list[NoteEvent]] = field(default_factory=dict)
    # Beat at which drums first enter. Iterations of the loop whose
    # entire bar falls before this point are skipped. Use to delay
    # drums past an intro section.
    start_beat: float = 0.0
    # Beat at which drums stop. None means "play to end of song".
    end_beat: float | None = None


@dataclass
class PluginChainSpec:
    """An ordered chain of plugins for a single track.

    `plugins` is a list of plugin names/types in signal order. Each
    entry can be:
      * a built-in name: "sampler", "4osc", "3bandEq", "comp", "limiter"
      * a VST name: "sforzando", "Anvil", "MT-A", "TSC 1.1"

    `params` is a parallel dict-list — params[i] is the param dict for
    plugins[i]. For VSTs, params can carry `state` (base64 plugin
    state) which the writer will pass through verbatim.
    """

    plugins: list[str] = field(default_factory=list)
    params: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Pad params to match plugins length — caller-friendly
        while len(self.params) < len(self.plugins):
            self.params.append({})


@dataclass
class TrackSpec:
    """One track in the final edit."""

    name: str
    role: TrackRole
    kind: Literal["midi", "audio"] = "midi"
    pan: float = 0.0
    chain: PluginChainSpec = field(default_factory=PluginChainSpec)
    # Optional per-track mix offset in dB on top of role/genre lookup
    mix_offset_db: float = 0.0
    # Per-track note content (MIDI tracks). For drums, this is usually
    # empty and the drum_pattern is consulted instead.
    notes: list[NoteEvent] = field(default_factory=list)
    # Drum tracks reference their pattern here
    drum_pattern: DrumPatternSpec | None = None
    # Audio tracks reference a source file
    audio_path: str | None = None
    audio_offset_beats: float = 0.0
    # ---- production-tone hints (Phase B2 will populate) ----
    # Reverb estimate for this track: rt60_seconds, wet_mix (0..1).
    # Builder maps these onto a plate reverb plugin.
    reverb_rt60_s: float | None = None
    reverb_wet: float | None = None
    # Whether this track is sidechain-pumped by drums. When True,
    # the builder adds a kick-triggered ducking compressor (or, if
    # full sidechaining isn't available, a 4-on-floor amplitude LFO).
    sidechain_to_drums: bool = False
    # ---- automation hints (Phase B3 will populate) ----
    # If non-empty, lists of (beat, value) breakpoints for volume_db
    # and pan automation respectively. Builder writes them as
    # AutomationLane records.
    volume_automation: list[tuple[float, float]] = field(default_factory=list)
    pan_automation: list[tuple[float, float]] = field(default_factory=list)


@dataclass
class CompositionSpec:
    """The full spec — everything the builder needs to construct an Edit."""

    # ---- global ----
    name: str = "Untitled"
    tempo_bpm: float = 120.0
    time_sig_num: int = 4
    time_sig_denom: int = 4
    key_root_pc: int = 0             # 0..11 (0=C)
    key_mode: Literal["major", "minor"] = "major"
    # Genre key for MIX_BALANCE lookup (e.g., "metal", "lofi", "pop")
    genre: str = "pop"

    # ---- form ----
    sections: list[SectionSpec] = field(default_factory=list)

    # ---- harmony ----
    chord_track: list[ChordEvent] = field(default_factory=list)

    # ---- per-track content ----
    tracks: list[TrackSpec] = field(default_factory=list)

    # ---- master bus ----
    master_chain: PluginChainSpec = field(default_factory=PluginChainSpec)

    # ---- provenance ----
    # Free-form notes about how this spec was built (analyzer version,
    # reference path, fidelity caveats). Carried through for debugging.
    provenance: dict[str, Any] = field(default_factory=dict)

    # ---- derived helpers ----

    @property
    def total_length_beats(self) -> float:
        """End beat of the last section, or 0 if no sections."""
        if not self.sections:
            return 0.0
        return max(s.start_beat + s.length_beats for s in self.sections)

    @property
    def total_length_bars(self) -> float:
        return self.total_length_beats / self.time_sig_num

    # ---- serialisation ----

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CompositionSpec":
        """Reconstruct a CompositionSpec from a plain dict (typically
        produced by `to_dict()`). Tolerant: missing fields fall back to
        defaults; unknown fields are ignored with a warning."""
        if not isinstance(data, dict):
            raise TypeError(f"expected dict, got {type(data).__name__}")

        def _parse_section(d: dict) -> SectionSpec:
            return SectionSpec(
                type=d.get("type", "unknown"),
                start_beat=float(d.get("start_beat", 0.0)),
                length_beats=float(d.get("length_beats", 0.0)),
                density=float(d.get("density", 0.5)),
                brightness=float(d.get("brightness", 0.5)),
                instrumentation=list(d.get("instrumentation", [])),
                label=str(d.get("label", "")),
            )

        def _parse_chord(d: dict) -> ChordEvent:
            return ChordEvent(
                start_beat=float(d["start_beat"]),
                length_beats=float(d["length_beats"]),
                root_pc=int(d["root_pc"]),
                quality=str(d.get("quality", "maj")),
                bass_pc=(int(d["bass_pc"]) if d.get("bass_pc") is not None else None),
                inversion=int(d.get("inversion", 0)),
            )

        def _parse_note(d: dict) -> NoteEvent:
            return NoteEvent(
                start_beat=float(d["start_beat"]),
                length_beats=float(d["length_beats"]),
                pitch=int(d["pitch"]),
                velocity=int(d.get("velocity", 100)),
                channel=int(d.get("channel", 0)),
            )

        def _parse_drum(d: dict | None) -> DrumPatternSpec | None:
            if d is None:
                return None
            return DrumPatternSpec(
                notes=[_parse_note(n) for n in d.get("notes", [])],
                loop_length_beats=float(d.get("loop_length_beats", 4.0)),
                section_variants={
                    int(k): [_parse_note(n) for n in v]
                    for k, v in (d.get("section_variants") or {}).items()
                },
                start_beat=float(d.get("start_beat", 0.0)),
                end_beat=(float(d["end_beat"]) if d.get("end_beat") is not None else None),
            )

        def _parse_chain(d: dict | None) -> PluginChainSpec:
            d = d or {}
            return PluginChainSpec(
                plugins=list(d.get("plugins", [])),
                params=list(d.get("params", [])),
            )

        def _parse_track(d: dict) -> TrackSpec:
            return TrackSpec(
                name=str(d.get("name", "")),
                role=d.get("role", "pad"),
                kind=d.get("kind", "midi"),
                pan=float(d.get("pan", 0.0)),
                chain=_parse_chain(d.get("chain")),
                mix_offset_db=float(d.get("mix_offset_db", 0.0)),
                notes=[_parse_note(n) for n in d.get("notes", [])],
                drum_pattern=_parse_drum(d.get("drum_pattern")),
                audio_path=d.get("audio_path"),
                audio_offset_beats=float(d.get("audio_offset_beats", 0.0)),
                reverb_rt60_s=d.get("reverb_rt60_s"),
                reverb_wet=d.get("reverb_wet"),
                sidechain_to_drums=bool(d.get("sidechain_to_drums", False)),
                volume_automation=[
                    (float(t), float(v)) for t, v in d.get("volume_automation", [])
                ],
                pan_automation=[
                    (float(t), float(v)) for t, v in d.get("pan_automation", [])
                ],
            )

        spec = cls(
            name=str(data.get("name", "Untitled")),
            tempo_bpm=float(data.get("tempo_bpm", 120.0)),
            time_sig_num=int(data.get("time_sig_num", 4)),
            time_sig_denom=int(data.get("time_sig_denom", 4)),
            key_root_pc=int(data.get("key_root_pc", 0)),
            key_mode=data.get("key_mode", "major"),
            genre=str(data.get("genre", "pop")),
            sections=[_parse_section(s) for s in data.get("sections", [])],
            chord_track=[_parse_chord(c) for c in data.get("chord_track", [])],
            tracks=[_parse_track(t) for t in data.get("tracks", [])],
            master_chain=_parse_chain(data.get("master_chain")),
            provenance=dict(data.get("provenance", {})),
        )
        return spec

    @classmethod
    def from_json(cls, raw: str) -> "CompositionSpec":
        return cls.from_dict(json.loads(raw))

    # ---- validation ----

    def validate(self) -> list[str]:
        """Return a list of human-readable problems with this spec.
        Empty list = ok. The builder calls this before constructing the
        edit; non-empty results are logged but don't necessarily abort
        — the caller decides.
        """
        problems: list[str] = []

        # Globals
        if not (20 <= self.tempo_bpm <= 400):
            problems.append(f"tempo_bpm out of range: {self.tempo_bpm}")
        if self.time_sig_num <= 0 or self.time_sig_denom <= 0:
            problems.append(f"invalid time signature: {self.time_sig_num}/{self.time_sig_denom}")
        if not (0 <= self.key_root_pc <= 11):
            problems.append(f"key_root_pc out of range: {self.key_root_pc}")
        if self.key_mode not in ("major", "minor"):
            problems.append(f"key_mode invalid: {self.key_mode!r}")

        # Sections
        if self.sections:
            sorted_secs = sorted(self.sections, key=lambda s: s.start_beat)
            for i, s in enumerate(sorted_secs):
                if s.length_beats <= 0:
                    problems.append(f"section[{i}] {s.type}: non-positive length {s.length_beats}")
                if s.start_beat < 0:
                    problems.append(f"section[{i}] {s.type}: negative start_beat {s.start_beat}")
                if not (0.0 <= s.density <= 1.0):
                    problems.append(f"section[{i}] density out of [0,1]: {s.density}")
                if i + 1 < len(sorted_secs):
                    nxt = sorted_secs[i + 1]
                    if s.start_beat + s.length_beats > nxt.start_beat + 1e-3:
                        problems.append(
                            f"section[{i}] {s.type} overlaps section[{i+1}] {nxt.type}"
                        )

        # Chord track
        if self.chord_track:
            for i, c in enumerate(self.chord_track):
                if c.length_beats <= 0:
                    problems.append(f"chord[{i}]: non-positive length {c.length_beats}")
                if not (0 <= c.root_pc <= 11):
                    problems.append(f"chord[{i}]: root_pc out of range {c.root_pc}")
                if c.bass_pc is not None and not (0 <= c.bass_pc <= 11):
                    problems.append(f"chord[{i}]: bass_pc out of range {c.bass_pc}")

        # Tracks
        for i, t in enumerate(self.tracks):
            if t.kind == "audio" and not t.audio_path:
                problems.append(f"track[{i}] {t.name}: audio kind but no audio_path")
            if t.kind == "midi" and t.audio_path:
                problems.append(f"track[{i}] {t.name}: midi kind but audio_path is set")
            if not (-1.0 <= t.pan <= 1.0):
                problems.append(f"track[{i}] {t.name}: pan out of [-1,1]: {t.pan}")
            for j, n in enumerate(t.notes):
                if not (0 <= n.pitch <= 127):
                    problems.append(f"track[{i}] note[{j}]: pitch out of MIDI range {n.pitch}")
                if not (1 <= n.velocity <= 127):
                    problems.append(f"track[{i}] note[{j}]: velocity out of [1,127] {n.velocity}")

        return problems

    def is_valid(self) -> bool:
        return not self.validate()
