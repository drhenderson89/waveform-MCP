# Tool catalog (v0.1)

Tools the LLM can call right now.

## Edit lifecycle
| name | purpose |
|---|---|
| `edit_create` | New empty Edit (tempo, time-sig, key, length). |
| `edit_open` | Load existing `.tracktionedit` from disk. |
| `edit_save` | Serialize current Edit to a path; sets the flush target. |
| `flush` | Re-write the current Edit to the previously-saved path. |
| `edit_summary` | JSON snapshot of tracks/clips/markers. |
| `undo` | Roll back the last mutation. |
| `narrate` | Tell the user what's about to happen (visible in the preview UI). |

## Tracks & structure
| name | purpose |
|---|---|
| `track_add` | Add audio/midi/folder/submix/automation track. |
| `track_remove` | Delete a track. |
| `mix_set` | Set volume_db, pan, mute, solo on a track. |
| `send_add` | Aux send between tracks. |
| `marker_add` | Drop a section marker (verse/chorus/drop). |
| `tempo_set` | Change project tempo. |
| `key_set` | Change project key. |

## MIDI authoring
| name | purpose |
|---|---|
| `midi_clip_add` | Empty MIDI clip on a track. |
| `midi_notes_add` | Add an array of notes to a clip. |
| `midi_notes_clear` | Empty a clip. |
| `midi_clip_quantize` | Snap notes to a grid with adjustable strength. |

## Audio
| name | purpose |
|---|---|
| `audio_clip_import` | Place a WAV/AIFF/etc. on an audio track. |

## Plugins
| name | purpose |
|---|---|
| `plugin_list` | Curated list of built-ins. |
| `plugin_add` | Add a plugin to a track's chain. |
| `plugin_set_param` | Set a named parameter. |
| `plugin_remove` | Remove a plugin from a chain. |

## Render (stub)
| name | purpose |
|---|---|
| `render` | Mixdown to WAV — **not implemented** until `wf-engine-helper` exists. Returns ok=false with a fallback message. |
| `render_stems` | Per-track stems — **not implemented**. |

## Roadmap (not in v0.1)

Live / OSC: `transport`, `scene_launch`, `arm`, `record_start`, `record_stop`.
Settings: `audio_device_set`, `plugin_rescan`, `controller_map_install`.
Automation curves: `automation_add`, `automation_clip_modifier`.
Specialized instrument programmers: `instrument_4osc_program`, `instrument_drumsampler_load_kit`, `instrument_wavetable_load`.
Clip Launcher: `scene_create`, `scene_set_clip`.
ARA / Melodyne integration: deferred indefinitely (opaque plugin state).
