"""Desktop automation — keyboard, mouse, screenshots.

Used to drive Waveform's GUI when no API surface covers what we need.
Built on `pyautogui`. Cross-platform but Windows-tested.

Screenshots are saved into the project's `state/screenshots/` dir and the
return value includes the path so the host can surface them. We don't try
to base64-stuff images through the MCP response — too heavy.

Safety:
  - Every tool here depends on something visible on screen. Always call
    waveform_focus first when targeting Waveform.
  - pyautogui has a 0.1s pause between actions by default; we keep that.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

try:
    import pyautogui  # type: ignore[import-untyped]
except ImportError:
    pyautogui = None  # type: ignore[assignment]


SHOTS_DIR = Path("state/screenshots")


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
    if pyautogui is None:
        return {"ok": False, "error": "pyautogui not installed"}
    return None


def desktop_screenshot(args: dict) -> dict:
    err = _check()
    if err:
        return err
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    name = args.get("name") or f"shot_{int(time.time()*1000)}.png"
    if not name.endswith(".png"):
        name += ".png"
    path = SHOTS_DIR / name
    region = args.get("region")
    img = pyautogui.screenshot(region=tuple(region) if region else None)
    img.save(path)
    return {"ok": True, "path": str(path), "size": list(img.size)}


def desktop_key(args: dict) -> dict:
    err = _check()
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
    err = _check()
    if err:
        return err
    pyautogui.write(args["text"], interval=float(args.get("interval", 0.01)))
    return {"ok": True, "chars": len(args["text"])}


def desktop_click(args: dict) -> dict:
    err = _check()
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
