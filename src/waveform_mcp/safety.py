"""Safety guards shared by the tool modules.

Settings come from environment variables, read at call time. They are set by
whoever configures the MCP server, never by a tool argument, so the model
driving the tools cannot widen its own permissions:

  WAVEFORM_MCP_ENABLE_DESKTOP  "1" registers the screen-wide desktop_* tools.
                               Off by default: they send input to whatever window
                               has focus. The win_* tools cover Waveform without them.
  WAVEFORM_EXE                 Path to the Waveform executable, for installs the
                               auto-detection misses (e.g. Waveform Free). Must be
                               named Waveform*.
  WAVEFORM_MCP_STATE_DIR       Folder for events.log and state/ (screenshots,
                               schema snapshots). Default: the current directory.
  WAVEFORM_MCP_BACKUP_KEEP     Backups kept per .tracktionedit before it is
                               overwritten. Default 10; 0 turns backups off.

The input guards below (Windows only) check what is actually under the cursor
or in the foreground before real OS input is sent, so a click or keystroke
meant for Waveform can never land in another application.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

_TRUE = {"1", "true", "yes", "on"}

DESKTOP_DISABLED_ERROR = (
    "desktop_* tools are disabled. They send input to whatever window has focus, "
    "so they only run when the server is started with WAVEFORM_MCP_ENABLE_DESKTOP=1. "
    "Use the win_* tools, which target Waveform only."
)


# ---------- settings ----------


def desktop_enabled() -> bool:
    return os.environ.get("WAVEFORM_MCP_ENABLE_DESKTOP", "").strip().lower() in _TRUE


def state_dir() -> Path:
    configured = os.environ.get("WAVEFORM_MCP_STATE_DIR", "").strip()
    return Path(configured).expanduser() if configured else Path.cwd()


def events_log_path() -> Path:
    return state_dir() / "events.log"


def screenshots_dir() -> Path:
    return state_dir() / "state" / "screenshots"


def snapshots_dir() -> Path:
    return state_dir() / "state" / "schema_snapshots"


def backup_keep() -> int:
    raw = os.environ.get("WAVEFORM_MCP_BACKUP_KEEP", "").strip()
    if not raw:
        return 10
    try:
        return max(0, int(raw))
    except ValueError:
        return 10


# ---------- file names ----------


_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._ -]")


def safe_filename(name: str | None, default: str, suffix: str) -> str:
    """Reduce a tool-supplied file name to a bare name inside its folder.

    Drops any directory part (so "../../x" cannot escape the screenshots
    folder), replaces unusual characters, and ensures the suffix.
    """
    base = Path(str(name or "")).name.strip()
    base = _UNSAFE_CHARS.sub("_", base).strip(" .")
    if not base:
        base = default
    if not base.lower().endswith(suffix.lower()):
        base += suffix
    return base


# ---------- the Waveform process ----------


def is_waveform_process_name(name: str | None) -> bool:
    """True for Waveform's own executable ("Waveform 13.exe", "Waveform.exe"...).

    Rejects this server's own entry points ("waveform-mcp.exe",
    "waveform-mcp-preview.exe"), which a plain substring match picked up, and
    Tracktion's other tools (they don't start with "waveform").
    """
    n = (name or "").strip().lower()
    if not n.startswith("waveform"):
        return False
    rest = n[len("waveform"):]
    return rest[:1] not in ("-", "_")


def find_waveform_process() -> tuple[int | None, str | None]:
    """(pid, exe path) of the running Waveform, or (None, None)."""
    try:
        import psutil
    except ImportError:
        return None, None
    own = os.getpid()
    for proc in psutil.process_iter(["pid", "name", "exe"]):
        try:
            pid = int(proc.info["pid"])
            if pid == own:
                continue
            if is_waveform_process_name(proc.info.get("name")):
                return pid, proc.info.get("exe") or None
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None, None


def waveform_exe_error(path: str | Path) -> str | None:
    """Why `path` is not an acceptable Waveform executable, or None if it is."""
    p = Path(path)
    if not p.is_file():
        return f"not a file: {p}"
    if not is_waveform_process_name(p.name):
        return f"not a Waveform executable (name must start with 'Waveform'): {p.name}"
    if sys.platform == "win32" and p.suffix.lower() != ".exe":
        return f"not an .exe: {p.name}"
    return None


def configured_waveform_exe() -> str | None:
    """WAVEFORM_EXE if it is set and names a real Waveform executable."""
    configured = os.environ.get("WAVEFORM_EXE", "").strip()
    if configured and waveform_exe_error(configured) is None:
        return configured
    return None


def _version_key(folder: Path) -> int:
    m = re.search(r"\d+", folder.name)
    return int(m.group()) if m else -1


def waveform_install_dirs() -> list[Path]:
    """Folders that may hold Waveform and its bundled tools, best first:
    WAVEFORM_EXE's folder, the running Waveform's folder, then every
    Program Files/Tracktion/Waveform* folder, newest version first."""
    dirs: list[Path] = []
    configured = configured_waveform_exe()
    if configured:
        dirs.append(Path(configured).parent)
    _, running_exe = find_waveform_process()
    if running_exe:
        dirs.append(Path(running_exe).parent)
    for root in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if not root:
            continue
        tracktion = Path(root) / "Tracktion"
        if tracktion.is_dir():
            found = [d for d in tracktion.iterdir()
                     if d.is_dir() and d.name.lower().startswith("waveform")]
            dirs.extend(sorted(found, key=_version_key, reverse=True))
    unique: list[Path] = []
    for d in dirs:
        if d not in unique:
            unique.append(d)
    return unique


def find_bundled_tool(name: str) -> str | None:
    """Path to a tool Waveform ships (ffmpeg, ffprobe) from whichever Waveform
    is installed, falling back to PATH. The original hard-coded the Waveform
    13/12 folders, so other versions fell through to a system copy."""
    exe_name = name if name.lower().endswith(".exe") or sys.platform != "win32" else f"{name}.exe"
    for folder in waveform_install_dirs():
        candidate = folder / exe_name
        if candidate.is_file():
            return str(candidate)
    import shutil

    return shutil.which(name)


# ---------- input guards (Windows) ----------


_USER32 = None


def _user32():
    """A private user32 handle with typed signatures.

    Private so the argtypes/restype set here don't change behaviour for the
    other modules, which call the shared ctypes.windll.user32 untyped.
    """
    global _USER32
    if _USER32 is not None:
        return _USER32
    import ctypes
    import ctypes.wintypes as wt

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.WindowFromPoint.argtypes = [wt.POINT]
    user32.WindowFromPoint.restype = wt.HWND
    user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
    user32.GetAncestor.restype = wt.HWND
    user32.GetForegroundWindow.restype = wt.HWND
    user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
    user32.GetWindowThreadProcessId.restype = wt.DWORD
    _USER32 = user32
    return user32


def window_pid(hwnd) -> int | None:
    if not hwnd or sys.platform != "win32":
        return None
    import ctypes
    import ctypes.wintypes as wt

    pid = wt.DWORD()
    _user32().GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value) or None


def foreground_pid() -> int | None:
    if sys.platform != "win32":
        return None
    return window_pid(_user32().GetForegroundWindow())


def pid_at_point(x: int, y: int) -> int | None:
    """PID owning the top-level window under screen point (x, y)."""
    if sys.platform != "win32":
        return None
    import ctypes.wintypes as wt

    user32 = _user32()
    hwnd = user32.WindowFromPoint(wt.POINT(int(x), int(y)))
    if not hwnd:
        return None
    GA_ROOT = 2
    return window_pid(user32.GetAncestor(hwnd, GA_ROOT) or hwnd)


def point_on_waveform_error(x: int, y: int) -> str | None:
    """None if screen point (x, y) is on a Waveform window, else why not.

    Off Windows the check cannot be made and passes, so the explicit opt-in
    (WAVEFORM_MCP_ENABLE_DESKTOP) is the only gate there.
    """
    if sys.platform != "win32":
        return None
    wf_pid, _ = find_waveform_process()
    if wf_pid is None:
        return "Waveform is not running"
    owner = pid_at_point(x, y)
    if owner != wf_pid:
        return (f"refusing to send mouse input to ({x}, {y}): that point is not on a "
                "Waveform window (another window is on top, or it is off the window)")
    return None


def foreground_is_waveform_error() -> str | None:
    """None if Waveform owns the foreground window, else why not."""
    if sys.platform != "win32":
        return None
    wf_pid, _ = find_waveform_process()
    if wf_pid is None:
        return "Waveform is not running"
    if foreground_pid() != wf_pid:
        return ("refusing to send keystrokes: Waveform is not the foreground window, "
                "so they would go to another application")
    return None
