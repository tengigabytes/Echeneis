"""Tests for LaTeX/Markdown → Unicode post-processing and /raw toggle."""

from __future__ import annotations

import pytest

from echeneis.bot.format_math import (
    is_raw,
    reset_raw,
    to_unicode,
    toggle_raw,
)


@pytest.fixture(autouse=True)
def _clean_raw():
    reset_raw()
    yield
    reset_raw()


# ── LaTeX math ────────────────────────────────────────────────────────


def test_dollar_math_with_latex_is_converted():
    out = to_unicode(r"約 $1.71 \times 10^{-8} \ \Omega\cdot\text{m}$")
    assert "×" in out
    assert "⁻⁸" in out
    assert "Ω" in out
    assert "·" in out
    # Dollar markers gone for math span
    assert "$" not in out
    # \text{m} stripped to m (attached to · since LaTeX has no spaces here)
    assert "·m" in out


def test_dollar_currency_without_latex_preserved():
    """$100 USD pairs should NOT be collapsed — they have no LaTeX inside."""
    out = to_unicode("價格從 $100 到 $200 之間")
    assert "$100" in out
    assert "$200" in out


def test_shell_var_single_dollar_preserved():
    out = to_unicode("export $HOME=/tmp")
    assert "$HOME" in out


def test_unknown_latex_command_preserved():
    """\\frac{a}{b} is not in whitelist — leave as-is."""
    out = to_unicode(r"$\frac{a}{b}$")
    # \frac stays; whole span still present
    assert "\\frac" in out


def test_known_latex_command_outside_dollars_converted():
    """Bare \\alpha and \\Omega appearing without $...$ still convert."""
    out = to_unicode(r"功率 = \alpha \times \beta")
    assert "α" in out
    assert "β" in out
    assert "×" in out


def test_windows_path_not_converted():
    """C:\\Users\\... shouldn't get mauled — no whitelisted command matches."""
    out = to_unicode(r"Path: C:\Users\terry\file.txt")
    assert r"C:\Users\terry\file.txt" in out


def test_superscript_and_subscript():
    out = to_unicode(r"$x^{2}+y_{0}$")
    assert "x²" in out
    assert "y₀" in out


def test_text_and_mathrm_wrappers_unwrap():
    out = to_unicode(r"$\text{speed}$ and $\mathrm{km/h}$")
    assert "speed" in out
    assert "km/h" in out


def test_spacing_commands_become_space():
    out = to_unicode(r"$a \, b \; c$")
    # No stray backslash spacing commands left
    assert "\\," not in out
    assert "\\;" not in out
    # Collapse of whitespace is not guaranteed; just confirm the letters
    # appear in order with only whitespace between them.
    import re as _re

    assert _re.fullmatch(r"a\s+b\s+c", out.strip())


# ── Markdown cleanup ──────────────────────────────────────────────────


def test_bold_markers_stripped():
    out = to_unicode("這是 **重點** 內容")
    assert "重點" in out
    assert "**" not in out


def test_bullet_list_line_start_becomes_dot():
    out = to_unicode("* 第一項\n* 第二項\n句中的 *斜體* 不動")
    assert "• 第一項" in out
    assert "• 第二項" in out
    # mid-sentence *斜體* stays
    assert "*斜體*" in out


def test_empty_input_is_safe():
    assert to_unicode("") == ""
    assert to_unicode("plain text with no markup") == ("plain text with no markup")


def test_chinese_only_unchanged():
    zh = "這段文字沒有任何 LaTeX 或 Markdown"
    assert to_unicode(zh) == zh


# ── /raw toggle ───────────────────────────────────────────────────────


def test_toggle_raw_flips_state():
    assert not is_raw(42)
    assert toggle_raw(42) is True
    assert is_raw(42)
    assert toggle_raw(42) is False
    assert not is_raw(42)


def test_raw_is_per_user():
    toggle_raw(1)
    assert is_raw(1)
    assert not is_raw(2)
