"""Synthetic v2 backup validation and safe extraction; no Docker daemon or real backup data."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts import restauracion_backup as restore


class BackupRestore(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='rja-backup-restore-')
        self.root = Path(self.temp.name) / 'backup'
        self.root.mkdir()
        self.db = self.root / 'real-jaen.sqlite'
        with sqlite3.connect(self.db) as connection:
            connection.execute('CREATE TABLE alembic_version(version_num TEXT NOT NULL)')
            connection.execute('INSERT INTO alembic_version VALUES (?)', ('rev-test',))
        connection.close()
        self.service_db = Path(self.temp.name) / 'service.sqlite'
        with sqlite3.connect(self.service_db) as connection:
            connection.execute('CREATE TABLE sample(value TEXT)')
            connection.execute('INSERT INTO sample VALUES (?)', ('fixture',))
        connection.close()
        self._archive('actualizaciones.tar', [
            ('real-jaen.sqlite.actualizaciones/', 'dir'),
            ('real-jaen.sqlite.actualizaciones/jobs.sqlite', 'file'),
            ('real-jaen.sqlite.actualizaciones/control/', 'dir'),
            ('real-jaen.sqlite.actualizaciones/control/jobs.sqlite', 'file'),
        ], {'real-jaen.sqlite.actualizaciones/jobs.sqlite': self.service_db.read_bytes(),
            'real-jaen.sqlite.actualizaciones/control/jobs.sqlite': self.service_db.read_bytes()})
        self._archive('acquisition-jobs.tar', [('.', 'dir'), ('./jobs.sqlite', 'file')],
                      {'./jobs.sqlite': self.service_db.read_bytes()})
        self._archive('video-service-state.tar', [('.', 'dir'), ('./video-service.sqlite', 'file')],
                      {'./video-service.sqlite': self.service_db.read_bytes()})
        self._archive('video-catalog.tar', [('video-catalog/', 'dir'), ('video-catalog/videos.json', 'file')])
        (self.root / 'video-catalog.tar').write_bytes(self._tar_bytes(
            [('video-catalog/', 'dir'), ('video-catalog/videos.json', 'file')],
            {'video-catalog/videos.json': b'{"schema_version":1,"videos":[]}'}))
        self.manifest = {
            'formato_respaldo': 2, 'revision': 'rev-test', 'huella': 'a' * 64,
            'integridad': 'ok', 'generacion': 'generation-test',
            'fotos': {'formato': 1, 'incluidas': False, 'ruta': '/state/player-photos'},
            'catalogo_video': {'formato': 1, 'incluida': True, 'directorio': '/app/data',
                               'manifiesto': 'videos.json', 'calibraciones': 0, 'archivos': 1,
                               'huella_archivos': 'b' * 64},
        }
        self.copied = {'ruta': '/state/copias/real-jaen.sqlite.test.sqlite',
                       'sha256_archivo': restore.sha256(self.db), 'revision': 'rev-test', 'huella': 'a' * 64}
        (self.root / 'copiar.json').write_text(json.dumps(self.copied), encoding='utf-8')
        self._write_manifest_and_sums()

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _tar_bytes(entries, contents=None):
        import io
        result = io.BytesIO()
        with tarfile.open(fileobj=result, mode='w') as archive:
            for name, kind in entries:
                if kind == 'dir':
                    item = tarfile.TarInfo(name)
                    item.type = tarfile.DIRTYPE
                    item.mode = 0o755
                    archive.addfile(item)
                elif kind == 'symlink':
                    item = tarfile.TarInfo(name)
                    item.type = tarfile.SYMTYPE
                    item.linkname = '/tmp/escape'
                    archive.addfile(item)
                else:
                    body = (contents or {}).get(name, b'test data')
                    item = tarfile.TarInfo(name)
                    item.size = len(body)
                    archive.addfile(item, io.BytesIO(body))
        return result.getvalue()

    def _archive(self, name, entries, contents=None):
        (self.root / name).write_bytes(self._tar_bytes(entries, contents))

    def _write_manifest_and_sums(self):
        (self.root / 'manifest.json').write_text(json.dumps(self.manifest), encoding='utf-8')
        names = sorted(path.name for path in self.root.iterdir() if path.is_file() and path.name != 'SHA256SUMS')
        (self.root / 'SHA256SUMS').write_text(''.join(
            f'{restore.sha256(self.root / name)}  ./{name}\n' for name in names), encoding='ascii')

    def test_accepts_complete_synthetic_v2_backup(self):
        self.assertEqual(restore.validate(self.root, sports_sqlite_module=False)['generacion'], 'generation-test')

    def test_rejects_changed_artifact_and_missing_artifact(self):
        (self.root / 'real-jaen.sqlite').write_bytes(b'changed')
        with self.assertRaisesRegex(restore.RestoreError, 'SHA-256'):
            restore.validate(self.root, sports_sqlite_module=False)
        (self.root / 'video-catalog.tar').unlink()
        with self.assertRaises(restore.RestoreError):
            restore.validate(self.root, sports_sqlite_module=False)

    def test_rejects_unknown_backup_version_even_with_valid_checksums(self):
        self.manifest['formato_respaldo'] = 99
        self._write_manifest_and_sums()
        with self.assertRaisesRegex(restore.RestoreError, 'formato_respaldo=2'):
            restore.validate(self.root, sports_sqlite_module=False)

    def test_rejects_traversal_and_special_archive_entries(self):
        self._archive('acquisition-jobs.tar', [('.', 'dir'), ('./jobs.sqlite', 'file'), ('../outside', 'file')])
        self._write_manifest_and_sums()
        with self.assertRaisesRegex(restore.RestoreError, 'ruta insegura'):
            restore.validate(self.root, sports_sqlite_module=False)

    def test_rejects_reserved_video_restore_marker_in_archive(self):
        self._archive('video-service-state.tar', [('.', 'dir'), ('./video-service.sqlite', 'file'),
                                                  ('./.real-jaen-restore-incomplete', 'file')],
                      {'./video-service.sqlite': self.service_db.read_bytes()})
        self._write_manifest_and_sums()
        with self.assertRaisesRegex(restore.RestoreError, 'marcador reservado'):
            restore.validate(self.root, sports_sqlite_module=False)

    def test_extract_refuses_existing_destination_and_does_not_follow_links(self):
        archive = self.root / 'safe.tar'
        archive.write_bytes(self._tar_bytes([('root/', 'dir'), ('root/file', 'file')]))
        destination = self.root / 'target'
        restore.extract(archive, destination, prefix='root', strip_prefix=True)
        self.assertEqual((destination / 'file').read_bytes(), b'test data')
        with self.assertRaisesRegex(restore.RestoreError, 'no está vacío'):
            restore.extract(archive, destination, prefix='root', strip_prefix=True)
        malicious = self.root / 'bad.tar'
        malicious.write_bytes(self._tar_bytes([('root/', 'dir'), ('root/link', 'symlink')]))
        with self.assertRaisesRegex(restore.RestoreError, 'enlace o archivo especial'):
            restore.extract(malicious, self.root / 'bad-target', prefix='root', strip_prefix=True)


if __name__ == '__main__':
    unittest.main()
