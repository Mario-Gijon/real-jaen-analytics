"""Focused tests for explicit attributes in Spanish shot comments."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment import extract_shot_attributes  # noqa: E402


class ShotAttributeTests(unittest.TestCase):
    def test_extracts_explicit_surface_area_sector_and_cross_assistant(self):
        result = extract_shot_attributes(
            "Remate rechazado, disparo de cabeza desde el lado izquierdo dentro del área. "
            "Asistencia de Javi Antón con un centro al área."
        )
        self.assertEqual(result, {
            "surface": "head",
            "area_zone": "inside_area",
            "sector": "left",
            "prior_pass": "cross",
            "assistant_name": "Javi Antón",
        })

    def test_detects_left_right_and_center_origin_without_reading_target_direction(self):
        left = extract_shot_attributes(
            "Disparo con la derecha desde la banda derecha que se pierde por la izquierda."
        )
        center = extract_shot_attributes("zurdazo desde el centro del área.")
        outside = extract_shot_attributes(
            "Disparo con la derecha desde fuera del área que se va alto y por la derecha."
        )
        self.assertEqual(left["sector"], "right")
        self.assertEqual(left["surface"], "right_foot")
        self.assertEqual(center["sector"], "center")
        self.assertEqual(center["area_zone"], "inside_area")
        self.assertEqual(outside["sector"], None)
        self.assertEqual(outside["area_zone"], "outside_area")

    def test_detects_long_and_through_pass_and_plain_assistant_name(self):
        long_pass = extract_shot_attributes(
            "Disparo con la izquierda desde fuera del área. Asistencia de Manu Sánchez pase largo."
        )
        through_ball = extract_shot_attributes(
            "zurdazo desde el centro del área. Asistencia de Roberto Martín después de un pase en profundidad."
        )
        self.assertEqual(long_pass["prior_pass"], "long_pass")
        self.assertEqual(long_pass["assistant_name"], "Manu Sánchez")
        self.assertEqual(through_ball["prior_pass"], "through_ball")
        self.assertEqual(through_ball["assistant_name"], "Roberto Martín")

    def test_ignores_goal_direction_and_assistant_pass_technique_as_shot_surface(self):
        result = extract_shot_attributes(
            "Tiro atajado por abajo a la izquierda. Disparo desde muy cerca. "
            "Asistencia de Gori con un pase de cabeza."
        )
        self.assertIsNone(result["surface"])
        self.assertIsNone(result["sector"])
        self.assertIsNone(result["area_zone"])
        self.assertIsNone(result["prior_pass"])
        self.assertEqual(result["assistant_name"], "Gori")

    def test_ambiguous_conflicting_cues_and_missing_text_remain_null(self):
        conflicting = extract_shot_attributes(
            "Disparo con la derecha y zurdazo desde fuera del área."
        )
        missing = extract_shot_attributes(None)
        self.assertIsNone(conflicting["surface"])
        self.assertEqual(conflicting["area_zone"], "outside_area")
        self.assertEqual(missing, {
            "surface": None,
            "area_zone": None,
            "sector": None,
            "prior_pass": None,
            "assistant_name": None,
        })

    def test_conflicting_area_sector_and_pass_cues_remain_null(self):
        result = extract_shot_attributes(
            "Disparo desde dentro del área y desde fuera del área, desde el lado izquierdo "
            "y desde el lado derecho. Asistencia de Gori con un centro al área tras un pase en profundidad."
        )
        self.assertIsNone(result["area_zone"])
        self.assertIsNone(result["sector"])
        self.assertIsNone(result["prior_pass"])
        self.assertEqual(result["assistant_name"], "Gori")


if __name__ == "__main__":
    unittest.main()
