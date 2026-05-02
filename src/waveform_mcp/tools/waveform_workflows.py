"""High-level Waveform UI workflows.

These tools chain the UIA / window primitives in `win_input` into named
reusable flows. The agent calls `waveform_new_project(name=...)` and gets
a fresh project on disk, end-to-end. Each workflow:

  - Restores Waveform from minimized if needed
  - Opens the relevant dialog via UIA Invoke
  - Fills fields via focus+keystroke (JUCE Edits don't honor ValuePattern)
  - Clicks the confirmation button via UIA Invoke
  - Waits for the dialog to disappear
  - Returns paths / ids the agent can use next

Add a new flow here when you find yourself writing the same UIA dance twice.
That's the rule for keeping this file from becoming a dumping ground.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any


def _windows_only() -> dict | None:
    if sys.platform != "win32":
        return {"ok": False, "error": "waveform_workflows is Windows-only"}
    return None


TOOLS: list[dict[str, Any]] = [
    {
        "name": "waveform_new_project",
        "description": (
            "Hands-off: open Waveform's New Project dialog, fill in project name, "
            "click Create Project, and return the resulting project + edit file paths. "
            "Default location is whatever Waveform pre-fills (typically OneDrive\\Documents). "
            "Set `location` to override. The dialog's 'Open Default Edit' checkbox is "
            "left at its default (checked) so an empty Edit opens after creation."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {"type": "string", "description": "Project name"},
                "location": {
                    "type": "string",
                    "description": "Optional: parent directory for the project folder",
                },
                "wait_seconds": {
                    "type": "number",
                    "default": 3.0,
                    "description": "How long to wait for project creation to finish",
                },
            },
        },
    },
    {
        "name": "waveform_save",
        "description": (
            "Save the current Edit (Ctrl+S equivalent via UIA). Forces Waveform to "
            "flush the in-memory Edit to disk. Returns the path of the .tracktionedit "
            "that was just written if discoverable."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "waveform_revert_to_saved",
        "description": (
            "Reload the active Edit from disk via File → Revert to saved state. "
            "Use after writing new content to the .tracktionedit so Waveform refreshes "
            "its in-memory copy. Auto-confirms the 'Are you sure?' popup with Enter. "
            "Retries up to 3x; gates on a loaded project."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "waveform_project_loaded",
        "description": (
            "Check whether a project edit is currently loaded in Waveform. Returns "
            "{loaded: bool, title: str}. Use as a precondition before UI tools."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "waveform_close_active_tab",
        "description": "Close the currently active tab (project or edit) via UIA.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "waveform_active_tab",
        "description": (
            "Return the title of the currently active tab in Waveform. Useful to confirm "
            "a project/edit is open before sending edit-specific actions."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "waveform_menu_invoke",
        "description": (
            "Walk Waveform's main 'Menu' (bottom-left button) and invoke a chain of "
            "menu items. Pass the chain as an array, e.g. ['Track', 'Add track']. "
            "Filters menu items to popup windows so we don't false-match on tracks/buttons "
            "named the same in the main view."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Sequence of menu item names to click",
                },
                "delay_after": {"type": "number", "default": 0.6},
            },
        },
    },
    {
        "name": "waveform_add_track",
        "description": "Add a new audio/MIDI track via Menu → Track → Add track.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "waveform_select_track",
        "description": (
            "Click a track header by its name (e.g. 'Track 9') to select it as the "
            "current target track for subsequent menu actions like 'add plugin to track'."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["name"],
            "properties": {"name": {"type": "string"}},
        },
    },
    {
        "name": "waveform_insert_clip_on_track",
        "description": (
            "Right-click on a track lane area at given window-relative coords, then "
            "invoke 'Insert new MIDI clip' or 'Insert new Audio clip' from the resulting "
            "context menu. Coordinates should land inside the desired track lane."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["x", "y", "kind"],
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "kind": {
                    "type": "string",
                    "enum": ["midi", "audio", "edit", "step"],
                    "default": "midi",
                },
            },
        },
    },
    {
        "name": "waveform_render_export",
        "description": (
            "Invoke File → Export: Render to a file. Opens Waveform's render dialog. "
            "(Note: dialog navigation is a future iteration; this only opens the dialog "
            "for now and returns the popup window state.)"
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "waveform_render_to_mp3",
        "description": (
            "Render the active Edit to an MP3. Opens Waveform's File → Export dialog "
            "(which auto-renders a WAV), waits for it to finish, then converts the WAV "
            "to MP3 using Waveform's bundled ffmpeg.exe + libmp3lame. Returns the MP3 path."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "out_path": {
                    "type": "string",
                    "description": "Optional output MP3 path. Defaults to the same Exported/ folder as the WAV with .mp3 extension.",
                },
                "bitrate": {
                    "type": "string",
                    "default": "256k",
                    "description": "MP3 bitrate, e.g. 192k, 256k, 320k",
                },
                "keep_wav": {
                    "type": "boolean",
                    "default": True,
                    "description": "Keep the intermediate WAV (true) or delete it after conversion (false)",
                },
                "render_timeout_seconds": {
                    "type": "number",
                    "default": 30,
                    "description": "Max seconds to wait for the render to finish",
                },
            },
        },
    },
    {
        "name": "waveform_build_skeleton",
        "description": (
            "High-level workflow: create a new project, add N tracks via UI menu, save. "
            "Returns project paths. Foundation for the eventual 'build full lofi track' "
            "workflow once instrument and note schemas are captured."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["project_name"],
            "properties": {
                "project_name": {"type": "string"},
                "extra_tracks": {
                    "type": "integer",
                    "default": 0,
                    "description": "Tracks to add beyond Waveform's default 8",
                },
            },
        },
    },
]


def _restore_if_minimized() -> None:
    from . import win_input
    info = win_input.win_waveform_info({})
    rect = info.get("rect", [0, 0, 0, 0])
    # Windows minimized rect sentinel
    if rect and rect[0] <= -32000:
        win_input.win_restore_waveform({})
        time.sleep(0.4)


def _wait_for_window(title: str, timeout: float = 5.0) -> bool:
    """Poll until a window with this title appears in Waveform's process."""
    from .win_input import _uia_app  # type: ignore[attr-defined]
    deadline = time.time() + timeout
    while time.time() < deadline:
        app, err = _uia_app()
        if app:
            for w in app.windows():
                if w.window_text() == title:
                    return True
        time.sleep(0.2)
    return False


def _wait_for_window_to_close(title: str, timeout: float = 8.0) -> bool:
    from .win_input import _uia_app  # type: ignore[attr-defined]
    deadline = time.time() + timeout
    while time.time() < deadline:
        app, err = _uia_app()
        if app:
            present = any(w.window_text() == title for w in app.windows())
            if not present:
                return True
        time.sleep(0.2)
    return False


def waveform_new_project(args: dict) -> dict:
    err = _windows_only()
    if err:
        return err

    name = args["name"]
    location = args.get("location")
    wait = float(args.get("wait_seconds", 3.0))

    from . import win_input

    _restore_if_minimized()

    # Open the dialog. The "New Project..." button lives on the Projects tab.
    # If we're on a different tab, we need to switch first.
    tab_switch = win_input.win_uia_invoke({"name": "Projects", "control_type": "Button", "delay_after": 0.4})
    # Tab switch failure is fine — we may already be on Projects.

    open_dlg = win_input.win_uia_invoke({"name": "New Project...", "control_type": "Button", "delay_after": 0.8})
    if not open_dlg.get("ok"):
        return {"ok": False, "error": f"could not open New Project dialog: {open_dlg.get('error')}"}

    if not _wait_for_window("New Project", timeout=4.0):
        return {"ok": False, "error": "New Project dialog did not appear"}

    # Edit 0 is Name, Edit 1 is Location.
    type_name = win_input.win_uia_focus_and_type({
        "text": name,
        "index": 0,
        "window_title": "New Project",
        "clear_first": True,
    })
    if not type_name.get("ok"):
        return {"ok": False, "error": f"could not type name: {type_name.get('error')}"}

    if location:
        win_input.win_uia_focus_and_type({
            "text": location,
            "index": 1,
            "window_title": "New Project",
            "clear_first": True,
        })

    # Read back the location so we know where files will land.
    inspect = win_input.win_uia_inspect({
        "control_type": "Edit",
        "window_title": "New Project",
        "max_results": 5,
    })
    # we can't easily read JUCE Edit text via UIA; fall back to whatever Waveform pre-filled
    # by checking known default paths after creation

    create = win_input.win_uia_invoke({
        "name": "Create Project",
        "control_type": "Button",
        "window_title": "New Project",
        "delay_after": 0.5,
    })
    if not create.get("ok"):
        return {"ok": False, "error": f"could not click Create: {create.get('error')}"}

    if not _wait_for_window_to_close("New Project", timeout=8.0):
        return {"ok": False, "error": "New Project dialog did not close — creation may have failed"}

    # Locate the new project on disk. Try the location the user provided, else
    # search common defaults.
    candidates = []
    if location:
        candidates.append(Path(location) / name)
    # Default JUCE/Waveform locations
    docs = Path.home() / "Documents"
    onedrive_docs = Path.home() / "OneDrive" / "Documents"
    for root in (onedrive_docs, docs):
        candidates.append(root / name)

    project_dir = None
    for c in candidates:
        if c.exists() and c.is_dir():
            project_dir = c
            break

    if project_dir is None:
        return {
            "ok": True,
            "name": name,
            "warning": "project created but folder not located on disk",
            "tried": [str(c) for c in candidates],
        }

    tracktion_files = list(project_dir.glob("*.tracktion"))
    edit_files = list(project_dir.glob("*.tracktionedit"))

    return {
        "ok": True,
        "name": name,
        "project_dir": str(project_dir),
        "project_file": str(tracktion_files[0]) if tracktion_files else None,
        "edit_files": [str(p) for p in edit_files],
    }


def waveform_save(args: dict) -> dict:
    err = _windows_only()
    if err:
        return err
    from . import win_input

    _restore_if_minimized()
    # Standard DAW save shortcut — Waveform binds Ctrl+S.
    res = win_input.win_send_keys({"keys": "^s", "silent_focus": True, "delay_after": 0.8})
    return {"ok": res.get("ok", False), **res}


def waveform_revert_to_saved(args: dict) -> dict:
    """Reload the active edit from disk via File → Revert to saved state.

    Use this after writing new content to the .tracktionedit while Waveform
    has it open. Without this, Waveform keeps its in-memory copy and will
    overwrite your disk changes the next time it auto-saves. Sends Enter to
    confirm the resulting "Are you sure?" popup.

    Built-in retry (3 attempts with 0.5s backoff) — the menu invoke flakes
    sometimes when there's stale menu state lingering from previous calls.
    """
    err = _windows_only()
    if err:
        return err
    if not _project_is_loaded():
        return {"ok": False, "error": "no project loaded — call waveform_launch first"}
    from . import win_input
    import time

    _restore_if_minimized()

    last_error: str | None = None
    for attempt in range(3):
        # Clear any half-open menu so the menu_invoke starts clean.
        win_input.win_send_keys({"keys": "{ESC}{ESC}{ESC}", "silent_focus": True, "delay_after": 0.25})
        res = waveform_menu_invoke({"path": ["File", "Revert to saved state..."]})
        if res.get("ok"):
            time.sleep(0.5)
            # Confirm the "Are you sure?" popup. Default button is Yes; Enter activates it.
            win_input.win_send_keys({"keys": "{ENTER}", "silent_focus": True, "delay_after": 0.5})
            return {"ok": True, "attempts": attempt + 1}
        last_error = res.get("error") or "unknown"
        time.sleep(0.5)
    return {"ok": False, "error": f"revert failed after 3 attempts: {last_error}"}


def _project_is_loaded() -> bool:
    """True if the active Waveform window has at least one project edit tab.

    Lighter-weight than waveform_active_tab; used as a precondition gate.

    Heuristics (any-of):
      1. Window title contains a project name (form "<Project> - Waveform"
         or window text contains " Edit ").
      2. Any UIA control's name matches "<something> Edit <N>" (the standard
         Waveform tab label for an open edit, e.g. "MCP-Validate Edit 1").
         This is the most reliable signal — the title bar of Waveform FREE
         remains "Waveform FREE" even when a project is open, but the tab
         strip always shows "<Project> Edit N" for each loaded edit.
      3. Fallback: a Button whose name is exactly "Edit" sits in the toolbar
         when an edit is active (mode switcher).
    """
    try:
        from .win_input import _uia_app, _uia_window, _walk_uia  # type: ignore[attr-defined]
        import re as _re

        app, _err = _uia_app()
        if app is None:
            return False
        win = _uia_window(app)
        title = win.window_text() or ""
        # Common project-loaded title formats
        if " - " in title and "Waveform" in title:
            return True

        # Walk all controls once and look for tab-strip / toolbar signals
        rows = _walk_uia(win, want_types=None, max_results=400)
        edit_tab_re = _re.compile(r".+\s+Edit\s+\d+$")
        nav_names = {"Welcome", "Projects", "Settings", "Edit"}
        for r in rows:
            name = (r.get("name") or "").strip()
            if not name:
                continue
            # Signal 2: project-edit tab labels
            if edit_tab_re.match(name):
                return True
            # Signal 3: toolbar mode-switcher "Edit" button (only present
            # when an edit is loaded). Distinguish from tabs/menus by
            # control type when possible.
            if name == "Edit" and r.get("control_type") in ("Button", "TabItem"):
                # The Welcome screen has no such control.
                return True

        return False
    except Exception:
        return False


def waveform_project_loaded(args: dict) -> dict:
    """Return whether a project edit is currently loaded in Waveform.

    Use this as a sanity check before calling UI tools that assume a project
    is open (revert, render, save, menu invoke for File/Edit submenus).
    """
    err = _windows_only()
    if err:
        return err
    loaded = _project_is_loaded()
    title = ""
    try:
        from .win_input import _uia_app, _uia_window  # type: ignore[attr-defined]
        app, _e = _uia_app()
        if app is not None:
            title = _uia_window(app).window_text() or ""
    except Exception:
        pass
    return {"ok": True, "loaded": loaded, "title": title}


def waveform_close_active_tab(args: dict) -> dict:
    err = _windows_only()
    if err:
        return err
    from . import win_input

    _restore_if_minimized()
    res = win_input.win_send_keys({"keys": "^w", "silent_focus": True, "delay_after": 0.5})
    return {"ok": res.get("ok", False), **res}


def waveform_active_tab(args: dict) -> dict:
    """Find the currently selected tab. Tabs are Buttons in the top tab bar; the
    selected one has its name shown in the title bar of the main window after
    the project name. We approach by listing the Tab buttons and returning all
    of them (the agent can also just read this from the main window title).
    """
    err = _windows_only()
    if err:
        return err
    from .win_input import _uia_app, _uia_window, _walk_uia  # type: ignore[attr-defined]

    app, error = _uia_app()
    if app is None:
        return {"ok": False, "error": error}
    win = _uia_window(app)
    rows = _walk_uia(win, want_types=("Button",), max_results=300)
    # Tabs cluster near the top; titles of project tabs include 'Edit' in their name
    tab_candidates = [r["name"] for r in rows if r["name"] and ("Edit" in r["name"] or r["name"] in ("Welcome", "Projects", "Settings"))]
    return {"ok": True, "main_window_title": win.window_text(), "tab_buttons": tab_candidates}


def waveform_menu_invoke(args: dict) -> dict:
    """Open the main Menu and invoke a chain of menu items by name.

    Strategy:
      1. Press Esc twice to clear any open menu state
      2. Invoke the 'Menu' button via UIA — opens main popup
      3. For each name in `path`: find a MenuItem with that name in any
         small popup window (filters out main-view false matches), invoke it,
         wait for next submenu to render
    """
    err = _windows_only()
    if err:
        return err

    from . import win_input
    from .win_input import _uia_app, _walk_uia  # type: ignore[attr-defined]

    path: list[str] = args["path"]
    delay = float(args.get("delay_after", 0.6))

    # Clear stale menu state
    win_input.win_send_keys({"keys": "{ESC}", "silent_focus": True, "delay_after": 0.2})
    win_input.win_send_keys({"keys": "{ESC}", "silent_focus": True, "delay_after": 0.2})

    open_menu = win_input.win_uia_invoke({"name": "Menu", "control_type": "Button", "delay_after": 0.4})
    if not open_menu.get("ok"):
        return {"ok": False, "error": f"could not open Menu: {open_menu.get('error')}"}

    invoked = []
    for step in path:
        time.sleep(0.3)
        elem = _find_menuitem_in_popups(step)
        if elem is None:
            return {
                "ok": False,
                "error": f"menu item not found: {step!r}",
                "invoked_so_far": invoked,
            }
        try:
            elem.invoke()
        except Exception as e:
            return {"ok": False, "error": f"invoke {step!r} failed: {e}"}
        invoked.append(step)
        time.sleep(0.4)

    if delay > 0:
        time.sleep(delay)

    return {"ok": True, "path": path, "invoked": invoked}


def _find_menuitem_in_popups(name: str):
    """Look for a MenuItem with this name in any process window that is
    smaller than the main app window (i.e. a popup). Excludes the main window
    so we don't false-match on track names like 'Track 1' that appear in lanes.
    """
    from .win_input import _uia_app, _walk_uia  # type: ignore[attr-defined]

    app, _ = _uia_app()
    if app is None:
        return None
    for w in app.windows():
        rect = w.element_info.rectangle
        if rect.right - rect.left >= 1200:  # main app window
            continue
        for it in _walk_uia(w, want_types=("MenuItem",), max_results=80):
            if it["name"] == name:
                return it["elem"]
    return None


def waveform_add_track(args: dict) -> dict:
    return waveform_menu_invoke({"path": ["Track", "Add track"]})


def waveform_select_track(args: dict) -> dict:
    """Select a track by clicking its header label via UIA."""
    err = _windows_only()
    if err:
        return err
    from . import win_input
    res = win_input.win_uia_invoke({
        "name": args["name"],
        "control_type": "Button",
        "delay_after": 0.3,
    })
    return res


def waveform_insert_clip_on_track(args: dict) -> dict:
    err = _windows_only()
    if err:
        return err
    from . import win_input

    x = int(args["x"])
    y = int(args["y"])
    kind = args.get("kind", "midi")

    label_map = {
        "midi": "Insert new MIDI clip",
        "audio": "Insert new Audio clip",
        "edit": "Insert new Edit clip",
        "step": "Insert new Step clip",
    }
    label = label_map.get(kind)
    if not label:
        return {"ok": False, "error": f"unknown clip kind: {kind}"}

    win_input.win_send_keys({"keys": "{ESC}", "silent_focus": True, "delay_after": 0.2})
    rc = win_input.win_click_real({"x": x, "y": y, "button": "right", "delay_after": 0.6})
    if not rc.get("ok"):
        return rc

    elem = _find_menuitem_in_popups(label)
    if elem is None:
        return {"ok": False, "error": f"context menu item not found: {label!r}"}
    try:
        elem.invoke()
    except Exception as e:
        return {"ok": False, "error": f"invoke failed: {e}"}
    time.sleep(1.0)
    return {"ok": True, "kind": kind, "at": [x, y]}


def waveform_render_export(args: dict) -> dict:
    """Open the Render-to-file dialog. Filling in the dialog is a future
    iteration — for now this just opens it so the user can complete manually,
    or the agent can drive it once we capture the dialog UIA tree."""
    err = _windows_only()
    if err:
        return err
    return waveform_menu_invoke({"path": ["File", "Export: Render to a file..."], "delay_after": 1.5})


def waveform_render_to_mp3(args: dict) -> dict:
    """Render the active Edit to MP3.

    Pipeline:
      1. Snapshot all WAV files under user-Documents Exported folders BEFORE.
      2. Open File → Export dialog (auto-renders to WAV because Waveform
         keeps "Render Automatically" toggled on).
      3. Wait for a new WAV to appear (and finish growing) in any Exported
         folder.
      4. Close the dialog (Esc).
      5. Run ffmpeg from Waveform's install dir to convert WAV → MP3 with
         libmp3lame at the requested bitrate.
      6. Return the MP3 path. Optionally delete the WAV.
    """
    err = _windows_only()
    if err:
        return err

    import subprocess
    from pathlib import Path

    bitrate = args.get("bitrate", "256k")
    keep_wav = bool(args.get("keep_wav", True))
    timeout = float(args.get("render_timeout_seconds", 30))
    out_path_arg = args.get("out_path")

    # Locate ffmpeg
    ffmpeg = Path(r"C:\Program Files\Tracktion\Waveform 13\ffmpeg.exe")
    if not ffmpeg.exists():
        # Try Waveform 12 / fall back
        for cand in [
            r"C:\Program Files\Tracktion\Waveform 12\ffmpeg.exe",
            r"C:\Program Files\Tracktion\Waveform 11\ffmpeg.exe",
        ]:
            if Path(cand).exists():
                ffmpeg = Path(cand)
                break
        else:
            return {"ok": False, "error": "ffmpeg.exe not found in Waveform install"}

    # Find candidate Exported folders (Documents/OneDrive Documents subtrees)
    candidate_roots = [
        Path.home() / "OneDrive" / "Documents",
        Path.home() / "Documents",
    ]
    exported_dirs: list[Path] = []
    for root in candidate_roots:
        if root.exists():
            exported_dirs.extend([p for p in root.glob("**/Exported") if p.is_dir()])
    if not exported_dirs:
        return {"ok": False, "error": "no Exported/ folder found under Documents"}

    # Snapshot existing WAVs by mtime
    pre = {p: p.stat().st_mtime for d in exported_dirs for p in d.glob("*.wav")}

    # Open the Render dialog (auto-renders). Restore Waveform first in case
    # it's minimized — UIA can't drive a minimized window. Retry once if
    # menu items don't show up in time (heavy File submenu).
    from . import win_input
    _restore_if_minimized()
    time.sleep(0.5)
    open_dlg = {"ok": False, "error": "not attempted"}
    for attempt in range(3):
        win_input.win_send_keys({"keys": "{ESC}", "silent_focus": True, "delay_after": 0.3})
        win_input.win_send_keys({"keys": "{ESC}", "silent_focus": True, "delay_after": 0.3})
        open_dlg = waveform_menu_invoke({
            "path": ["File", "Export: Render to a file..."],
            "delay_after": 0.8,
        })
        if open_dlg.get("ok"):
            break
        time.sleep(0.5)
    if not open_dlg.get("ok"):
        return {"ok": False, "error": f"could not open Render dialog: {open_dlg.get('error')}"}

    # Wait for a NEW or UPDATED wav to appear and stabilize (size stops growing)
    deadline = time.time() + timeout
    new_wav: Path | None = None
    last_size = -1
    stable_count = 0
    while time.time() < deadline:
        time.sleep(0.5)
        candidates: list[Path] = []
        for d in exported_dirs:
            for p in d.glob("*.wav"):
                if p not in pre or p.stat().st_mtime > pre[p]:
                    candidates.append(p)
        if not candidates:
            continue
        # Take the most recently modified
        cand = max(candidates, key=lambda p: p.stat().st_mtime)
        size = cand.stat().st_size
        if size == last_size and size > 0:
            stable_count += 1
            if stable_count >= 2:        # stable for 2 polls (1 second)
                new_wav = cand
                break
        else:
            stable_count = 0
            last_size = size
    if new_wav is None:
        # Close dialog before returning
        win_input.win_send_keys({"keys": "{ESC}", "silent_focus": True, "delay_after": 0.3})
        return {"ok": False, "error": "render did not produce a new WAV within timeout"}

    # IMPORTANT: convert BEFORE closing the dialog. Waveform interprets Esc
    # on the Render dialog as "cancel" and deletes the rendered WAV — even
    # if "Export Complete" was already shown. So ffmpeg first, dialog second.

    # Decide output MP3 path
    if out_path_arg:
        mp3 = Path(out_path_arg)
    else:
        mp3 = new_wav.with_suffix(".mp3")
    mp3.parent.mkdir(parents=True, exist_ok=True)

    # Convert
    proc = subprocess.run(
        [str(ffmpeg), "-y", "-i", str(new_wav),
         "-codec:a", "libmp3lame", "-b:a", bitrate, str(mp3)],
        capture_output=True, text=True, timeout=120,
    )

    # NOW close the dialog
    win_input.win_send_keys({"keys": "{ESC}", "silent_focus": True, "delay_after": 0.3})
    if proc.returncode != 0:
        return {
            "ok": False,
            "error": "ffmpeg failed",
            "stderr": proc.stderr[-500:],
            "wav": str(new_wav),
        }

    if not keep_wav:
        try:
            new_wav.unlink()
        except OSError:
            pass

    return {
        "ok": True,
        "mp3": str(mp3),
        "wav": str(new_wav) if keep_wav else None,
        "size_bytes": mp3.stat().st_size,
        "bitrate": bitrate,
    }


def waveform_build_skeleton(args: dict) -> dict:
    """Demo high-level workflow: project → tracks → save. Each step is one
    of our existing MCP tools chained.
    """
    err = _windows_only()
    if err:
        return err

    name = args["project_name"]
    extra = int(args.get("extra_tracks", 0))

    res = waveform_new_project({"name": name})
    if not res.get("ok"):
        return res
    project_dir = res.get("project_dir")
    edit_files = res.get("edit_files", [])

    for _ in range(extra):
        time.sleep(0.3)
        add = waveform_add_track({})
        if not add.get("ok"):
            return {"ok": False, "error": f"add track failed: {add.get('error')}"}

    save = waveform_save({})
    return {
        "ok": True,
        "project_dir": project_dir,
        "edit_file": edit_files[0] if edit_files else None,
        "extra_tracks_added": extra,
        "save_result": save,
    }


HANDLERS = {
    "waveform_new_project": waveform_new_project,
    "waveform_save": waveform_save,
    "waveform_revert_to_saved": waveform_revert_to_saved,
    "waveform_project_loaded": waveform_project_loaded,
    "waveform_close_active_tab": waveform_close_active_tab,
    "waveform_active_tab": waveform_active_tab,
    "waveform_menu_invoke": waveform_menu_invoke,
    "waveform_add_track": waveform_add_track,
    "waveform_select_track": waveform_select_track,
    "waveform_insert_clip_on_track": waveform_insert_clip_on_track,
    "waveform_render_export": waveform_render_export,
    "waveform_render_to_mp3": waveform_render_to_mp3,
    "waveform_build_skeleton": waveform_build_skeleton,
}
