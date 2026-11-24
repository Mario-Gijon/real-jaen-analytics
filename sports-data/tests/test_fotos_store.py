"""Photo store: classification, atomicity, idempotency, recovery, several sources, and isolation from the sporting
generations and the coordinator fingerprint."""
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

from fotos import bundle, serving, store
from fotos.players import FixedPlayers, PlayersFromQuery
from fotos_fixture import KNOWN, Bundle, photo, webp

ROOT = Path(__file__).resolve().parents[1]


class Fault(Exception):
    pass


def fail_at(point):
    def fault(where):
        if where == point:
            raise Fault(point)
    return fault


def tree(path: Path) -> dict:
    return {p.relative_to(path).as_posix(): p.read_bytes() for p in sorted(path.rglob('*')) if p.is_file()}


class StoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix='rj-fotos-')
        self.root = Path(self._tmp.name) / 'state' / 'fotos'
        self.known = FixedPlayers(KNOWN)
        self.store = store.PhotoStore(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def importar(self, b, *, photo_store=None, known=None):
        return (photo_store or self.store).import_bundle(b.verify(), known or self.known)

    def active(self, player, photo_store=None):
        return {p['source_id']: p['sha256'] for p in (photo_store or self.store).active_photos(player)}

    def blob_mtimes(self):
        return {p.name: p.stat().st_mtime_ns for p in self.store.blobs.iterdir()}

    def index_rows(self):
        with closing(sqlite3.connect(self.store.db_path)) as c, c:
            return {t: c.execute(f'SELECT * FROM {t} ORDER BY 1').fetchall()
                    for t in ('blob', 'photo', 'photo_import', 'photo_import_entry')}

    def snapshot(self):
        return tree(self.root / 'blobs'), self.index_rows()


class FirstImport(StoreTest):
    def test_first_import_stores_blobs_and_active_photos(self):
        b = Bundle([photo(3007), photo(82474)])
        result = self.importar(b)
        self.assertEqual(result['counts'], dict(new=2, changed=0, unchanged=0, invalid=0, unknown_player=0))
        self.assertFalse(result['reused'])
        self.assertEqual(result['blobs_written'], 2)
        for entry in b.manifest['entries']:
            stored = self.store.active_photos(int(entry['sofascore_id']))
            self.assertEqual(len(stored), 1)
            self.assertEqual(stored[0]['sha256'], entry['sha256'])
            self.assertEqual(stored[0]['path'].read_bytes(), b.files[entry['file']])
            self.assertEqual(stored[0]['provenance'], entry['provenance'])  # as declared, nothing added
            self.assertEqual((stored[0]['collection_id'], stored[0]['collection_version']), ('df11', '1'))
            self.assertEqual(stored[0]['request_id'], b.request_id)
        self.assertEqual(sorted(p.name for p in self.store.blobs.iterdir()), sorted(b.files))
        self.assertEqual(self.store.check(), [])
        self.assertEqual(self.store.orphans(), [])
        self.assertEqual(list(self.store.tmp.iterdir()), [])

    def test_default_root_is_player_photos_and_configurable(self):
        with mock.patch.dict('os.environ', clear=False) as environ:
            environ.pop('SPORTS_DATA_PHOTOS_DIR', None)
            self.assertEqual(store.default_root(), Path('/state/player-photos'))
        with mock.patch.dict('os.environ', {'SPORTS_DATA_PHOTOS_DIR': str(self.root / 'otra')}):
            self.assertEqual(store.default_root(), self.root / 'otra')
            self.assertTrue(store.PhotoStore().db_path.is_file())

    def test_unknown_player_is_excluded_and_recorded(self):
        b = Bundle([photo(3007), photo(999999)])
        result = self.importar(b)
        self.assertEqual((result['counts']['new'], result['counts']['unknown_player']), (1, 1))
        self.assertEqual(self.store.active_photos(999999), [])
        record = self.store.import_record(b.request_id)
        self.assertEqual([(e['player_id'], e['outcome'], e['reason']) for e in record['entries']],
                         [(3007, 'new', None), (999999, 'unknown_player', 'Sports Data no conoce este jugador')])
        self.assertEqual(len(list(self.store.blobs.iterdir())), 1)  # its bytes are not kept either

    def test_players_from_the_public_read_model(self):
        calls = []

        def consult(path):
            calls.append(path)
            return (200 if path == '/api/v1/players/3007' else 404), {}
        known = PlayersFromQuery(consult)
        self.assertIn(3007, known)
        self.assertNotIn(82474, known)
        self.assertNotIn('3007', known)
        self.assertIn(3007, known)
        self.assertEqual(calls, ['/api/v1/players/3007', '/api/v1/players/82474'])
        result = self.importar(Bundle([photo(3007), photo(82474)]), known=known)
        self.assertEqual((result['counts']['new'], result['counts']['unknown_player']), (1, 1))


class Updates(StoreTest):
    def setUp(self):
        super().setUp()
        self.first = Bundle([photo(3007), photo(82474)])
        self.importar(self.first)

    def test_repeated_request_changes_nothing(self):
        before, mtimes = self.snapshot(), self.blob_mtimes()
        for _ in range(2):
            result = self.store.import_bundle(self.first.verify(), self.known)
            self.assertTrue(result['reused'])
            self.assertEqual(result['counts']['new'], 2)
        self.assertEqual((self.snapshot(), self.blob_mtimes()), (before, mtimes))

    def test_same_request_with_other_content_is_a_conflict(self):
        other = Bundle([photo(3007, webp(payload=b'otra'))], request_id=self.first.request_id)
        before = self.snapshot()
        with self.assertRaises(store.ImportConflict):
            self.importar(other)
        self.assertEqual(self.snapshot(), before)

    def test_new_request_without_changes(self):
        mtimes = self.blob_mtimes()
        again = Bundle([photo(3007), photo(82474)], version='2')
        result = self.importar(again)
        self.assertEqual(result['counts'], dict(new=0, changed=0, unchanged=2, invalid=0, unknown_player=0))
        self.assertEqual(result['blobs_written'], 0)
        self.assertEqual(self.blob_mtimes(), mtimes)  # identical bytes are reused, not rewritten
        self.assertEqual(self.store.active_photos(3007)[0]['request_id'], self.first.request_id)

    def test_import_without_files_using_the_inventory(self):
        inventory = self.store.inventory('df11')
        specs = [photo(3007, transfer='omitted_unchanged'), photo(82474, transfer='omitted_unchanged')]
        b = Bundle(specs, inventory=inventory, version='2')
        self.assertEqual(b.files, {})
        result = self.importar(b)
        self.assertEqual(result['counts']['unchanged'], 2)
        self.assertEqual(result['blobs_written'], 0)

    def test_new_photo_is_added_and_others_kept(self):
        result = self.importar(Bundle([photo(3007), photo(82474), photo(83687)], version='2'))
        self.assertEqual((result['counts']['new'], result['counts']['unchanged']), (1, 2))
        self.assertEqual(len(self.store.active_photos(83687)), 1)

    def test_replacement_supersedes_and_keeps_history(self):
        old = self.active(3007)['df11']
        replacement = webp(payload=b'nueva equipacion')
        b = Bundle([photo(3007, replacement), photo(82474)], version='2')
        result = self.importar(b)
        self.assertEqual((result['counts']['changed'], result['counts']['unchanged']), (1, 1))
        self.assertEqual(self.active(3007), {'df11': bundle.sha(replacement)})
        with closing(sqlite3.connect(self.store.db_path)) as c, c:
            rows = c.execute('SELECT sha256, status, superseded_by_request_id, collection_version FROM photo '
                             'WHERE player_id = 3007 ORDER BY id').fetchall()
        self.assertEqual(rows, [(old, 'superseded', b.request_id, '1'), (bundle.sha(replacement), 'active', None, '2')])
        self.assertTrue(self.store.blob_path(old, 'image/webp').is_file())  # old content stays for history/rollback

    def test_missing_photo_in_a_new_version_is_not_removed(self):
        result = self.importar(Bundle([photo(3007)], version='2'))
        self.assertEqual(result['counts']['unchanged'], 1)
        self.assertEqual(len(self.store.active_photos(82474)), 1)

    def test_rejected_photo_never_replaces_a_valid_one(self):
        before = self.active(3007)
        b = Bundle([photo(3007, transfer='rejected', reason='recorte no fiable'), photo(82474)], version='2')
        result = self.importar(b)
        self.assertEqual(result['counts']['invalid'], 1)
        self.assertEqual(result['collection_result'], 'partial')
        self.assertEqual(self.active(3007), before)
        self.assertEqual(self.store.import_record(b.request_id)['entries'][0]['reason'], 'recorte no fiable')

    def test_omitted_photo_that_is_not_the_active_one_is_invalid(self):
        entry, _ = photo(3007, webp(payload=b'otra'), transfer='omitted_unchanged')
        before = self.active(3007)
        result = self.importar(Bundle([(entry, None)], version='2'))  # no inventory: the store still checks it
        self.assertEqual(result['counts']['invalid'], 1)
        self.assertEqual(self.active(3007), before)

    def test_two_sources_for_one_player(self):
        other = webp(payload=b'foto de otra fuente')
        result = self.importar(Bundle([photo(3007, other)], source='web-club'))
        self.assertEqual(result['counts']['new'], 1)  # new for that source, the df11 photo is untouched
        self.assertEqual(self.active(3007), {'df11': self.first.manifest['entries'][0]['sha256'],
                                             'web-club': bundle.sha(other)})
        self.assertEqual(self.store.inventory('web-club')['photos'], [{'sofascore_id': '3007',
                                                                        'sha256': bundle.sha(other)}])

    def test_same_content_from_two_sources_shares_one_blob(self):
        data = self.first.files[self.first.manifest['entries'][0]['file']]
        before = set(p.name for p in self.store.blobs.iterdir())
        self.importar(Bundle([photo(3007, data)], source='otra-fuente'))
        self.assertEqual(set(p.name for p in self.store.blobs.iterdir()), before)

    def test_damaged_blob_is_healed_by_a_bundle_that_carries_it(self):
        path = self.store.active_photos(3007)[0]['path']
        path.write_bytes(b'damaged')
        self.assertEqual(len(self.store.check()), 1)
        self.importar(Bundle([photo(3007), photo(82474)], version='2'))
        self.assertEqual(self.store.check(), [])


class Failures(StoreTest):
    def setUp(self):
        super().setUp()
        self.first = Bundle([photo(3007), photo(82474)])
        self.importar(self.first)
        self.before = self.snapshot()
        self.update = Bundle([photo(3007, webp(payload=b'v2')), photo(83687)], version='2')

    def failing_store(self, point):
        return store.PhotoStore(self.root, fault=fail_at(point))

    def test_failure_while_writing_blobs_keeps_everything(self):
        with self.assertRaises(Fault):
            self.importar(self.update, photo_store=self.failing_store('blob_written'))
        self.assertEqual(self.index_rows(), self.before[1])
        self.assertEqual(self.active(3007), {'df11': self.first.manifest['entries'][0]['sha256']})
        self.assertEqual(list(self.store.tmp.iterdir()), [])
        self.assertEqual(self.store.check(), [])

    def test_write_error_of_the_file_system(self):
        with mock.patch.object(store.os, 'replace', side_effect=OSError(28, 'No space left on device')):
            with self.assertRaises(OSError):
                self.importar(self.update)
        self.assertEqual(self.snapshot(), self.before)
        self.assertEqual(list(self.store.tmp.iterdir()), [])

    def test_failure_while_updating_the_index_rolls_back(self):
        for point in ('before_index', 'before_commit'):
            with self.subTest(point):
                with self.assertRaises(Fault):
                    self.importar(self.update, photo_store=self.failing_store(point))
                self.assertEqual(self.index_rows(), self.before[1])
                self.assertEqual(self.store.check(), [])
        # The blobs of the failed attempt are orphans, never referenced; the retry reuses them and succeeds.
        self.assertEqual(len(self.store.orphans()), 2)
        result = self.importar(self.update)
        self.assertEqual((result['counts']['changed'], result['counts']['new']), (1, 1))
        self.assertEqual(self.store.orphans(), [])

    def test_index_constraint_failure_rolls_back(self):
        broken = Bundle([photo(3007, webp(payload=b'v3'))], version='3')
        with mock.patch.object(self.store, '_clock', return_value=None):  # NOT NULL imported_at
            with self.assertRaises(sqlite3.IntegrityError):
                self.importar(broken)
        self.assertEqual(self.index_rows(), self.before[1])

    def test_interrupted_process_is_recovered_on_reopen(self):
        """A real crash: the process dies inside the index transaction after writing blobs and a staging file."""
        body, descriptor = self.update.body()
        work = Path(self._tmp.name)
        (work / 'bundle.json').write_bytes(body)
        script = textwrap.dedent(f'''
            import json, os, sys
            from pathlib import Path
            sys.path[:0] = [{str(ROOT)!r}, {str(Path(__file__).parent)!r}]
            from fotos import bundle, store
            from fotos.players import FixedPlayers
            from fotos_fixture import APPROVED
            root = Path({str(self.root)!r})
            def crash(point):
                if point == 'before_commit':
                    (root / 'tmp' / 'leftover.partial').write_bytes(b'half written')
                    os._exit(17)
            body = Path({str(work / 'bundle.json')!r}).read_bytes()
            verified = bundle.verify(body, {descriptor!r}, context=({self.update.job_id!r}, {self.update.request_id!r}), approved=APPROVED)
            store.PhotoStore(root, fault=crash).import_bundle(verified, FixedPlayers({KNOWN!r}))
        ''')
        completed = subprocess.run([sys.executable, '-B', '-c', script], capture_output=True, timeout=60)
        self.assertEqual(completed.returncode, 17, completed.stderr.decode(errors='replace'))
        reopened = store.PhotoStore(self.root)
        self.assertEqual(list(reopened.tmp.iterdir()), [])  # staging cleaned
        self.assertEqual(self.index_rows(), self.before[1])  # SQLite rolled the transaction back
        self.assertIsNone(reopened.import_record(self.update.request_id))
        self.assertEqual(reopened.check(), [])
        result = reopened.import_bundle(self.update.verify(), self.known)  # the same request can be retried
        self.assertFalse(result['reused'])
        self.assertEqual(reopened.check(), [])


class Persistence(StoreTest):
    def test_reopening_keeps_photos_and_idempotency(self):
        b = Bundle([photo(3007), photo(82474)])
        self.importar(b)
        snapshot = self.snapshot()
        reopened = store.PhotoStore(self.root)
        self.assertEqual(self.active(3007, reopened), self.active(3007))
        self.assertTrue(reopened.import_bundle(b.verify(), self.known)['reused'])
        self.assertEqual(self.snapshot(), snapshot)

    def test_unsupported_index_version_is_refused(self):
        with closing(sqlite3.connect(self.store.db_path)) as c, c:
            c.execute("UPDATE meta SET value = '99' WHERE key = 'schema_version'")
        with self.assertRaises(store.StoreError):
            store.PhotoStore(self.root)

    def test_store_lives_outside_the_sporting_database(self):
        self.importar(Bundle([photo(3007)]))
        with closing(sqlite3.connect(self.store.db_path)) as c, c:
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertEqual(tables, {'meta', 'blob', 'photo', 'photo_import', 'photo_import_entry'})
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['blobs', 'fotos.sqlite', 'tmp'])

    def test_read_only_inventory_and_idempotency_receipt(self):
        b = Bundle([photo(3007)])
        self.importar(b)
        reader = serving.PhotoReader(self.root)
        self.assertEqual(reader.inventory('df11')['photos'], [{'sofascore_id': '3007',
                                                                'sha256': b.manifest['entries'][0]['sha256']}])
        saved = reader.imported_bundle(b.request_id)
        self.assertEqual(saved[0], bundle.sha(b.body()[0]))
        self.assertEqual(saved[1]['request_id'], b.request_id)


class ReadWithoutStore(unittest.TestCase):
    def test_missing_catalog_reads_as_empty_without_creating_it(self):
        with tempfile.TemporaryDirectory(prefix='rj-fotos-read-') as directory:
            root = Path(directory) / 'player-photos'
            reader = serving.PhotoReader(root)
            self.assertEqual(reader.inventory('df11')['photos'], [])
            self.assertIsNone(reader.imported_bundle('not-imported'))
            self.assertEqual(reader.urls([3007]), {})
            self.assertFalse(root.exists())

    def test_symlinked_blob_directory_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix='rj-fotos-links-') as directory:
            root = Path(directory) / 'catalog'
            external = Path(directory) / 'external'
            external.mkdir()
            photo_store = store.PhotoStore(root)
            verified = Bundle([photo(3007)]).verify()
            photo_store.import_bundle(verified, FixedPlayers([3007]))
            digest = verified.entries[0].sha256
            (root / 'blobs').rename(root / 'blobs-real')
            (root / 'blobs').symlink_to(external, target_is_directory=True)
            with self.assertRaises(serving.PhotoStoreFault):
                serving.PhotoReader(root).serve(3007, digest)


class Isolation(unittest.TestCase):
    """The photo code must not take part in the coordinator's rule fingerprint nor depend on persistencia/."""

    def test_fotos_is_not_in_the_coordinator_fingerprint(self):
        from persistencia import carga
        files = carga.archivos_de_codigo()
        self.assertFalse([f for f in files if f.startswith('fotos/') or 'fotos' in Path(f).parts])
        self.assertTrue((ROOT / 'fotos' / 'store.py').is_file())

    def test_changing_photo_code_does_not_change_the_fingerprint(self):
        from persistencia import carga
        with tempfile.TemporaryDirectory(prefix='rj-fotos-fingerprint-') as directory:
            root = Path(directory)
            for relative in carga.archivos_de_codigo():
                (root / relative).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, root / relative)
            shutil.copytree(ROOT / 'fotos', root / 'fotos', ignore=shutil.ignore_patterns('__pycache__'))
            with mock.patch.object(carga, 'RAIZ', root):
                original = carga.referencia_del_codigo()['sha256_agregada']
                (root / 'fotos' / 'bundle.py').write_text('# changed photo contract\n', encoding='utf-8')
                (root / 'fotos' / 'nuevo.py').write_text('x = 1\n', encoding='utf-8')
                self.assertEqual(carga.referencia_del_codigo()['sha256_agregada'], original)

    def test_photo_modules_do_not_import_the_sporting_persistence(self):
        import ast
        for path in (ROOT / 'fotos').glob('*.py'):
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if isinstance(node, ast.Import):
                    names = [alias.name.split('.')[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [] if node.level else [node.module.split('.')[0]]  # relative: inside fotos/
                else:
                    continue
                self.assertNotIn('persistencia', names, path.name)
            self.assertNotIn('real-jaen.sqlite', path.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
