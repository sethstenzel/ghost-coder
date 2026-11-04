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
