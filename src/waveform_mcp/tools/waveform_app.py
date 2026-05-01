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
    file_path = args.get("file_path")
    exe_path = args.get("exe_path") or _locate()
    if exe_path is None:
        return {"ok": False, "error": "Waveform binary not found"}

    cmd = [exe_path]
    if file_path:
        if not Path(file_path).exists():
            return {"ok": False, "error": f"file not found: {file_path}"}
        cmd.append(file_path)

    try:
        # Detach so the MCP server doesn't own Waveform's lifetime
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
    return {"ok": True, "exe_path": exe_path, "file_path": file_path}


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
