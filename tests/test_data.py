from ghost_coder.data import (
    KeyCombo, KeyPress, MouseScroll, Newline, PausePoint, Text, TimedPause, parse, parse_marker,
)


def texts(tokens):
    return "".join(t.text if isinstance(t, Text) else "\n" if isinstance(t, Newline) else "" for t in tokens)


def test_plain_text_round_trips():
    src = "def f(x):\n    return x + 1\n"
    assert texts(parse(src)) == src


def test_words_and_newlines():
    assert parse("ab cd\nef") == [Text("ab "), Text("cd"), Newline(), Text("ef")]


def test_crlf_normalised():
    assert parse("a\r\nb") == [Text("a"), Newline(), Text("b")]


def test_leading_quad_spaces_become_tabs_only_at_line_start():
    tokens = parse("        x = '    '\n", replace_quad_spaces_with_tab=True)
    assert texts(tokens) == "\t\tx = '    '\n"


def test_cpp_shift_operators_are_literal():
    src = 'cout << "hi" << endl; a<<b>>c; x <<= 2;'
    assert texts(parse(src)) == src


def test_unknown_marker_is_literal():
    assert texts(parse("<<bogus>>")) == "<<bogus>>"


def test_commands():
    assert parse_marker("PAUSE") == PausePoint()
    assert parse_marker("pause=1.5") == TimedPause(1.5)
    assert parse_marker("SCROLLUP=3") == MouseScroll(3, 1)
    assert parse_marker("scrolldown:2") == MouseScroll(2, -1)
    assert parse_marker("DOWN_ARROW=10") == KeyPress("down", 10)
    assert parse_marker("ENTER:5") == KeyPress("enter", 5)
    assert parse_marker("backspace") == KeyPress("backspace", 1)
    assert parse_marker("PAGEDOWN") == KeyPress("page_down", 1)
    assert parse_marker("CTRL+S") == KeyCombo(("ctrl", "s"))
    assert parse_marker("ctrl+shift+p") == KeyCombo(("ctrl", "shift", "p"))
    assert parse_marker("CTRL++") == KeyCombo(("ctrl", "+"))


def test_invalid_commands():
    for bad in ("a", "ENTER=0", "ENTER=abc", "pause=x", "CTRL+", "foo+bar", "SCROLLUP=99999"):
        assert parse_marker(bad) is None, bad


def test_markers_inline():
    tokens = parse("x<<PAUSE>>y<<CTRL+S>>")
    assert tokens == [Text("x"), PausePoint(), Text("y"), KeyCombo(("ctrl", "s"))]
