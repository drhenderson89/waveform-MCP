# Architecture

## Surfaces

The MCP exposes Waveform's functionality through four planned surfaces, layered by what each is good at. v0.1 only ships #1 + a stub of #4; the rest are scaffolded.

| # | Surface | Purpose | Status |
|---|---|---|---|
| 1 | `.tracktionedit` XML generator | Songwriting / structural edits | **Built** (unvalidated against real Waveform) |
| 2 | `wf-engine-helper` (C++ on tracktion_engine) | Headless render to WAV/stems | Planned |
| 3 | OSC bridge | Drive a running Waveform (transport, scenes, action triggers) | Planned |
| 4 | Settings-file editor | Audio I/O, control-surface maps, plugin DB | Stub (render tool returns "not implemented") |

The decision matrix behind these picks is in the research report; the short version: Waveform's in-app JS macros and OSC mapping are too thin to drive songwriting from outside, but XML generation covers everything that lives inside an Edit, and the engine library can render headlessly.

## Data flow

```
LLM (Claude)
    │  MCP tool call
    ▼
server.py — registers tools, dispatches by name
    │
    ▼
tools/*.py — thin schema layer
    │  call into
    ▼
model.py:STORE.apply(op, args, fn)
    │  ┌─ snapshot before
    │  ├─ run fn (mutates Edit)
    │  ├─ snapshot after, push to undo stack
    │  └─ notify subscribers ─┐
    ▼                         │
xml_writer.write() on flush   │
    │                         ▼
.tracktionedit on disk      events.py — fans out to:
    │                           ├─ events.log JSONL
    ▼                           ├─ in-memory ring buffer
Waveform reads on open          └─ asyncio queues (websocket)
```

The preview UI is a separate process that tails `events.log` and pushes events over websocket to the browser.

## Single source of truth

The in-memory `Edit` (held by `STORE` in `model.py`) is authoritative during a session. The on-disk `.tracktionedit` is a write-target, not a source — except via `edit_open(path)`, which deliberately replaces the in-memory state. This avoids the merge-hell of two-way sync and matches how the user actually works (one DAW window, one song file).

When Waveform has the file open and the user edits in the GUI while we're also writing, behavior is undefined. The roadmap calls for either a "Waveform-is-open" detection (lockfile / OSC ping) or moving to the OSC-driven save/reload dance for live sessions.

## Why subscribers, not pull-based

Tool implementations stay terse — they mutate the Edit and return. The decorator in `tools/common.py` handles snapshot, diff, event publishing, and error packaging. This is the same pattern Redux/Elm/Bevy ECS use: mutations are descriptive, side-effects are wired up centrally.

Adding a new visibility surface (e.g. push to a TUI dashboard, post to Slack on flush) is `events.subscribe(fn)` — never a tool-implementation change.

## Why two processes

The MCP server speaks JSON-RPC over **stdin/stdout** with the host (Claude Desktop, Claude Code). Binding a TCP port from inside that process is fine but couples preview UI lifecycle to the agent conversation. The user wants to be able to refresh / reopen / restart the preview without disrupting their session. So: separate process, shared state via the JSONL file.

The tradeoff: a small replication delay (preview tails the file at 250ms cadence). This is invisible at human reaction speeds.

## Schema validation strategy

The single highest-risk thing in v0.1 is the `.tracktionedit` XML format. Tracktion has not published a schema; everything we emit is reverse-engineered. The plan to de-risk this:

1. Get a clean install of Waveform Free.
2. Save a one-track-one-MIDI-clip-one-note edit. Save again with different tempo. Diff.
3. Repeat for each major entity (audio clip, plugin, send, marker, automation).
4. Build a fixture corpus in `tests/fixtures/`, with attribute-by-attribute coverage.
5. Add round-trip tests that load a fixture, re-serialize, and assert equivalence.
6. **Then** ship a binary that any user can trust.

Until step 6, treat XML output as "Waveform might open this; might not" — the tools are usable, but the integration loop with Waveform is not stable.

## Tracktion engine licensing

`tracktion_engine` is dual-licensed: GPL v3 or commercial. v0.1 is GPL since we're going to link it for the render helper. If anyone wants to ship a proprietary fork, they buy the Tracktion commercial license.
