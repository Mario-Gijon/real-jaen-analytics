"""Focused tests for deterministic attributes in Spanish goal comments."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment import extract_goal_attributes  # noqa: E402


class GoalAttributeTests(unittest.TestCase):
    def test_status_and_penalty_origin_follow_original_type(self):
        scored = extract_goal_attributes(
            "penaltyScored",
            "¡Gol! Marco Siverio convirtió el penalti. Disparo con la derecha.",
        )
        cancelled = extract_goal_attributes(
            "varCancelledGoal",
            "GOL CANCELADO POR EL VAR: Davo anotó pero el gol fue anulado luego de la revisión del VAR.",
        )
        self.assertEqual(scored["goal_status"], "valid")
        self.assertEqual(scored["goal_kind"], "ordinary")
        self.assertEqual(scored["goal_origin"], "penalty")
        self.assertEqual(cancelled["goal_status"], "disallowed")
        self.assertIn("var_cancellation", cancelled["contexts"])

    def test_extracts_set_piece_and_open_play_only_from_explicit_cues(self):
        corner = extract_goal_attributes(
            "scoreChange",
            "¡Gol! Disparo de cabeza. Asistencia de Javi Antón con un centro al área tras un saque de esquina.",
        )
        free_kick = extract_goal_attributes(
            "scoreChange", "¡Gol! César Bonafé marca de tiro libre directo, disparo con la derecha."
        )
        open_play = extract_goal_attributes(
            "scoreChange", "¡Gol! Asistencia de Antonio Caballero después de un pase en profundidad."
        )
        ordinary = extract_goal_attributes(
            "scoreChange", "¡Gol! Disparo con la derecha. Asistencia de Álvaro Santos."
        )
        self.assertEqual(corner["goal_origin"], "set_piece")
        self.assertEqual(free_kick["goal_origin"], "set_piece")
        self.assertEqual(open_play["goal_origin"], "open_play")
        self.assertIsNone(ordinary["goal_origin"])

    def test_reuses_shot_attributes_and_keeps_goal_mouth_direction_out_of_sector(self):
        result = extract_goal_attributes(
            "scoreChange",
            "¡Gol! Disparo con la derecha desde el lado izquierdo dentro del área "
            "por el lado derecho de la portería. Asistencia de Gori.",
        )
        self.assertEqual(result["surface"], "right_foot")
        self.assertEqual(result["area_zone"], "inside_area")
        self.assertEqual(result["sector"], "left")
        self.assertEqual(result["assistant_name_text"], "Gori")

    def test_contexts_include_cross_pass_corner_and_var_review(self):
        result = extract_goal_attributes(
            "scoreChange",
            "¡Gol! Disparo de cabeza desde el centro del área. Asistencia de Javi Antón "
            "con un centro al área tras un saque de esquina. Gol confirmado luego de la revisión del VAR.",
        )
        self.assertEqual(result["goal_origin"], "set_piece")
        self.assertEqual(
            result["contexts"], ["after_corner", "cross", "var_confirmation"]
        )

    def test_annulled_goal_assistant_is_only_a_textual_mention(self):
        result = extract_goal_attributes(
            "varCancelledGoal",
            "GOL CANCELADO POR EL VAR: X anotó pero fue anulado. Asistencia de Y.",
        )
        self.assertEqual(result["goal_status"], "disallowed")
        self.assertEqual(result["assistant_name_text"], "Y")
        self.assertNotIn("official_assist", result)

    def test_conflicting_status_or_origin_and_unknown_type_remain_null(self):
        conflict = extract_goal_attributes(
            "scoreChange", "Gol anulado: gol confirmado luego de la revisión del VAR."
        )
        origin_conflict = extract_goal_attributes(
            "scoreChange", "Gol tras un saque de esquina y un contragolpe."
        )
        unsupported = extract_goal_attributes("shotSaved", "Disparo con la derecha.")
        invalid_type = extract_goal_attributes(None, "Gol con un pase en profundidad.")
        self.assertIsNone(conflict["goal_status"])
        self.assertIsNone(conflict["goal_kind"])
        self.assertIsNone(origin_conflict["goal_origin"])
        self.assertIsNone(unsupported["goal_status"])
        self.assertIsNone(unsupported["goal_kind"])
        self.assertEqual(unsupported["contexts"], [])
        self.assertEqual(invalid_type["contexts"], [])

    def test_identifies_real_corpus_own_goal_without_changing_other_attributes(self):
        result = extract_goal_attributes(
            "scoreChange",
            "Autogol de Juan Durán, Rayo Majadahonda. CE Europa 1, Rayo Majadahonda 0.",
        )
        self.assertEqual(result["goal_kind"], "own_goal")
        self.assertEqual(result["goal_status"], "valid")
        self.assertIsNone(result["goal_origin"])
        self.assertEqual(result["contexts"], [])

    def test_recognizes_explicit_own_goal_phrasing_variants(self):
        for wording in (
            "Gol en propia puerta de Alex Cano.",
            "Jorge Cestero introdujo el balón en su propia portería.",
            "Autogol de Dani Merchán.",
        ):
            with self.subTest(wording=wording):
                self.assertEqual(
                    extract_goal_attributes("scoreChange", wording)["goal_kind"],
                    "own_goal",
                )

    def test_ordinary_goal_heading_and_insufficient_evidence(self):
        ordinary = extract_goal_attributes("scoreChange", "¡Gol! Real Jaén 1, Ibiza 0.")
        unknown = extract_goal_attributes("scoreChange", "Real Jaén 1, Ibiza 0.")
        self.assertEqual(ordinary["goal_kind"], "ordinary")
        self.assertIsNone(unknown["goal_kind"])

    def test_explicit_own_goal_overrides_generic_goal_heading(self):
        result = extract_goal_attributes("scoreChange", "¡Gol! Autogol de Alex Cano.")
        self.assertEqual(result["goal_kind"], "own_goal")
        self.assertEqual(result["goal_status"], "valid")


if __name__ == "__main__":
    unittest.main()
