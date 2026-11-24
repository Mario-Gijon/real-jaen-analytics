"""The development source tree must build a producer approved by Sports Data."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import acquisition.recipe as recipe


EXPECTED_PRODUCER_RULES = '73c9765c7c5058fbf330f7549b4969153a6bfab12dc4bea95b855e9fb8c49847'
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

    def test_isolated_runtime_imports_planner_and_bundle_without_sports_data_helpers(self):
        repository = Path(__file__).resolve().parents[2]
        sources = repository / 'acquisition/src/acquisition'
        backend = repository / 'sports-data/persistencia'
        temporary = tempfile.TemporaryDirectory(prefix='rja-acquisition-runtime-')
        self.addCleanup(temporary.cleanup)
        runtime_root = Path(temporary.name)
        shutil.copytree(sources, runtime_root / 'acquisition')
        runtime_persistencia = runtime_root / 'persistencia'
        runtime_persistencia.mkdir()
        for name in ('bundle.py', 'comentarios.py', 'bloqueo.py'):
            (runtime_persistencia / name).symlink_to((backend / name).resolve())
        # The isolated image layout deliberately contains no Sports Data api.py, queries.py or importar.py.
        self.assertFalse(any((runtime_root / name).exists() for name in ('api.py', 'queries.py', 'importar.py')))

        probe = """
from pathlib import Path
import api, importar
from acquisition import group_plan
from acquisition.service import configuration
from persistencia.bundle import check_inventory
from persistencia.comentarios import validar_respuesta
root = Path.cwd()
assert Path(group_plan.__file__).resolve() == root / 'acquisition/group_plan.py'
assert Path(api.__file__).resolve() == root / 'acquisition/bundle_compat/api.py'
assert Path(importar.__file__).resolve() == root / 'acquisition/bundle_compat/importar.py'
assert group_plan.recent_rounds(3) == [1, 2, 3]
assert configuration({'ACQ_PROVIDER': 'sofascore', 'ACQ_ENABLE_REAL': '1'}) == (
    'sofascore', {'recent_round_count': 3, 'max_total_sends': 50})
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

    def test_dev_and_prod_use_the_same_acquisition_planner_source(self):
        repository = Path(__file__).resolve().parents[2]
        planner = repository / 'acquisition/src/acquisition/group_plan.py'
        dockerfile = (repository / 'acquisition/Dockerfile').read_text(encoding='utf-8')
        sports_data_dockerfile = (repository / 'sports-data/Dockerfile').read_text(encoding='utf-8')
        development = (repository / 'compose.dev.yaml').read_text(encoding='utf-8')
        production = (repository / 'compose.prod.yaml').read_text(encoding='utf-8')
        self.assertFalse(planner.is_symlink())
        self.assertIn('source: ./acquisition/src/acquisition', development)
        self.assertIn('implementation: ./acquisition/src/acquisition', development)
        self.assertIn('implementation: ./acquisition/src/acquisition', production)
        self.assertNotIn('source: ./sports-data/acquisition', development)
        self.assertNotIn('/opt/shared-backend/acquisition', development)
        self.assertNotIn('COPY --from=backend acquisition/group_plan.py', dockerfile)
        self.assertIn('COPY --chown=10001:10001 acquisition/group_plan.py ./acquisition/group_plan.py',
                      sports_data_dockerfile)
        self.assertEqual(current_runtime_hash(), EXPECTED_PRODUCER_RULES)


if __name__ == '__main__':
    unittest.main(verbosity=2)
