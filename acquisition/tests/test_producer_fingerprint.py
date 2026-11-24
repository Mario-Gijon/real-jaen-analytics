"""The development source tree must build a producer approved by Sports Data."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import acquisition.recipe as recipe


EXPECTED_PRODUCER_RULES = 'af79a3ebf2d1e127fef1cf22d09817f58a5725a0e9c95b6e24b8fdf19db0a2ca'
PRODUCER_FILES = (
    'recipe.py', 'group_plan.py', 'transport.py', 'sdk_adapter.py', 'operation_budget.py',
    'service.py', 'app.py', 'job.py', 'bundle_compat/__init__.py', 'bundle_compat/api.py',
    'bundle_compat/importar.py',
    '../persistencia/bundle.py', '../persistencia/comentarios.py',
)


def current_runtime_hash():
    runtime_acquisition = Path(recipe.__file__).resolve().parent
    runtime_persistencia = runtime_acquisition.parent / 'persistencia'
    if (runtime_persistencia / 'bundle.py').is_file() and (runtime_persistencia / 'comentarios.py').is_file():
        return recipe.producer_rules()

    repository = Path(__file__).resolve().parents[2]
    acquisition_sources = repository / 'acquisition/src/acquisition'
    backend_sources = repository / 'sports-data/persistencia'
    with tempfile.TemporaryDirectory(prefix='rja-producer-runtime-') as temporary:
        runtime = Path(temporary)
        runtime_acquisition = runtime / 'acquisition'
        runtime_persistencia = runtime / 'persistencia'
        runtime_acquisition.mkdir()
        runtime_persistencia.mkdir()
        for name in PRODUCER_FILES[:11]:
            target = runtime_acquisition / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to((acquisition_sources / name).resolve())
        for name in ('bundle.py', 'comentarios.py'):
            (runtime_persistencia / name).symlink_to((backend_sources / name).resolve())

        with mock.patch.object(recipe, '__file__', str(runtime_acquisition / 'recipe.py')):
            return recipe.producer_rules()


def approval_registry_path():
    runtime_registry = Path(__file__).resolve().parent / 'productores-adquisicion.json'
    if runtime_registry.is_file():
        return runtime_registry
    repository = Path(__file__).resolve().parents[2]
    return repository / 'sports-data/productores-adquisicion.json'


class ProducerFingerprintTests(unittest.TestCase):
    def test_current_runtime_sources_match_a_reviewed_sports_data_approval(self):
        actual = current_runtime_hash()
        approvals = json.loads(approval_registry_path().read_text(encoding='utf-8'))['aprobados']
        approved_hashes = {approval['rules_sha256'] for approval in approvals}
        self.assertEqual(actual, EXPECTED_PRODUCER_RULES)
        self.assertIn(actual, approved_hashes)

    def test_runtime_bundle_imports_using_only_acquisition_compatibility_helpers(self):
        runtime_acquisition = Path(recipe.__file__).resolve().parent
        runtime_root = runtime_acquisition.parent
        runtime_persistencia = runtime_root / 'persistencia'
        if not (runtime_persistencia / 'bundle.py').is_file():
            repository = Path(__file__).resolve().parents[2]
            sources = repository / 'acquisition/src/acquisition'
            backend = repository / 'sports-data/persistencia'
            temporary = tempfile.TemporaryDirectory(prefix='rja-bundle-runtime-')
            self.addCleanup(temporary.cleanup)
            runtime_root = Path(temporary.name)
            runtime_persistencia = runtime_root / 'persistencia'
            runtime_persistencia.mkdir()
            runtime_compat = runtime_root / 'acquisition/bundle_compat'
            runtime_compat.mkdir(parents=True)
            (runtime_compat / '__init__.py').symlink_to((sources / 'bundle_compat/__init__.py').resolve())
            (runtime_compat / 'api.py').symlink_to((sources / 'bundle_compat/api.py').resolve())
            (runtime_compat / 'importar.py').symlink_to((sources / 'bundle_compat/importar.py').resolve())
            for name in ('bundle.py', 'comentarios.py', 'bloqueo.py'):
                (runtime_persistencia / name).symlink_to((backend / name).resolve())

        probe = """
import api, importar
from persistencia.bundle import check_inventory
from persistencia.comentarios import validar_respuesta
assert api.PENDIENTES == ('notstarted', 'postponed')
assert api.vacia('lineups', {'home': {'players': []}, 'away': {'players': []}})
assert not api.vacia('lineups', {'home': {'players': [{'id': 1}]}, 'away': {'players': []}})
event = {'event': {'id': 7, 'homeTeam': {'id': 1}, 'awayTeam': {'id': 2},
                   'tournament': {'id': 3, 'uniqueTournament': {'id': 4}}, 'season': {'id': 5},
                   'status': {'type': 'finished'}, 'homeScore': {'current': 1},
                   'awayScore': {'current': 0}, 'startTimestamp': 1767323045}}
assert importar.evento_de(event) == event['event']
assert importar.rasgos_evento(event) == {'home_team_id': 1, 'away_team_id': 2, 'tournament_id': 3,
     'unique_tournament_id': 4, 'season_id': 5, 'status': 'finished', 'home_score': 1,
     'away_score': 0, 'start': 1767323045}
listing = {'start_utc': '2026-01-02T03:04:05Z', 'status': {'type': 'finished'},
           'stage': {'id': 9, 'name': 'Group 2'}, 'season_id': 10, 'round': {'number': 4},
           'participants': {'home': {'id': 1}, 'away': {'id': 2}}, 'score': {'home': 2, 'away': 1}}
assert importar.partido_de_listado(listing) == {'kickoff_utc': '2026-01-02T03:04:05+00:00',
     'status': 'finished', 'tournament_id': 9, 'competition': 'Group 2', 'season_id': 10, 'round': 4,
     'home_team_id': 1, 'away_team_id': 2, 'home_score': 2, 'away_score': 1}
assert validar_respuesta(b'{"comments": []}') == 0
assert callable(check_inventory)
"""
        env = os.environ.copy()
        env['PYTHONPATH'] = os.pathsep.join((str(runtime_root), str(runtime_root / 'acquisition/bundle_compat')))
        subprocess.run([sys.executable, '-B', '-c', probe], cwd=runtime_root, env=env, check=True,
                       capture_output=True, text=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
