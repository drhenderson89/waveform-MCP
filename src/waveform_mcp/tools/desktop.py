"""Desktop automation — keyboard, mouse, screenshots.

Used to drive Waveform's GUI when no API surface covers what we need.
Built on `pyautogui`. Cross-platform but Windows-tested.

Screenshots are saved into the project's `state/screenshots/` dir and the
return value includes the path so the host can surface them. We don't try
to base64-stuff images through the MCP response — too heavy.

Safety:
  - These tools are NOT registered unless the server runs with
    WAVEFORM_MCP_ENABLE_DESKTOP=1, and every handler re-checks that, because
    they act on the whole desktop rather than on Waveform alone.
  - On Windows, desktop_key / desktop_type refuse unless Waveform is the
    foreground window, and desktop_click refuses unless the point is on a
    Waveform window, so input can't land in another application.
  - Every tool here depends on something visible on screen. Always call
    waveform_focus first when targeting Waveform.
  - pyautogui has a 0.1s pause between actions by default; we keep that.
"""

from __future__ import annotations

import time
from typing import Any

from .. import safety

try:
    import pyautogui  # type: ignore[import-untyped]
except Exception:  # noqa: BLE001 - see below; must not be narrowed to ImportError
    # Deliberately broad. Importing pyautogui is not a pure import: it pulls in
    # mouseinfo, which on X11 evaluates os.environ['DISPLAY'] AT IMPORT TIME and
    # raises KeyError on a headless machine. That is not an ImportError, so a
    # narrower guard here does not catch it -- and because this module is
    # imported by server.py's tool collection, the failure took down the whole
    # server, all 157 tools, not just the desktop ones.
    #
    # Found when a Linux container build died with KeyError: 'DISPLAY' before
    # the server could answer a single request. Every caller below already
    # handles `pyautogui is None`, so degrading to that is the correct
    # behaviour: the desktop tools report themselves unavailable and the rest
    # of the server works.
    pyautogui = None  # type: ignore[assignment]



TOOLS: list[dict[str, Any]] = [
    {
        "name": "desktop_screenshot",
        "description": (
            "Capture the current screen (or a region) to a PNG file under "
            "state/screenshots/. Returns the path. Use this to verify that a UI "
            "action had the expected effect before sending the next."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "region": {
                    "type": "array",
                    "description": "Optional [x, y, width, height] crop",
                    "items": {"type": "integer"},
                    "minItems": 4,
                    "maxItems": 4,
                },
                "name": {"type": "string", "description": "Optional filename hint"},
            },
        },
    },
    {
        "name": "desktop_key",
        "description": (
            "Press a single key or chord. Examples: 'enter', 'esc', 'ctrl+s', "
            "'ctrl+shift+n', 'f5'. Sent to whatever window currently has focus."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["keys"],
            "properties": {
                "keys": {"type": "string"},
                "presses": {"type": "integer", "default": 1, "minimum": 1, "maximum": 10},
                "interval": {"type": "number", "default": 0.05},
            },
        },
    },
    {
        "name": "desktop_type",
        "description": "Type a string of text into the focused window.",
        "inputSchema": {
            "type": "object",
            "required": ["text"],
            "properties": {
                "text": {"type": "string"},
                "interval": {"type": "number", "default": 0.01},
            },
        },
    },
    {
        "name": "desktop_click",
        "description": (
            "Click at screen coordinates. Use desktop_screenshot first to know what's "
            "where. button ∈ {left, right, middle}."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["x", "y"],
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
                "clicks": {"type": "integer", "default": 1, "minimum": 1, "maximum": 3},
            },
        },
    },
    {
        "name": "desktop_move",
        "description": "Move the mouse to (x, y) without clicking.",
        "inputSchema": {
            "type": "object",
            "required": ["x", "y"],
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "duration": {"type": "number", "default": 0.1},
            },
        },
    },
    {
        "name": "desktop_screen_size",
        "description": "Return current screen resolution.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "desktop_wait",
        "description": "Sleep for `seconds` seconds. Use sparingly; prefer screenshot-then-act.",
        "inputSchema": {
            "type": "object",
            "required": ["seconds"],
            "properties": {
                "seconds": {"type": "number", "minimum": 0, "maximum": 30},
            },
        },
    },
]


def _check() -> dict | None:
    if not safety.desktop_enabled():
        return {"ok": False, "error": safety.DESKTOP_DISABLED_ERROR}
    if pyautogui is None:
        return {"ok": False, "error": "pyautogui not installed"}
    return None


def _check_keys() -> dict | None:
    err = _check()
    if err:
        return err
    reason = safety.foreground_is_waveform_error()
    return {"ok": False, "error": reason} if reason else None


def _check_point(x: int, y: int) -> dict | None:
    err = _check()
    if err:
        return err
    reason = safety.point_on_waveform_error(x, y)
    return {"ok": False, "error": reason} if reason else None


def desktop_screenshot(args: dict) -> dict:
    err = _check()
    if err:
        return err
    shots = safety.screenshots_dir()
    shots.mkdir(parents=True, exist_ok=True)
    name = safety.safe_filename(args.get("name"), f"shot_{int(time.time()*1000)}", ".png")
    path = shots / name
    region = args.get("region")
    img = pyautogui.screenshot(region=tuple(region) if region else None)
    img.save(path)
    return {"ok": True, "path": str(path), "size": list(img.size)}


def desktop_key(args: dict) -> dict:
    err = _check_keys()
    if err:
        return err
    keys = args["keys"]
    presses = int(args.get("presses", 1))
    interval = float(args.get("interval", 0.05))
    if "+" in keys:
        # chord: ctrl+s, ctrl+shift+n
        pyautogui.hotkey(*[k.strip() for k in keys.split("+")])
    else:
        pyautogui.press(keys, presses=presses, interval=interval)
    return {"ok": True, "keys": keys}


def desktop_type(args: dict) -> dict:
    err = _check_keys()
    if err:
        return err
    pyautogui.write(args["text"], interval=float(args.get("interval", 0.01)))
    return {"ok": True, "chars": len(args["text"])}


def desktop_click(args: dict) -> dict:
    err = _check_point(int(args["x"]), int(args["y"]))
    if err:
        return err
    pyautogui.click(
        x=int(args["x"]),
        y=int(args["y"]),
        button=args.get("button", "left"),
        clicks=int(args.get("clicks", 1)),
    )
    return {"ok": True, "at": [args["x"], args["y"]]}


def desktop_move(args: dict) -> dict:
    err = _check()
    if err:
        return err
    pyautogui.moveTo(int(args["x"]), int(args["y"]), duration=float(args.get("duration", 0.1)))
    return {"ok": True, "at": [args["x"], args["y"]]}


def desktop_screen_size(args: dict) -> dict:
    err = _check()
    if err:
        return err
    w, h = pyautogui.size()
    return {"ok": True, "width": int(w), "height": int(h)}


def desktop_wait(args: dict) -> dict:
    time.sleep(float(args["seconds"]))
    return {"ok": True}


HANDLERS = {
    "desktop_screenshot": desktop_screenshot,
    "desktop_key": desktop_key,
    "desktop_type": desktop_type,
    "desktop_click": desktop_click,
    "desktop_move": desktop_move,
    "desktop_screen_size": desktop_screen_size,
    "desktop_wait": desktop_wait,
}
