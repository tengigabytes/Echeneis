"""Shared scoring utilities for benchmark dimensions.

All scoring is deterministic and automated — no manual review needed.
"""

from __future__ import annotations

import re


def substring_match(text: str, expected: str, case_insensitive: bool = True) -> bool:
    """Check if expected substring appears in text.

    Args:
        text: The text to search in.
        expected: The substring to look for.
        case_insensitive: Whether to ignore case.

    Returns:
        True if found.
    """
    if case_insensitive:
        return expected.lower() in text.lower()
    return expected in text


def regex_match(text: str, pattern: str) -> bool:
    """Check if a regex pattern matches anywhere in text.

    Args:
        text: The text to search in.
        pattern: Regex pattern string.

    Returns:
        True if the pattern matches.
    """
    return bool(re.search(pattern, text, re.IGNORECASE))


def count_matches(
    text: str,
    items: list[dict[str, list[str]]],
) -> tuple[int, list[str], list[str]]:
    """Count how many items are detected in text via keyword lists.

    Each item has a name and a list of detection keywords/patterns.
    An item is considered "found" if ANY of its keywords match.

    Args:
        text: The response text to scan.
        items: List of dicts with "name" and "keywords" keys.

    Returns:
        Tuple of (found_count, found_names, missed_names).
    """
    found: list[str] = []
    missed: list[str] = []
    for item in items:
        name = item["name"]
        keywords = item["keywords"]
        if any(regex_match(text, kw) for kw in keywords):
            found.append(name)
        else:
            missed.append(name)
    return len(found), found, missed


def count_preserved_terms(
    text: str, terms: list[str]
) -> tuple[int, list[str], list[str]]:
    """Count how many English terms appear verbatim in (possibly Chinese) text.

    Args:
        text: The translated output text.
        terms: List of English terms that should be preserved as-is.

    Returns:
        Tuple of (preserved_count, preserved_terms, missing_terms).
    """
    preserved: list[str] = []
    missing: list[str] = []
    for term in terms:
        if substring_match(text, term, case_insensitive=False):
            preserved.append(term)
        else:
            missing.append(term)
    return len(preserved), preserved, missing
