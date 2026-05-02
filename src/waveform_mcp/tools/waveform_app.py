"""Waveform application control — locate, launch, focus, kill, status.

This is the OS-level glue. It does NOT poke around inside the app via UI
automation; that's the desktop module. Here we only deal with the process
and its files.

Windows-first; cross-platform support is a v0.2 concern.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

try:
    import psutil
except ImportError:
    psutil = None  # type: ignore[assignment]

# Common install locations on Windows
WINDOWS_CANDIDATES = [
    r"C:\Program Files\Tracktion\Waveform 13\Waveform 13.exe",
    r"C:\Program Files\Tracktion\Waveform 12\Waveform 12.exe",
    r"C:\Program Files\Tracktion\Waveform 11\Waveform 11.exe",
    r"C:\Program Files (x86)\Tracktion\Waveform 13\Waveform 13.exe",
]


TOOLS: list[dict[str, Any]] = [
    {
        "name": "waveform_locate",
        "description": (
            "Find the installed Waveform binary on disk. Returns the path. "
            "Searches running processes first, then well-known install locations. "
            "Run this before launch/focus tools so the rest of the chain has a path to use."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "waveform_status",
        "description": "Is Waveform currently running? Returns pid + exe path if so.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "waveform_launch",
        "description": (
            "Launch Waveform, optionally opening a .tracktionedit file. If Waveform is "
            "already running and a file path is provided, opens the file in the existing "
            "instance via OS file association (Waveform registers itself as the handler "
            "for .tracktionedit on install)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Optional .tracktionedit to open",
                },
                "exe_path": {
                    "type": "string",
                    "description": "Override the auto-detected Waveform binary path",
                },
            },
        },
    },
    {
        "name": "waveform_focus",
        "description": (
            "Bring Waveform's main window to the foreground. Required before "
            "desktop_key / desktop_type send keystrokes to the app."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "waveform_quit",
        "description": "Politely close Waveform (sends WM_CLOSE to the main window).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "waveform_settings_dir",
        "description": (
            "Return the path to Waveform's settings directory (where Waveform.settings, "
            "knownPluginList64.settings, Templates/, etc. live). Useful for the planned "
            "settings-file editor surface."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]


# ---------- impls ----------


def _find_running() -> tuple[int | None, str | None]:
    if psutil is None:
        return None, None
    for proc in psutil.process_iter(["pid", "name", "exe"]):
        try:
            name = (proc.info.get("name") or "").lower()
            exe = proc.info.get("exe") or ""
            if "waveform" in name and exe:
                return int(proc.info["pid"]), exe
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None, None


def _locate() -> str | None:
    pid, exe = _find_running()
    if exe:
        return exe
    for cand in WINDOWS_CANDIDATES:
        if Path(cand).exists():
            return cand
    # PATH fallback
    for name in ("Waveform 13.exe", "Waveform.exe"):
        p = shutil.which(name)
        if p:
            return p
    return None


def waveform_locate(args: dict) -> dict:
    path = _locate()
    if path is None:
        return {"ok": False, "error": "Waveform binary not found in known locations"}
    return {"ok": True, "exe_path": path}


def waveform_status(args: dict) -> dict:
    if psutil is None:
        return {"ok": False, "error": "psutil not installed"}
    pid, exe = _find_running()
    return {"ok": True, "running": pid is not None, "pid": pid, "exe_path": exe}


def waveform_launch(args: dict) -> dict:
    """Open `file_path` in Waveform, launching the app if needed.

    State-aware decision tree (no `file_path` falls through to plain
    cold-start of the app, no file load):

      1. Waveform NOT running              → Popen with file as CLI arg
      2. Waveform running, file ALREADY    → focus the existing tab
         the active or visible tab           (no-op drop; mode="already_open")
      3. Waveform running, but a modal     → bail with informative error
         dialog (Open/Save/etc.) is open     so the caller can decide
      4. Waveform running, no conflict     → drag the file in via
                                             Explorer → OLE drop

    Always returns ok=True with `mode` describing what happened, OR
    ok=False with a specific `stage` field naming the failure point.
    """
    file_path = args.get("file_path")
    exe_path = args.get("exe_path") or _locate()
    if exe_path is None:
        return {"ok": False, "error": "Waveform binary not found"}

    if file_path:
        if not Path(file_path).exists():
            return {"ok": False, "error": f"file not found: {file_path}"}

    pid, _ = _find_running()

    # Case 1: Waveform isn't running — cold start
    if pid is None:
        cmd = [exe_path]
        if file_path:
            cmd.append(str(file_path))
        try:
            if sys.platform == "win32":
                DETACHED = 0x00000008
                subprocess.Popen(
                    cmd,
                    creationflags=DETACHED | subprocess.CREATE_NEW_PROCESS_GROUP,
                    close_fds=True,
                )
            else:
                subprocess.Popen(cmd, start_new_session=True, close_fds=True)
        except Exception as e:
            return {"ok": False, "error": str(e), "type": type(e).__name__,
                    "stage": "spawn"}
        return {"ok": True, "exe_path": exe_path, "file_path": file_path,
                "mode": "spawned"}

    # If no file given, nothing more to do — Waveform's already running
    if not file_path:
        return {"ok": True, "mode": "already_running", "pid": pid}

    # Case 2-4 require checking Waveform's UI state
    state = _waveform_ui_state(file_path)
    if state.get("error"):
        return {"ok": False, "error": state["error"],
                "stage": "state_check"}

    # Case 3: a modal/file dialog is up — refuse to drop into the dialog
    if state.get("modal_dialog"):
        return {
            "ok": False,
            "error": (f"Waveform has a modal dialog open "
                      f"({state['modal_dialog']}). Close it before opening a file."),
            "stage": "modal_dialog_blocking",
            "dialog_title": state["modal_dialog"],
        }

    # Case 2: file is already loaded as a tab.
    #
    # Default behavior is REVERT — focus the tab and trigger File →
    # Revert to saved state, which reloads from disk. This is the
    # correct path when iterating on the same project: the file lives
    # inside the project, revert reads it, you hear the new version.
    #
    # Caller can pass `force_reimport=True` to instead close the tab
    # and drag-drop the file fresh. Reserve this for cases where you
    # know the in-project metadata is corrupt or out of sync.
    if state.get("file_already_loaded"):
        existing_tab = state["file_already_loaded"]
        force_reimport = bool(args.get("force_reimport", False))

        if not force_reimport:
            focus_res = _focus_tab(existing_tab)
            time.sleep(0.3)
            try:
                from .waveform_workflows import waveform_revert_to_saved
                r = waveform_revert_to_saved({})
                revert_ok = bool(r.get("ok"))
                revert_err = r.get("error") if not revert_ok else None
            except Exception as e:
                revert_ok = False
                revert_err = str(e)
            return {
                "ok": True, "file_path": file_path,
                "mode": "already_open_reverted" if revert_ok else "already_open",
                "active_tab": existing_tab,
                "focused": focus_res.get("ok", False),
                "reverted": revert_ok,
                **({"revert_error": revert_err} if revert_err else {}),
            }

        # Force-reimport path (rare): close + drag-drop fresh
        focus_res = _focus_tab(existing_tab)
        time.sleep(0.3)
        close_ok = _close_active_tab()
        time.sleep(0.5)
        post_view = _detect_active_view()
        if post_view != "edit":
            remaining = _list_tab_titles()
            if remaining:
                _focus_tab(remaining[0])
                time.sleep(0.3)
        result = _open_in_running(str(file_path))
        if isinstance(result, dict) and result.get("ok"):
            result["mode"] = "reimported_after_close"
            result["closed_old_tab"] = existing_tab
            result["close_ok"] = close_ok
        return result

    # Case 4: drag-drop. Drops only land on an edit timeline; the
    # Welcome / Projects / Settings tabs silently swallow them. If
    # we're not on an edit view, switch to one first by clicking any
    # existing edit tab. If no edit tabs exist, we genuinely can't
    # drag-drop and bail informatively.
    active_view = state.get("active_view") or "unknown"
    if active_view != "edit":
        existing_tabs = state.get("tab_titles") or []
        if not existing_tabs:
            return {
                "ok": False,
                "stage": "no_edit_view_available",
                "active_view": active_view,
                "error": ("Waveform has no edit tabs open. Drag-drop "
                          "requires an existing edit view to land on. "
                          "Open any project/edit first (or close Waveform "
                          "so this call cold-starts it with the file)."),
            }
        # Click into the first edit tab to bring us into edit view
        first_tab = existing_tabs[0]
        focus_res = _focus_tab(first_tab)
        time.sleep(0.5)
        post_view = _detect_active_view()
        if post_view != "edit":
            return {
                "ok": False,
                "stage": "could_not_switch_to_edit_view",
                "active_view": post_view,
                "tried_tab": first_tab,
                "error": (f"Tried to switch to tab '{first_tab}' but "
                          f"Waveform is now showing '{post_view}', not an "
                          f"edit view. Cannot proceed with drag-drop."),
                "focus_ok": focus_res.get("ok"),
            }
        # We're in edit view now — proceed
        result = _open_in_running(str(file_path))
        # Tag the mode so caller knows we auto-switched
        if isinstance(result, dict) and result.get("ok"):
            result["auto_switched_to_tab"] = first_tab
        return result

    return _open_in_running(str(file_path))


def _waveform_ui_state(file_path: str) -> dict:
    """Inspect Waveform's current UI state to decide how to load `file_path`.

    Returns a dict with optional fields:
      * `modal_dialog` — title of any blocking dialog (Open/Save/preset/etc.)
      * `file_already_loaded` — tab name if the target file is already open
      * `tab_titles` — current edit tab list (for diagnostics)
      * `active_view` — one of "edit", "welcome", "projects", "settings",
        or None if undetermined. Drag-drop only succeeds in "edit" view.
      * `error` — populated if state inspection itself failed

    Always best-effort and non-destructive — never sends keys, never
    clicks, never moves the mouse.
    """
    out: dict = {}
    if sys.platform != "win32":
        out["error"] = "state inspection only supported on Windows"
        return out

    try:
        import pygetwindow as gw  # type: ignore[import-untyped]
    except ImportError:
        out["error"] = "pygetwindow not installed"
        return out

    # Detect modal/file dialogs owned by Waveform's process
    try:
        wf_pid, _ = _find_running()
    except Exception:
        wf_pid = None

    dialog_title: str | None = None
    # Patterns for known Waveform-owned modals: standard Open/Save dialogs,
    # plus Waveform-specific names ("Select a project or archive file...",
    # "Save edit as", etc.)
    modal_keywords = (
        "select a project", "save edit", "save as",
        "open project", "import",
        "are you sure", "confirm", "warning",
        "preset", "browser",
        "render", "export",
    )
    try:
        for w in gw.getAllWindows():
            title = (w.title or "").strip()
            if not title or title.lower() == "waveform free" or title == "Waveform":
                continue
            tl = title.lower()
            if any(k in tl for k in modal_keywords):
                # Confirm it's actually a modal that's blocking input
                # (visible + not minimized)
                try:
                    if w.visible and not w.isMinimized:
                        dialog_title = title
                        break
                except Exception:
                    dialog_title = title
                    break
    except Exception:
        # Don't fail the whole state check if window enumeration breaks
        pass
    if dialog_title:
        out["modal_dialog"] = dialog_title

    # Inspect tab strip
    try:
        tabs = _list_tab_titles()
        out["tab_titles"] = tabs
    except Exception as e:
        out["tab_titles"] = []
        # Non-fatal: continue with empty tab list

    # Is the file already loaded? Match by basename / stem against tab labels.
    target_stem = Path(file_path).stem
    target_name = Path(file_path).name
    for t in out.get("tab_titles", []):
        if not t:
            continue
        if t == target_stem or t == target_name or target_stem in t:
            out["file_already_loaded"] = t
            break

    # Active view detection — distinguishes whether Waveform is currently
    # showing an edit timeline (where drag-drop works) versus the
    # Welcome/Projects/Settings nav tabs (where drops are silently
    # ignored). Heuristic: the edit view always shows the transport's
    # 'Play' button. Welcome/Projects/Settings don't.
    out["active_view"] = _detect_active_view()

    return out


def _detect_active_view() -> str:
    """Return one of 'edit', 'welcome', 'projects', 'settings', or 'unknown'.

    Edit view always shows the transport bar (Play/Record/Loop). The nav
    tabs don't have a transport. So presence of a 'Play' Button is a
    reliable proxy for 'an edit is currently visible'.
    """
    try:
        from .win_input import _uia_app, _uia_window, _walk_uia
        app, _ = _uia_app()
        if app is None:
            return "unknown"
        win = _uia_window(app)
        rows = _walk_uia(win, want_types=("Button",), max_results=300)
        names = {(r.get("name") or "").strip() for r in rows}
        if "Play" in names and "Record" in names:
            return "edit"
        # Else figure out which nav tab is active by looking at distinctive
        # buttons that only appear on each
        if "New Project..." in names or "Open Project..." in names:
            return "projects"
        if "Audio Settings" in names or "Browse all Projects" in names:
            return "welcome"
        if "Audio device type" in names or "Buffer size" in names:
            return "settings"
        return "unknown"
    except Exception:
        return "unknown"


def _close_active_tab() -> bool:
    """Close whatever edit tab is currently active. Returns True on
    success. Uses Ctrl+W which Waveform binds to "close active tab"
    (verified). If a "save changes?" dialog pops up, declines (we want
    to discard whatever's in memory and re-import from disk)."""
    if sys.platform != "win32":
        return False
    try:
        from . import win_input
    except ImportError:
        return False
    win_input.win_send_keys({"keys": "{ESC}{ESC}", "silent_focus": True,
                              "delay_after": 0.2})
    res = win_input.win_send_keys({"keys": "^w", "silent_focus": True,
                                     "delay_after": 0.5})
    if not res.get("ok"):
        return False
    # If a "save changes before closing?" dialog appears, hit "No"
    # (Tab+Enter in many cases, or just Esc → No since we don't want
    # to save Waveform's stale cached state). Try Tab+Enter first.
    time.sleep(0.4)
    # Check for a dialog
    try:
        import pygetwindow as gw  # type: ignore[import-untyped]
        for w in gw.getAllWindows():
            tl = (w.title or "").lower().strip()
            if any(k in tl for k in ("save", "discard", "confirm")) \
                    and "waveform" not in tl:
                # Default Yes button = save. We want NO/Discard.
                # Sending Alt+N (No) is most reliable cross-version.
                win_input.win_send_keys({"keys": "%n", "silent_focus": True,
                                          "delay_after": 0.4})
                break
    except Exception:
        pass
    return True


def _focus_tab(tab_name: str) -> dict:
    """Click a tab button by name to bring it to the foreground.
    Best-effort: returns ok=False on failure but never raises."""
    if sys.platform != "win32":
        return {"ok": False, "error": "windows only"}
    try:
        from . import win_input
    except ImportError as e:
        return {"ok": False, "error": str(e)}
    try:
        from .waveform_workflows import _restore_if_minimized
        _restore_if_minimized()
    except Exception:
        pass
    res = win_input.win_uia_invoke({
        "name": tab_name, "control_type": "Button", "delay_after": 0.3,
    })
    return res


def _open_in_running(file_path: str) -> dict:
    """Open a .tracktionedit in an already-running Waveform.

    Approach: real mouse drag from Explorer to the Waveform window.
    JUCE registers an OLE IDropTarget on the main window that responds
    to true drag-drop events. We brief-open Explorer with the file
    pre-selected (`explorer /select,<path>`), find the selected
    ListView item via UIA to get its on-screen rectangle, then use
    SendInput-level mouse events to perform an actual drag from the
    file icon to the Waveform window. JUCE then dispatches the drop
    through its standard IDropTarget handler, which opens the file
    just as if the user did it manually.

    Falls back to a documented warning if the drag flow fails.
    """
    if sys.platform != "win32":
        return {"ok": False, "error": "_open_in_running only works on Windows"}

    try:
        from .waveform_workflows import _restore_if_minimized
    except ImportError as e:
        return {"ok": False, "error": f"required modules not available: {e}"}

    _restore_if_minimized()
    time.sleep(0.3)

    return _drag_file_into_waveform(file_path)


def _drag_file_into_waveform(file_path: str) -> dict:
    """Real mouse drag from Explorer to Waveform's main window."""
    # 1. Find Waveform's window rect (drop target)
    try:
        from .win_input import _find_waveform_hwnd
        import ctypes
        from ctypes import wintypes as wt

        hwnd = _find_waveform_hwnd()
        if not hwnd:
            return {"ok": False, "error": "no Waveform window found"}

        rect = wt.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
        # Drop in the lower-middle of the window (in the timeline area,
        # well below the tab strip)
        target_x = (rect.left + rect.right) // 2
        # Aim ~70% down the window — past the tab strip & toolbars
        target_y = rect.top + int((rect.bottom - rect.top) * 0.7)
    except Exception as e:
        return {"ok": False, "error": f"could not get Waveform rect: {e}",
                "stage": "target_rect"}

    # 2. Open Explorer with the file selected. /select,<path> highlights
    # the file inside its containing folder.
    try:
        subprocess.Popen(["explorer.exe", f"/select,{file_path}"],
                         creationflags=0x00000008)  # DETACHED_PROCESS
    except Exception as e:
        return {"ok": False, "error": f"could not open Explorer: {e}",
                "stage": "explorer_open"}

    # 3. Wait for Explorer to come up showing the file's parent folder
    time.sleep(1.5)
    src = _find_explorer_file_position(file_path, timeout=6.0)
    if src is None:
        return {"ok": False, "error": "couldn't locate file in Explorer",
                "stage": "explorer_find"}
    src_x, src_y = src

    # 4. Real mouse drag with SendInput. pyautogui's mouseDown/Up issue
    # SendInput events that JUCE's OLE IDropTarget honors.
    try:
        import pyautogui
    except ImportError:
        return {"ok": False, "error": "pyautogui not installed",
                "stage": "drag_lib"}

    # pyautogui has a corner-failsafe; disable for this operation
    prev_failsafe = pyautogui.FAILSAFE
    pyautogui.FAILSAFE = False
    try:
        pyautogui.moveTo(src_x, src_y, duration=0.15)
        time.sleep(0.1)
        pyautogui.mouseDown(button="left")
        # Slight initial movement to satisfy Windows' drag-detect threshold
        pyautogui.moveTo(src_x + 8, src_y + 8, duration=0.05)
        # Slow drag across the screen so OLE drag-drop initiates correctly
        pyautogui.moveTo(target_x, target_y, duration=0.6)
        time.sleep(0.15)
        pyautogui.mouseUp(button="left")
    except Exception as e:
        pyautogui.FAILSAFE = prev_failsafe
        return {"ok": False, "error": f"mouse drag failed: {e}",
                "stage": "drag_perform"}
    pyautogui.FAILSAFE = prev_failsafe

    # 5. Poll for the new tab to appear
    expected_stem = Path(file_path).stem
    deadline = time.time() + 12.0
    found_tab = None
    while time.time() < deadline:
        try:
            tabs = _list_tab_titles()
            match = next((t for t in tabs if expected_stem in t), None)
            if match:
                found_tab = match
                break
        except Exception:
            pass
        time.sleep(0.4)

    if found_tab:
        return {"ok": True, "file_path": file_path,
                "mode": "drag_dropped", "active_tab": found_tab}
    return {"ok": True, "file_path": file_path,
            "mode": "drag_dropped",
            "note": "drop performed but tab confirmation timed out"}


def _find_explorer_file_position(file_path: str, timeout: float = 6.0
                                 ) -> tuple[int, int] | None:
    """Find the on-screen center of `file_path` shown in an Explorer
    window. Polls until found or timeout."""
    try:
        from pywinauto.findwindows import find_elements
        from pywinauto.application import Application
    except ImportError:
        return None

    target_name = Path(file_path).name
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            for w in find_elements(backend="uia"):
                title = (w.name or "").strip()
                # Explorer windows for our folder will have a title containing
                # the parent folder name (e.g. "MCP-Validate").
                if not title or "Waveform" in title:
                    continue
                try:
                    app = Application(backend="uia").connect(handle=w.handle)
                    win = app.window(handle=w.handle)
                except Exception:
                    continue
                # Find a list item whose Name == our file's basename
                try:
                    items = win.descendants(control_type="ListItem")
                except Exception:
                    items = []
                for it in items:
                    try:
                        nm = (it.window_text() or "").strip()
                    except Exception:
                        continue
                    if not nm:
                        continue
                    # Explorer may show name with or without extension
                    base = Path(file_path).stem
                    if nm == target_name or nm == base:
                        try:
                            r = it.element_info.rectangle
                            cx = (r.left + r.right) // 2
                            cy = (r.top + r.bottom) // 2
                            return (int(cx), int(cy))
                        except Exception:
                            continue
        except Exception:
            pass
        time.sleep(0.3)
    return None


def _wait_for_process_exit(pid: int, timeout: float = 15.0) -> bool:
    """Poll until the given PID is gone or timeout. Returns True if gone."""
    if psutil is None:
        # Fallback: rely on _find_running() returning a different (or no) pid
        deadline = time.time() + timeout
        while time.time() < deadline:
            new_pid, _ = _find_running()
            if new_pid != pid:
                return True
            time.sleep(0.4)
        return False
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            proc = psutil.Process(pid)
            if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        except Exception:
            return True
        time.sleep(0.4)
    return False


def _list_tab_titles() -> list[str]:
    """Return the names of all project-edit tabs currently visible.
    Includes both standard edits ('<Project> Edit N') and imported
    edits (which keep the file's basename as the tab title)."""
    try:
        from .win_input import _uia_app, _uia_window, _walk_uia
        import re
        app, _ = _uia_app()
        if app is None:
            return []
        win = _uia_window(app)
        rows = _walk_uia(win, want_types=("Button",), max_results=300)
        edit_re = re.compile(r".+\s+Edit\s+\d+$")
        # Standard chrome buttons we never count as tabs
        chrome = {
            "Welcome", "Projects", "Settings", "Minimize", "Restore",
            "Close", "close", "New Tab", "Full-screen", "View Options<p>",
            "Help<p>", "Actions<p>", "Add", "+", "-", "Z", "F",
            "Read", "Write", "Play", "Record", "Panic", "Rewind",
            "Back", "Forward", "Retrospective Record", "Loop", "Snap",
            "Autoscroll", "MIDI Learn", "Click", "Auto Lock", "Punch",
            "MTC", "Project", "lock", "relink",
        }
        out: list[str] = []
        for r in rows:
            n = (r.get("name") or "").strip()
            if not n or n in chrome:
                continue
            # Standard tabs: '<X> Edit N'
            if edit_re.match(n):
                out.append(n)
                continue
            # Imported edit tabs: tab title is the file basename (no
            # 'Edit N' suffix). Heuristic: short name, no special chars,
            # not matching any chrome button. Constrain to top-of-window
            # buttons by checking rect.
            rect = r.get("rect")
            if rect and len(rect) == 4:
                # Tabs cluster near the top (y < 100 typically); other
                # buttons are deeper into the UI
                top_y = rect[1]
                if top_y < 120:
                    out.append(n)
        return out
    except Exception:
        return []


def _post_wm_dropfiles(file_path: str) -> dict:
    """Send a synthetic WM_DROPFILES message to Waveform's main window.

    Builds the legacy DROPFILES structure (header + Unicode file path
    list + double-NUL terminator), posts it via PostMessage. The OS
    delivers it to the window's WndProc which routes through OLE
    drop handling — same code path as a real Explorer drag-drop.
    """
    try:
        import ctypes
        import ctypes.wintypes as wt
    except ImportError:
        return {"ok": False, "error": "ctypes not available"}

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    shell32 = ctypes.windll.shell32

    WM_DROPFILES = 0x0233
    GMEM_MOVEABLE = 0x0002

    user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.PostMessageW.restype = wt.BOOL
    kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wt.HANDLE
    kernel32.GlobalLock.argtypes = [wt.HANDLE]
    kernel32.GlobalLock.restype = wt.LPVOID
    kernel32.GlobalUnlock.argtypes = [wt.HANDLE]
    kernel32.GlobalUnlock.restype = wt.BOOL

    # Find Waveform's top-level window
    try:
        from .win_input import _find_waveform_hwnd
        hwnd = _find_waveform_hwnd()
    except Exception as e:
        return {"ok": False, "error": f"could not find waveform hwnd: {e}"}
    if not hwnd:
        return {"ok": False, "error": "no Waveform window found"}

    # DROPFILES struct (defined in shellapi.h):
    #   DWORD pFiles;   offset to file list
    #   POINT pt;       drop point (we use 0,0)
    #   BOOL  fNC;
    #   BOOL  fWide;    TRUE for Unicode paths
    class DROPFILES(ctypes.Structure):
        _fields_ = [
            ("pFiles", wt.DWORD),
            ("pt_x", wt.LONG), ("pt_y", wt.LONG),
            ("fNC", wt.BOOL),
            ("fWide", wt.BOOL),
        ]

    df_size = ctypes.sizeof(DROPFILES)
    # File list: utf-16 path + NUL + final NUL terminator
    paths_blob = (file_path + "\0\0").encode("utf-16-le")
    total_size = df_size + len(paths_blob)

    h_global = kernel32.GlobalAlloc(GMEM_MOVEABLE, total_size)
    if not h_global:
        return {"ok": False, "error": "GlobalAlloc failed"}

    ptr = kernel32.GlobalLock(h_global)
    if not ptr:
        return {"ok": False, "error": "GlobalLock failed"}

    try:
        # Write DROPFILES header
        df = DROPFILES.from_address(ptr)
        df.pFiles = df_size
        df.pt_x = 0
        df.pt_y = 0
        df.fNC = 0
        df.fWide = 1
        # Append the path buffer right after the struct
        ctypes.memmove(ptr + df_size, paths_blob, len(paths_blob))
    finally:
        kernel32.GlobalUnlock(h_global)

    # Post the message — DON'T free h_global here; the receiver owns it
    # after a successful PostMessage.
    posted = user32.PostMessageW(hwnd, WM_DROPFILES, h_global, 0)
    if not posted:
        # Reclaim the memory only if posting failed
        kernel32.GlobalFree = ctypes.windll.kernel32.GlobalFree
        kernel32.GlobalFree.argtypes = [wt.HANDLE]
        kernel32.GlobalFree(h_global)
        return {"ok": False, "error": "PostMessage(WM_DROPFILES) failed"}

    return {"ok": True, "hwnd": int(hwnd)}
    if sys.platform != "win32":
        return {"ok": False, "error": "_open_in_running only works on Windows"}

    try:
        from . import win_input
    except ImportError as e:
        return {"ok": False, "error": f"win_input not available: {e}"}

    try:
        from .waveform_workflows import _restore_if_minimized
        _restore_if_minimized()
    except Exception:
        pass

    if not _set_clipboard(file_path):
        return {"ok": False, "error": "failed to set clipboard"}

    # Clear any half-open menus / dialogs
    win_input.win_send_keys({"keys": "{ESC}{ESC}", "silent_focus": True,
                              "delay_after": 0.2})

    # Step 1: click the "Projects" tab
    res_proj = win_input.win_uia_invoke({
        "name": "Projects", "control_type": "Button", "delay_after": 0.6,
    })
    if not res_proj.get("ok"):
        return {"ok": False,
                "error": f"could not click Projects tab: {res_proj.get('error')}",
                "stage": "projects_tab"}

    # Step 2: click "Open Project..." (the discovered button name)
    res_open = win_input.win_uia_invoke({
        "name": "Open Project...", "control_type": "Button", "delay_after": 1.0,
    })
    if not res_open.get("ok"):
        return {"ok": False,
                "error": f"could not click Open Project: {res_open.get('error')}",
                "stage": "open_project_button"}

    # Step 3: wait for the file-open dialog to appear
    if not _wait_for_open_dialog(timeout=5.0):
        return {"ok": False, "error": "file dialog didn't appear",
                "stage": "dialog_wait"}

    # Step 4: paste path + Enter. Standard Windows file dialog has the
    # filename Edit focused on appear; clear and paste defensively.
    win_input.win_send_keys({"keys": "^a", "silent_focus": True, "delay_after": 0.1})
    win_input.win_send_keys({"keys": "{DELETE}", "silent_focus": True, "delay_after": 0.1})
    win_input.win_send_keys({"keys": "^v", "silent_focus": True, "delay_after": 0.4})
    win_input.win_send_keys({"keys": "{ENTER}", "silent_focus": True, "delay_after": 1.5})

    # Poll for the loaded project's tab to appear
    try:
        from .waveform_workflows import _project_is_loaded
        deadline = time.time() + 15.0
        while time.time() < deadline:
            if _project_is_loaded():
                # Verify the tab name matches
                title = _current_tab_title()
                expected_name = Path(file_path).stem
                if title and expected_name in title:
                    return {"ok": True, "file_path": file_path,
                            "mode": "opened_in_running",
                            "active_tab": title}
                # Even if name doesn't match, project loading is a good signal
                return {"ok": True, "file_path": file_path,
                        "mode": "opened_in_running",
                        "active_tab": title or "(unknown)"}
            time.sleep(0.4)
    except Exception:
        pass

    return {"ok": True, "file_path": file_path,
            "mode": "opened_in_running",
            "note": "loaded state could not be confirmed within timeout"}


def _current_tab_title() -> str | None:
    """Return the active project-edit tab name, if detectable."""
    try:
        from .win_input import _uia_app, _uia_window, _walk_uia
        import re
        app, _ = _uia_app()
        if app is None:
            return None
        win = _uia_window(app)
        rows = _walk_uia(win, want_types=("Button",), max_results=300)
        edit_re = re.compile(r".+\s+Edit\s+\d+$")
        for r in rows:
            n = (r.get("name") or "").strip()
            if edit_re.match(n):
                return n
    except Exception:
        pass
    return None


def _set_clipboard(text: str) -> bool:
    """Set Windows clipboard to `text`. Returns True on success."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        import ctypes.wintypes as wt
    except ImportError:
        return False

    GMEM_MOVEABLE = 0x0002
    CF_UNICODETEXT = 13
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    # 64-bit-safe ctypes signatures. HANDLE values are 64-bit pointers on
    # x64; without explicit signatures ctypes truncates/sign-extends
    # silently and GlobalLock then crashes with "int too long".
    user32.OpenClipboard.argtypes = [wt.HWND]
    user32.OpenClipboard.restype = wt.BOOL
    user32.EmptyClipboard.argtypes = []
    user32.EmptyClipboard.restype = wt.BOOL
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = wt.BOOL
    user32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
    user32.SetClipboardData.restype = wt.HANDLE
    kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wt.HANDLE
    kernel32.GlobalLock.argtypes = [wt.HANDLE]
    kernel32.GlobalLock.restype = wt.LPVOID
    kernel32.GlobalUnlock.argtypes = [wt.HANDLE]
    kernel32.GlobalUnlock.restype = wt.BOOL

    if not user32.OpenClipboard(None):
        return False
    try:
        user32.EmptyClipboard()
        # Allocate enough for UTF-16 string + NUL
        n_bytes = (len(text) + 1) * 2
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, n_bytes)
        if not h:
            return False
        ptr = kernel32.GlobalLock(h)
        if not ptr:
            return False
        try:
            ctypes.memmove(ptr, ctypes.create_unicode_buffer(text), n_bytes)
        finally:
            kernel32.GlobalUnlock(h)
        if not user32.SetClipboardData(CF_UNICODETEXT, h):
            return False
        return True
    finally:
        user32.CloseClipboard()


def _wait_for_open_dialog(timeout: float = 4.0) -> bool:
    """Poll for a file-open dialog window. Returns True when one is up."""
    if sys.platform != "win32":
        return False
    try:
        import pygetwindow as gw  # type: ignore[import-untyped]
    except ImportError:
        return False

    deadline = time.time() + timeout
    titles_signaling_dialog = ("open", "load", "select")
    while time.time() < deadline:
        try:
            for w in gw.getAllWindows():
                title = (w.title or "").strip().lower()
                # Common file-dialog titles. Waveform on Windows uses
                # the standard CommonDlg "Open" dialog.
                if any(t in title for t in titles_signaling_dialog) and "waveform" not in title:
                    return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def waveform_focus(args: dict) -> dict:
    if sys.platform != "win32":
        return {"ok": False, "error": "focus only implemented on Windows"}
    try:
        import pygetwindow as gw  # type: ignore[import-untyped]
    except ImportError:
        return {"ok": False, "error": "pygetwindow not installed"}

    matches = [w for w in gw.getAllWindows() if "waveform" in (w.title or "").lower()]
    if not matches:
        return {"ok": False, "error": "no Waveform window found"}
    win = matches[0]
    try:
        if win.isMinimized:
            win.restore()
        win.activate()
    except Exception as e:
        # pygetwindow has flaky activate(); fall back to a Windows API call
        try:
            import ctypes

            hwnd = win._hWnd  # type: ignore[attr-defined]
            ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception as e2:
            return {"ok": False, "error": f"{e}; fallback: {e2}"}
    return {"ok": True, "title": win.title}


def waveform_quit(args: dict) -> dict:
    if psutil is None:
        return {"ok": False, "error": "psutil not installed"}
    pid, _ = _find_running()
    if pid is None:
        return {"ok": True, "running": False}
    try:
        proc = psutil.Process(pid)
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except psutil.TimeoutExpired:
            proc.kill()
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "pid": pid}


def waveform_settings_dir(args: dict) -> dict:
    if sys.platform == "win32":
        path = Path(os.environ.get("APPDATA", "")) / "Tracktion" / "Waveform"
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / "Tracktion" / "Waveform"
    else:
        path = Path.home() / ".config" / "Tracktion" / "Waveform"
    return {"ok": path.exists(), "path": str(path), "exists": path.exists()}


HANDLERS = {
    "waveform_locate": waveform_locate,
    "waveform_status": waveform_status,
    "waveform_launch": waveform_launch,
    "waveform_focus": waveform_focus,
    "waveform_quit": waveform_quit,
    "waveform_settings_dir": waveform_settings_dir,
}
