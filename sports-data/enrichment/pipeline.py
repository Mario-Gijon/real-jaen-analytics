"""Compose comment taxonomy, participant roles, and deterministic attributes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .corner_attributes import extract_corner_attributes
from .foul_attributes import extract_foul_attributes
from .goal_attributes import extract_goal_attributes
from .normalizer import enrich_comment
from .shot_attributes import extract_shot_attributes


_SHOT_TYPES = {"shotSaved", "shotOffTarget", "shotBlocked", "post"}
_FOUL_TYPES = {"freeKickLost", "freeKickWon"}
_GOAL_TYPES = {"scoreChange", "penaltyScored", "varCancelledGoal"}


def enrich_comment_pipeline(comment: Mapping[str, Any]) -> dict[str, Any]:
    """Return one comment's normalized category, participants, and attributes.

    The original comment mapping is passed to ``enrich_comment`` so participant
    roles and source IDs keep their existing rules. Text extractors run only for
    their supported original types; all other types receive an empty attributes
    mapping. This function treats each comment independently and never joins
    related events.

    For a clearly identified own goal, the structured participant keeps its
    ``source_role`` and ``source_id`` but receives the integrated role
    ``own_goal_author``. Its original normalizer role is retained in
    ``normalizer_role`` for traceability, preventing it from being treated as an
    ordinary scorer downstream. Text-only assistant names remain attributes and
    are never added as participants or assigned IDs. Foul roles are similarly
    withheld when the text extractor cannot confirm an infringement; the
    normalizer's type-based role is retained in ``normalizer_role``.
    """
    if not isinstance(comment, Mapping):
        raise TypeError("comment must be a mapping")

    normalized = enrich_comment(comment)
    original_type = normalized["original_type"]
    text = comment.get("text")

    if original_type in _SHOT_TYPES:
        attributes = extract_shot_attributes(text)
    elif original_type in _FOUL_TYPES:
        attributes = extract_foul_attributes(text)
    elif original_type in _GOAL_TYPES:
        attributes = extract_goal_attributes(original_type, text)
    elif original_type == "cornerKick":
        attributes = extract_corner_attributes(original_type, text)
    else:
        attributes = {}

    participants = normalized["participants"]
    if attributes.get("goal_kind") == "own_goal":
        for participant in participants:
            if participant["source_role"] == "player" and participant["role"] == "scorer":
                participant["normalizer_role"] = participant["role"]
                participant["role"] = "own_goal_author"
    elif original_type in _FOUL_TYPES and attributes.get("infraction_type") is None:
        for participant in participants:
            if participant["role"] in {"foul_committer", "foul_recipient"}:
                participant["normalizer_role"] = participant["role"]
                participant["role"] = None

    return {
        "category": normalized["category"],
        "original_type": normalized["original_type"],
        "participants": participants,
        "attributes": attributes,
    }


__all__ = ["enrich_comment_pipeline"]
