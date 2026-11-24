"""Acquisition's producer fingerprint and runtime stay self-contained."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import acquisition.recipe as recipe


EXPECTED_PRODUCER_RULES = '8a657cc296bb8338979b595ffa7dc6ab05297dcbe956757f0f6374e68de5298f'


def current_runtime_hash():
    return recipe.producer_rules()


class ProducerFingerprintTests(unittest.TestCase):
    def test_current_runtime_sources_match_the_reviewed_rules_hash(self):
        self.assertEqual(current_runtime_hash(), EXPECTED_PRODUCER_RULES)

    def test_runtime_imports_protocol_without_a_sports_data_tree(self):
        sources = Path(recipe.__file__).resolve().parent
        with tempfile.TemporaryDirectory(prefix='rja-acquisition-runtime-') as temporary:
            runtime = Path(temporary)
            shutil.copytree(sources, runtime / 'acquisition', ignore=shutil.ignore_patterns('__pycache__'))
            probe = f"""
from pathlib import Path
import sys
import acquisition.bundle_compat.api as api
import acquisition.bundle_compat.importar as importar
import acquisition.group_plan as group_plan
import acquisition.narrative as narrative
import acquisition.protocol as protocol
import acquisition.recipe as recipe
from acquisition.service import configuration

root = Path.cwd()
for module in (api, importar, group_plan, narrative, protocol, recipe):
    assert Path(module.__file__).resolve().is_relative_to(root / 'acquisition')
assert not (root / 'sports-data').exists()
assert not (root / 'persistencia').exists()
assert not any(name in sys.modules for name in ('api', 'importar', 'queries', 'persistencia'))
assert group_plan.recent_rounds(3) == [1, 2, 3]
assert configuration({{'ACQ_PROVIDER': 'sofascore', 'ACQ_ENABLE_REAL': '1'}}) == (
    'sofascore', {{'recent_round_count': 3, 'max_total_sends': 50}})
inventory = {{'schema': 1, 'generation_id': 'fixture', 'teams': [2841, 135672, *range(90001, 90019)],
             'events': [{{'id': 1, 'current': True, 'replaced_by': None, 'home': 2841, 'away': 135672,
                         'status': 'finished', 'tournament_id': 94191, 'season_id': 97382}}], 'records': []}}
inventory['sha256'] = protocol.fingerprint(inventory)
assert 1 in protocol.check_inventory(inventory)
assert protocol.verify is not None
invalid = protocol.encode({{'manifest': {{'contract': 'group-v3'}}, 'files': {{}}}})
descriptor = {{'bytes': len(invalid), 'sha256': protocol.sha(invalid)}}
try:
    protocol.verify(invalid, descriptor, inventory, context=('job', 'request'), expected_rules='rules',
                    test_transport=True)
except protocol.Rejected as error:
    assert str(error) == 'Incompatible contract'
else:
    raise AssertionError('Malformed sealed bundle was accepted')
assert narrative.validar_respuesta(b'{{"comments": []}}') == 0
assert recipe.producer_rules() == {EXPECTED_PRODUCER_RULES!r}
"""
            environment = os.environ.copy()
            environment['PYTHONPATH'] = str(runtime)
            result = subprocess.run([sys.executable, '-B', '-c', probe], cwd=runtime, env=environment,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_compose_builds_acquisition_from_its_own_context(self):
        repository = next((parent for parent in Path(recipe.__file__).resolve().parents
                           if (parent / 'compose.dev.yaml').is_file()), None)
        if repository is None:
            self.skipTest('monorepo Compose files are not included in the Acquisition test image')
        dockerfile = (repository / 'acquisition/Dockerfile').read_text(encoding='utf-8')
        self.assertIn('COPY src/acquisition/ /opt/app/acquisition/', dockerfile)
        self.assertNotIn('--from=backend', dockerfile)
        self.assertNotIn('--from=implementation', dockerfile)
        self.assertNotIn('--from=checks', dockerfile)
        for name in ('compose.dev.yaml', 'compose.prod.yaml'):
            compose = (repository / name).read_text(encoding='utf-8')
            self.assertIn('context: ./acquisition', compose)
            self.assertNotIn('additional_contexts:', compose)
            self.assertNotIn('backend: ./sports-data', compose)


if __name__ == '__main__':
    unittest.main(verbosity=2)
