r"""Post-process model output: convert LaTeX and bits of Markdown to Unicode.

Conservative by design. The main rules:

- ``$...$`` spans are reformatted **only** when they contain an actual
  LaTeX marker (backslash, ``^{``, ``_{``) — so ``$100 USD`` in a currency
  answer stays intact, but ``$1.71 \times 10^{-8}$`` becomes
  ``1.71 × 10⁻⁸``.
- Bare ``\command`` tokens outside dollars are mapped only if the command
  is in our whitelist; unknown commands (e.g. ``\frac``) are left alone.
- ``**bold**`` → ``bold`` (markers stripped; Telegram plain text can't
  render bold without parse_mode, and leftover asterisks look worse).
- ``* item`` at line start → ``• item``.

``/raw`` in the bot flips a per-user toggle that disables the conversion
for debugging / price discussions / anything where the raw text matters.
"""

from __future__ import annotations

import re
import threading

# ── LaTeX command whitelist (math + Greek) ────────────────────────────

_LATEX_COMMAND: dict[str, str] = {
    # Operators / relations
    r"\times": "×",
    r"\cdot": "·",
    r"\div": "÷",
    r"\pm": "±",
    r"\mp": "∓",
    r"\approx": "≈",
    r"\neq": "≠",
    r"\le": "≤",
    r"\ge": "≥",
    r"\leq": "≤",
    r"\geq": "≥",
    r"\ll": "≪",
    r"\gg": "≫",
    r"\equiv": "≡",
    r"\sim": "∼",
    r"\propto": "∝",
    # Misc
    r"\infty": "∞",
    r"\partial": "∂",
    r"\nabla": "∇",
    r"\sum": "∑",
    r"\prod": "∏",
    r"\int": "∫",
    r"\sqrt": "√",
    r"\degree": "°",
    # Arrows
    r"\to": "→",
    r"\leftarrow": "←",
    r"\rightarrow": "→",
    r"\Rightarrow": "⇒",
    r"\Leftarrow": "⇐",
    # Greek lowercase
    r"\alpha": "α",
    r"\beta": "β",
    r"\gamma": "γ",
    r"\delta": "δ",
    r"\epsilon": "ε",
    r"\varepsilon": "ε",
    r"\zeta": "ζ",
    r"\eta": "η",
    r"\theta": "θ",
    r"\vartheta": "θ",
    r"\iota": "ι",
    r"\kappa": "κ",
    r"\lambda": "λ",
    r"\mu": "μ",
    r"\nu": "ν",
    r"\xi": "ξ",
    r"\pi": "π",
    r"\rho": "ρ",
    r"\sigma": "σ",
    r"\tau": "τ",
    r"\upsilon": "υ",
    r"\phi": "φ",
    r"\varphi": "φ",
    r"\chi": "χ",
    r"\psi": "ψ",
    r"\omega": "ω",
    # Greek uppercase
    r"\Gamma": "Γ",
    r"\Delta": "Δ",
    r"\Theta": "Θ",
    r"\Lambda": "Λ",
    r"\Xi": "Ξ",
    r"\Pi": "Π",
    r"\Sigma": "Σ",
    r"\Upsilon": "Υ",
    r"\Phi": "Φ",
    r"\Psi": "Ψ",
    r"\Omega": "Ω",
}

_SUP_MAP = str.maketrans("0123456789+-=()n", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ")
_SUB_MAP = str.maketrans("0123456789+-=()", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎")

_DOLLAR_MATH_RE = re.compile(r"\$([^\n$]+?)\$")
_SUP_RE = re.compile(r"\^\{([0-9+\-()=n]+)\}")
_SUB_RE = re.compile(r"_\{([0-9+\-()=]+)\}")
_TEXT_RE = re.compile(r"\\text\{([^}]*)\}")
_MATHRM_RE = re.compile(r"\\mathrm\{([^}]*)\}")
_SPACING_RE = re.compile(r"\\[\s,;:!]")
_BULLET_RE = re.compile(r"^(\s{0,4})\*\s+", re.MULTILINE)
_BOLD_RE = re.compile(r"\*\*([^*\n]+)\*\*")

# Whitelist-driven token match: only known commands get replaced, which
# means \cdot in \cdotm matches just \cdot (leaving 'm') and unknown
# commands like \frac pass through untouched. Sort by length desc so
# longer prefixes (\varepsilon) win over shorter ones (\var).
_LATEX_TOKEN_RE = re.compile(
    r"(?:"
    + "|".join(re.escape(k) for k in sorted(_LATEX_COMMAND, key=len, reverse=True))
    + r")"
)


def _has_latex(fragment: str) -> bool:
    """True if ``fragment`` looks like it might contain LaTeX."""
    return "\\" in fragment or "^{" in fragment or "_{" in fragment


def _convert_commands(s: str) -> str:
    """Apply LaTeX → Unicode conversions. Safe to call on any string;
    leaves non-LaTeX content unchanged.
    """
    # \text{x} / \mathrm{x} → x (these are formatting-only wrappers)
    s = _TEXT_RE.sub(r"\1", s)
    s = _MATHRM_RE.sub(r"\1", s)
    # \, \; \: \! \<space> → plain space
    s = _SPACING_RE.sub(" ", s)
    # ^{digits} → Unicode superscript, _{digits} → subscript
    s = _SUP_RE.sub(lambda m: m.group(1).translate(_SUP_MAP), s)
    s = _SUB_RE.sub(lambda m: m.group(1).translate(_SUB_MAP), s)
    # Known commands only — unknown \command tokens pass through untouched
    s = _LATEX_TOKEN_RE.sub(lambda m: _LATEX_COMMAND[m.group(0)], s)
    return s


def to_unicode(text: str) -> str:
    """Main entry point. Converts LaTeX/Markdown fragments in-place."""
    if not text:
        return text

    # Pass 1: process $...$ spans — only if they actually contain LaTeX.
    def _process_dollar(m: re.Match[str]) -> str:
        inner = m.group(1)
        if _has_latex(inner):
            return _convert_commands(inner)
        return m.group(0)  # preserve $100 USD, $VAR, etc.

    text = _DOLLAR_MATH_RE.sub(_process_dollar, text)

    # Pass 2: bare \command tokens outside dollars. Whitelist keeps this
    # safe — anything not in our map (incl. Windows paths like \Users)
    # is preserved verbatim.
    text = _convert_commands(text)

    # Pass 3: light Markdown cleanup. Telegram plain text can't render
    # bold without parse_mode, so stripping **markers** looks cleaner
    # than leaving them visible.
    text = _BOLD_RE.sub(r"\1", text)
    text = _BULLET_RE.sub(r"\1• ", text)

    return text


# ── Per-user /raw toggle ─────────────────────────────────────────────

_raw_mode_users: set[int] = set()
_raw_lock = threading.Lock()


def is_raw(user_id: int) -> bool:
    with _raw_lock:
        return user_id in _raw_mode_users


def toggle_raw(user_id: int) -> bool:
    """Flip /raw for a user. Returns True if raw is now ON."""
    with _raw_lock:
        if user_id in _raw_mode_users:
            _raw_mode_users.discard(user_id)
            return False
        _raw_mode_users.add(user_id)
        return True


def reset_raw() -> None:
    """Clear all /raw toggles (testing convenience)."""
    with _raw_lock:
        _raw_mode_users.clear()
