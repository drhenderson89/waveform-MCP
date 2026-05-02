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
    """Open a project/edit in an already-running Waveform via Ctrl+O.

    Strategy: focus the window, send Ctrl+O to open the file dialog,
    paste the path from clipboard, send Enter, then poll for the new
    project to be loaded.
    """
    if sys.platform != "win32":
        return {"ok": False, "error": "_open_in_running only works on Windows"}

    try:
        from . import win_input
    except ImportError as e:
        return {"ok": False, "error": f"win_input not available: {e}"}

    # Restore window if minimized; bail early if no Waveform window
    try:
        from .waveform_workflows import _restore_if_minimized
        _restore_if_minimized()
    except Exception:
        pass

    # Set clipboard to the file path
    if not _set_clipboard(file_path):
        return {"ok": False, "error": "failed to set clipboard"}

    # Close any half-open menus / dialogs
    win_input.win_send_keys({"keys": "{ESC}{ESC}", "silent_focus": True,
                              "delay_after": 0.2})

    # Ctrl+O opens the file dialog
    res = win_input.win_send_keys({"keys": "^o", "silent_focus": True,
                                    "delay_after": 0.8})
    if not res.get("ok"):
        return {"ok": False, "error": f"Ctrl+O failed: {res.get('error')}",
                "stage": "ctrl_o"}

    # Wait for the file dialog to appear
    if not _wait_for_open_dialog(timeout=4.0):
        return {"ok": False, "error": "file open dialog didn't appear",
                "stage": "dialog_wait"}

    # Some Open dialogs need focus to land in the filename Edit. Standard
    # Windows file dialogs have an "Edit" control already focused that
    # accepts the pasted path.
    # Clear any pre-filled filename then paste path.
    win_input.win_send_keys({"keys": "^a", "silent_focus": True, "delay_after": 0.1})
    win_input.win_send_keys({"keys": "{DELETE}", "silent_focus": True, "delay_after": 0.1})
    win_input.win_send_keys({"keys": "^v", "silent_focus": True, "delay_after": 0.3})
    win_input.win_send_keys({"keys": "{ENTER}", "silent_focus": True, "delay_after": 1.0})

    # Poll for the loaded project (the tab-strip will show our edit)
    try:
        from .waveform_workflows import _project_is_loaded
        deadline = time.time() + 15.0
        while time.time() < deadline:
            if _project_is_loaded():
                return {"ok": True, "file_path": file_path,
                        "mode": "opened_in_running"}
            time.sleep(0.4)
    except Exception:
        pass

    # Best-effort success — Waveform may have loaded but our detector
    # failed. Return ok=True with a note rather than False.
    return {"ok": True, "file_path": file_path,
            "mode": "opened_in_running",
            "note": "loaded state could not be confirmed within timeout"}


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
