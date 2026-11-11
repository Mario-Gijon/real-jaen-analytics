"""Focused tests for explicit corner comment attributes."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment import extract_corner_attributes  # noqa: E402


class CornerAttributeTests(unittest.TestCase):
    def test_extracts_benefiting_team_and_conceding_player_as_text(self):
        result = extract_corner_attributes(
            "cornerKick",
            "Saque de esquina, Real Jaén. Saque de esquina cometido por Tomás Sánchez.",
        )
        self.assertEqual(result, {
            "benefiting_team_text": "Real Jaén",
            "conceding_player_text": "Tomás Sánchez",
        })

    def test_keeps_short_player_alias_as_text_without_resolving_an_id(self):
        result = extract_corner_attributes(
            "cornerKick",
            "Saque de esquina, Cartagena. Saque de esquina cometido por Cestero.",
        )
        self.assertEqual(result["conceding_player_text"], "Cestero")
        self.assertNotIn("player_id", result)

    def test_extracts_fields_independently_and_keeps_missing_values_null(self):
        result = extract_corner_attributes("cornerKick", "Saque de esquina, Real Jaén.")
        self.assertEqual(result["benefiting_team_text"], "Real Jaén")
        self.assertIsNone(result["conceding_player_text"])

    def test_unsupported_type_and_unrecognized_text_remain_unknown(self):
        self.assertEqual(
            extract_corner_attributes("scoreChange", "Saque de esquina, Real Jaén."),
            {"benefiting_team_text": None, "conceding_player_text": None},
        )
        self.assertEqual(
            extract_corner_attributes("cornerKick", "Córner para Real Jaén."),
            {"benefiting_team_text": None, "conceding_player_text": None},
        )
        self.assertEqual(
            extract_corner_attributes("cornerKick", None),
            {"benefiting_team_text": None, "conceding_player_text": None},
        )


if __name__ == "__main__":
    unittest.main()
