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

import os
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
            "The project goes in Waveform's default project folder (the one its dialog "
            "pre-fills; Waveform 14: OneDrive\\Music\\waveform). To use another folder, "
            "change it in Waveform before calling this. The dialog's 'Open Default Edit' "
            "checkbox is left checked so an empty Edit opens after creation."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {"type": "string", "description": "Project name"},
                "location": {
                    "type": "string",
                    "description": (
                        "Optional parent folder. Waveform 14's dialog ignores typed "
                        "locations, so the result has a warning if the project landed "
                        "in the default folder instead."
                    ),
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
            "its in-memory copy. Confirms the 'Are you sure?' popup (its Revert button "
            "on Waveform 14, Enter on 13). Waveform 14 closes the edit on revert, so it "
            "is reopened from edit_file_path (default: the file last written by "
            "edit_save). Retries up to 3x; gates on a loaded project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "edit_file_path": {
                    "type": "string",
                    "description": (
                        "The .tracktionedit to reopen if Waveform closes the edit. "
                        "Defaults to the last path saved with edit_save."
                    ),
                },
            },
        },
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
            "Invoke a chain of Waveform menu items, e.g. ['Track', 'Add track']. "
            "Opens the first item from the menu bar (Waveform 14, switching to the edit "
            "tab if needed) or from the 'Menu' button (Waveform 13). Filters menu items "
            "to popup windows so we don't false-match on tracks/buttons named the same "
            "in the main view. Brings Waveform to the foreground."
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
                "edit_tab": {
                    "type": "string",
                    "description": (
                        "Edit tab to switch to if the menu isn't showing (needed when "
                        "several edits are open), e.g. 'My Song Edit 1'"
                    ),
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


def _waveform_settings_text() -> str:
    path = Path(os.environ.get("APPDATA", "")) / "Tracktion" / "Waveform" / "Waveform.settings"
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _settings_project_dirs() -> list[Path]:
    """Project folders listed in Waveform.settings (its project list)."""
    import html
    import re

    text = _waveform_settings_text()
    return [Path(html.unescape(m)) for m in re.findall(r'\bfile="([^"]+)"', text)]


def _settings_project_roots() -> list[Path]:
    """The folder Waveform last created/opened projects in (its default)."""
    import html
    import re

    text = _waveform_settings_text()
    return [Path(html.unescape(m)) for m in
            re.findall(r'name="lastpath_projectfile"\s+val="([^"]+)"', text)]


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
    # commit=True tabs out of each field: JUCE only commits a field's text when
    # it loses focus, so without it Create Project used the pre-filled location.
    type_name = win_input.win_uia_focus_and_type({
        "text": name,
        "index": 0,
        "window_title": "New Project",
        "clear_first": True,
        "commit": True,
    })
    if not type_name.get("ok"):
        return {"ok": False, "error": f"could not type name: {type_name.get('error')}"}

    if location:
        type_location = win_input.win_uia_focus_and_type({
            "text": location,
            "index": 1,
            "window_title": "New Project",
            "clear_first": True,
            "commit": True,
        })
        if not type_location.get("ok"):
            return {"ok": False,
                    "error": f"could not type location: {type_location.get('error')}"}

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
    # Where Waveform says its projects are (Waveform 14 defaults to
    # OneDrive\Music\waveform, which the fixed list below never covered).
    candidates.extend(d for d in _settings_project_dirs() if d.name == name)
    candidates.extend(root / name for root in _settings_project_roots())
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

    result = {
        "ok": True,
        "name": name,
        "project_dir": str(project_dir),
        "project_file": str(tracktion_files[0]) if tracktion_files else None,
        "edit_files": [str(p) for p in edit_files],
    }
    if location and _location_ignored(location, project_dir):
        result["warning"] = (
            f"location {location!r} was not used; Waveform created the project in its "
            f"default folder {str(project_dir.parent)!r}. Change the default in Waveform "
            "to put projects elsewhere.")
    return result


def _location_ignored(location: str, project_dir: Path) -> bool:
    """True if the project didn't land in the requested parent folder."""
    try:
        return project_dir.parent.resolve() != Path(location).resolve()
    except OSError:
        return True


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

    edit_path = args.get("edit_file_path")
    if not edit_path:
        from . import edit as edit_tools
        edit_path = getattr(edit_tools, "_open_path", None)

    last_error: str | None = None
    for attempt in range(3):
        # waveform_menu_invoke focuses Waveform and clears half-open menus itself.
        # The tab hint is the edit's file name, which is its tab title.
        res = waveform_menu_invoke({"path": ["File", "Revert to saved state..."],
                                    "edit_tab": Path(edit_path).stem if edit_path else None})
        if res.get("ok"):
            confirmed = _confirm_revert_dialog(win_input)
            # Checked twice: Waveform either reloads the edit in place or
            # closes it, and a close can land after the first look.
            time.sleep(1.5)
            still_open = _project_is_loaded()
            if still_open:
                time.sleep(1.5)
                still_open = _project_is_loaded()
            if still_open:
                # The edit reloaded in place.
                return {"ok": True, "attempts": attempt + 1, "confirmed_by": confirmed,
                        "mode": "reloaded"}
            # Waveform 14 closes the edit on revert; open the file again.
            reopened = _reopen_edit(edit_path)
            if not reopened.get("ok"):
                return {"ok": False, "attempts": attempt + 1, "confirmed_by": confirmed,
                        "error": f"Waveform closed the edit on revert and it could not be "
                                 f"reopened: {reopened.get('error')}"}
            return {"ok": True, "attempts": attempt + 1, "confirmed_by": confirmed,
                    "mode": "reopened", "edit_file_path": edit_path}
        last_error = res.get("error") or "unknown"
        time.sleep(0.5)
    return {"ok": False, "error": f"revert failed after 3 attempts: {last_error}"}


def _confirm_revert_dialog(win_input, timeout: float = 3.0) -> str:
    """Confirm the revert prompt. Returns how: "button" or "enter".

    Waveform 14 shows a window titled "Revert" with Cancel / Revert buttons,
    where Enter does not confirm; its "Revert" button is invoked by exact name.
    Waveform 13's prompt is confirmed with Enter, the original behaviour.
    """
    from .win_input import _uia_app, _walk_uia  # type: ignore[attr-defined]

    deadline = time.time() + timeout
    while time.time() < deadline:
        app, _ = _uia_app()
        if app is not None:
            for w in app.windows():
                if w.window_text() != "Revert":
                    continue
                for it in _walk_uia(w, want_types=("Button",), max_results=50):
                    if it["name"] == "Revert":
                        it["elem"].invoke()
                        return "button"
        time.sleep(0.2)
    win_input.win_send_keys({"keys": "{ENTER}", "silent_focus": False, "delay_after": 0.5})
    return "enter"


def _reopen_edit(edit_path: str | None, timeout: float = 15.0) -> dict:
    """Open a .tracktionedit in the running Waveform and wait for its tab.

    Runs Waveform's own executable with the file, which is what its registered
    file association does; a running Waveform takes the file over. Waveform 14
    only opens edits that belong to a Waveform project (a folder with
    project_info.json, created via waveform_new_project).
    """
    if not edit_path:
        return {"ok": False, "error": "no edit file known; pass edit_file_path"}
    p = Path(edit_path)
    if p.suffix.lower() != ".tracktionedit" or not p.is_file():
        return {"ok": False, "error": f"not an existing .tracktionedit: {edit_path}"}
    import subprocess

    from .. import safety
    from .waveform_app import _locate

    exe = _locate()
    if exe is None or safety.waveform_exe_error(exe):
        return {"ok": False, "error": "Waveform executable not found"}
    subprocess.Popen([exe, str(p)], close_fds=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.5)
        if _project_is_loaded():
            return {"ok": True}
    return {"ok": False, "error": (
        f"Waveform did not open {p.name} within {timeout:g}s; Waveform 14 only opens "
        "edits inside a Waveform project (see waveform_new_project)")}


def _project_is_loaded() -> bool:
    """True if the active Waveform window has at least one project edit tab.

    Delegates to waveform_app._list_tab_titles which already understands
    both naming conventions:
      * '<Project> Edit N' — standard project edits
      * '<basename>'        — drag-dropped/imported edits (no Edit N suffix)

    Falls back to a window-title check for the rarer case where a project
    is loaded but the tab strip is occluded.
    """
    try:
        from .waveform_app import _list_tab_titles
        if _list_tab_titles():
            return True
    except Exception:
        pass

    # Title fallback (Pro edition shows '<project> - Waveform')
    try:
        from .win_input import _uia_app, _uia_window
        app, _ = _uia_app()
        if app is None:
            return False
        win = _uia_window(app)
        title = win.window_text() or ""
        return " - " in title and "Waveform" in title
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


def _main_window(app):
    """Waveform's main window: the largest of its top-level windows. Popup
    menus are also titled "Waveform", so the title alone can't pick it."""
    def area(w):
        r = w.element_info.rectangle
        return (r.right - r.left) * (r.bottom - r.top)
    windows = app.windows()
    return max(windows, key=area) if windows else None


def _show_edit_tab(app, edit_tab: str | None) -> bool:
    """Switch to an edit tab so Waveform 14 shows its edit menu bar.

    Uses `edit_tab` if that tab is open, else the only open edit tab. With
    several edits open and no hint, does nothing rather than guess.
    """
    from .waveform_app import _list_tab_titles
    from .win_input import _walk_uia  # type: ignore[attr-defined]

    tabs = _list_tab_titles()
    target = edit_tab if edit_tab in tabs else (tabs[0] if len(tabs) == 1 else None)
    if target is None:
        return False
    main = _main_window(app)
    for it in _walk_uia(main, want_types=("Button",), max_results=500):
        if it["name"] == target:
            it["elem"].invoke()
            time.sleep(0.6)
            return True
    return False


def _open_first_menu(path: list[str], edit_tab: str | None = None
                     ) -> tuple[list[str] | None, str | None]:
    """Open the menu that holds `path`. Returns (steps still to invoke, error).

    Waveform 13 has a single "Menu" button whose popup lists File, Track...,
    so the whole path is walked from that popup. Waveform 14 replaced it with
    a menu bar (File, Edit, ..., Track, shown while an edit is open), so
    path[0] is opened from the bar and the walk continues from path[1].

    Exact names only: win_uia_invoke falls back to substring matches, which
    on Waveform 14 hit the wrong controls (e.g. "Project" -> "Projects" tab).
    """
    from .win_input import _uia_app, _walk_uia  # type: ignore[attr-defined]

    app, error = _uia_app()
    if app is None:
        return None, error

    def scan():
        main = _main_window(app)
        if main is None:
            return None, None
        bar_bottom = main.element_info.rectangle.top + 40
        button = item = None
        for it in _walk_uia(main, want_types=("Button", "MenuItem"), max_results=2000):
            if it["control_type"] == "Button" and it["name"] == "Menu":
                button = it["elem"]
                break
            if (item is None and it["control_type"] == "MenuItem" and it["name"] == path[0]
                    and it["rect"] and it["rect"][1] < bar_bottom):
                item = it["elem"]
        return button, item

    menu_button, bar_item = scan()
    if menu_button is None and bar_item is None and _show_edit_tab(app, edit_tab):
        # Waveform 14 only shows File/Edit/Track... while an edit tab is
        # active; the Welcome/Projects/Settings tabs have a shorter bar.
        menu_button, bar_item = scan()
    try:
        if menu_button is not None:
            menu_button.invoke()
            time.sleep(0.4)
            return list(path), None
        if bar_item is not None:
            bar_item.invoke()
            time.sleep(0.4)
            return list(path[1:]), None
    except Exception as e:
        return None, f"could not open the {path[0]!r} menu: {e}"
    return None, (f"no 'Menu' button (Waveform 13) or {path[0]!r} menu-bar item "
                  "(Waveform 14) found; is an edit open?")


def waveform_menu_invoke(args: dict) -> dict:
    """Open Waveform's menu and invoke a chain of menu items by name.

    Strategy:
      1. Bring Waveform to the foreground (JUCE closes popup menus when its
         app isn't active) and press Esc twice to clear any open menu
      2. Open the first menu: the 'Menu' button on Waveform 13, or the
         path[0] menu-bar item on Waveform 14 (see _open_first_menu)
      3. For each remaining name: find a MenuItem with that name in a popup
         window (not the main window, to avoid matching track names), invoke
         it, wait for the next submenu to render

    Waveform is left in the foreground.
    """
    err = _windows_only()
    if err:
        return err

    from . import win_input
    from .waveform_app import waveform_focus

    path: list[str] = list(args["path"])
    delay = float(args.get("delay_after", 0.6))
    if not path:
        return {"ok": False, "error": "path is empty"}

    focus = waveform_focus({})
    if not focus.get("ok"):
        return {"ok": False, "error": f"could not focus Waveform: {focus.get('error')}"}
    time.sleep(0.2)

    # Clear stale menu state (Waveform is already in front, so no focus juggling)
    win_input.win_send_keys({"keys": "{ESC}", "silent_focus": False, "delay_after": 0.2})
    win_input.win_send_keys({"keys": "{ESC}", "silent_focus": False, "delay_after": 0.2})

    remaining, error = _open_first_menu(path, args.get("edit_tab"))
    if remaining is None:
        return {"ok": False, "error": error}

    invoked = path[: len(path) - len(remaining)]
    for step in remaining:
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
    """Look for a MenuItem with this name in any process window other than the
    main app window (i.e. a popup). Excludes the main window so we don't
    false-match on track names like 'Track 1' that appear in lanes, or on the
    Waveform 14 menu bar itself.
    """
    from .win_input import _uia_app, _walk_uia  # type: ignore[attr-defined]

    app, _ = _uia_app()
    if app is None:
        return None
    main = _main_window(app)
    main_handle = main.handle if main is not None else None
    for w in app.windows():
        # By handle, not width: the old ">= 1200 px wide" test let a smaller
        # main window through and treated it as a popup.
        if w.handle == main_handle:
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

    # Locate ffmpeg (Waveform's bundled copy, whichever version is installed)
    from ..safety import find_bundled_tool

    found_ffmpeg = find_bundled_tool("ffmpeg")
    if not found_ffmpeg:
        return {"ok": False, "error": "ffmpeg.exe not found in Waveform install"}
    ffmpeg = Path(found_ffmpeg)

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

    # Auto-trim trailing silence. Waveform's UI render dialog often
    # uses a stale OUT marker that extends past the song's actual
    # content, baking minutes of silence onto the end of the export.
    # Default on; pass `trim_trailing_silence=False` to keep the raw
    # export. Threshold defaults to -50 dB and stop_duration 2 s, so
    # natural reverb tails / soft fades aren't truncated.
    trim_silence = bool(args.get("trim_trailing_silence", True))
    duration_before = duration_after = None
    if trim_silence:
        threshold_db = float(args.get("trim_silence_threshold_db", -50.0))
        stop_duration_sec = float(args.get("trim_silence_stop_duration_sec", 2.0))
        before, after = _trim_mp3_trailing_silence(
            mp3, ffmpeg, threshold_db=threshold_db,
            stop_duration_sec=stop_duration_sec,
        )
        duration_before, duration_after = before, after

    if not keep_wav:
        try:
            new_wav.unlink()
        except OSError:
            pass

    result = {
        "ok": True,
        "mp3": str(mp3),
        "wav": str(new_wav) if keep_wav else None,
        "size_bytes": mp3.stat().st_size,
        "bitrate": bitrate,
    }
    if duration_before is not None:
        result["duration_before_trim_sec"] = duration_before
        result["duration_after_trim_sec"] = duration_after
    return result


def _trim_mp3_trailing_silence(
    mp3: "Path", ffmpeg: "Path", *,
    threshold_db: float = -50.0,
    stop_duration_sec: float = 2.0,
) -> tuple[float | None, float | None]:
    """Strip trailing silence from an MP3 in place using ffmpeg's
    `silenceremove` filter. Returns (duration_before, duration_after)
    in seconds, or (None, None) if the operation failed.

    Uses stop_periods=-1 (strip all silent chunks past the start),
    stop_duration=N seconds, stop_threshold=THRESHOLD dB. Re-encodes
    to the same bitrate to preserve quality.
    """
    import subprocess
    if not mp3.exists():
        return None, None

    # Probe original duration via ffprobe (fall back to ffmpeg parsing)
    def _probe_duration(path: "Path") -> float | None:
        proc = subprocess.run(
            [str(ffmpeg), "-i", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        # ffmpeg writes "Duration: HH:MM:SS.ms" to stderr
        import re as _re
        m = _re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", proc.stderr)
        if m:
            h, mm, s = m.groups()
            return int(h) * 3600 + int(mm) * 60 + float(s)
        return None

    before = _probe_duration(mp3)
    tmp = mp3.with_suffix(".trim_tmp.mp3")
    proc = subprocess.run(
        [str(ffmpeg), "-y", "-i", str(mp3),
         "-af",
         f"silenceremove=stop_periods=-1:stop_duration={stop_duration_sec}"
         f":stop_threshold={threshold_db}dB",
         "-codec:a", "libmp3lame", "-b:a", "256k", str(tmp)],
        capture_output=True, text=True, timeout=180,
    )
    if proc.returncode != 0 or not tmp.exists():
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return before, before  # original kept; trim failed silently
    try:
        # Replace original with trimmed
        mp3.unlink()
        tmp.rename(mp3)
    except Exception:
        return before, before
    after = _probe_duration(mp3)
    return before, after


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
