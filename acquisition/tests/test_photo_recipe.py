"""Recipe player-photos-local-v1 and its job process: synthetic collections in temporary folders. No browser, profile,
network, FFmpeg or megapack. Runs in the acquisition test image and on any host with fotos/ on the path."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import uuid

from acquisition.photo_protocol import bundle
from acquisition import photo_job, photo_recipe


def webp(width=215, height=268, payload=b''):
    frame = b'\x10\x95\x00' + b'\x9d\x01\x2a' + width.to_bytes(2, 'little') + height.to_bytes(2, 'little') + payload
    if len(frame) % 2:
        frame += b'\x00'
    chunk = b'VP8 ' + len(frame).to_bytes(4, 'little') + frame
    return b'RIFF' + (4 + len(chunk)).to_bytes(4, 'little') + b'WEBP' + chunk


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_collection(path: Path, photos: dict, *, source='df11', version='1', tamper=None):
    """A Player Photos collection: manifest.json + photos/<id>.webp. `tamper(manifest)` edits the manifest."""
    (path / 'photos').mkdir(parents=True)
    entries = []
    for sid, data in sorted(photos.items(), key=lambda kv: int(kv[0])):
        (path / 'photos' / f'{sid}.webp').write_bytes(data)
        entries.append(dict(sofascore_id=sid, file=f'photos/{sid}.webp', sha256=sha(data), bytes=len(data),
                            format='webp', media_type='image/webp', width=215, height=268,
                            provenance=dict(fm_id=str(67000000 + int(sid)), source_file=f'{sid}.png')))
    manifest = dict(format='rja-player-photo-collection', format_version=1,
                    collection=dict(id=source, version=version), source=dict(id=source, label=None),
                    processing=dict(tool='real-jaen-player-photos', processing_ids=['df11-opaque-crop-v1']),
                    photo_count=len(entries), photos=entries)
    if tamper:
        tamper(manifest)
    (path / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return manifest


def spec():
    return dict(job_id=str(uuid.uuid4()), request_id=str(uuid.uuid4()), operation_id=str(uuid.uuid4()),
                recipe_id=photo_recipe.PHOTO_RECIPE)


def approved():
    rules = photo_recipe.photo_producer_rules()
    return {rules: dict(rules_sha256=rules, contract='photos-v1', recipe_id=photo_recipe.PHOTO_RECIPE,
                        productor='test', uso='test')}


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix='rj-photo-recipe-')
        self.root = Path(self._tmp.name) / 'collections'
        self.photos = {str(i): webp(payload=f'p{i}'.encode()) for i in (3007, 82474, 83687)}
        self.collection = self.root / 'df11' / '1'
        write_collection(self.collection, self.photos)
        self.empty = bundle.inventory('df11', {})

    def tearDown(self):
        self._tmp.cleanup()

    def build(self, inventory=None, collection=None):
        job = spec()
        body, descriptor, summary = photo_recipe.build(
            photo_recipe.read_collection(collection or self.collection), inventory or self.empty, job,
            started_at='2026-11-18T10:00:00+00:00')
        verified = bundle.verify(body, descriptor, context=(job['job_id'], job['request_id']), approved=approved(),
                                 inventory=inventory or self.empty)
        return verified, summary


class Building(Base):
    def test_first_bundle_includes_every_photo_with_provenance(self):
        verified, summary = self.build()
        self.assertEqual(summary['counts'], dict(included=3, omitted_unchanged=0, rejected=0))
        self.assertEqual(summary['result'], 'complete')
        self.assertEqual({e.player_id for e in verified.entries}, {3007, 82474, 83687})
        self.assertEqual(set(verified.blobs), {sha(d) for d in self.photos.values()})
        m = verified.manifest
        self.assertEqual((m['recipe_id'], m['rules_sha256']), (photo_recipe.PHOTO_RECIPE,
                                                               photo_recipe.photo_producer_rules()))
        self.assertEqual(m['collection']['manifest_sha256'], sha((self.collection / 'manifest.json').read_bytes()))
        self.assertEqual(m['entries'][0]['provenance'], dict(fm_id='67003007', source_file='3007.png'))

    def test_unchanged_photos_travel_without_bytes(self):
        inventory = bundle.inventory('df11', {int(k): sha(v) for k, v in self.photos.items()})
        verified, summary = self.build(inventory)
        self.assertEqual(summary['counts'], dict(included=0, omitted_unchanged=3, rejected=0))
        self.assertEqual((verified.blobs, summary['bytes_included']), ({}, 0))

    def test_only_new_or_changed_photos_are_included(self):
        inventory = bundle.inventory('df11', {3007: sha(self.photos['3007']), 82474: 'a' * 64})
        verified, summary = self.build(inventory)
        transfers = {e.player_id: e.transfer for e in verified.entries}
        self.assertEqual(transfers, {3007: 'omitted_unchanged', 82474: 'included', 83687: 'included'})

    def test_unusable_files_are_rejected_with_their_reason(self):
        cases = {
            'missing': (lambda p: (p / 'photos' / '82474.webp').unlink(), 'no existe'),
            'other bytes': (lambda p: (p / 'photos' / '82474.webp').write_bytes(webp(payload=b'otra')),
                            'no coincide'),
            'corrupt': (lambda p: (p / 'photos' / '82474.webp').write_bytes(self.photos['82474'][:-1]), 'no coincide'),
        }
        for name, (damage, reason) in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'c'
                write_collection(path, self.photos)
                damage(path)
                verified, summary = self.build(collection=path)
                entry = next(e for e in verified.entries if e.player_id == 82474)
                self.assertEqual(entry.transfer, 'rejected')
                self.assertIn(reason, entry.reason)
                self.assertEqual((summary['result'], summary['counts']['included']), ('partial', 2))
                self.assertEqual(verified.manifest['result']['issues'][0]['sofascore_id'], '82474')

    def test_declared_values_that_do_not_match_the_file(self):
        for field, value, reason in (('width', 216, 'dimensiones'), ('bytes', 1, 'tamaño'),
                                     ('media_type', 'image/png', 'formato')):
            with self.subTest(field), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'c'
                write_collection(path, self.photos, tamper=lambda m: m['photos'][0].update({field: value}))
                verified, _ = self.build(collection=path)
                self.assertIn(reason, verified.entries[0].reason)

    def test_corrupt_image_with_matching_declared_hash(self):
        broken = b'RIFF' + b'\x00' * 40
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'c'
            write_collection(path, dict(self.photos, **{'3007': broken}))
            verified, _ = self.build(collection=path)
            self.assertIn('imagen no válida', verified.entries[0].reason)

    def test_invalid_manifests_fail_the_whole_collection(self):
        cases = {
            'duplicate id': lambda m: m['photos'].append(dict(m['photos'][0])) or m.update(photo_count=4),
            'unsafe path': lambda m: m['photos'][0].update(file='../../etc/passwd'),
            'nested path': lambda m: m['photos'][0].update(file='photos/sub/3007.webp'),
            'format': lambda m: m.update(format_version=2),
            'count': lambda m: m.update(photo_count=9),
            'bad id': lambda m: m['photos'][0].update(sofascore_id='0123'),
            'bad provenance': lambda m: m['photos'][0].update(provenance='not an object'),
            'extra field': lambda m: m.update(extra=1),
        }
        for name, tamper in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'c'
                write_collection(path, self.photos, tamper=tamper)
                with self.assertRaises(photo_recipe.PhotoRecipeError) as caught:
                    photo_recipe.read_collection(path)
                self.assertEqual(caught.exception.code, 'collection_invalid')

    def test_duplicate_json_keys_are_rejected(self):
        manifest = self.collection / 'manifest.json'
        manifest.write_text(manifest.read_text(encoding='utf-8').replace('{', '{"photo_count": 3,', 1),
                            encoding='utf-8')
        with self.assertRaises(photo_recipe.PhotoRecipeError) as caught:
            photo_recipe.read_collection(self.collection)
        self.assertEqual(caught.exception.code, 'collection_invalid')

    def test_source_mismatch_and_invalid_inventory(self):
        with self.assertRaises(photo_recipe.PhotoRecipeError) as caught:
            self.build(bundle.inventory('web-club', {}))
        self.assertEqual(caught.exception.code, 'source_mismatch')
        tampered = dict(self.empty, sha256='0' * 64)
        with self.assertRaises(photo_recipe.PhotoRecipeError) as caught:
            photo_recipe.build(photo_recipe.read_collection(self.collection), tampered, spec(), started_at='x')
        self.assertEqual(caught.exception.code, 'inventory_invalid')

    def test_bundle_size_limit(self):
        with mock.patch.object(bundle, 'MAX_TOTAL', 10):
            with self.assertRaises(photo_recipe.PhotoRecipeError) as caught:
                self.build()
        self.assertEqual(caught.exception.code, 'bundle_too_large')

    def test_wire_size_limit(self):
        with mock.patch.object(bundle, 'MAX_WIRE', 10):
            with self.assertRaises(photo_recipe.PhotoRecipeError) as caught:
                self.build()
        self.assertEqual(caught.exception.code, 'bundle_too_large')

    def test_collection_is_never_modified(self):
        before = {p: p.read_bytes() for p in self.collection.rglob('*') if p.is_file()}
        self.build()
        self.build(bundle.inventory('df11', {3007: sha(self.photos['3007'])}))
        self.assertEqual({p: p.read_bytes() for p in self.collection.rglob('*') if p.is_file()}, before)


class Locating(Base):
    def test_configured_collection(self):
        self.assertEqual(photo_recipe.locate(self.root, 'df11/1'), self.collection.resolve())

    def test_not_configured(self):
        for value in ('', None):
            with self.assertRaises(photo_recipe.PhotoRecipeError) as caught:
                photo_recipe.locate(self.root, value)
            self.assertEqual(caught.exception.code, 'collection_not_configured')

    def test_missing_collection_or_root(self):
        for root, selector in ((self.root, 'df11/2'), (self.root / 'nada', 'df11/1')):
            with self.subTest(selector=selector), self.assertRaises(photo_recipe.PhotoRecipeError) as caught:
                photo_recipe.locate(root, selector)
            self.assertEqual(caught.exception.code, 'collection_unavailable')
        (self.collection / 'manifest.json').unlink()
        with self.assertRaises(photo_recipe.PhotoRecipeError) as caught:
            photo_recipe.read_collection(self.collection)
        self.assertEqual(caught.exception.code, 'collection_unavailable')

    def test_selectors_cannot_escape_the_collections_root(self):
        for value in ('../df11', 'df11/../../x', '/etc', 'C:/x', 'df11\\1', 'df11//1', '.hidden', 'df11/.',
                      'a/b/c/d/e'):
            with self.subTest(value=value), self.assertRaises(photo_recipe.PhotoRecipeError) as caught:
                photo_recipe.locate(self.root, value)
            self.assertEqual(caught.exception.code, 'collection_invalid')

    @unittest.skipIf(os.name == 'nt', 'symbolic links need privileges on Windows')
    def test_symlink_out_of_the_root(self):
        outside = Path(self._tmp.name) / 'outside'
        write_collection(outside, self.photos)
        (self.root / 'escape').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(photo_recipe.PhotoRecipeError) as caught:
            photo_recipe.locate(self.root, 'escape')
        self.assertEqual(caught.exception.code, 'collection_invalid')


class JobProcess(Base):
    def job_folder(self, inventory=None):
        folder = Path(self._tmp.name) / 'work' / str(uuid.uuid4())
        folder.mkdir(parents=True)
        job = spec()
        (folder / 'job.json').write_text(json.dumps(job), encoding='utf-8')
        (folder / 'inventory.json').write_bytes(bundle.encode(inventory or self.empty))
        return folder, job

    def environ(self, selector='df11/1'):
        return {'ACQ_COLLECTIONS_ROOT': str(self.root), 'ACQ_PHOTO_COLLECTION': selector}

    def test_seals_a_read_only_bundle_and_reports_its_descriptor(self):
        folder, job = self.job_folder()
        self.assertEqual(photo_job.run(folder, self.environ()), 0)
        body = (folder / 'bundle.json').read_bytes()
        report = json.loads((folder / 'report.json').read_bytes())
        self.assertEqual(report['descriptor'], dict(bytes=len(body), sha256=sha(body)))
        self.assertFalse((folder / 'bundle.json').stat().st_mode & 0o222)  # what Jobs.run requires
        self.assertEqual((report['error'], report['error_code'], report['summary']['counts']['included']),
                         (None, None, 3))
        bundle.verify(body, report['descriptor'], context=(job['job_id'], job['request_id']), approved=approved(),
                      inventory=self.empty)

    def test_failures_report_a_stable_code_and_seal_nothing(self):
        for selector, code in (('', 'collection_not_configured'), ('df11/9', 'collection_unavailable'),
                               ('../x', 'collection_invalid')):
            with self.subTest(selector=selector):
                folder, _ = self.job_folder()
                self.assertEqual(photo_job.run(folder, self.environ(selector)), 1)
                report = json.loads((folder / 'report.json').read_bytes())
                self.assertEqual((report['error_code'], report['descriptor']), (code, None))
                self.assertFalse((folder / 'bundle.json').exists())

    def test_cancellation_before_sealing(self):
        folder, _ = self.job_folder()
        (folder / 'cancel').touch()
        self.assertEqual(photo_job.run(folder, self.environ()), 1)
        self.assertFalse((folder / 'bundle.json').exists())
        self.assertTrue(json.loads((folder / 'report.json').read_bytes())['cancelled'])

    def test_runs_without_external_processes(self):
        folder, _ = self.job_folder()
        with mock.patch.object(subprocess, 'Popen', side_effect=AssertionError('no external process')), \
                mock.patch.object(subprocess, 'run', side_effect=AssertionError('no external process')):
            self.assertEqual(photo_job.run(folder, self.environ()), 0)

    def test_fresh_job_process_loads_no_browser_profile_or_sdk(self):
        # A fresh interpreter, like the job process: other tests of this suite may already have imported those modules.
        folder, _ = self.job_folder()
        probe = ('import json, sys; from acquisition import photo_job; '
                 f'code = photo_job.run({str(folder)!r}, {self.environ()!r}); '
                 'print(json.dumps([code, sorted(m for m in sys.modules if m.startswith(("acquisition.", "sofascore", '
                 '"patchright", "playwright", "fotos", "persistencia", "queries")))]))')
        completed = subprocess.run([sys.executable, '-B', '-c', probe], capture_output=True, text=True, timeout=60,
                                   env=dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path)))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        code, loaded = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertEqual(code, 0)
        self.assertEqual(loaded, ['acquisition.photo_job', 'acquisition.photo_protocol',
                                  'acquisition.photo_protocol.bundle', 'acquisition.photo_protocol.images',
                                  'acquisition.photo_recipe'])

    def test_manual_cli_requires_confirmation_without_loading_the_profile_or_browser(self):
        probe = ('import sys; from acquisition import admin; '
                 'from acquisition import photo_import; '
                 'result = admin.main(["import-photos", "--collection", "df11/1"]); '
                 'assert result == 2; '
                 'assert not any(name.startswith(("fotos", "persistencia", "queries")) for name in sys.modules); '
                 'assert "acquisition.profile" not in sys.modules; '
                 'assert "acquisition.browser" not in sys.modules')
        completed = subprocess.run([sys.executable, '-B', '-c', probe], capture_output=True, text=True, timeout=30,
                                   env=dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path)))
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


class ProducerFingerprint(unittest.TestCase):
    def test_current_producer_is_approved_only_in_the_photo_registry(self):
        here = Path(__file__).resolve().parent
        repository = here.parents[1]
        photo_registry = repository / 'sports-data' / 'productores-fotos.json'
        sporting_registry = repository / 'sports-data' / 'productores-adquisicion.json'
        if not photo_registry.is_file():
            photo_registry = here / 'productores-fotos.json'
            sporting_registry = here / 'productores-adquisicion.json'
        if not photo_registry.is_file():
            self.skipTest('registro de productores de fotos no copiado junto a las pruebas (imagen de pruebas)')
        rules = photo_recipe.photo_producer_rules()
        photo = {e['rules_sha256']: e for e in json.loads(photo_registry.read_text(encoding='utf-8'))['aprobados']}
        self.assertIn(rules, photo)
        self.assertEqual((photo[rules]['contract'], photo[rules]['recipe_id']), ('photos-v1', photo_recipe.PHOTO_RECIPE))
        if sporting_registry.is_file():
            sporting = {e['rules_sha256'] for e in json.loads(sporting_registry.read_text(encoding='utf-8'))['aprobados']}
            self.assertNotIn(rules, sporting)

    def test_is_deterministic_and_covers_the_producer_files(self):
        first = photo_recipe.photo_producer_rules()
        self.assertEqual(first, photo_recipe.photo_producer_rules())
        self.assertRegex(first, r'^[0-9a-f]{64}$')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in photo_recipe.PRODUCER_FILES:
                source = (Path(photo_recipe.__file__).parent / name).resolve()
                target = root / 'acquisition' / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())
            fake = root / 'acquisition' / 'photo_recipe.py'
            with mock.patch.object(photo_recipe, '__file__', str(fake)):
                self.assertEqual(photo_recipe.photo_producer_rules(), first)
                for name in photo_recipe.PRODUCER_FILES:
                    path = (root / 'acquisition' / name).resolve()
                    original = path.read_bytes()
                    path.write_bytes(original + b'\n# changed\n')
                    self.assertNotEqual(photo_recipe.photo_producer_rules(), first, name)
                    path.write_bytes(original)


if __name__ == '__main__':
    unittest.main()
