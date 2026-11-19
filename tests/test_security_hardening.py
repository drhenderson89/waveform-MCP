"""Tests for the security-hardening changes (see "Security hardening" in the README).

Everything that would touch the real desktop is exercised through monkeypatched
lookups, so these run headless on any platform. The last few tests call the
real Win32 guard functions on Windows, without sending any input.
"""

from __future__ import annotations

import os
import sys
import time
from types import SimpleNamespace

import pytest

from waveform_mcp import safety, xml_writer
from waveform_mcp.model import Edit
from waveform_mcp.server import _collect_tools
from waveform_mcp.tools import desktop, waveform_app

DESKTOP_TOOLS = {"desktop_screenshot", "desktop_key", "desktop_type", "desktop_click",
                 "desktop_move", "desktop_screen_size", "desktop_wait"}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("WAVEFORM_MCP_ENABLE_DESKTOP", "WAVEFORM_EXE", "WAVEFORM_MCP_STATE_DIR",
                "WAVEFORM_MCP_BACKUP_KEEP"):
        monkeypatch.delenv(var, raising=False)


# ---------- process matching ----------


@pytest.mark.parametrize("name", [
    "Waveform 13.exe", "Waveform.exe", "waveform 12.exe", "Waveform Free.exe", "Waveform 13",
])
def test_waveform_process_names_match(name):
    assert safety.is_waveform_process_name(name)


@pytest.mark.parametrize("name", [
    "waveform-mcp.exe", "waveform-mcp-preview.exe", "waveform_mcp", "python.exe",
    "Tracktion Download Manager.exe", "", None, "my-waveform.exe",
])
def test_non_waveform_process_names_rejected(name):
    assert not safety.is_waveform_process_name(name)


def _fake_proc(pid, name, exe):
    return SimpleNamespace(info={"pid": pid, "name": name, "exe": exe})


def test_find_waveform_process_skips_the_server_itself(monkeypatch):
    import psutil

    procs = [
        _fake_proc(os.getpid(), "Waveform 13.exe", r"C:\x\Waveform 13.exe"),  # ourselves
        _fake_proc(111, "waveform-mcp.exe", r"C:\venv\Scripts\waveform-mcp.exe"),
        _fake_proc(222, "Waveform 13.exe", r"C:\Program Files\Tracktion\Waveform 13.exe"),
    ]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter(procs))
    assert safety.find_waveform_process() == (222, r"C:\Program Files\Tracktion\Waveform 13.exe")


def test_find_waveform_process_none_when_only_the_server_runs(monkeypatch):
    import psutil

    procs = [_fake_proc(111, "waveform-mcp.exe", r"C:\venv\Scripts\waveform-mcp.exe")]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter(procs))
    assert safety.find_waveform_process() == (None, None)


# ---------- desktop tools ----------


def test_desktop_tools_not_registered_by_default():
    names = {t.name for t in _collect_tools()[0]}
    assert names.isdisjoint(DESKTOP_TOOLS)


def test_desktop_tools_registered_when_opted_in(monkeypatch):
    monkeypatch.setenv("WAVEFORM_MCP_ENABLE_DESKTOP", "1")
    tools, handlers = _collect_tools()
    assert DESKTOP_TOOLS <= {t.name for t in tools}
    assert DESKTOP_TOOLS <= set(handlers)


@pytest.mark.parametrize("tool,args", [
    ("desktop_screenshot", {}),
    ("desktop_key", {"keys": "enter"}),
    ("desktop_type", {"text": "hello"}),
    ("desktop_click", {"x": 1, "y": 1}),
    ("desktop_move", {"x": 1, "y": 1}),
])
def test_desktop_handlers_refuse_when_disabled(tool, args):
    result = desktop.HANDLERS[tool](args)
    assert result["ok"] is False
    assert "WAVEFORM_MCP_ENABLE_DESKTOP" in result["error"]


def test_desktop_click_refuses_off_waveform(monkeypatch):
    monkeypatch.setenv("WAVEFORM_MCP_ENABLE_DESKTOP", "1")
    monkeypatch.setattr(desktop, "pyautogui", SimpleNamespace(
        click=lambda **_: pytest.fail("clicked although the point is not on Waveform")))
    monkeypatch.setattr(safety, "point_on_waveform_error", lambda x, y: "not on Waveform")
    assert desktop.desktop_click({"x": 5, "y": 5}) == {"ok": False, "error": "not on Waveform"}


def test_desktop_type_refuses_when_waveform_not_foreground(monkeypatch):
    monkeypatch.setenv("WAVEFORM_MCP_ENABLE_DESKTOP", "1")
    monkeypatch.setattr(desktop, "pyautogui", SimpleNamespace(
        write=lambda *a, **k: pytest.fail("typed although Waveform is not in front")))
    monkeypatch.setattr(safety, "foreground_is_waveform_error", lambda: "not foreground")
    assert desktop.desktop_type({"text": "x"}) == {"ok": False, "error": "not foreground"}


# ---------- file names and state folder ----------


@pytest.mark.parametrize("name,expected", [
    ("../../evil", "evil.png"),
    (r"..\..\Windows\evil.png", "evil.png"),
    ("C:/Users/x/shot", "shot.png"),
    ("ok name.png", "ok name.png"),
    ("", "default.png"),
    (None, "default.png"),
    ("..", "default.png"),
    ("a<b>|c", "a_b__c.png"),
])
def test_safe_filename_stays_in_folder(name, expected):
    assert safety.safe_filename(name, "default", ".png") == expected


def test_state_dir_env_var(monkeypatch, tmp_path):
    assert safety.state_dir() == type(tmp_path).cwd()
    monkeypatch.setenv("WAVEFORM_MCP_STATE_DIR", str(tmp_path))
    assert safety.events_log_path() == tmp_path / "events.log"
    assert safety.screenshots_dir() == tmp_path / "state" / "screenshots"
    assert safety.snapshots_dir() == tmp_path / "state" / "schema_snapshots"


# ---------- launching Waveform ----------


def test_waveform_launch_rejects_exe_path():
    result = waveform_app.waveform_launch({"exe_path": r"C:\Windows\System32\calc.exe"})
    assert result["ok"] is False
    assert "WAVEFORM_EXE" in result["error"]


def test_launch_schema_has_no_exe_path():
    launch = next(t for t in waveform_app.TOOLS if t["name"] == "waveform_launch")
    assert "exe_path" not in launch["inputSchema"]["properties"]


def test_waveform_exe_validation(tmp_path):
    good = tmp_path / "Waveform 13.exe"
    good.write_bytes(b"")
    bad = tmp_path / "calc.exe"
    bad.write_bytes(b"")
    assert safety.waveform_exe_error(good) is None
    assert "not a Waveform executable" in safety.waveform_exe_error(bad)
    assert "not a file" in safety.waveform_exe_error(tmp_path / "missing.exe")


def test_waveform_exe_env_var_is_validated(monkeypatch, tmp_path):
    bad = tmp_path / "calc.exe"
    bad.write_bytes(b"")
    monkeypatch.setenv("WAVEFORM_EXE", str(bad))
    assert safety.configured_waveform_exe() is None
    good = tmp_path / "Waveform Free.exe"
    good.write_bytes(b"")
    monkeypatch.setenv("WAVEFORM_EXE", str(good))
    assert safety.configured_waveform_exe() == str(good)


def test_waveform_launch_only_opens_tracktionedit(monkeypatch, tmp_path):
    exe = tmp_path / "Waveform 13.exe"
    exe.write_bytes(b"")
    monkeypatch.setenv("WAVEFORM_EXE", str(exe))
    other = tmp_path / "script.bat"
    other.write_text("echo hi")
    result = waveform_app.waveform_launch({"file_path": str(other)})
    assert result["ok"] is False
    assert "not a .tracktionedit" in result["error"]


def test_bundled_tools_follow_the_installed_version(monkeypatch, tmp_path):
    tracktion = tmp_path / "Tracktion"
    for version in ("Waveform 9", "Waveform 13", "Waveform 14"):
        (tracktion / version).mkdir(parents=True)
        (tracktion / version / "ffmpeg.exe").write_bytes(b"")
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.setattr(safety, "find_waveform_process", lambda: (None, None))
    if sys.platform == "win32":
        expected = tracktion / "Waveform 14" / "ffmpeg.exe"
        assert safety.find_bundled_tool("ffmpeg") == str(expected)


def test_bundled_tools_prefer_the_running_waveform(monkeypatch, tmp_path):
    running = tmp_path / "Custom" / "Waveform 14.exe"
    running.parent.mkdir()
    running.write_bytes(b"")
    (running.parent / "ffprobe.exe").write_bytes(b"")
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "nothing"))
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.setattr(safety, "find_waveform_process", lambda: (1, str(running)))
    if sys.platform == "win32":
        assert safety.find_bundled_tool("ffprobe") == str(running.parent / "ffprobe.exe")


# ---------- quitting Waveform ----------


class _FakeProcess:
    def __init__(self, closes: bool):
        self.closes = closes
        self.killed = False
        self.terminated = False

    def wait(self, timeout=None):
        import psutil

        if self.closes or self.killed:
            return 0
        raise psutil.TimeoutExpired(timeout)

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


def _patch_quit(monkeypatch, proc):
    import psutil

    monkeypatch.setattr(waveform_app, "_find_running", lambda: (4242, "Waveform 13.exe"))
    monkeypatch.setattr(psutil, "Process", lambda pid: proc)
    monkeypatch.setattr(waveform_app, "_post_close", lambda pid: 1)


def test_waveform_quit_does_not_kill_by_default(monkeypatch):
    proc = _FakeProcess(closes=False)
    _patch_quit(monkeypatch, proc)
    result = waveform_app.waveform_quit({"timeout": 1})
    assert result["ok"] is False
    assert "force=true" in result["error"]
    assert not proc.killed


def test_waveform_quit_graceful_close(monkeypatch):
    proc = _FakeProcess(closes=True)
    _patch_quit(monkeypatch, proc)
    assert waveform_app.waveform_quit({}) == {"ok": True, "pid": 4242, "mode": "closed"}
    assert not proc.killed


def test_waveform_quit_force_kills(monkeypatch):
    proc = _FakeProcess(closes=False)
    _patch_quit(monkeypatch, proc)
    result = waveform_app.waveform_quit({"timeout": 1, "force": True})
    assert result == {"ok": True, "pid": 4242, "mode": "killed"}
    assert proc.killed


# ---------- project backups ----------


def _backups(folder):
    return sorted((folder / xml_writer.BACKUP_DIR_NAME).glob("*.tracktionedit"))


def test_write_backs_up_existing_file(tmp_path):
    target = tmp_path / "song.tracktionedit"
    target.write_text("original")
    xml_writer.write(Edit(), str(target))
    backups = _backups(tmp_path)
    assert len(backups) == 1
    assert backups[0].read_text() == "original"
    assert target.read_text().startswith("<?xml")
    assert not (tmp_path / "song.tracktionedit.tmp").exists()


def test_write_without_existing_file_makes_no_backup(tmp_path):
    xml_writer.write(Edit(), str(tmp_path / "new.tracktionedit"))
    assert _backups(tmp_path) == []


def test_backups_are_pruned_to_keep(monkeypatch, tmp_path):
    monkeypatch.setenv("WAVEFORM_MCP_BACKUP_KEEP", "3")
    target = tmp_path / "song.tracktionedit"
    target.write_text("v0")
    for i in range(1, 6):
        xml_writer.write(Edit(), str(target))
        target.write_text(f"v{i}")
        time.sleep(0.002)
    backups = _backups(tmp_path)
    assert len(backups) == 3


def test_pruning_leaves_other_projects_alone(monkeypatch, tmp_path):
    monkeypatch.setenv("WAVEFORM_MCP_BACKUP_KEEP", "1")
    other = tmp_path / "song.v2.tracktionedit"
    other.write_text("other")
    xml_writer.write(Edit(), str(other))
    target = tmp_path / "song.tracktionedit"
    for _ in range(3):
        target.write_text("x")
        xml_writer.write(Edit(), str(target))
    names = [p.name for p in _backups(tmp_path)]
    assert sum(n.startswith("song.v2.") for n in names) == 1
    assert sum(not n.startswith("song.v2.") for n in names) == 1


def test_backups_can_be_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("WAVEFORM_MCP_BACKUP_KEEP", "0")
    target = tmp_path / "song.tracktionedit"
    target.write_text("original")
    xml_writer.write(Edit(), str(target))
    assert not (tmp_path / xml_writer.BACKUP_DIR_NAME).exists()


# ---------- input guards (logic) ----------


@pytest.mark.skipif(sys.platform != "win32", reason="guards only check on Windows")
def test_point_guard_logic(monkeypatch):
    monkeypatch.setattr(safety, "find_waveform_process", lambda: (None, None))
    assert "not running" in safety.point_on_waveform_error(10, 10)
    monkeypatch.setattr(safety, "find_waveform_process", lambda: (500, "Waveform 13.exe"))
    monkeypatch.setattr(safety, "pid_at_point", lambda x, y: 999)
    assert "not on a Waveform window" in safety.point_on_waveform_error(10, 10)
    monkeypatch.setattr(safety, "pid_at_point", lambda x, y: 500)
    assert safety.point_on_waveform_error(10, 10) is None


@pytest.mark.skipif(sys.platform != "win32", reason="guards only check on Windows")
def test_foreground_guard_logic(monkeypatch):
    monkeypatch.setattr(safety, "find_waveform_process", lambda: (500, "Waveform 13.exe"))
    monkeypatch.setattr(safety, "foreground_pid", lambda: 999)
    assert "not the foreground window" in safety.foreground_is_waveform_error()
    monkeypatch.setattr(safety, "foreground_pid", lambda: 500)
    assert safety.foreground_is_waveform_error() is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only tools")
def test_win_click_real_refuses_off_waveform(monkeypatch):
    from waveform_mcp.tools import win_input

    monkeypatch.setattr(win_input, "win_waveform_info",
                        lambda args: {"ok": True, "rect": [0, 0, 800, 600]})
    monkeypatch.setattr(safety, "point_on_waveform_error", lambda x, y: "covered")
    import pyautogui

    monkeypatch.setattr(pyautogui, "click",
                        lambda **_: pytest.fail("clicked although the point is covered"))
    assert win_input.win_click_real({"x": 10, "y": 10}) == {"ok": False, "error": "covered"}


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only tools")
def test_win_drag_real_checks_both_ends(monkeypatch):
    from waveform_mcp.tools import win_input

    checked = []

    def guard(x, y):
        checked.append((x, y))
        return "covered" if (x, y) == (90, 90) else None

    monkeypatch.setattr(safety, "point_on_waveform_error", guard)
    import pyautogui

    monkeypatch.setattr(pyautogui, "mouseDown",
                        lambda **_: pytest.fail("dragged although the drop point is covered"))
    result = win_input.win_drag_real(
        {"from_x": 10, "from_y": 10, "to_x": 90, "to_y": 90, "absolute": True})
    assert result == {"ok": False, "error": "covered"}
    assert checked == [(10, 10), (90, 90)]


# ---------- input guards (real Win32 calls, no input sent) ----------


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 API")
def test_real_win32_lookups_return_pids():
    import ctypes

    fg = safety.foreground_pid()
    assert fg is None or fg > 0
    w = ctypes.windll.user32.GetSystemMetrics(0)
    h = ctypes.windll.user32.GetSystemMetrics(1)
    pid = safety.pid_at_point(w // 2, h // 2)
    assert pid is None or pid > 0


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 API")
def test_private_user32_does_not_retype_shared_one():
    import ctypes

    safety._user32()
    # The shared handle must keep its default (untyped) signature, which other
    # modules rely on.
    assert ctypes.windll.user32.GetForegroundWindow.restype is ctypes.c_int


# ---------- preview app ----------


def _preview_client(tmp_path):
    from fastapi.testclient import TestClient

    from waveform_mcp.preview.app import create_app

    log = tmp_path / "events.log"
    log.write_text('{"kind": "narrate", "text": "secret path C:/x"}\n')
    return TestClient(create_app(log))


def test_preview_rejects_foreign_websocket_origin(tmp_path):
    from starlette.websockets import WebSocketDisconnect

    client = _preview_client(tmp_path)
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(
                "/ws", headers={"origin": "https://evil.example", "host": "localhost"}) as ws:
            ws.receive_text()
    assert excinfo.value.code == 1008


def test_preview_accepts_same_origin_websocket(tmp_path):
    client = _preview_client(tmp_path)
    with client.websocket_connect(
            "/ws", headers={"origin": "http://localhost", "host": "localhost"}) as ws:
        assert "narrate" in ws.receive_text()


def test_preview_rejects_rebound_host(tmp_path):
    client = _preview_client(tmp_path)
    assert client.get("/api/events", headers={"host": "evil.example"}).status_code == 400
    assert client.get("/api/events", headers={"host": "localhost"}).status_code == 200
