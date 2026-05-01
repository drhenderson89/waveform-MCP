"""Windows-only: send input to Waveform without stealing focus.

Two surfaces:
  1. **PostMessage-based keystrokes** — sends WM_KEYDOWN/WM_KEYUP/WM_CHAR
     events directly to Waveform's window handle. Works while another app
     (the user's game, browser, whatever) keeps foreground focus. Reliable
     for menu-bar shortcuts (Ctrl+N, Ctrl+S, Alt-mnemonics). Less reliable
     for in-canvas input.

  2. **PrintWindow screenshots** — captures Waveform's window contents into
     a bitmap regardless of whether it's foreground, occluded, or minimized.
     Lets us "see" the app without alt-tabbing the user there.

When PostMessage isn't enough (e.g. for actions that go through the keyboard
focus chain into a child control), we fall back to the AttachThreadInput
trick: briefly attach to Waveform's input queue, set focus, fire the key,
detach. The user's perceived foreground app is not changed.

This module is a no-op on non-Windows platforms.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

# Defer Win32-specific imports until call-time so the module imports cleanly
# on Linux/macOS (where it just refuses to run).


def _windows_only() -> dict | None:
    if sys.platform != "win32":
        return {"ok": False, "error": "win_input is Windows-only"}
    return None


SHOTS_DIR = Path("state/screenshots")


# ---------- tool defs ----------

TOOLS: list[dict[str, Any]] = [
    {
        "name": "win_waveform_info",
        "description": (
            "Locate Waveform's main window and report HWND, title, position, size. "
            "Use this before win_send_keys / win_screenshot to confirm the target exists."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "win_screenshot_waveform",
        "description": (
            "Capture Waveform's window contents (full window, including chrome) to a "
            "PNG under state/screenshots/. Works regardless of foreground state — "
            "Waveform can be behind other windows or minimized. Returns the path."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Optional filename hint"},
            },
        },
    },
    {
        "name": "win_send_keys",
        "description": (
            "Send keystrokes to Waveform without stealing the user's foreground focus. "
            "Use the pywinauto syntax: '^n' = Ctrl+N, '^s' = Ctrl+S, "
            "'%fs' = Alt-F then S (File menu → Save), 'Hello~' = type 'Hello' then Enter, "
            "'{ENTER}', '{ESC}', '{F5}', '{TAB 3}' for special keys/repeats. "
            "If silent_focus=true (default), uses AttachThreadInput so the user's apparent "
            "foreground app is preserved. Set false to actually take focus."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["keys"],
            "properties": {
                "keys": {"type": "string"},
                "silent_focus": {"type": "boolean", "default": True},
                "delay_after": {
                    "type": "number",
                    "default": 0.2,
                    "description": "Seconds to wait after sending so the app can react",
                },
            },
        },
    },
    {
        "name": "win_uia_inspect",
        "description": (
            "Walk Waveform's UI Automation tree (recursive children() — JUCE doesn't "
            "honor descendants()). Returns named controls with rects. window_title "
            "targets a specific dialog (e.g. 'New Project'); omit for main app window."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "filter": {"type": "string", "description": "Substring filter on name"},
                "control_type": {"type": "string", "description": "Button, Edit, MenuItem, etc."},
                "window_title": {"type": "string", "description": "Target a specific window/dialog"},
                "max_results": {"type": "integer", "default": 200},
            },
        },
    },
    {
        "name": "win_uia_list_windows",
        "description": "List all top-level UIA windows in the Waveform process (main app + open dialogs).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "win_uia_invoke",
        "description": (
            "Click/activate a Waveform control by its accessible name via UIA Invoke "
            "pattern. Bypasses mouse and keyboard. Pass window_title to target a dialog."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {"type": "string"},
                "control_type": {"type": "string", "default": "Button"},
                "window_title": {"type": "string"},
                "delay_after": {"type": "number", "default": 0.5},
            },
        },
    },
    {
        "name": "win_uia_focus_and_type",
        "description": (
            "Type text into an Edit control. JUCE Edits don't expose ValuePattern, "
            "so we focus the control via UIA and then send keystrokes. Target by `name` "
            "if the Edit has an accessible name, or by `index` (Nth Edit in the window) "
            "for unnamed JUCE fields. Pass window_title to target a dialog."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["text"],
            "properties": {
                "text": {"type": "string"},
                "name": {"type": "string"},
                "index": {"type": "integer", "description": "Nth Edit (0-based)"},
                "window_title": {"type": "string"},
                "clear_first": {"type": "boolean", "default": True},
                "delay_after": {"type": "number", "default": 0.3},
            },
        },
    },
    {
        "name": "win_drag_real",
        "description": (
            "Drag from one point to another via real OS-level mouse input. "
            "Mouse-down at (from_x, from_y), gradual move to (to_x, to_y), mouse-up. "
            "JUCE drag-targets need the move to be paced so dragOver events fire and "
            "drop zones can highlight; default duration is 0.5s. Coordinates are "
            "ABSOLUTE screen coords (not window-relative) — use win_waveform_info to "
            "compute absolutes from window-relative points first, or pass abs=true."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["from_x", "from_y", "to_x", "to_y"],
            "properties": {
                "from_x": {"type": "integer"},
                "from_y": {"type": "integer"},
                "to_x": {"type": "integer"},
                "to_y": {"type": "integer"},
                "duration": {"type": "number", "default": 0.5},
                "absolute": {
                    "type": "boolean",
                    "default": False,
                    "description": "If false (default), coords are window-relative to Waveform",
                },
                "settle_after": {"type": "number", "default": 0.6},
            },
        },
    },
    {
        "name": "win_click_real",
        "description": (
            "Move the cursor to a screen position and synthesize a real OS-level mouse "
            "click via SendInput. Unlike win_click_waveform's PostMessage approach, this "
            "WORKS on JUCE controls because it goes through the same input path as a real "
            "user click. Cost: briefly takes foreground focus (~50ms). Use when the target "
            "control isn't UIA-invokable (e.g. unnamed JUCE custom controls). Coordinates "
            "are in window-relative space; we add Waveform's window origin."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["x", "y"],
            "properties": {
                "x": {"type": "integer", "description": "Window-relative X"},
                "y": {"type": "integer", "description": "Window-relative Y"},
                "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
                "clicks": {"type": "integer", "default": 1},
                "delay_after": {"type": "number", "default": 0.4},
            },
        },
    },
    {
        "name": "win_click_waveform",
        "description": (
            "Send a left-click to Waveform at window-relative (x, y) coordinates "
            "via WM_LBUTTONDOWN / WM_LBUTTONUP messages — bypasses focus. "
            "Useful when the action lives only on a button (no keyboard shortcut). "
            "Note: JUCE's hit-testing may or may not honor synthetic mouse messages."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["x", "y"],
            "properties": {
                "x": {"type": "integer", "description": "Window-relative X (from window left edge)"},
                "y": {"type": "integer", "description": "Window-relative Y (from window top edge)"},
                "delay_after": {"type": "number", "default": 0.4},
            },
        },
    },
    {
        "name": "win_restore_waveform",
        "description": (
            "Restore Waveform's window from minimized state without stealing foreground "
            "focus. ShowWindow(SW_SHOWNOACTIVATE) — Waveform becomes visible but the "
            "user's currently-active app stays active."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "win_list_windows",
        "description": "Diagnostic: list all top-level windows with HWND, PID, title.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filter": {
                    "type": "string",
                    "description": "Optional substring filter on title (case-insensitive)",
                }
            },
        },
    },
]


# ---------- helpers ----------


def _waveform_pid() -> int | None:
    """Return the PID of the running Waveform.exe process, or None."""
    try:
        import psutil
    except ImportError:
        return None
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if "waveform" in name and "tracktion" not in name:  # exclude Tracktion Download Manager
                return int(proc.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


def _find_waveform_hwnd() -> int | None:
    """Find the main Waveform window. Filters by PID to avoid false matches
    (e.g. a browser tab whose title contains 'Waveform')."""
    import ctypes
    import ctypes.wintypes as wt

    target_pid = _waveform_pid()
    if target_pid is None:
        return None

    user32 = ctypes.windll.user32
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    found: list[int] = []

    def callback(hwnd: int, lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        # PID filter — must belong to the Waveform process
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if int(pid.value) != target_pid:
            return True
        # Must have a caption (real window, not popup/tooltip)
        GWL_STYLE = -16
        WS_CAPTION = 0x00C00000
        style = user32.GetWindowLongW(hwnd, GWL_STYLE)
        if not (style & WS_CAPTION):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        found.append(hwnd)
        return True

    user32.EnumWindows(EnumWindowsProc(callback), 0)
    return found[0] if found else None


def _get_window_rect(hwnd: int) -> tuple[int, int, int, int]:
    import ctypes
    import ctypes.wintypes as wt

    rect = wt.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def _get_pid(hwnd: int) -> int:
    import ctypes
    import ctypes.wintypes as wt

    pid = wt.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


# ---------- impls ----------


def win_waveform_info(args: dict) -> dict:
    err = _windows_only()
    if err:
        return err

    hwnd = _find_waveform_hwnd()
    if not hwnd:
        return {"ok": False, "error": "no Waveform window found"}

    import ctypes

    user32 = ctypes.windll.user32
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    title = buf.value

    l, t, r, b = _get_window_rect(hwnd)
    pid = _get_pid(hwnd)

    return {
        "ok": True,
        "hwnd": hwnd,
        "pid": pid,
        "title": title,
        "rect": [l, t, r, b],
        "size": [r - l, b - t],
    }


def win_screenshot_waveform(args: dict) -> dict:
    err = _windows_only()
    if err:
        return err

    hwnd = _find_waveform_hwnd()
    if not hwnd:
        return {"ok": False, "error": "no Waveform window found"}

    import ctypes
    import ctypes.wintypes as wt

    from PIL import Image

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    l, t, r, b = _get_window_rect(hwnd)
    width, height = r - l, b - t
    if width <= 0 or height <= 0:
        return {"ok": False, "error": f"invalid window rect: {[l, t, r, b]}"}

    hwnd_dc = user32.GetWindowDC(hwnd)
    mfc_dc = gdi32.CreateCompatibleDC(hwnd_dc)
    bmp = gdi32.CreateCompatibleBitmap(hwnd_dc, width, height)
    gdi32.SelectObject(mfc_dc, bmp)

    # PW_RENDERFULLCONTENT = 2 — needed for many modern apps incl. JUCE
    PW_RENDERFULLCONTENT = 0x00000002
    result = user32.PrintWindow(hwnd, mfc_dc, PW_RENDERFULLCONTENT)

    bmpinfo = (ctypes.c_byte * (width * height * 4))()

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wt.DWORD),
            ("biWidth", ctypes.c_long),
            ("biHeight", ctypes.c_long),
            ("biPlanes", wt.WORD),
            ("biBitCount", wt.WORD),
            ("biCompression", wt.DWORD),
            ("biSizeImage", wt.DWORD),
            ("biXPelsPerMeter", ctypes.c_long),
            ("biYPelsPerMeter", ctypes.c_long),
            ("biClrUsed", wt.DWORD),
            ("biClrImportant", wt.DWORD),
        ]

    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth = width
    bi.biHeight = -height  # negative = top-down
    bi.biPlanes = 1
    bi.biBitCount = 32
    bi.biCompression = 0  # BI_RGB

    DIB_RGB_COLORS = 0
    gdi32.GetDIBits(mfc_dc, bmp, 0, height, bmpinfo, ctypes.byref(bi), DIB_RGB_COLORS)

    img = Image.frombuffer("RGBA", (width, height), bmpinfo, "raw", "BGRA", 0, 1)
    img = img.convert("RGB")

    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mfc_dc)
    user32.ReleaseDC(hwnd, hwnd_dc)

    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    name = args.get("name") or f"waveform_{int(time.time()*1000)}.png"
    if not name.endswith(".png"):
        name += ".png"
    path = SHOTS_DIR / name
    img.save(path)

    return {
        "ok": True,
        "path": str(path),
        "size": [width, height],
        "print_window_result": int(result),
    }


def win_send_keys(args: dict) -> dict:
    err = _windows_only()
    if err:
        return err

    keys: str = args["keys"]
    silent: bool = bool(args.get("silent_focus", True))
    delay: float = float(args.get("delay_after", 0.2))

    hwnd = _find_waveform_hwnd()
    if not hwnd:
        return {"ok": False, "error": "no Waveform window found"}

    try:
        from pywinauto.application import Application  # type: ignore[import-untyped]
        from pywinauto.keyboard import send_keys  # type: ignore[import-untyped]
    except ImportError:
        return {"ok": False, "error": "pywinauto not installed"}

    pid = _get_pid(hwnd)

    if silent:
        # AttachThreadInput trick: attach our thread to Waveform's input queue,
        # set focus on Waveform's HWND, send key via the standard Windows input
        # path, then detach. The OS records the keystroke against Waveform but
        # the user's perceived foreground app is unchanged.
        import ctypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        target_thread = user32.GetWindowThreadProcessId(hwnd, None)
        current_thread = kernel32.GetCurrentThreadId()
        attached = user32.AttachThreadInput(current_thread, target_thread, True)
        try:
            user32.SetFocus(hwnd)
            send_keys(keys, with_spaces=True, pause=0.02)
        finally:
            if attached:
                user32.AttachThreadInput(current_thread, target_thread, False)
    else:
        try:
            app = Application(backend="win32").connect(process=pid)
            app.window(handle=hwnd).set_focus()
        except Exception as e:
            return {"ok": False, "error": f"connect failed: {e}"}
        send_keys(keys, with_spaces=True, pause=0.02)

    if delay > 0:
        time.sleep(delay)

    return {"ok": True, "hwnd": hwnd, "keys": keys, "silent": silent}


def _uia_app():
    """Return a pywinauto UIA Application connected to Waveform, or None."""
    try:
        from pywinauto.application import Application  # type: ignore[import-untyped]
    except ImportError:
        return None, "pywinauto not installed"
    pid = _waveform_pid()
    if pid is None:
        return None, "Waveform not running"
    try:
        app = Application(backend="uia").connect(process=pid, timeout=3)
        return app, None
    except Exception as e:
        return None, f"connect failed: {e}"


def _uia_window(app, title: str | None = None):
    """Pick a window from the Waveform process. If title is given, find that
    one specifically (used to target dialogs); otherwise the main window.
    """
    if title:
        return app.window(title=title)
    # Prefer the window whose title equals 'Waveform' (main app).
    for w in app.windows():
        if w.window_text().lower().startswith("waveform"):
            return w
    return app.top_window()


def _walk_uia(elem, want_types: tuple | None = None, max_results: int = 500) -> list:
    """Recursive children() walker. JUCE's UIA implementation is shallow enough
    that pywinauto's descendants() returns nothing — but children() works at
    each level, so we recurse manually.
    """
    out: list = []

    def visit(e):
        if len(out) >= max_results:
            return
        try:
            ctype = e.element_info.control_type
            if want_types is None or ctype in want_types:
                rect = e.element_info.rectangle
                out.append(
                    {
                        "elem": e,
                        "name": e.window_text() or "",
                        "control_type": ctype,
                        "rect": [rect.left, rect.top, rect.right, rect.bottom] if rect else None,
                    }
                )
            for kid in e.children():
                visit(kid)
        except Exception:
            return

    visit(elem)
    return out


def win_uia_inspect(args: dict) -> dict:
    err = _windows_only()
    if err:
        return err
    app, error = _uia_app()
    if app is None:
        return {"ok": False, "error": error}

    flt = (args.get("filter") or "").lower()
    ctype = args.get("control_type")
    limit = int(args.get("max_results", 200))
    window_title = args.get("window_title")

    try:
        win = _uia_window(app, window_title)
        types = (ctype,) if ctype else None
        rows = _walk_uia(win, want_types=types, max_results=limit * 4)
        out = []
        for r in rows:
            if flt and flt not in r["name"].lower():
                continue
            out.append({k: v for k, v in r.items() if k != "elem"})
            if len(out) >= limit:
                break
        return {"ok": True, "controls": out, "window_title": win.window_text()}
    except Exception as e:
        return {"ok": False, "error": f"inspect failed: {e}", "type": type(e).__name__}


def _find_by_name(win, name: str, ctype: str | None) -> list:
    """Find UIA elements by exact-name then substring. Returns wrappers list."""
    types = (ctype,) if ctype else None
    rows = _walk_uia(win, want_types=types, max_results=500)
    exact = [r for r in rows if r["name"] == name]
    if exact:
        return [r["elem"] for r in exact]
    needle = name.lower()
    sub = [r for r in rows if needle in r["name"].lower()]
    return [r["elem"] for r in sub]


def win_uia_invoke(args: dict) -> dict:
    err = _windows_only()
    if err:
        return err
    app, error = _uia_app()
    if app is None:
        return {"ok": False, "error": error}

    name = args["name"]
    ctype = args.get("control_type", "Button")
    window_title = args.get("window_title")
    delay = float(args.get("delay_after", 0.5))

    try:
        win = _uia_window(app, window_title)
        candidates = _find_by_name(win, name, ctype)
        if not candidates:
            return {"ok": False, "error": f"no {ctype} with name {name!r} found"}
        elem = candidates[0]
        try:
            elem.invoke()
        except Exception:
            try:
                elem.click_input()
            except Exception as e2:
                return {"ok": False, "error": f"invoke and click both failed: {e2}"}
        if delay > 0:
            time.sleep(delay)
        return {"ok": True, "name": name, "matched": len(candidates)}
    except Exception as e:
        return {"ok": False, "error": str(e), "type": type(e).__name__}


def win_uia_focus_and_type(args: dict) -> dict:
    """Focus an Edit control via UIA, then type text via the keystroke channel.

    Targeting strategies (in priority order):
      - by `name` if the field has an accessible name
      - by `index` (Nth Edit control in the window) — useful for unnamed JUCE fields
      - by `near_label` — find a Text/Static near a label, then the Edit closest to it
    """
    err = _windows_only()
    if err:
        return err
    app, error = _uia_app()
    if app is None:
        return {"ok": False, "error": error}

    text = args["text"]
    name = args.get("name")
    index = args.get("index")
    window_title = args.get("window_title")
    clear_first = bool(args.get("clear_first", True))
    delay = float(args.get("delay_after", 0.3))

    try:
        from pywinauto.keyboard import send_keys  # type: ignore[import-untyped]

        win = _uia_window(app, window_title)

        elem = None
        if name:
            cands = _find_by_name(win, name, "Edit")
            if cands:
                elem = cands[0]
        if elem is None and index is not None:
            edits = _walk_uia(win, want_types=("Edit",), max_results=50)
            i = int(index)
            if 0 <= i < len(edits):
                elem = edits[i]["elem"]
        if elem is None:
            return {"ok": False, "error": "Edit control not found by name or index"}

        try:
            elem.set_focus()
        except Exception:
            pass
        time.sleep(0.1)
        if clear_first:
            send_keys("^a{DELETE}", with_spaces=True, pause=0.02)
        send_keys(text, with_spaces=True, pause=0.01)
        if delay > 0:
            time.sleep(delay)
        return {"ok": True, "text": text}
    except Exception as e:
        return {"ok": False, "error": str(e), "type": type(e).__name__}


def win_uia_list_windows(args: dict) -> dict:
    """List all top-level UIA windows in the Waveform process. Useful to see
    dialog windows separately from the main app window.
    """
    err = _windows_only()
    if err:
        return err
    app, error = _uia_app()
    if app is None:
        return {"ok": False, "error": error}
    out = []
    try:
        for w in app.windows():
            r = w.element_info.rectangle
            out.append(
                {
                    "title": w.window_text(),
                    "class": w.element_info.class_name,
                    "rect": [r.left, r.top, r.right, r.bottom] if r else None,
                }
            )
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "windows": out}


def win_drag_real(args: dict) -> dict:
    """Real OS-level drag via SendInput. Briefly takes focus."""
    err = _windows_only()
    if err:
        return err
    try:
        import pyautogui  # type: ignore[import-untyped]
    except ImportError:
        return {"ok": False, "error": "pyautogui not installed"}

    fx = int(args["from_x"])
    fy = int(args["from_y"])
    tx = int(args["to_x"])
    ty = int(args["to_y"])
    absolute = bool(args.get("absolute", False))

    if not absolute:
        info = win_waveform_info({})
        if not info.get("ok"):
            return info
        rect = info["rect"]
        fx += rect[0]
        fy += rect[1]
        tx += rect[0]
        ty += rect[1]

    duration = float(args.get("duration", 0.5))
    settle = float(args.get("settle_after", 0.6))

    # Move to start, mouse-down, slow drag to end, mouse-up
    pyautogui.moveTo(fx, fy, duration=0.1)
    pyautogui.mouseDown(button="left")
    time.sleep(0.1)  # let JUCE register the down event
    # gradual move so JUCE drag-over events fire
    pyautogui.moveTo(tx, ty, duration=duration)
    time.sleep(0.15)  # let drop target highlight
    pyautogui.mouseUp(button="left")

    if settle > 0:
        time.sleep(settle)

    return {
        "ok": True,
        "from_abs": [fx, fy],
        "to_abs": [tx, ty],
    }


def win_click_real(args: dict) -> dict:
    """Real OS-level click via SendInput, positioned at Waveform-window-relative (x,y).
    Briefly takes focus. The only viable path for JUCE custom controls that ignore
    PostMessage clicks and have no UIA name."""
    err = _windows_only()
    if err:
        return err
    try:
        import pyautogui  # type: ignore[import-untyped]
    except ImportError:
        return {"ok": False, "error": "pyautogui not installed"}

    info = win_waveform_info({})
    if not info.get("ok"):
        return info
    rect = info["rect"]
    abs_x = rect[0] + int(args["x"])
    abs_y = rect[1] + int(args["y"])

    button = args.get("button", "left")
    clicks = int(args.get("clicks", 1))

    pyautogui.click(x=abs_x, y=abs_y, button=button, clicks=clicks, interval=0.05)
    delay = float(args.get("delay_after", 0.4))
    if delay > 0:
        time.sleep(delay)
    return {"ok": True, "abs": [abs_x, abs_y], "rel": [int(args["x"]), int(args["y"])]}


def win_click_waveform(args: dict) -> dict:
    err = _windows_only()
    if err:
        return err
    hwnd = _find_waveform_hwnd()
    if not hwnd:
        return {"ok": False, "error": "no Waveform window found"}
    import ctypes
    user32 = ctypes.windll.user32

    x = int(args["x"])
    y = int(args["y"])
    lparam = (y << 16) | (x & 0xFFFF)

    WM_LBUTTONDOWN = 0x0201
    WM_LBUTTONUP = 0x0202
    MK_LBUTTON = 0x0001

    user32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lparam)
    time.sleep(0.05)
    user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lparam)

    delay = float(args.get("delay_after", 0.4))
    if delay > 0:
        time.sleep(delay)

    return {"ok": True, "hwnd": hwnd, "at": [x, y]}


def win_restore_waveform(args: dict) -> dict:
    err = _windows_only()
    if err:
        return err
    hwnd = _find_waveform_hwnd()
    if not hwnd:
        return {"ok": False, "error": "no Waveform window found"}
    import ctypes
    user32 = ctypes.windll.user32
    SW_SHOWNOACTIVATE = 4
    SW_RESTORE = 9
    # If minimized, restore; otherwise ensure shown without activating.
    if user32.IsIconic(hwnd):
        # SW_RESTORE will activate; we want a no-activate variant. The closest
        # is to send WM_SYSCOMMAND SC_RESTORE which restores without activation.
        WM_SYSCOMMAND = 0x0112
        SC_RESTORE = 0xF120
        user32.PostMessageW(hwnd, WM_SYSCOMMAND, SC_RESTORE, 0)
    else:
        user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
    return {"ok": True, "hwnd": hwnd}


def win_list_windows(args: dict) -> dict:
    err = _windows_only()
    if err:
        return err

    import ctypes
    import ctypes.wintypes as wt

    user32 = ctypes.windll.user32
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    flt = (args.get("filter") or "").lower()
    rows: list[dict] = []

    def cb(hwnd: int, lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        if flt and flt not in title.lower():
            return True
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        rows.append({"hwnd": hwnd, "pid": int(pid.value), "title": title})
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return {"ok": True, "windows": rows[:200]}


HANDLERS = {
    "win_waveform_info": win_waveform_info,
    "win_screenshot_waveform": win_screenshot_waveform,
    "win_send_keys": win_send_keys,
    "win_uia_inspect": win_uia_inspect,
    "win_uia_list_windows": win_uia_list_windows,
    "win_uia_invoke": win_uia_invoke,
    "win_uia_focus_and_type": win_uia_focus_and_type,
    "win_drag_real": win_drag_real,
    "win_click_real": win_click_real,
    "win_click_waveform": win_click_waveform,
    "win_restore_waveform": win_restore_waveform,
    "win_list_windows": win_list_windows,
}
