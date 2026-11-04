"""Playback state-machine tests (no real keystrokes: keyboard/window access is faked)."""

import threading
import time

import pytest

from ghost_coder import typer as typer_mod
from ghost_coder.typer import Typer


class FakeKb:
    def __init__(self):
        self.typed = []

    def press(self, key):
        self.typed.append(key)

    def release(self, key):
        pass


@pytest.fixture
def t(monkeypatch, tmp_path):
    monkeypatch.setattr(typer_mod.winutil, "get_foreground_window", lambda: 42)
    monkeypatch.setattr(typer_mod.winutil, "get_window_title", lambda h: "Editor")
    monkeypatch.setattr(typer_mod.winutil, "is_window", lambda h: True)
    monkeypatch.setattr(typer_mod.winutil, "focus_window", lambda h: True)
    obj = Typer("127.0.0.1", 1)
    obj.kb = FakeKb()
    obj.published = []
    obj.publish = lambda topic, data: obj.published.append((topic, data))
    obj.settings.update(typing_speed_value=10, start_delay=0, control_on_newline=False,
                        auto_home_on_newline=False)
    src = tmp_path / "src.txt"
    src.write_text("ab\ncd\n<<PAUSE>>ef", encoding="utf-8")
    obj._load_file(str(src))
    return obj


def wait_for(cond, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def typed(t):
    return "".join(k if isinstance(k, str) else f"<{k.name}>" for k in t.kb.typed)


def test_plays_until_pause_marker_then_resumes(t):
    t._play()
    assert wait_for(lambda: t.status == "paused")
    assert typed(t) == "ab<enter>cd<enter>"
    t._resume()
    assert wait_for(lambda: t.status == "stopped")
    assert typed(t) == "ab<enter>cd<enter>ef"


def test_pause_on_new_line_and_advance(t):
    t.settings["pause_on_new_line"] = True
    t._play()
    assert wait_for(lambda: t.status == "paused")
    assert typed(t) == "ab<enter>"
    t._advance(newline=False)  # one token: "cd"
    assert wait_for(lambda: typed(t) == "ab<enter>cd")
    time.sleep(0.1)
    assert typed(t) == "ab<enter>cd"
    t._advance(newline=True)
    assert wait_for(lambda: typed(t) == "ab<enter>cd<enter>")


def test_stop_cancels_session(t):
    t.settings["start_playback_paused"] = True
    t._play()
    assert wait_for(lambda: t.status == "paused")
    t._stop_playback()
    assert t.status == "stopped"
    t._resume()  # no effect once stopped
    time.sleep(0.1)
    assert typed(t) == ""
    assert threading.active_count() >= 1


def test_focus_loss_pauses(t, monkeypatch):
    t._play()
    assert wait_for(lambda: t.status == "paused")  # hits <<PAUSE>>
    monkeypatch.setattr(typer_mod.winutil, "get_foreground_window", lambda: 7)
    monkeypatch.setattr(typer_mod.winutil, "focus_window", lambda h: False)
    t.settings["refocus_window_on_resume"] = False
    t._resume()
    assert wait_for(lambda: t.status == "paused")
    assert typed(t) == "ab<enter>cd<enter>"


class TimedKb(FakeKb):
    def __init__(self):
        super().__init__()
        self.times = []

    def press(self, key):
        self.times.append(time.monotonic())
        super().press(key)


@pytest.mark.parametrize("speed_ms", [50, 200])
def test_keystroke_interval_matches_speed_setting(t, tmp_path, speed_ms):
    src = tmp_path / "timing.txt"
    src.write_text("abcdefgh", encoding="utf-8")
    t._load_file(str(src))
    t.kb = TimedKb()
    t.settings["typing_speed_value"] = speed_ms
    t._play()
    assert wait_for(lambda: t.status == "stopped")
    gaps = [b - a for a, b in zip(t.kb.times, t.kb.times[1:])]
    assert len(gaps) == 7
    mean = sum(gaps) / len(gaps)
    assert abs(mean - speed_ms / 1000) < 0.004, gaps


def test_stop_interrupts_long_pause_promptly(t, tmp_path):
    src = tmp_path / "pause.txt"
    src.write_text("a<<PAUSE=30>>b", encoding="utf-8")
    t._load_file(str(src))
    before = set(threading.enumerate())
    t._play()
    sessions = [th for th in set(threading.enumerate()) - before if th.name.startswith("playback-")]
    assert len(sessions) == 1
    assert wait_for(lambda: typed(t) == "a")
    time.sleep(0.05)
    started = time.monotonic()
    t._stop_playback()
    for th in sessions:
        th.join(timeout=1)
    assert all(not th.is_alive() for th in sessions)
    assert time.monotonic() - started < 0.1
    assert typed(t) == "a"


def test_tokens_parsed_once_until_file_changes(t, tmp_path, monkeypatch):
    calls = []
    real_parse = typer_mod.parse
    monkeypatch.setattr(typer_mod, "parse", lambda *a, **k: calls.append(1) or real_parse(*a, **k))
    src = tmp_path / "cache.txt"
    src.write_text("one", encoding="utf-8")
    first = t._read_tokens(str(src))
    assert t._read_tokens(str(src)) is first
    t.settings["replace_quad_spaces_with_tab"] = not t.settings["replace_quad_spaces_with_tab"]
    t._read_tokens(str(src))
    src.write_text("one two", encoding="utf-8")
    assert len(t._read_tokens(str(src))) == 2
    assert len(calls) == 3
