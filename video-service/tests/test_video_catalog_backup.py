"""Synthetic external video catalog backup and restore tests; no video files or Docker."""
import importlib.util
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'video-service' / 'src'))
spec = importlib.util.spec_from_file_location('video_catalog_backup', ROOT / 'dashboard/scripts/video-catalog-respaldo.py')
backup = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = backup
spec.loader.exec_module(backup)
from video_service.catalog import VideoCatalog
from video_service.state import VideoStateStore


class VideoCatalogBackup(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='rja-video-restore-')
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.out = self.root / 'out'
        self.parent = self.root / 'external'
        for path in (self.source, self.out, self.parent):
            path.mkdir()
        calibration = {
            'status': 'calibrated', 'version': 1,
            'segments': [{'period': 'first', 'game_clock_start': 0, 'game_clock_end': 90,
                          'offset_seconds': 2.5, 'reference_count': 2, 'max_residual_seconds': 1.0}],
        }
        (self.source / 'calibrations').mkdir()
        (self.source / 'calibrations/match.json').write_text(json.dumps(calibration), encoding='utf-8')
        manifest = {'schema_version': 1, 'videos': [
            {'video_id': 'match-1', 'display_name': 'Match 1', 'match_ref': 'season:match:1',
             'origin': 'youtube', 'source_url': 'https://youtu.be/abcdefghijk',
             'calibration_file': 'calibrations/match.json', 'synchronization_status': 'calibrated'},
        ]}
        (self.source / 'videos.json').write_text(json.dumps(manifest), encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def make_backup(self):
        summary = backup.respaldar(self.source, 'videos.json', self.out)
        (self.out / 'manifest.json').write_text(json.dumps({'catalogo_video': summary}), encoding='utf-8')
        return summary

    def test_validates_restores_manifest_and_calibration_for_runtime_loader(self):
        summary = self.make_backup()
        self.assertEqual(backup.validar_respaldo(self.out / 'video-catalog.tar', self.out / 'manifest.json'), summary)
        target = self.parent / 'restored'
        self.assertEqual(backup.restaurar(self.out / 'video-catalog.tar', target, summary), summary)
        loaded = VideoCatalog.load(target, 'videos.json')
        self.assertEqual(len(loaded.list()), 1)
        self.assertEqual(loaded.list()[0].synchronization_status, 'calibrated')
        self.assertFalse((target / 'original-video.mp4').exists())
        with self.assertRaisesRegex(backup.BackupError, 'ya existe'):
            backup.restaurar(self.out / 'video-catalog.tar', target, summary)

    def test_rejects_tar_traversal_without_creating_target(self):
        summary = self.make_backup()
        malicious = self.out / 'malicious.tar'
        with tarfile.open(malicious, 'w') as archive:
            item = tarfile.TarInfo('video-catalog/../../outside')
            item.size = 1
            import io
            archive.addfile(item, io.BytesIO(b'x'))
        target = self.parent / 'rejected'
        with self.assertRaises(backup.BackupError):
            backup.restaurar(malicious, target, summary)
        self.assertFalse(target.exists())
        self.assertFalse((self.parent / 'outside').exists())

    def test_video_state_does_not_initialize_while_restore_marker_exists(self):
        state = self.root / 'state'
        state.mkdir()
        (state / '.real-jaen-restore-incomplete').write_text('incomplete', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, 'Restauración incompleta'):
            VideoStateStore(state).initialize()
        self.assertFalse((state / 'video-service.sqlite').exists())


if __name__ == '__main__':
    unittest.main()
