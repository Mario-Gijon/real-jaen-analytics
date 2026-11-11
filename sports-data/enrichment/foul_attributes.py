"""Conservative extraction of explicit foul attributes from Spanish comments."""

from __future__ import annotations

import re
from typing import Any


# These patterns follow wording observed in freeKickLost/freeKickWon comments.
# Do not classify a bare mention of "falta": simulation comments mention one
# without describing an actual infringement.
_HANDBALL = re.compile(r"\bmano\s+de\b", re.IGNORECASE)
_FOUL_PATTERNS = (
    re.compile(r"\bfalta\s+de\b", re.IGNORECASE),
    re.compile(r"\bha\s+recibido\s+una\s+falta\b", re.IGNORECASE),
    re.compile(r"\bjuego\s+peligroso\s+de\b", re.IGNORECASE),
)

_LOCATION_PATTERNS = {
    "defensive_zone": re.compile(r"\ben\s+la\s+zona\s+defensiva\b", re.IGNORECASE),
    "opponent_half": re.compile(r"\ben\s+campo\s+contrario\b", re.IGNORECASE),
    "flank": re.compile(r"\ben\s+la\s+banda\s+(?:izquierda|derecha)\b", re.IGNORECASE),
}
_FLANK_PATTERNS = {
    "left": re.compile(r"\ben\s+la\s+banda\s+izquierda\b", re.IGNORECASE),
    "right": re.compile(r"\ben\s+la\s+banda\s+derecha\b", re.IGNORECASE),
}


def _one_explicit_value(text: str, patterns: dict[str, re.Pattern[str]]) -> str | None:
    matches = [value for value, pattern in patterns.items() if pattern.search(text)]
    return matches[0] if len(matches) == 1 else None


def _infraction_type(text: str) -> str | None:
    is_handball = bool(_HANDBALL.search(text))
    is_foul = any(pattern.search(text) for pattern in _FOUL_PATTERNS)
    if is_handball == is_foul:
        # Both cues or neither cue are ambiguous / unsupported.
        return None
    return "handball" if is_handball else "foul"


def _location(text: str) -> tuple[str | None, str | None]:
    context = _one_explicit_value(text, _LOCATION_PATTERNS)
    flank = _one_explicit_value(text, _FLANK_PATTERNS)
    if context != "flank":
        flank = None
    return context, flank


def extract_foul_attributes(text: Any) -> dict[str, str | None]:
    """Extract only explicit infringement and location cues from foul comments.

    The current Spanish corpus uses ``Falta de``, ``Mano de``,
    ``ha recibido una falta`` and ``Juego peligroso de``. Locations are
    recognized only in explicit phrases for defensive zone, opposing half, or
    left/right flank. A band is returned as a broad ``flank`` context plus its
    side. Location stays attached to the wording of this one comment: it is
    never transferred to another participant or paired lost/won event. No
    participant names or roles are inferred. Call this only for the original
    ``freeKickLost`` and ``freeKickWon`` types.
    """
    result: dict[str, str | None] = {
        "infraction_type": None,
        "location_context": None,
        "flank": None,
    }
    if not isinstance(text, str) or not text.strip():
        return result

    result["infraction_type"] = _infraction_type(text)
    result["location_context"], result["flank"] = _location(text)
    return result


__all__ = ["extract_foul_attributes"]
