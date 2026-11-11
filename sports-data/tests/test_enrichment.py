"""Focused tests for deterministic comment taxonomy and participant roles."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment import CATEGORY_BY_TYPE, SUPPORTED_TYPES, enrich_comment  # noqa: E402


class CommentEnrichmentTests(unittest.TestCase):
    def test_taxonomy_covers_the_27_observed_provider_types(self):
        self.assertEqual(len(SUPPORTED_TYPES), 27)
        self.assertEqual(len(CATEGORY_BY_TYPE), 27)
        self.assertEqual(CATEGORY_BY_TYPE["scoreChange"], "goal")
        self.assertEqual(CATEGORY_BY_TYPE["varCancelledGoal"], "goal_review")
        self.assertEqual(CATEGORY_BY_TYPE["videoAssistantReferee"], "var")
        self.assertIsNone(enrich_comment({"type": "futureProviderType"})["category"])

    def test_goal_roles_use_provider_ids_not_text_name_matching(self):
        result = enrich_comment({
            "type": "scoreChange",
            "text": "Gol de Someone Else. Asistencia de Another Name.",
            "player": {"id": 2235113, "name": "Joe Riley"},
            "assist1": {"id": "1010527", "name": "Pau Martínez"},
        })
        self.assertEqual(result, {
            "category": "goal",
            "original_type": "scoreChange",
            "participants": [
                {"source_role": "player", "role": "scorer", "source_id": 2235113, "name": "Joe Riley"},
                {"source_role": "assist1", "role": "assistant", "source_id": "1010527", "name": "Pau Martínez"},
            ],
        })

    def test_structured_foul_corner_and_substitution_roles(self):
        foul = enrich_comment({
            "type": "freeKickWon",
            "participants": {"player": {"id": 77, "name": "Paris Adot"}},
        })
        corner = enrich_comment({
            "type": "cornerKick",
            "participants_json": json.dumps({"player": {"id": 88, "name": "Tomás Sánchez"}}),
        })
        substitution = enrich_comment({
            "type": "substitution",
            "player": {"id": 10, "name": "David Montejano"},
            "playerIn": {"id": 10, "name": "David Gómez Montejano"},
            "playerOut": {"id": 20, "name": "Mario Martos"},
        })
        self.assertEqual(foul["participants"][0]["role"], "foul_recipient")
        self.assertEqual(corner["participants"][0]["role"], "corner_conceding_player")
        self.assertEqual(substitution["participants"], [
            {"source_role": "playerIn", "role": "incoming_player", "source_id": 10,
             "name": "David Gómez Montejano"},
            {"source_role": "playerOut", "role": "outgoing_player", "source_id": 20,
             "name": "Mario Martos"},
        ])

    def test_ambiguous_var_and_missing_ids_remain_unidentified(self):
        var = enrich_comment({
            "type": "videoAssistantReferee",
            "text": "Decisión del VAR: No Fue Penalti.",
            "player": {"id": 123, "name": "Jugador no mencionado"},
        })
        no_id = enrich_comment({
            "type": "yellowCard",
            "participants": {"player": {"name": "Nombre visible"}},
        })
        self.assertIsNone(var["participants"][0]["role"])
        self.assertEqual(var["participants"][0]["source_id"], 123)
        self.assertIsNone(no_id["participants"][0]["role"])
        self.assertIsNone(no_id["participants"][0]["source_id"])

    def test_source_ids_accept_only_positive_integers_or_ascii_decimal_strings(self):
        for source_id in (123, "123", "00123"):
            with self.subTest(source_id=source_id):
                result = enrich_comment({
                    "type": "scoreChange",
                    "player": {"id": source_id, "name": "Jugador"},
                })
                self.assertEqual(result["participants"][0]["source_id"], source_id)
                self.assertEqual(result["participants"][0]["role"], "scorer")

        invalid_ids = (
            None, True, False, 0, -1, 1.5, "", "   ", "123abc", " 123", "123 ",
            "１２３", "000",
        )
        for source_id in invalid_ids:
            with self.subTest(source_id=source_id):
                result = enrich_comment({
                    "type": "scoreChange",
                    "player": {"id": source_id, "name": "Jugador"},
                })
                self.assertIsNone(result["participants"][0]["source_id"])
                self.assertIsNone(result["participants"][0]["role"])


if __name__ == "__main__":
    unittest.main()
