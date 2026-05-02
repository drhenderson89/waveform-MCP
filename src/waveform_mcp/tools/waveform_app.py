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

    Behavior:
      * If Waveform isn't running and `file_path` is provided, spawn
        Waveform.exe with the file as a CLI argument — Waveform loads
        it on startup.
      * If Waveform is already running, the spawn-with-CLI-arg approach
        is unreliable (the second instance is eaten or doesn't forward
        the file). In that case we drive Waveform's UI to open the file
        via Ctrl+O + clipboard-paste of the path + Enter.

    Returns ok=True once the file is loaded (or once the launch is
    initiated for cold-start), with `mode` indicating which path was
    taken: "spawned" or "opened_in_running".
    """
    file_path = args.get("file_path")
    exe_path = args.get("exe_path") or _locate()
    if exe_path is None:
        return {"ok": False, "error": "Waveform binary not found"}

    if file_path:
        if not Path(file_path).exists():
            return {"ok": False, "error": f"file not found: {file_path}"}

    # Branch: is Waveform already running?
    pid, _ = _find_running()
    if pid is not None and file_path:
        return _open_in_running(str(file_path))

    # Cold start: spawn with file as CLI arg
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
        return {"ok": False, "error": str(e), "type": type(e).__name__}
    return {"ok": True, "exe_path": exe_path, "file_path": file_path,
            "mode": "spawned"}


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
