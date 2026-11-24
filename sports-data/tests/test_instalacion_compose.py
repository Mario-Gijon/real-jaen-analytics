"""Nombres efectivos de una instalación (scripts/instalacion_compose.py), a partir de configuraciones con la forma de
`docker compose config --format json`: prefijo por defecto, prefijo y proyecto personalizados, producción con nombres
propios y configuraciones que no deben aceptarse."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import instalacion_compose as helper

HELPER = Path(helper.__file__)


def config(project='real-jaen-analytics-dev', prefix='rja-dev', image='local/real-jaen-analytics/sports-data:dev',
           imports='/srv/imports', video_catalog='/srv/video-catalog'):
    """Shape of `docker compose -f compose.dev.yaml config --format json` (only what the helper reads, plus noise)."""
    def volume(source, target, **extra):
        return dict(type='volume', source=source, target=target, volume={}, **extra)
    return {
        'name': project,
        'services': {
            'sports-data': {'image': image, 'volumes': [
                volume('sports-state', '/state'),
                {'type': 'bind', 'source': f'{imports}/../seed/sports', 'target': '/run/rja-seed', 'read_only': True},
                {'type': 'bind', 'source': imports, 'target': '/imports', 'read_only': True},
            ]},
            'acquisition': {'image': 'local/real-jaen-analytics/acquisition:dev', 'volumes': [
                volume('acquisition-profiles', '/profiles'), volume('acquisition-jobs', '/acquisition-jobs'),
                {'type': 'bind', 'source': '/repo/acquisition/src/acquisition', 'target': '/opt/app/acquisition',
                 'read_only': True},
            ]},
            'video-service': {'environment': {'VIDEO_SERVICE_MANIFEST': 'videos.json'}, 'volumes': [
                volume('video-state', '/state'),
                {'type': 'bind', 'source': video_catalog, 'target': '/app/data', 'read_only': True},
            ]},
            'dashboard': {'volumes': [volume('dashboard-next', '/app/.next')]},
        },
        'volumes': {key: {'name': f'{prefix}-{key}'} for key in
                    ('sports-state', 'acquisition-profiles', 'acquisition-jobs', 'video-state', 'dashboard-next')},
        'networks': {'dashboard-edge': {'name': f'{prefix}-dashboard-edge'}},
    }


class Resolve(unittest.TestCase):
    def test_default_development_prefix(self):
        self.assertEqual(helper.resolve(config()), {
            'project': 'real-jaen-analytics-dev',
            'sports_data_image': 'local/real-jaen-analytics/sports-data:dev',
            'sports_state': 'rja-dev-sports-state',
            'acquisition_jobs': 'rja-dev-acquisition-jobs',
            'acquisition_profiles': 'rja-dev-acquisition-profiles',
            'video_state': 'rja-dev-video-state',
            'imports': '/srv/imports',
            'video_catalog_dir': '/srv/video-catalog',
            'video_manifest': 'videos.json',
        })

    def test_custom_prefix_and_project(self):
        resolved = helper.resolve(config(project='real-jaen-analytics-alvaro', prefix='rja-alvaro',
                                         imports=r'C:\Users\someone\rja\.rja-private\imports'))
        self.assertEqual(resolved['project'], 'real-jaen-analytics-alvaro')
        self.assertEqual([resolved[k] for k in ('sports_state', 'acquisition_jobs', 'acquisition_profiles', 'video_state')],
                         ['rja-alvaro-sports-state', 'rja-alvaro-acquisition-jobs', 'rja-alvaro-acquisition-profiles',
                          'rja-alvaro-video-state'])
        self.assertEqual(resolved['imports'], r'C:\Users\someone\rja\.rja-private\imports')  # Windows host path kept
        self.assertNotIn('rja-dev', json.dumps(resolved))

    def test_production_with_its_own_volume_names(self):
        c = config(project='real-jaen-analytics-sinbad2-prod', image='local/real-jaen-analytics/sports-data:prod')
        c['volumes'] = {'sports-state': {'name': 'rja-sinbad2-sports-state'},
                        'acquisition-profiles': {'name': 'rja-sinbad2-acquisition-profiles'},
                        'acquisition-jobs': {'name': 'rja-sinbad2-acquisition-jobs'},
                        'video-state': {'name': 'rja-sinbad2-video-state'}}
        resolved = helper.resolve(c)
        self.assertEqual(resolved['sports_state'], 'rja-sinbad2-sports-state')
        self.assertEqual(resolved['sports_data_image'], 'local/real-jaen-analytics/sports-data:prod')
        self.assertEqual(resolved['video_catalog_dir'], '/srv/video-catalog')

    def test_refuses_configurations_that_do_not_describe_an_installation(self):
        def broken(change):
            c = copy.deepcopy(config())
            change(c)
            return c
        cases = {
            'invalid project': broken(lambda c: c.update(name='Proyecto X')),
            'no project': broken(lambda c: c.pop('name')),
            'missing service': broken(lambda c: c['services'].pop('video-service')),
            'volume not declared': broken(lambda c: c['volumes'].pop('acquisition-jobs')),
            'volume without name': broken(lambda c: c['volumes']['video-state'].pop('name')),
            'state as a bind mount': broken(lambda c: c['services']['sports-data']['volumes'].__setitem__(
                0, {'type': 'bind', 'source': '/tmp/state', 'target': '/state'})),
            'another volume at /state': broken(lambda c: c['services']['video-service']['volumes'].__setitem__(
                0, {'type': 'volume', 'source': 'sports-state', 'target': '/state'})),
            'read-only data volume': broken(lambda c: c['services']['acquisition']['volumes'][1].update(read_only=True)),
            'shared volume name': broken(lambda c: c['volumes']['video-state'].update(name='rja-dev-sports-state')),
            'no imports': broken(lambda c: c['services']['sports-data']['volumes'].pop(2)),
            'imports as a volume': broken(lambda c: c['services']['sports-data']['volumes'][2].update(type='volume')),
            'no image': broken(lambda c: c['services']['sports-data'].pop('image')),
            'video catalog is writable': broken(lambda c: c['services']['video-service']['volumes'][1].update(
                read_only=False)),
            'video catalog is a named volume': broken(lambda c: c['services']['video-service']['volumes'][1].update(
                type='volume')),
            'invalid video manifest path': broken(lambda c: c['services']['video-service']['environment'].update(
                VIDEO_SERVICE_MANIFEST='../videos.json')),
            'not an object': [],
        }
        for label, value in cases.items():
            with self.subTest(label), self.assertRaises(helper.ConfigError):
                helper.resolve(value)


class CommandLine(unittest.TestCase):
    def run_helper(self, text):
        # -X utf8: on Windows the child's stderr would otherwise use the console code page (in the container: UTF-8).
        return subprocess.run([sys.executable, '-I', '-B', '-X', 'utf8', str(HELPER)], input=text, capture_output=True,
                              text=True,
                              encoding='utf-8')

    def test_prints_one_key_per_line(self):
        result = self.run_helper(json.dumps(config(prefix='rja-alvaro', project='real-jaen-analytics-alvaro')))
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = dict(line.split('=', 1) for line in result.stdout.splitlines())
        self.assertEqual(lines['project'], 'real-jaen-analytics-alvaro')
        self.assertEqual(lines['video_state'], 'rja-alvaro-video-state')

    def test_prints_nothing_and_fails_on_bad_input(self):
        for text in ('no es json', json.dumps({'name': 'x'})):
            with self.subTest(text):
                result = self.run_helper(text)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, '')
                self.assertIn('Configuración de Compose', result.stderr)


if __name__ == '__main__':
    unittest.main()
