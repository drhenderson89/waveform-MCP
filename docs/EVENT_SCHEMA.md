# Event schema

Every event is a flat JSON object. Required fields:

| field | type | notes |
|---|---|---|
| `id` | string | `evt_<hex>_<seq>` |
| `seq` | integer | monotonic, server-assigned |
| `ts` | string | ISO-8601 UTC, ms precision |
| `kind` | string | discriminator (see below) |

## Kinds

### `tool_call`
Emitted by the `@op` decorator after every tool invocation, success or failure.
```json
{
  "kind": "tool_call",
  "tool": "midi_notes_add",
  "intent": "Add notes to clip_3",
  "args": {"clip_id": "clip_3", "notes": [...]},
  "result": "ok",
  "duration_ms": 1.2,
  "error": null,
  "target": null,
  "diff": null
}
```
The `diff` field on `tool_call` is currently null — model_change events carry the actual diff. We may inline a compact diff here later if the UI wants both.

### `model_change`
Emitted by the `EditStore` subscriber whenever an `apply` produces a change. Coarse structural diff:
```json
{
  "kind": "model_change",
  "op": "midi_notes_add",
  "tracks_changed": [
    {
      "track_id": "trk_a1b2",
      "notes_changed": [{"clip_id": "clip_3", "from": 0, "to": 4}]
    }
  ]
}
```
Possible top-level keys: `edit` (top-level scalar changes), `tracks_added`, `tracks_removed`, `tracks_changed`. Within `tracks_changed`: `name`, `volume_db`, `pan`, `mute`, `solo`, `output`, `plugins`, `clips_added`, `clips_removed`, `notes_changed`.

### `narrate`
Plain-language statement of intent. The agent calls this before a batch of mutations so the user can follow along even if they're not watching the timeline.
```json
{ "kind": "narrate", "text": "Adding a 4-bar drum loop on the new track" }
```

### `flush`
Indicates a write to disk happened.
```json
{
  "kind": "flush",
  "target": "C:/.../song.tracktionedit",
  "result": "ok",
  "error": null
}
```

### `edit_opened`
Loaded a file from disk; carries a full summary of the resulting Edit so subscribers don't need to walk the model.
```json
{
  "kind": "edit_opened",
  "path": "C:/.../song.tracktionedit",
  "summary": { /* Edit.summary() */ }
}
```

### `undo`
```json
{ "kind": "undo", "ok": true }
```

### `render_attempt`
Render tool fired (currently always `not_implemented`).

## Subscriber contract

- Subscribers must not block. Use queues for slow consumers.
- Subscribers must not raise; the bus catches and ignores exceptions to keep mutation flow alive.
- Order is preserved within a single `EditStore.apply`.
- Subscribers are called synchronously from the mutation thread; treat them as critical-path.
