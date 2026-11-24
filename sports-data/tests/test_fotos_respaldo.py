"""Backup-only tests for the current /state/player-photos store layout."""
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fotos import store
from fotos.players import FixedPlayers
from fotos_fixture import KNOWN, Bundle, photo
from scripts import fotos_respaldo as backup


class PlayerPhotosBackup(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='rja-player-photos-backup-')
        self.root = Path(self.temporary.name)
        self.state = self.root / 'state'
        self.out = self.root / 'out'
        self.state.mkdir()
        self.out.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def build_store(self):
        photo_store = store.PhotoStore(self.state / 'player-photos')
        collection = Bundle([photo(3007), photo(82474)])
        photo_store.import_bundle(collection.verify(), FixedPlayers(KNOWN))
        (self.state / 'player-photos' / 'tmp' / 'staging.partial').write_bytes(b'temporary')
        return photo_store

    def test_copies_index_and_webp_blobs_without_changing_source_or_including_tmp(self):
        photo_store = self.build_store()
        before = {p.relative_to(self.state).as_posix(): p.read_bytes() for p in self.state.rglob('*') if p.is_file()}

        summary = backup.respaldar(self.state, self.out)

        after = {p.relative_to(self.state).as_posix(): p.read_bytes() for p in self.state.rglob('*') if p.is_file()}
        self.assertEqual(after, before)
        self.assertEqual(summary['ruta'], '/state/player-photos')
        self.assertEqual(summary['blobs'], 2)
        self.assertTrue(summary['incluidas'])
        self.assertTrue((self.out / 'fotos.json').is_file())
        with tarfile.open(self.out / 'fotos.tar') as archive:
            names = set(archive.getnames())
            self.assertIn('player-photos/fotos.sqlite', names)
            self.assertEqual(sum(name.startswith('player-photos/blobs/') for name in names), 2)
            self.assertFalse(any('/tmp/' in f'/{name}/' or name.startswith('player-photos/tmp/') for name in names))
            for blob in photo_store.blobs.iterdir():
                member = next(name for name in names if name.endswith('/' + blob.name))
                self.assertEqual(archive.extractfile(member).read(), blob.read_bytes())

    def test_refuses_a_damaged_blob_without_leaving_an_archive(self):
        photo_store = self.build_store()
        blob = next(photo_store.blobs.iterdir())
        blob.write_bytes(blob.read_bytes() + b'corruption')

        with self.assertRaisesRegex(backup.BackupError, 'SHA-256'):
            backup.respaldar(self.state, self.out)

        self.assertFalse((self.out / 'fotos.tar').exists())
        self.assertFalse((self.out / '.fotos.tar.partial').exists())
        self.assertFalse((self.out / 'fotos.json').exists())

    def test_records_an_uninitialized_store_without_creating_one(self):
        summary = backup.respaldar(self.state, self.out)

        self.assertFalse(summary['incluidas'])
        self.assertFalse((self.state / 'player-photos').exists())
        self.assertFalse((self.out / 'fotos.tar').exists())


if __name__ == '__main__':
    unittest.main()
