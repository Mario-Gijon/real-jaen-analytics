"""The development source tree must build a producer approved by Sports Data."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import acquisition.recipe as recipe


EXPECTED_PRODUCER_RULES = 'd00fc9808953b30f550220f254573f93d488d1f427dd6e88766b2df9ae02a1d6'
PRODUCER_FILES = (
    'recipe.py', 'group_plan.py', 'transport.py', 'sdk_adapter.py', 'operation_budget.py',
    'service.py', 'app.py', 'job.py', '../persistencia/bundle.py', '../persistencia/comentarios.py',
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
        for name in PRODUCER_FILES[:8]:
            (runtime_acquisition / name).symlink_to((acquisition_sources / name).resolve())
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


if __name__ == '__main__':
    unittest.main(verbosity=2)
