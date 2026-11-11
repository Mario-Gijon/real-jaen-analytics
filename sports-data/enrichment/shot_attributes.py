"""Conservative extraction of explicit shot attributes from Spanish comments."""

from __future__ import annotations

import re
from typing import Any

_ASSISTANCE = re.compile(r"\basistencia\s+de\s+", re.IGNORECASE)
_ASSISTANCE_CLAUSE = re.compile(
    r"[,.!?;]|\s+(?:con\b|tras\b|después\b|luego\b|pase\s+largo\b|"
    r"pase\s+en\s+profundidad\b|pase\s+de\s+cabeza\b|centro\s+al\s+área\b)",
    re.IGNORECASE,
)

_SURFACE_PATTERNS = {
    "right_foot": re.compile(
        r"\b(?:con\s+la\s+derecha|con\s+el\s+pie\s+derecho|derechazo)\b",
        re.IGNORECASE,
    ),
    "left_foot": re.compile(
        r"\b(?:con\s+la\s+izquierda|con\s+el\s+pie\s+izquierdo|zurdazo)\b",
        re.IGNORECASE,
    ),
    "head": re.compile(
        r"\b(?:disparo|remate|tiro)\s+de\s+cabeza\b|\bcabezazo\b|\bcon\s+la\s+cabeza\b",
        re.IGNORECASE,
    ),
}

_AREA_PATTERNS = {
    "inside_area": re.compile(
        r"\b(?:dentro\s+del\s+área|interior\s+del\s+área|centro\s+del\s+área)\b",
        re.IGNORECASE,
    ),
    "outside_area": re.compile(r"\bdesde\s+fuera\s+del\s+área\b", re.IGNORECASE),
}

_SECTOR_PATTERNS = {
    "left": re.compile(
        r"\bdesde\s+(?:(?:el\s+)?lado\s+izquierdo|(?:la\s+)?banda\s+izquierda|la\s+izquierda)\b",
        re.IGNORECASE,
    ),
    "center": re.compile(r"\bdesde\s+el\s+centro(?:\s+del\s+área)?\b", re.IGNORECASE),
    "right": re.compile(
        r"\bdesde\s+(?:(?:el\s+)?lado\s+derecho|(?:la\s+)?banda\s+derecha|la\s+derecha)\b",
        re.IGNORECASE,
    ),
}

_PASS_PATTERNS = {
    "cross": re.compile(
        r"\bcentro\s+al\s+área\b|\b(?:con|tras|después\s+de|luego\s+de)\s+(?:un\s+)?centro\b",
        re.IGNORECASE,
    ),
    "long_pass": re.compile(r"\bpase\s+largo\b", re.IGNORECASE),
    "through_ball": re.compile(r"\bpase\s+en\s+profundidad\b", re.IGNORECASE),
}


def _one_explicit_value(text: str, patterns: dict[str, re.Pattern[str]]) -> str | None:
    matches = [value for value, pattern in patterns.items() if pattern.search(text)]
    return matches[0] if len(matches) == 1 else None


def _assistant_name(text: str) -> str | None:
    mentions = list(_ASSISTANCE.finditer(text))
    if len(mentions) != 1:
        return None

    remainder = text[mentions[0].end():]
    boundary = _ASSISTANCE_CLAUSE.search(remainder)
    candidate = remainder[:boundary.start()] if boundary else remainder
    candidate = candidate.strip()
    if not candidate or not any(character.isalpha() for character in candidate):
        return None
    if any(not (character.isalpha() or character in " -'’.") for character in candidate):
        return None
    return candidate


def extract_shot_attributes(text: Any) -> dict[str, str | None]:
    """Extract only attributes explicitly stated in a Spanish shot narrative.

    Sector means the shot's origin sector and is recognized only after an explicit
    ``desde`` location phrase. Goal-mouth direction (for example, ``por la derecha``)
    is intentionally ignored. The function is a separate layer from ``enrich_comment``
    and does not assign identifiers to an assistant mentioned in prose.
    """
    result: dict[str, str | None] = {
        "surface": None,
        "area_zone": None,
        "sector": None,
        "prior_pass": None,
        "assistant_name": None,
    }
    if not isinstance(text, str) or not text.strip():
        return result

    # Assistive clauses can mention the passer's technique (e.g. a header), which
    # must not be mistaken for the shot surface.
    assistance = _ASSISTANCE.search(text)
    shot_description = text[:assistance.start()] if assistance else text
    result["surface"] = _one_explicit_value(shot_description, _SURFACE_PATTERNS)
    result["area_zone"] = _one_explicit_value(shot_description, _AREA_PATTERNS)
    result["sector"] = _one_explicit_value(shot_description, _SECTOR_PATTERNS)
    result["prior_pass"] = _one_explicit_value(text, _PASS_PATTERNS)
    result["assistant_name"] = _assistant_name(text)
    return result


__all__ = ["extract_shot_attributes"]
