"""Focused tests for explicit attributes in Spanish foul comments."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment import extract_foul_attributes  # noqa: E402


class FoulAttributeTests(unittest.TestCase):
    def test_detects_foul_handball_and_dangerous_play(self):
        foul = extract_foul_attributes("Falta de Imanol Baz (Cartagena).")
        handball = extract_foul_attributes("Mano de Mouhamadou Keita (Real Jaén).")
        dangerous_play = extract_foul_attributes("Juego peligroso de Busi (Real Jaén).")
        self.assertEqual(foul["infraction_type"], "foul")
        self.assertEqual(handball["infraction_type"], "handball")
        self.assertEqual(dangerous_play["infraction_type"], "foul")

    def test_detects_received_foul_and_explicit_context(self):
        defensive = extract_foul_attributes(
            "Mouhamadou Keita (Real Jaén) ha recibido una falta en la zona defensiva."
        )
        opponent_half = extract_foul_attributes(
            "Davo (Alcorcón) ha recibido una falta en campo contrario."
        )
        self.assertEqual(defensive["infraction_type"], "foul")
        self.assertEqual(defensive["location_context"], "defensive_zone")
        self.assertEqual(opponent_half["location_context"], "opponent_half")

    def test_detects_flank_and_side_only_when_explicit(self):
        left = extract_foul_attributes(
            "Mouhamadou Keita (Real Jaén) ha recibido una falta en la banda izquierda."
        )
        right = extract_foul_attributes(
            "Romeo Hueso (Atlético Madrileño) ha recibido una falta en la banda derecha."
        )
        self.assertEqual(left["location_context"], "flank")
        self.assertEqual(left["flank"], "left")
        self.assertEqual(right["location_context"], "flank")
        self.assertEqual(right["flank"], "right")

    def test_does_not_confuse_a_name_or_simulation_with_an_infringement(self):
        name = extract_foul_attributes(
            "Imanol Baz (Cartagena) ha recibido una falta en la zona defensiva."
        )
        simulation = extract_foul_attributes(
            "Javi Antón (Antequera) se ha ido al suelo, pero ha simulado una falta."
        )
        no_description = extract_foul_attributes("Rober Ibáñez (Alcorcón).")
        self.assertEqual(name["infraction_type"], "foul")
        self.assertIsNone(simulation["infraction_type"])
        self.assertIsNone(no_description["infraction_type"])

    def test_conflicting_or_unknown_location_and_missing_text_remain_null(self):
        conflict = extract_foul_attributes(
            "Ha recibido una falta en la zona defensiva y en campo contrario."
        )
        unknown = extract_foul_attributes("Falta de un jugador en el centro del campo.")
        missing = extract_foul_attributes(None)
        self.assertIsNone(conflict["location_context"])
        self.assertIsNone(conflict["flank"])
        self.assertIsNone(unknown["location_context"])
        self.assertIsNone(unknown["flank"])
        self.assertEqual(missing, {
            "infraction_type": None,
            "location_context": None,
            "flank": None,
        })

    def test_contradictory_infraction_cues_remain_unknown(self):
        result = extract_foul_attributes("Mano de X y falta de Y.")
        self.assertIsNone(result["infraction_type"])


if __name__ == "__main__":
    unittest.main()
