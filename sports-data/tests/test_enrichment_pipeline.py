"""Focused tests for the deterministic enrichment pipeline."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment import enrich_comment_pipeline  # noqa: E402


class EnrichmentPipelineTests(unittest.TestCase):
    def test_shot_dispatches_attributes_and_keeps_structured_shooter(self):
        result = enrich_comment_pipeline({
            "type": "shotSaved",
            "text": "Disparo con la derecha desde dentro del área.",
            "player": {"id": 17, "name": "Jugador"},
        })
        self.assertEqual(set(result), {"category", "original_type", "participants", "attributes"})
        self.assertEqual(result["category"], "shot")
        self.assertEqual(result["original_type"], "shotSaved")
        self.assertEqual(result["participants"][0]["role"], "shooter")
        self.assertEqual(result["attributes"]["surface"], "right_foot")
        self.assertEqual(result["attributes"]["area_zone"], "inside_area")

    def test_simulation_comment_does_not_become_a_confirmed_foul(self):
        result = enrich_comment_pipeline({
            "type": "freeKickWon",
            "text": "Javi Antón se ha ido al suelo, pero ha simulado una falta.",
            "player": {"id": 18, "name": "Javi Antón"},
        })
        self.assertIsNone(result["participants"][0]["role"])
        self.assertEqual(result["participants"][0]["normalizer_role"], "foul_recipient")
        self.assertIsNone(result["attributes"]["infraction_type"])

        confirmed = enrich_comment_pipeline({
            "type": "freeKickLost",
            "text": "Falta de Cristian Rodríguez (Juventud Torremolinos).",
            "player": {"id": 975170, "name": "Cristian Rodríguez"},
        })
        self.assertEqual(confirmed["participants"][0]["role"], "foul_committer")
        self.assertEqual(confirmed["attributes"]["infraction_type"], "foul")

    def test_own_goal_author_is_distinct_and_original_participant_reference_is_kept(self):
        result = enrich_comment_pipeline({
            "type": "scoreChange",
            "text": "¡Gol! Autogol de Juan Durán. Asistencia de Gori.",
            "player": {"id": 1393275, "name": "Juan Durán"},
        })
        self.assertEqual(result["attributes"]["goal_kind"], "own_goal")
        self.assertEqual(result["participants"], [{
            "source_role": "player",
            "role": "own_goal_author",
            "source_id": 1393275,
            "name": "Juan Durán",
            "normalizer_role": "scorer",
        }])
        self.assertEqual(result["attributes"]["assistant_name_text"], "Gori")
        self.assertEqual(len(result["participants"]), 1)

    def test_annulled_goal_keeps_disallowed_status(self):
        result = enrich_comment_pipeline({
            "type": "varCancelledGoal",
            "text": "Gol cancelado por el VAR: el balón entró, pero fue anulado.",
            "player": {"id": 19, "name": "Jugador"},
        })
        self.assertEqual(result["attributes"]["goal_status"], "disallowed")
        self.assertEqual(result["participants"][0]["role"], "disallowed_scorer")

    def test_corner_dispatches_only_text_names_without_creating_participants(self):
        result = enrich_comment_pipeline({
            "type": "cornerKick",
            "text": "Saque de esquina, Real Jaén. Saque de esquina cometido por Cestero.",
            "player": {"id": 1590121, "name": "Jorge Cestero"},
        })
        self.assertEqual(result["attributes"], {
            "benefiting_team_text": "Real Jaén",
            "conceding_player_text": "Cestero",
        })
        self.assertEqual(len(result["participants"]), 1)
        self.assertEqual(result["participants"][0]["role"], "corner_conceding_player")

    def test_card_and_substitution_use_normalized_roles_without_text_extractors(self):
        card = enrich_comment_pipeline({
            "type": "yellowCard",
            "text": "Tarjeta amarilla.",
            "player": {"id": 20, "name": "Jugador"},
        })
        substitution = enrich_comment_pipeline({
            "type": "substitution",
            "text": "Cambio.",
            "playerIn": {"id": 21, "name": "Entra"},
            "playerOut": {"id": 22, "name": "Sale"},
        })
        self.assertEqual(card["category"], "discipline")
        self.assertEqual(card["participants"][0]["role"], "booked_player")
        self.assertEqual(card["attributes"], {})
        self.assertEqual(
            [participant["role"] for participant in substitution["participants"]],
            ["incoming_player", "outgoing_player"],
        )
        self.assertEqual(substitution["attributes"], {})

    def test_var_decision_has_no_invented_text_attributes_or_participant_role(self):
        result = enrich_comment_pipeline({
            "type": "videoAssistantReferee",
            "text": "Decisión del VAR.",
            "player": {"id": 23, "name": "Jugador"},
        })
        self.assertEqual(result["category"], "var")
        self.assertEqual(result["attributes"], {})
        self.assertIsNone(result["participants"][0]["role"])


if __name__ == "__main__":
    unittest.main()
