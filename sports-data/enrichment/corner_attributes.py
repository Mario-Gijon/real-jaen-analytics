"""Extract explicit textual attributes from Spanish corner comments."""

from __future__ import annotations

import re
from typing import Any


_SUPPORTED_TYPE = "cornerKick"
_BENEFITING_TEAM = re.compile(
    r"^\s*Saque\s+de\s+esquina,\s*(?P<team>[^.]+?)\s*\.",
    re.IGNORECASE,
)
_CONCEDING_PLAYER = re.compile(
    r"\bSaque\s+de\s+esquina\s+cometido\s+por\s+(?P<player>[^.]+?)\s*\.",
    re.IGNORECASE,
)


def _name_text(value: str) -> str | None:
    candidate = value.strip()
    if not candidate or not any(character.isalpha() for character in candidate):
        return None
    if any(not (character.isalpha() or character.isspace() or character in "-'’") for character in candidate):
        return None
    return candidate


def extract_corner_attributes(original_type: Any, text: Any) -> dict[str, str | None]:
    """Extract only team and player names explicitly stated in corner prose.

    The observed corpus uses ``Saque de esquina, {team}. Saque de esquina
    cometido por {player}.`` The names remain text only: this function never
    resolves them to IDs. The normalizer remains the source of the structured
    ``corner_conceding_player`` role, so the textual clause is not used to infer
    or duplicate that participant.
    """
    result: dict[str, str | None] = {
        "benefiting_team_text": None,
        "conceding_player_text": None,
    }
    if original_type != _SUPPORTED_TYPE or not isinstance(text, str) or not text.strip():
        return result

    team = _BENEFITING_TEAM.search(text)
    if team:
        result["benefiting_team_text"] = _name_text(team.group("team"))

    player = _CONCEDING_PLAYER.search(text)
    if player:
        result["conceding_player_text"] = _name_text(player.group("player"))

    return result


__all__ = ["extract_corner_attributes"]
