"""Closed taxonomy for the SofaScore comment types currently in the corpus."""

from types import MappingProxyType


# Categories stay deliberately broad. ``original_type`` remains in every result
# so later consumers can distinguish subtypes without losing provider semantics.
CATEGORY_BY_TYPE = MappingProxyType({
    "scoreChange": "goal",
    "penaltyScored": "goal",
    "varCancelledGoal": "goal_review",
    "freeKickLost": "foul",
    "freeKickWon": "foul",
    "shotOffTarget": "shot",
    "shotBlocked": "shot",
    "shotSaved": "shot",
    "post": "shot",
    "cornerKick": "set_piece",
    "substitution": "substitution",
    "yellowCard": "discipline",
    "redCard": "discipline",
    "yellowRedCard": "discipline",
    "offside": "offside",
    "videoAssistantReferee": "var",
    "penaltyLost": "penalty",
    "penaltyAwarded": "penalty",
    "penaltySaved": "penalty",
    "penaltyMissed": "penalty",
    "startDelay": "match_flow",
    "matchStarted": "match_flow",
    "addedTime": "match_flow",
    "endDelay": "match_flow",
    "matchEnded": "match_flow",
    "endFirstHalf": "match_flow",
    "endSecondHalf": "match_flow",
})


# Keys are the exact participant fields preserved by persistencia.comentarios.
# A missing mapping is intentional: that participant's semantic role is unknown.
ROLE_BY_TYPE = MappingProxyType({
    "scoreChange": MappingProxyType({"player": "scorer", "assist1": "assistant"}),
    "penaltyScored": MappingProxyType({"player": "penalty_scorer"}),
    "varCancelledGoal": MappingProxyType({
        "player": "disallowed_scorer",
        "assist1": "disallowed_goal_assistant",
    }),
    "freeKickLost": MappingProxyType({"player": "foul_committer"}),
    "freeKickWon": MappingProxyType({"player": "foul_recipient"}),
    "shotOffTarget": MappingProxyType({"player": "shooter"}),
    "shotBlocked": MappingProxyType({"player": "shooter"}),
    "shotSaved": MappingProxyType({"player": "shooter"}),
    "post": MappingProxyType({"player": "shooter"}),
    "cornerKick": MappingProxyType({"player": "corner_conceding_player"}),
    "substitution": MappingProxyType({
        "playerIn": "incoming_player",
        "playerOut": "outgoing_player",
    }),
    "yellowCard": MappingProxyType({"player": "booked_player"}),
    "redCard": MappingProxyType({"player": "sent_off_player"}),
    "yellowRedCard": MappingProxyType({"player": "second_yellow_sent_off_player"}),
    "offside": MappingProxyType({"player": "offside_player"}),
    # The participant on a generic VAR decision is not reliably the person
    # whose action is described, so videoAssistantReferee intentionally has no
    # role mapping.
    "penaltyLost": MappingProxyType({"player": "penalty_committer"}),
    "penaltyAwarded": MappingProxyType({"player": "penalty_foul_recipient"}),
    "penaltySaved": MappingProxyType({"player": "penalty_taker"}),
    "penaltyMissed": MappingProxyType({"player": "penalty_taker"}),
})


SUPPORTED_TYPES = tuple(CATEGORY_BY_TYPE)
