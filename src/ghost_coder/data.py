"""Parse a source file into playback tokens.

Plain text is split into word-sized ``Text`` tokens (a word plus its trailing spaces), line
breaks become ``Newline`` tokens and ``<<COMMAND>>`` markers become command tokens.

Anything that looks like a marker but is not a valid command (for example C++ ``a<<b>>c``)
is typed literally, so ordinary source code never gets swallowed by the parser.
"""

import re
from dataclasses import dataclass
from typing import Optional, Union

MAX_REPEAT = 1000


@dataclass(frozen=True)
class Text:
    text: str


@dataclass(frozen=True)
class Newline:
    pass


@dataclass(frozen=True)
class KeyPress:
    key: str  # pynput Key attribute name
    count: int = 1


@dataclass(frozen=True)
class KeyCombo:
    keys: tuple[str, ...]  # pynput Key attribute names or single characters


@dataclass(frozen=True)
class TimedPause:
    seconds: float


@dataclass(frozen=True)
class PausePoint:
    pass


@dataclass(frozen=True)
class MouseScroll:
    count: int
    direction: int  # 1 = up, -1 = down


Token = Union[Text, Newline, KeyPress, KeyCombo, TimedPause, PausePoint, MouseScroll]

# Marker names (case-insensitive) -> pynput Key attribute names.
KEY_ALIASES: dict[str, str] = {
    "enter": "enter", "return": "enter",
    "tab": "tab",
    "space": "space",
    "backspace": "backspace", "bksp": "backspace",
    "delete": "delete", "del": "delete",
    "insert": "insert", "ins": "insert",
    "home": "home",
    "end": "end",
    "pageup": "page_up", "page_up": "page_up", "pgup": "page_up",
    "pagedown": "page_down", "page_down": "page_down", "pgdn": "page_down",
    "esc": "esc", "escape": "esc",
    "up": "up", "up_arrow": "up",
    "down": "down", "down_arrow": "down",
    "left": "left", "left_arrow": "left",
    "right": "right", "right_arrow": "right",
    "ctrl": "ctrl", "control": "ctrl",
    "alt": "alt",
    "shift": "shift",
    "win": "cmd", "cmd": "cmd", "super": "cmd",
    **{f"f{i}": f"f{i}" for i in range(1, 21)},
}

MARKER_RE = re.compile(r"<<([^<>\s]{1,40})>>")
COMMAND_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?:[=:](.+))?$")
WORD_RE = re.compile(r"\S+[^\S\n]*|[^\S\n]+")
LEADING_QUADS_RE = re.compile(r"^(?:    )+", re.MULTILINE)


def _parse_count(arg: Optional[str]) -> Optional[int]:
    if arg is None:
        return 1
    if not arg.isdigit():
        return None
    count = int(arg)
    return count if 1 <= count <= MAX_REPEAT else None


def parse_marker(content: str) -> Optional[Token]:
    """Parse the inside of a ``<<...>>`` marker. Returns None if it is not a valid command."""
    if "+" in content and len(content) > 1:
        if content.endswith("++"):
            raw_parts = content[:-2].split("+") + ["+"]
        else:
            raw_parts = content.split("+")
        keys = []
        for part in raw_parts:
            if not part:
                return None
            alias = KEY_ALIASES.get(part.lower())
            if alias:
                keys.append(alias)
            elif len(part) == 1:
                keys.append(part.lower())
            else:
                return None
        return KeyCombo(tuple(keys)) if len(keys) >= 2 else None

    match = COMMAND_RE.match(content)
    if not match:
        return None
    name, arg = match.group(1).lower(), match.group(2)

    if name == "pause":
        if arg is None:
            return PausePoint()
        try:
            seconds = float(arg)
        except ValueError:
            return None
        return TimedPause(seconds) if 0 <= seconds <= 3600 else None

    if name in ("scrollup", "scroll_up", "scrolldown", "scroll_down"):
        count = _parse_count(arg)
        if count is None:
            return None
        return MouseScroll(count=count, direction=1 if "up" in name else -1)

    if name in KEY_ALIASES:
        count = _parse_count(arg)
        if count is None:
            return None
        return KeyPress(KEY_ALIASES[name], count)

    return None


def _split_text(text: str, tokens: list[Token]) -> None:
    """Split plain text into word-sized Text tokens and Newline tokens."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if i > 0:
            tokens.append(Newline())
        tokens.extend(Text(m.group(0)) for m in WORD_RE.finditer(line))


def parse(text: str, replace_quad_spaces_with_tab: bool = False) -> list[Token]:
    """Parse source text into a list of playback tokens."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if replace_quad_spaces_with_tab:
        text = LEADING_QUADS_RE.sub(lambda m: "\t" * (len(m.group(0)) // 4), text)

    tokens: list[Token] = []
    pending = []  # literal text accumulated between valid markers
    pos = 0
    for m in MARKER_RE.finditer(text):
        command = parse_marker(m.group(1))
        if command is None:
            continue
        pending.append(text[pos:m.start()])
        _split_text("".join(pending), tokens)
        pending = []
        tokens.append(command)
        pos = m.end()
    pending.append(text[pos:])
    _split_text("".join(pending), tokens)
    return tokens


def describe(token: Token) -> str:
    """Short human readable form, used for logging."""
    if isinstance(token, Text):
        return repr(token.text)
    if isinstance(token, Newline):
        return "<newline>"
    if isinstance(token, KeyPress):
        return f"<{token.key}x{token.count}>"
    if isinstance(token, KeyCombo):
        return "<" + "+".join(token.keys) + ">"
    if isinstance(token, TimedPause):
        return f"<pause {token.seconds}s>"
    if isinstance(token, PausePoint):
        return "<pause>"
    if isinstance(token, MouseScroll):
        return f"<scroll {'up' if token.direction > 0 else 'down'} x{token.count}>"
    return str(token)
