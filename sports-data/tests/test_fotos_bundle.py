"""photos-v1 verifier: every structural, integrity and content check at the receiving trust boundary."""
import base64
import json
import unittest
from unittest import mock
import uuid

from pathlib import Path

from fotos import bundle, images, producers

ROOT = Path(__file__).resolve().parents[1]
from fotos_fixture import APPROVED, RULES, Bundle, photo, webp


class ValidBundles(unittest.TestCase):
    def test_complete_bundle_is_verified_with_its_blobs(self):
        b = Bundle([photo(3007), photo(82474)])
        verified = b.verify()
        self.assertEqual([e.player_id for e in verified.entries], [3007, 82474])
        self.assertEqual(set(verified.blobs), {e.sha256 for e in verified.entries})
        self.assertEqual(verified.request_id, b.request_id)
        self.assertEqual(verified.source_id, 'df11')

    def test_unchanged_photos_travel_without_bytes(self):
        entry, _ = photo(3007, transfer='omitted_unchanged')
        inventory = bundle.inventory('df11', {3007: entry['sha256']})
        verified = Bundle([(entry, None), photo(82474)], inventory=inventory).verify()
        self.assertEqual(len(verified.blobs), 1)
        self.assertEqual(verified.entries[0].transfer, 'omitted_unchanged')

    def test_bundle_without_files(self):
        entry, _ = photo(3007, transfer='omitted_unchanged')
        verified = Bundle([(entry, None)], inventory=bundle.inventory('df11', {3007: entry['sha256']})).verify()
        self.assertEqual(verified.blobs, {})

    def test_two_players_may_share_one_blob(self):
        data = webp(payload=b'same image')
        verified = Bundle([photo(3007, data), photo(82474, data)]).verify()
        self.assertEqual(len(verified.blobs), 1)

    def test_partial_collection_with_rejected_photo(self):
        verified = Bundle([photo(3007), photo(82474, transfer='rejected', reason='recorte no fiable')]).verify()
        self.assertEqual(verified.entries[1].reason, 'recorte no fiable')


class RejectedBundles(unittest.TestCase):
    def assertRejected(self, b, text=None, **kwargs):
        with self.assertRaises(bundle.Rejected) as caught:
            b.verify(**kwargs)
        if text:
            self.assertIn(text, str(caught.exception))

    def test_unknown_protocol_version_or_contract(self):
        for field, value in (('schema', 2), ('schema', True), ('contract', 'photos-v2'), ('contract', 'group-v3')):
            with self.subTest(field=field, value=value):
                b = Bundle([photo(3007)])
                b.manifest[field] = value
                self.assertRejected(b, 'Incompatible contract')

    def test_unknown_collection_format_or_recipe(self):
        b = Bundle([photo(3007)])
        b.manifest['collection']['format_version'] = 2
        self.assertRejected(b, 'Invalid collection')
        b = Bundle([photo(3007)])
        b.manifest['recipe_id'] = 'sofascore-group2-2627-v1'
        self.assertRejected(b, 'Unknown recipe')

    def test_structure(self):
        cases = {
            'extra manifest field': lambda b: b.manifest.update(extra=1),
            'missing manifest field': lambda b: b.manifest.pop('result'),
            'extra entry field': lambda b: b.manifest['entries'][0].update(extra=1),
            'source fields': lambda b: b.manifest['source'].update(kind='x'),
            'unsafe source id': lambda b: b.manifest['source'].update(id='../df11'),
            'bad clock': lambda b: b.manifest.update(finished_at='2026-11-18T09:00:00+00:00'),
            'naive clock': lambda b: b.manifest.update(started_at='2026-11-18T10:00:00'),
            'bad uuid': lambda b: b.manifest.update(operation_id='not-a-uuid'),
            'entry count': lambda b: b.manifest.update(entry_count=5),
        }
        for name, change in cases.items():
            with self.subTest(name):
                b = Bundle([photo(3007)])
                change(b)
                self.assertRejected(b)

    def test_envelope_and_descriptor(self):
        b = Bundle([photo(3007)])
        body, descriptor = b.body()
        context = (b.job_id, b.request_id)
        for name, (raw, desc) in {
            'other size': (body, dict(descriptor, bytes=descriptor['bytes'] + 1)),
            'other hash': (body, dict(descriptor, sha256='0' * 64)),
            'tampered body': (body.replace(b'"df11"', b'"df12"', 1), descriptor),
            'extra envelope key': ((lambda raw: bundle.encode(dict(json.loads(raw), extra=1)))(body), None),
            'not json': (b'not json', None),
            'duplicate key': (body[:-1] + b',"files":{}}', None),
        }.items():
            with self.subTest(name), self.assertRaises(bundle.Rejected):
                bundle.verify(raw, desc or {'bytes': len(raw), 'sha256': bundle.sha(raw)}, context=context, approved=APPROVED)

    def test_wrong_job_context(self):
        b = Bundle([photo(3007)])
        body, descriptor = b.body()
        with self.assertRaisesRegex(bundle.Rejected, 'Wrong job context'):
            bundle.verify(body, descriptor, context=(b.job_id, str(uuid.uuid4())), approved=APPROVED)

    def test_invalid_sofascore_ids(self):
        for value in ('0', '0123', '-1', 'abc', '1' * 13, 3007, None):
            with self.subTest(value=value):
                b = Bundle([photo(3007)])
                b.manifest['entries'][0]['sofascore_id'] = value
                self.assertRejected(b, 'Invalid SofaScore id')

    def test_duplicate_ids(self):
        entry, data = photo(3007)
        self.assertRejected(Bundle([(entry, data), (dict(entry), data)]), 'Duplicate SofaScore id')

    def test_unsafe_or_unexpected_file_names(self):
        for name in ('../x.webp', '/etc/passwd', 'photos/3007.webp', '3007.webp', 'C:\\x.webp'):
            with self.subTest(name=name):
                b = Bundle([photo(3007)])
                data = b.files.popitem()[1]
                b.files[name] = data
                b.manifest['entries'][0]['file'] = name
                self.assertRejected(b, 'Unsafe or unexpected file name')

    def test_files_must_match_included_entries(self):
        b = Bundle([photo(3007)])
        b.files['f' * 64 + '.webp'] = webp(payload=b'extra')
        self.assertRejected(b, 'Files do not match')
        b = Bundle([photo(3007), photo(82474)])
        b.files.pop(b.manifest['entries'][1]['file'])
        self.assertRejected(b, 'Files do not match')  # incomplete bundle

    def test_incomplete_bundle_body(self):
        b = Bundle([photo(3007)])
        body, _ = b.body()
        truncated = body[:-20]
        with self.assertRaises(bundle.Rejected):
            bundle.verify(truncated, {'bytes': len(truncated), 'sha256': bundle.sha(truncated)},
                          context=(b.job_id, b.request_id), approved=APPROVED)

    def test_wrong_hash_size_or_dimensions(self):
        cases = {
            'hash': (lambda e: e.update(sha256='a' * 64, file='a' * 64 + '.webp'), 'Size or hash mismatch'),
            'size': (lambda e: e.update(bytes=e['bytes'] + 1), 'Size or hash mismatch'),
            'dimensions': (lambda e: e.update(width=216), 'Declared dimensions differ'),
        }
        for name, (change, text) in cases.items():
            with self.subTest(name):
                b = Bundle([photo(3007)])
                data = next(iter(b.files.values()))
                change(b.manifest['entries'][0])
                b.files = {b.manifest['entries'][0]['file']: data}
                self.assertRejected(b, text)

    def test_corrupt_or_fake_image(self):
        good = webp(payload=b'abcdef')
        for name, data in (('truncated', good[:-2]), ('trailing data', good + b'xx'), ('not an image', b'x' * 64),
                           ('png bytes', b'\x89PNG\r\n\x1a\n' + b'\x00' * 40),
                           ('zero size', webp(0, 268)), ('too large', webp(5000, 268))):
            with self.subTest(name):
                entry, _ = photo(3007, data)
                self.assertRejected(Bundle([(entry, data)]))

    def test_declared_media_type_is_not_trusted(self):
        b = Bundle([photo(3007)])
        b.manifest['entries'][0]['media_type'] = 'image/png'
        self.assertRejected(b, 'Invalid photo entry')

    def test_invalid_base64(self):
        b = Bundle([photo(3007)])
        body, _ = b.body()
        envelope = json.loads(body)
        name = next(iter(envelope['files']))
        envelope['files'][name] = '***not base64***'
        raw = bundle.encode(envelope)
        with self.assertRaisesRegex(bundle.Rejected, 'Invalid base64'):
            bundle.verify(raw, {'bytes': len(raw), 'sha256': bundle.sha(raw)}, context=(b.job_id, b.request_id), approved=APPROVED)

    def test_size_limits(self):
        big = webp(payload=b'\x00' * (bundle.MAX_PHOTO + 1))
        entry, _ = photo(3007, big)
        self.assertRejected(Bundle([(entry, big)]), 'Invalid photo entry')
        b = Bundle([photo(3007)])
        body, descriptor = b.body()
        with mock.patch.object(bundle, 'MAX_WIRE', len(body) - 1), \
                self.assertRaisesRegex(bundle.Rejected, 'Bundle descriptor mismatch'):
            bundle.verify(body, descriptor, context=(b.job_id, b.request_id), approved=APPROVED)

    def test_decoded_total_limit(self):
        original = bundle.MAX_TOTAL
        try:
            bundle.MAX_TOTAL = 100
            self.assertRejected(Bundle([photo(3007), photo(82474), photo(83687)]), 'decoded size limit')
        finally:
            bundle.MAX_TOTAL = original

    def test_partial_cannot_be_reported_as_complete(self):
        self.assertRejected(Bundle([photo(3007), photo(82474, transfer='rejected')], status='complete'),
                            'cannot be reported as complete')
        self.assertRejected(Bundle([photo(3007)], status='partial'), 'cannot be reported as complete')

    def test_rejected_entry_cannot_carry_a_photo(self):
        b = Bundle([photo(3007, transfer='rejected')])
        b.manifest['entries'][0]['sha256'] = 'a' * 64
        self.assertRejected(b, 'Invalid rejected entry')

    def test_omitted_entries_are_checked_against_the_inventory(self):
        entry, _ = photo(3007, transfer='omitted_unchanged')
        other = bundle.inventory('df11', {3007: 'b' * 64})
        self.assertRejected(Bundle([(entry, None)], inventory=other), 'not in the inventory')
        inventory = bundle.inventory('df11', {3007: entry['sha256']})
        b = Bundle([(entry, None)], inventory=inventory)
        b.manifest['inventory_sha256'] = 'c' * 64
        self.assertRejected(b, 'Inventory mismatch')
        self.assertRejected(Bundle([(entry, None)], inventory=bundle.inventory('otra', {3007: entry['sha256']})),
                            'another source')
        tampered = dict(inventory, photos=[{'sofascore_id': '3007', 'sha256': 'd' * 64}])
        self.assertRejected(Bundle([(entry, None)], inventory=tampered), 'Inventory fingerprint mismatch')

    def test_oversized_provenance(self):
        entry, data = photo(3007, provenance={'note': 'x' * (bundle.MAX_PROVENANCE + 1)})
        self.assertRejected(Bundle([(entry, data)]), 'Invalid provenance')


class ImageInspection(unittest.TestCase):
    def test_webp_variants(self):
        vp8l = b'\x2f' + ((299) | (399 << 14)).to_bytes(4, 'little') + b'\x00'
        vp8l = b'RIFF' + (4 + 8 + len(vp8l)).to_bytes(4, 'little') + b'WEBP' + b'VP8L' + len(vp8l).to_bytes(4, 'little') + vp8l
        vp8x = b'\x10\x00\x00\x00' + (1023).to_bytes(3, 'little') + (767).to_bytes(3, 'little')
        vp8x = b'RIFF' + (4 + 8 + len(vp8x)).to_bytes(4, 'little') + b'WEBP' + b'VP8X' + len(vp8x).to_bytes(4, 'little') + vp8x
        self.assertEqual(images.inspect(webp(215, 268), 'image/webp'), (215, 268))
        self.assertEqual(images.inspect(vp8l, 'image/webp'), (300, 400))
        self.assertEqual(images.inspect(vp8x, 'image/webp'), (1024, 768))
        with self.assertRaises(images.ImageError):
            images.inspect(webp(), 'image/jpeg')


class ProducerApproval(unittest.TestCase):
    """rules_sha256 binds a bundle to an approved photo producer; the photo and sporting registries are separate."""

    def assertRejected(self, b, text, **kwargs):
        with self.assertRaises(bundle.Rejected) as caught:
            b.verify(**kwargs)
        self.assertIn(text, str(caught.exception))

    def test_approved_producer_is_accepted(self):
        self.assertEqual(Bundle([photo(3007)]).verify().manifest['rules_sha256'], RULES)

    def test_missing_or_malformed_fingerprint(self):
        b = Bundle([photo(3007)])
        b.manifest.pop('rules_sha256')
        self.assertRejected(b, 'Invalid manifest structure')
        for value in (None, '', 'A' * 64, 'a' * 63, 123):
            with self.subTest(value=value):
                b = Bundle([photo(3007)])
                b.manifest['rules_sha256'] = value
                self.assertRejected(b, 'Missing producer rules')

    def test_unknown_fingerprint(self):
        b = Bundle([photo(3007)])
        b.manifest['rules_sha256'] = 'f' * 64
        self.assertRejected(b, 'Unapproved producer rules')
        self.assertRejected(Bundle([photo(3007)]), 'Unapproved producer rules', approved={})

    def test_sporting_approvals_never_authorize_photo_bundles(self):
        sporting = json.loads((ROOT / 'productores-adquisicion.json').read_text(encoding='utf-8'))['aprobados']
        for entry in sporting:
            with self.subTest(rules=entry['rules_sha256'][:12]):
                b = Bundle([photo(3007)])
                b.manifest['rules_sha256'] = entry['rules_sha256']
                self.assertRejected(b, 'Unapproved producer rules', approved=producers.load())

    def test_approval_for_another_contract_or_recipe(self):
        for change in ({'contract': 'group-v3'}, {'recipe_id': 'sofascore-group2-2627-v1'}):
            with self.subTest(change=change):
                self.assertRejected(Bundle([photo(3007)]), 'another contract or recipe',
                                    approved={RULES: dict(APPROVED[RULES], **change)})

    def test_real_registries_are_disjoint_and_well_formed(self):
        photo_rules = set(producers.load())
        sporting = {e['rules_sha256'] for e in json.loads(
            (ROOT / 'productores-adquisicion.json').read_text(encoding='utf-8'))['aprobados']}
        self.assertFalse(photo_rules & sporting)
        self.assertNotIn(RULES, photo_rules)  # the test approval never reaches the real registry
        for entry in producers.load().values():
            self.assertEqual((entry['contract'], entry['recipe_id']), ('photos-v1', 'player-photos-local-v1'))

    def test_malformed_registry_is_an_error_not_an_empty_list(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'productores-fotos.json'
            good = dict(rules_sha256='a' * 64, contract='photos-v1', recipe_id='player-photos-local-v1',
                        productor='x', uso='y')
            for content in ('{roto', '[]', json.dumps({'aprobados': []}),
                            json.dumps({'descripcion': 'x', 'aprobados': [dict(good, extra=1)]}),
                            json.dumps({'descripcion': 'x', 'aprobados': [good, good]}),
                            json.dumps({'descripcion': 'x', 'aprobados': [dict(good, rules_sha256='zz')]})):
                with self.subTest(content=content[:40]):
                    path.write_text(content, encoding='utf-8')
                    with self.assertRaises(producers.RegistryError):
                        producers.load(path)
            with self.assertRaises(producers.RegistryError):
                producers.load(Path(directory) / 'missing.json')


if __name__ == '__main__':
    unittest.main()
