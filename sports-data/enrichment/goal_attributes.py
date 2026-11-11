"""Conservative extraction of explicit attributes from Spanish goal comments."""

from __future__ import annotations

import re
from typing import Any

from .shot_attributes import extract_shot_attributes


_SUPPORTED_TYPES = {"scoreChange", "penaltyScored", "varCancelledGoal"}
_DISALLOWED_TEXT = re.compile(r"\b(?:gol\s+cancelado|gol\s+(?:fue\s+)?anulado)\b", re.IGNORECASE)
_VALID_REVIEW_TEXT = re.compile(r"\bgol\s+(?:confirmado|otorgado)\b", re.IGNORECASE)
_PENALTY_TEXT = re.compile(r"\bpenalt[ií]\b", re.IGNORECASE)
_OWN_GOAL_TEXT = re.compile(
    r"\b(?:autogol|auto[\s-]+gol)\b|"
    r"\bgol\s+en\s+propia\s+(?:puerta|meta)\b|"
    r"\b(?:introduce|introdujo|mete|metió)\s+(?:el\s+bal[oó]n\s+)?"
    r"en\s+su\s+propia\s+porter[ií]a\b",
    re.IGNORECASE,
)
_ORDINARY_GOAL_HEADING = re.compile(r"^\s*¡gol!\s", re.IGNORECASE)

_AFTER_CORNER = re.compile(
    r"\b(?:tras|después\s+de)\s+(?:un\s+)?(?:saque\s+de\s+esquina|córner)\b",
    re.IGNORECASE,
)
_AFTER_FREE_KICK = re.compile(r"\btras\s+el\s+cobro\s+de\s+una\s+falta\b", re.IGNORECASE)
_DIRECT_FREE_KICK = re.compile(r"\btiro\s+libre\s+directo\b", re.IGNORECASE)
_COUNTERATTACK = re.compile(r"\bcontragolpe\b", re.IGNORECASE)


def _status(original_type: str, text: str) -> str | None:
    expected = {
        "scoreChange": "valid",
        "penaltyScored": "valid",
        "varCancelledGoal": "disallowed",
    }[original_type]
    says_disallowed = bool(_DISALLOWED_TEXT.search(text))
    says_valid = bool(_VALID_REVIEW_TEXT.search(text))
    if (expected == "valid" and says_disallowed) or (expected == "disallowed" and says_valid):
        return None
    return expected


def _goal_kind(original_type: str, text: str) -> str | None:
    """Return a goal kind only when the source type or wording is explicit."""
    own_goal = bool(_OWN_GOAL_TEXT.search(text))
    ordinary_goal = original_type == "penaltyScored" or bool(_ORDINARY_GOAL_HEADING.search(text))
    if own_goal:
        # An explicit own-goal statement overrides the generic "¡Gol!" heading.
        return "own_goal"
    if not ordinary_goal:
        return None
    return "ordinary"


def _origin(original_type: str, text: str, shot: dict[str, str | None]) -> str | None:
    # The original event type is sufficient evidence for a scored penalty.
    if original_type == "penaltyScored":
        return "penalty"

    explicit_penalty = bool(_PENALTY_TEXT.search(text))
    set_piece = bool(_AFTER_CORNER.search(text) or _AFTER_FREE_KICK.search(text) or _DIRECT_FREE_KICK.search(text))
    # A through ball or counterattack explicitly describes open play. A long
    # pass alone is not enough to exclude a restart, so it stays context only.
    open_play = bool(_COUNTERATTACK.search(text) or shot["prior_pass"] == "through_ball")
    origins = sum((explicit_penalty, set_piece, open_play))
    if origins != 1:
        return None
    if explicit_penalty:
        return "penalty"
    if set_piece:
        return "set_piece"
    return "open_play"


def _contexts(original_type: str, text: str, shot: dict[str, str | None]) -> list[str]:
    contexts: list[str] = []
    if _AFTER_CORNER.search(text):
        contexts.append("after_corner")
    if _AFTER_FREE_KICK.search(text):
        contexts.append("after_free_kick")
    if _DIRECT_FREE_KICK.search(text):
        contexts.append("direct_free_kick")
    if shot["prior_pass"] == "cross":
        contexts.append("cross")
    if shot["prior_pass"] == "long_pass":
        contexts.append("long_pass")
    if shot["prior_pass"] == "through_ball":
        contexts.append("through_ball")
    if _COUNTERATTACK.search(text):
        contexts.append("counterattack")
    if _VALID_REVIEW_TEXT.search(text):
        contexts.append("var_confirmation")
    if original_type == "varCancelledGoal" and _DISALLOWED_TEXT.search(text):
        contexts.append("var_cancellation")
    return contexts


def extract_goal_attributes(original_type: Any, text: Any) -> dict[str, Any]:
    """Extract goal state, kind, source, shot detail, context, and assistant text.

    Goal status follows the original SofaScore type, unless the text explicitly
    contradicts it, in which case it remains unknown. Shot details and assistant
    text reuse ``extract_shot_attributes`` without changing its rules. The
    ``assistant_name_text`` field is only a prose mention; it is never an
    official-assist assertion or a participant ID (especially for annulled goals).
    Origin is emitted only for explicit penalty, set-piece, counterattack, or
    through-ball evidence. A cross or long pass alone does not determine origin.
    ``goal_kind`` is ``own_goal`` only for explicit own-goal wording (which
    overrides the generic ``¡Gol!`` heading), and ``ordinary`` only for the
    observed ordinary-goal heading or ``penaltyScored``; otherwise it is unknown.
    The normalizer's ``player=scorer`` role is unchanged:
    downstream goal totals must not credit that player's team when this field is
    ``own_goal``; the score is credited to the opponent while the participant is
    recorded as the own-goal player.
    """
    result: dict[str, Any] = {
        "goal_status": None,
        "goal_kind": None,
        "goal_origin": None,
        "surface": None,
        "area_zone": None,
        "sector": None,
        "contexts": [],
        "assistant_name_text": None,
    }
    if (
        not isinstance(original_type, str)
        or original_type not in _SUPPORTED_TYPES
        or not isinstance(text, str)
        or not text.strip()
    ):
        return result

    shot = extract_shot_attributes(text)
    result["goal_status"] = _status(original_type, text)
    result["goal_kind"] = _goal_kind(original_type, text)
    result["goal_origin"] = _origin(original_type, text, shot)
    result["surface"] = shot["surface"]
    result["area_zone"] = shot["area_zone"]
    result["sector"] = shot["sector"]
    result["contexts"] = _contexts(original_type, text, shot)
    result["assistant_name_text"] = shot["assistant_name"]
    return result


__all__ = ["extract_goal_attributes"]
