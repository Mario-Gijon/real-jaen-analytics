"""Comprobaciones pequeñas de la proyección API de alias de jugadores."""
import copy
import sys
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(Path(__file__).resolve().parent / 'fixtures'))

import api  # noqa: E402
from identidad import combinar_fichas  # noqa: E402
from tests.test_api import DATOS  # noqa: E402


class PerfilCanonico(unittest.TestCase):
    def test_prefiere_canonico_y_no_elige_aliases_contradictorios(self):
        ficha = combinar_fichas(100, {
            100: {'player_id': 100, 'name': 'Canónico', 'country': None, 'height_cm': None},
            200: {'player_id': 200, 'name': 'Alias uno', 'country': 'España', 'height_cm': 180},
            300: {'player_id': 300, 'name': 'Alias dos', 'country': 'Francia', 'height_cm': 180},
        })
        self.assertEqual(ficha['player_id'], 100)
        self.assertEqual(ficha['name'], 'Canónico')
        self.assertIsNone(ficha['country'])
        self.assertEqual(ficha['height_cm'], 180)


class RutasDeAlias(unittest.TestCase):
    def setUp(self):
        self.data = copy.deepcopy(DATOS)
        self.canonical, self.alias = 5001, 5006
        self.data['jugadores_alias'] = {self.alias: self.canonical}
        alias_profile = next(player for player in self.data['jugadores'] if player['player_id'] == self.alias)
        self.data['jugadores'] = [player for player in self.data['jugadores'] if player['player_id'] != self.alias]
        from identidad import combinar_fichas
        canonical_profile = next(player for player in self.data['jugadores']
                                 if player['player_id'] == self.canonical)
        self.data['jugadores'] = [player for player in self.data['jugadores']
                                  if player['player_id'] != self.canonical]
        self.data['jugadores'].append(combinar_fichas(self.canonical, {
            self.canonical: canonical_profile, self.alias: alias_profile,
        }))
        for row in self.data['jugador_partido']:
            if row['player_id'] == self.alias:
                row['player_id'] = self.canonical

    def test_los_dos_ids_abren_la_ficha_canonica(self):
        for player_id in (self.canonical, self.alias):
            status, response = api.consultar(self.data, f'/api/v1/players/{player_id}')
            self.assertEqual(status, 200)
            self.assertEqual(response['data']['player_id'], self.canonical)

    def test_filtro_por_alias_devuelve_las_convocatorias_consolidadas(self):
        status, response = api.consultar(self.data, f'/api/v1/players/{self.alias}/matches')
        self.assertEqual(status, 200)
        self.assertEqual(response['meta']['count'], 4)
        self.assertEqual({row['player_id'] for row in response['data']}, {self.canonical})
        self.assertEqual(len({row['match_id'] for row in response['data']}), 4)


if __name__ == '__main__':
    unittest.main()
