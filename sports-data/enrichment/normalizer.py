"""Pure normalization of SofaScore comments into deterministic tags and roles."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from .taxonomy import CATEGORY_BY_TYPE, ROLE_BY_TYPE

_PARTICIPANT_SLOTS = ("player", "playerIn", "playerOut", "assist1")


def _participant_source(comment: Mapping[str, Any]) -> Mapping[str, Any]:
    """Read the existing persisted/API shape, or the provider's direct fields."""
    if "participants_json" in comment and comment["participants_json"] is not None:
        value = comment["participants_json"]
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as error:
                raise ValueError("participants_json must be valid JSON") from error
        if not isinstance(value, Mapping):
            raise ValueError("participants_json must contain an object")
        return value

    nested = comment.get("participants")
    if isinstance(nested, Mapping):
        return nested
    return comment


def _valid_source_id(value: Any) -> bool:
    if isinstance(value, int) and not isinstance(value, bool):
        return value > 0
    if isinstance(value, str):
        return (
            value.isascii()
            and value.isdecimal()
            and any(character != "0" for character in value)
        )
    return False


def enrich_comment(comment: Mapping[str, Any]) -> dict[str, Any]:
    """Return a taxonomy label and structured participant roles for one comment.

    Accepted participant inputs are the raw provider slots, the public ``participants``
    mapping, or the persisted ``participants_json`` field. Names and narrative text
    never participate in category or role assignment. When a source ID is absent or
    invalid, the participant is retained but left with ``role=None``.
    """
    if not isinstance(comment, Mapping):
        raise TypeError("comment must be a mapping")

    original_type = comment.get("type")
    if not isinstance(original_type, str) or not original_type.strip():
        original_type = None

    role_map = ROLE_BY_TYPE.get(original_type, {})
    source = _participant_source(comment)
    participants: list[dict[str, Any]] = []

    for source_role in _PARTICIPANT_SLOTS:
        value = source.get(source_role)
        if not isinstance(value, Mapping):
            continue

        source_id = value.get("id")
        valid_id = _valid_source_id(source_id)
        # SofaScore commonly repeats a substitution's incoming player in both
        # ``player`` and ``playerIn``. Deduplicate only by the original ID.
        if original_type == "substitution" and source_role == "player" and valid_id:
            player_in = source.get("playerIn")
            if isinstance(player_in, Mapping) and player_in.get("id") == source_id:
                continue

        name = value.get("name")
        participants.append({
            "source_role": source_role,
            "role": role_map.get(source_role) if valid_id else None,
            "source_id": source_id if valid_id else None,
            "name": name if isinstance(name, str) else None,
        })

    return {
        "category": CATEGORY_BY_TYPE.get(original_type),
        "original_type": original_type,
        "participants": participants,
    }
