"""Build a new lofi track inspired by Ecstasy's harmonic palette.

Ecstasy is in F major and built around maj7/min7/add9 chords:
  Fmaj7, F#maj7, A#maj7, A#min7, Cmaj7, G#add9, G#7, A#add9

We lift the maj7 vocabulary, slow down to 75 BPM, and build a 56-bar
lofi structure: intro → verse → bridge → chorus → outro.

Run from repo root:
  PYTHONPATH=src python scripts/lofi_from_ecstasy.py
"""
import sys
sys.path.insert(0, "src")

from waveform_mcp.composition_spec import (
    ChordEvent, CompositionSpec, DrumPatternSpec, NoteEvent,
    PluginChainSpec, SectionSpec, TrackSpec,
)
from waveform_mcp.tools.compose_from_spec import compose_from_spec
from waveform_mcp.tools.waveform_app import waveform_launch


# --- 4-bar harmonic loop drawn from Ecstasy's most-used chords ---
# Each chord lasts one bar (4 beats). Using the borrowed-mode flavor
# Ecstasy leans on (♭III, ♭vii) for that lofi/jazz tinge.
LOOP = [
    (0, "maj7"),    # Fmaj7   (I)
    (10, "maj7"),   # A#maj7  (♭III)
    (7, "maj7"),    # Cmaj7   (V — major 7 to keep the dreamy feel)
    (10, "min7"),   # A#min7  (♭iii — minor color)
]
LOOP_BARS = 4
BAR_BEATS = 4

# --- Form ---
# intro 8 + verse 16 + bridge 8 + chorus 16 + outro 8 = 56 bars
SECTIONS = [
    ("intro",  8,  0.30),  # sparse: keys + pad only
    ("verse",  16, 0.65),  # add bass + drums
    ("bridge", 8,  0.45),  # drop drums, melody fronts
    ("chorus", 16, 0.85),  # full kit
    ("outro",  8,  0.30),  # fade out
]

# --- Build chord track: loop the 4-bar progression across the song ---
def build_chord_track(total_bars: int) -> list[ChordEvent]:
    chords = []
    bar = 0
    while bar < total_bars:
        for i, (root_pc, qual) in enumerate(LOOP):
            if bar >= total_bars:
                break
            chords.append(ChordEvent(
                start_beat=float(bar * BAR_BEATS),
                length_beats=float(BAR_BEATS),
                root_pc=root_pc, quality=qual,
            ))
            bar += 1
    return chords


# --- Build sections + their start positions ---
def build_sections() -> tuple[list[SectionSpec], int]:
    out = []
    cursor_bar = 0
    for sec_type, n_bars, density in SECTIONS:
        out.append(SectionSpec(
            type=sec_type,  # type: ignore[arg-type]
            start_beat=float(cursor_bar * BAR_BEATS),
            length_beats=float(n_bars * BAR_BEATS),
            density=density,
            brightness=0.4,  # lofi = warm/dark
        ))
        cursor_bar += n_bars
    return out, cursor_bar


# --- Lofi drum pattern: dusty, half-time feel ---
# Kick on 1, soft snare on 3, hi-hat with offset for swing.
LOFI_DRUMS = [
    NoteEvent(start_beat=0.00, length_beats=0.25, pitch=36, velocity=95),  # Kick
    NoteEvent(start_beat=2.00, length_beats=0.25, pitch=38, velocity=80),  # Snare
    # Slightly behind-the-beat hat (offset to give swing feel)
    NoteEvent(start_beat=0.50, length_beats=0.10, pitch=42, velocity=55),
    NoteEvent(start_beat=1.00, length_beats=0.10, pitch=42, velocity=65),
    NoteEvent(start_beat=1.50, length_beats=0.10, pitch=42, velocity=50),
    NoteEvent(start_beat=2.50, length_beats=0.10, pitch=42, velocity=55),
    NoteEvent(start_beat=3.00, length_beats=0.10, pitch=42, velocity=65),
    NoteEvent(start_beat=3.50, length_beats=0.10, pitch=42, velocity=50),
]


# --- Bass line: root note per chord, octave 2 ---
def build_bass(chords: list[ChordEvent]) -> list[NoteEvent]:
    notes = []
    for c in chords:
        # Root + occasionally fifth on beat 3 of each bar
        notes.append(NoteEvent(
            start_beat=c.start_beat,
            length_beats=c.length_beats * 0.8,
            pitch=36 + c.root_pc, velocity=80,  # MIDI 36 = C2
        ))
        # Fifth on beat 3 for a little movement
        notes.append(NoteEvent(
            start_beat=c.start_beat + 2.0,
            length_beats=1.5,
            pitch=36 + c.root_pc + 7, velocity=70,
        ))
    return notes


# --- Melody: simple sparse motif, stays high ---
def build_melody(chords: list[ChordEvent], skip_first_bars: int = 8) -> list[NoteEvent]:
    """A simple repeating motif on chord tones; stays out for the intro."""
    notes = []
    for c in chords:
        if c.start_beat < skip_first_bars * BAR_BEATS:
            continue
        # MIDI 72 + chord_tones to put us in C5 area
        base = 72 + c.root_pc
        # 3-note motif on beats 0.5, 2.0, 3.5 of each bar
        third = 4 if c.quality in ("maj", "maj7", "7", "add9") else 3
        fifth = 7 if c.quality not in ("dim", "dim7", "m7b5") else 6
        notes.append(NoteEvent(
            start_beat=c.start_beat + 0.5, length_beats=1.0,
            pitch=base + 12, velocity=85,  # octave up
        ))
        notes.append(NoteEvent(
            start_beat=c.start_beat + 2.0, length_beats=0.75,
            pitch=base + third, velocity=80,
        ))
        notes.append(NoteEvent(
            start_beat=c.start_beat + 3.5, length_beats=0.5,
            pitch=base + fifth, velocity=75,
        ))
    return notes


# --- Keys: chord-tone comping, quarter-note pulse ---
def build_keys(chords: list[ChordEvent]) -> list[NoteEvent]:
    """Mid-octave chord voicings struck on each beat."""
    notes = []
    for c in chords:
        # Voicing: root + 7 + 3 + 5, voiced 60-80 (C4-G#5)
        third = 4 if c.quality in ("maj", "maj7", "7", "add9") else 3
        fifth = 7 if c.quality not in ("dim", "dim7", "m7b5") else 6
        seventh = 11 if c.quality == "maj7" else 10 if c.quality in ("min7", "7") else None
        intervals = [0, third, fifth]
        if seventh is not None:
            intervals.append(seventh)
        # Quarter-note hits, slightly staccato
        for beat in range(int(c.length_beats)):
            for semis in intervals:
                notes.append(NoteEvent(
                    start_beat=c.start_beat + float(beat),
                    length_beats=0.7,  # gap before next hit
                    pitch=60 + c.root_pc + semis,
                    velocity=70,
                ))
    return notes


# --- Construct the spec ---
sections, total_bars = build_sections()
chord_track = build_chord_track(total_bars)

drums = TrackSpec(
    name="Drums", role="drums",
    chain=PluginChainSpec(plugins=["sampler"]),
    drum_pattern=DrumPatternSpec(
        notes=LOFI_DRUMS, loop_length_beats=4.0,
        # Drums in at bar 8 (end of intro). Out for bridge handled by
        # section_variants — empty list for the bridge section.
        start_beat=8.0 * BAR_BEATS,
        section_variants={2: []},  # bridge (index 2) has no drums
    ),
)
bass = TrackSpec(
    name="Bass", role="bass",
    chain=PluginChainSpec(plugins=["4osc"]),
    notes=build_bass(chord_track),
)
keys = TrackSpec(
    name="Keys", role="keys",
    chain=PluginChainSpec(plugins=["4osc"]),
    notes=build_keys(chord_track),
    mix_offset_db=4.0,  # keys are the soul of a lofi track
)
melody = TrackSpec(
    name="Melody", role="melody",
    chain=PluginChainSpec(plugins=["4osc"]),
    notes=build_melody(chord_track, skip_first_bars=8),
    mix_offset_db=2.0,
)
pad = TrackSpec(
    name="Pad", role="pad",
    chain=PluginChainSpec(plugins=["4osc"]),
    mix_offset_db=-3.0,
    # Empty notes — builder auto-fills from chord_track for pads
)

spec = CompositionSpec(
    name="Ecstasy-Inspired Lofi",
    tempo_bpm=75,
    time_sig_num=4, time_sig_denom=4,
    key_root_pc=5,           # F
    key_mode="major",
    genre="lofi",
    sections=sections,
    chord_track=chord_track,
    tracks=[drums, bass, keys, melody, pad],
    master_chain=PluginChainSpec(plugins=["3bandEq", "comp", "limiter"]),
    provenance={
        "inspired_by": "Ecstasy",
        "loop_chords": ["Fmaj7", "A#maj7", "Cmaj7", "A#min7"],
        "tempo_source": "lofi_default_75",
    },
)

problems = spec.validate()
if problems:
    print("Validation issues:")
    for p in problems[:5]:
        print(f"  - {p}")

OUT = r"C:\Users\jarms\OneDrive\Documents\MCP-Validate\Ecstasy-inspired-lofi.tracktionedit"
result = compose_from_spec({"spec": spec, "out_path": OUT})
print()
print("ok=", result.get("ok"))
print("bars=", result.get("bars"))
print("tracks=", result.get("tracks"))
print()
print("opening in Waveform...")
print(waveform_launch({"file_path": OUT}))
