"""Deterministic enrichment for stored SofaScore narrative comments.

The package deliberately has no database, network, or provider dependencies. It
classifies the original SofaScore type and assigns roles only from structured
participant slots. Text extraction can be added as a separate layer later.
"""

from .normalizer import enrich_comment
from .corner_attributes import extract_corner_attributes
from .foul_attributes import extract_foul_attributes
from .goal_attributes import extract_goal_attributes
from .pipeline import enrich_comment_pipeline
from .shot_attributes import extract_shot_attributes
from .taxonomy import CATEGORY_BY_TYPE, ROLE_BY_TYPE, SUPPORTED_TYPES

__all__ = [
    "CATEGORY_BY_TYPE",
    "ROLE_BY_TYPE",
    "SUPPORTED_TYPES",
    "enrich_comment",
    "extract_corner_attributes",
    "extract_foul_attributes",
    "extract_goal_attributes",
    "extract_shot_attributes",
    "enrich_comment_pipeline",
]
