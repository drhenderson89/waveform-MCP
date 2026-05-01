# Tracktion `.tracktionedit` XML schemas — captured fixtures

Each section here is a captured XML structure with its attributes and source. The
captures were done via the schema-capture loop (`schema_snapshot_current_edit`
before/after) by driving Waveform's UI with the MCP itself.

## Submix / Folder track — `<FOLDERTRACK>`

**Source:** Drove Waveform UI: Track menu → "Add submix track". Snapshotted
before/after, diffed.

```xml
<FOLDERTRACK id="1051">
  <MODIFIERS/>
  <PLUGIN type="volume" id="1052" enabled="1">
    <MODIFIERASSIGNMENTS/>
  </PLUGIN>
  <PLUGIN type="level" id="1053" enabled="1"/>
</FOLDERTRACK>
```

Identical structure to a regular `<TRACK>` but with the tag changed and no
`<CLIPSLOTS>` / `<OUTPUTDEVICES>` (folder tracks don't have clips). Folder
tracks for grouping use the same `<FOLDERTRACK>` tag.

**Status:** ✅ Wired into `xml_writer.py`. `bus_create(name, kind="submix")`
now emits valid Waveform-compatible XML.

**Outstanding:** Routing other tracks INTO the folder is done by physically
NESTING those `<TRACK>` elements as children of `<FOLDERTRACK>`. Need to
capture a fixture where a track has been packed into a submix to confirm
the exact nesting.

## Step clip — `<STEPCLIP>`

**Source:** Right-click on a MIDI clip → "Convert to Step clip".

```xml
<STEPCLIP name="..." start="0.0" length="256.0" offset="0.0"
          id="1054" sequence="0" colour="ffffaa00" repeatSequence="1">
  <AUTOMATIONCURVES/>
  <CHANNELS>
    <CHANNEL channel="1" note="60" velocity="127" name="C4"/>
    <CHANNEL channel="1" note="58" velocity="127" name="A#3"/>
    <!-- ... 16-32 CHANNEL elements, one per drum row, with default
         chromatic-scale notes -->
  </CHANNELS>
  <PATTERNS>
    <PATTERN numNotes="471" noteLength="1.0">
      <CHANNEL pattern="10101010..."
               velocities="127 127 127 ..."/>
      <!-- one CHANNEL per drum row, with binary on/off pattern string
           and space-separated per-step velocities -->
    </PATTERN>
  </PATTERNS>
</STEPCLIP>
```

**Status:** Schema captured. NOT yet wired into the writer; needs:
- `StepClip` dataclass in `model.py` with `channels: list[ChannelDef]` and
  `patterns: list[PatternDef]`
- `_build_step_clip` in `xml_writer.py`
- A `step_clip_add` tool

## Modifiers + assignments

**Source:** Tracktion engine source code (`tracktion_OldEditConversion.h`).

Track-level modifier definition lives under `<TRACK>` → `<MODIFIERS>`:

```xml
<TRACK>
  <MODIFIERS>
    <LFO id="..." syncType="1" depth="0.5" phase="0.0"
         rateType="1" rate="1.0"/>
    <!-- alternatively: <Envelope>, <Random>, <StepLfo>, ... -->
  </MODIFIERS>
  <PLUGIN ...>
    <MODIFIERASSIGNMENTS>
      <LFO source="<modifier-id>" paramID="filterFreq" value="1.0"/>
    </MODIFIERASSIGNMENTS>
  </PLUGIN>
</TRACK>
```

LFO attributes (from tracktion source):
- `syncType` (int): 1 = transport sync
- `depth` (0..1)
- `phase` (0..1, normalized from degrees/360)
- `rateType` (int): 0 = time-based (Hz), 1+ = beat-based
- `rate` (Hz when time-based, or beat multiplier)
- `intensity`, `frequency`, `sync`, `beat` — legacy attrs preserved on read

**Status:** Schema documented. NOT yet wired into the writer. Current
`plugin_add_modifier` writes to `modmatrix` (4OSC's internal modmatrix,
which is a different feature). To support real modifiers we need:
- `Track.modifiers: list[Modifier]` field
- `Plugin.modifier_assignments: list[ModifierAssignment]` field
- Writer support for `<MODIFIERS>` and `<MODIFIERASSIGNMENTS>`

## Macro parameters — `<MACROPARAMETERS>` / `<MACROPARAMETER>`

**Source:** tracktion_engine source (`tracktion_Edit.cpp` references).

Macros live under `<PLUGIN>` (and on the global Edit):

```xml
<PLUGIN>
  <MACROPARAMETERS id="<plugin-id>">
    <MACROPARAMETER id="<macro-id>" name="..." value="0.5"/>
    <!-- ... up to N macros per plugin -->
  </MACROPARAMETERS>
</PLUGIN>
```

The current writer already emits `<MACROPARAMETERS id="..."/>` (empty) for
every plugin. To populate it, we need `Plugin.macros: list[Macro]` and a
`plugin_add_macro` tool. Macro IDs may be needed for assignment targets.

**Status:** Container element already emitted; child `<MACROPARAMETER>`
elements not yet supported.

## Sources

- [tracktion_engine OldEditConversion.h](https://github.com/Tracktion/tracktion_engine/blob/master/modules/tracktion_engine/model/edit/tracktion_OldEditConversion.h) — LFO modifier conversion
- [tracktion_engine Edit.cpp](https://github.com/Tracktion/tracktion_engine/blob/master/modules/tracktion_engine/model/edit/tracktion_Edit.cpp) — macro parameter list iteration
- [augene-ng](https://github.com/atsushieno/augene-ng) — community Tracktion XML tool referencing MACROPARAMETERS
