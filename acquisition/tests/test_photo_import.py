"""Synthetic HTTP handoff to Sports Data's real photos-v1 verifier and store."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.parse
from unittest import mock

from acquisition import photo_import, photo_recipe
from acquisition.photo_protocol import bundle

ROOT = Path(__file__).resolve().parents[2]
SPORTS_DATA = ROOT / 'sports-data'
TOKEN = b'test-acquisition-token-that-is-not-a-secret'


def webp(width=215, height=268, payload=b'fixture'):
    frame = b'\x10\x95\x00' + b'\x9d\x01\x2a' + width.to_bytes(2, 'little') + height.to_bytes(2, 'little') + payload
    if len(frame) % 2:
        frame += b'\x00'
    chunk = b'VP8 ' + len(frame).to_bytes(4, 'little') + frame
    return b'RIFF' + (4 + len(chunk)).to_bytes(4, 'little') + b'WEBP' + chunk


def write_collection(path: Path, photos: dict[str, bytes]):
    (path / 'photos').mkdir(parents=True)
    entries = []
    for player_id, content in photos.items():
        (path / 'photos' / f'{player_id}.webp').write_bytes(content)
        entries.append(dict(sofascore_id=player_id, file=f'photos/{player_id}.webp',
                            sha256=hashlib.sha256(content).hexdigest(), bytes=len(content), format='webp',
                            media_type='image/webp', width=215, height=268,
                            provenance=dict(source_file=f'{player_id}.webp', source_record=player_id)))
    manifest = dict(format='rja-player-photo-collection', format_version=1,
                    collection=dict(id='df11', version='test1'), source=dict(id='df11', label='synthetic test'),
                    processing=dict(tool='synthetic fixture'), photo_count=len(entries), photos=entries)
    (path / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')


@unittest.skipUnless(SPORTS_DATA.is_dir(), 'Sports Data source is absent in the Acquisition-only test image')
class PhotoImportHTTPCompatibility(unittest.TestCase):
    """HTTP fixture delegates verification and storage to Sports Data's own modules, not a copy of photos-v1."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(SPORTS_DATA))
        from fotos import bundle as receiver_bundle, producers, store as receiver_store
        from fotos.players import FixedPlayers

        cls.receiver_bundle = receiver_bundle
        cls.producers = producers
        cls.receiver_store = receiver_store
        cls.FixedPlayers = FixedPlayers

    @classmethod
    def tearDownClass(cls):
        try:
            sys.path.remove(str(SPORTS_DATA))
        except ValueError:
            pass

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='rja-photo-http-')
        self.root = Path(self.temporary.name)
        self.collection = self.root / 'collections' / 'df11' / '1'
        self.store = self.receiver_store.PhotoStore(self.root / 'receiver-state')
        self.known = self.FixedPlayers([3007])
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *_args):
                pass

            def respond(self, status, value):
                payload = json.dumps(value, separators=(',', ':')).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def authorized(self):
                return self.headers.get('Authorization') == 'Bearer ' + TOKEN.decode()

            def do_GET(self):
                parsed = urllib.parse.urlsplit(self.path)
                outer.requests.append(('GET', parsed.path))
                if not self.authorized():
                    return self.respond(401, {'error': 'unauthorized'})
                if parsed.path != '/internal/v1/player-photos/inventory':
                    return self.respond(404, {'error': 'not found'})
                source_id = urllib.parse.parse_qs(parsed.query).get('source_id', [''])[0]
                return self.respond(200, outer.store.inventory(source_id))

            def do_POST(self):
                outer.requests.append(('POST', self.path))
                if not self.authorized():
                    return self.respond(401, {'error': 'unauthorized'})
                if self.path != '/internal/v1/player-photos/import':
                    return self.respond(404, {'error': 'not found'})
                raw = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                try:
                    descriptor = {'bytes': int(self.headers['X-Photo-Bundle-Bytes']),
                                  'sha256': self.headers['X-Photo-Bundle-SHA256']}
                    job_id = self.headers['X-Acquisition-Job-ID']
                    request_id = self.headers['X-Acquisition-Request-ID']
                    envelope = outer.receiver_bundle.parse(raw)
                    source_id = envelope['manifest']['source']['id']
                    current = outer.store.inventory(source_id)
                    verified = outer.receiver_bundle.verify(
                        raw, descriptor, context=(job_id, request_id), approved=outer.producers.load(),
                        inventory=current)
                    outer.uploads.append(dict(content_type=self.headers.get('Content-Type'), descriptor=descriptor,
                                              job_id=job_id, request_id=request_id, files=len(envelope['files']),
                                              headers={key: self.headers.get(key) for key in (
                                                  'X-Photo-Bundle-Bytes', 'X-Photo-Bundle-SHA256',
                                                  'X-Acquisition-Job-ID', 'X-Acquisition-Request-ID')}))
                    result = outer.store.import_bundle(verified, outer.known)
                except Exception as error:
                    return self.respond(422, {'error': type(error).__name__})
                return self.respond(200, result)

        self.uploads = []
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.server_url = f'http://127.0.0.1:{self.server.server_port}'
        self.secret_file = self.root / 'acquisition-token'
        self.secret_file.write_bytes(TOKEN)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temporary.cleanup()

    def run_import(self):
        return photo_import.import_collection('df11/1', confirm_usage_rights=True,
                                              collections_root=self.root / 'collections',
                                              secret_file=self.secret_file, sports_data_url=self.server_url)

    def test_synthetic_photo_is_accepted_by_sports_data_and_stored(self):
        write_collection(self.collection, {'3007': webp()})

        result = self.run_import()

        self.assertEqual([method for method, _path in self.requests], ['GET', 'POST'])
        self.assertEqual(result['status'], 'imported')
        self.assertEqual(result['prepared']['counts'], dict(included=1, omitted_unchanged=0, rejected=0))
        self.assertEqual(result['imported']['counts']['new'], 1)
        self.assertEqual(self.store.import_record(result['request_id'])['entries'][0]['outcome'], 'new')
        self.assertEqual(self.uploads[0]['content_type'], 'application/json')
        self.assertEqual(self.uploads[0]['descriptor']['sha256'], result['imported']['bundle_sha256'])
        self.assertEqual(self.uploads[0]['files'], 1)

    def test_second_import_uses_inventory_and_sends_no_unchanged_image_bytes(self):
        write_collection(self.collection, {'3007': webp()})

        first = self.run_import()
        second = self.run_import()

        self.assertEqual(first['imported']['counts']['new'], 1)
        self.assertEqual(second['prepared']['counts'], dict(included=0, omitted_unchanged=1, rejected=0))
        self.assertEqual(second['imported']['counts']['unchanged'], 1)
        self.assertEqual([upload['files'] for upload in self.uploads], [1, 0])
        self.assertEqual(len(self.requests), 4)

    def test_sports_data_classifies_unknown_exact_ids_without_name_matching(self):
        write_collection(self.collection, {'83687': webp(payload=b'unknown player')})

        result = self.run_import()

        self.assertEqual(result['imported']['counts']['unknown_player'], 1)
        self.assertEqual(self.store.import_record(result['request_id'])['entries'][0]['player_id'], 83687)

    def test_invalid_image_is_reported_and_never_transferred_as_a_blob(self):
        write_collection(self.collection, {'3007': b'not an image'})

        result = self.run_import()

        self.assertEqual(result['prepared']['counts'], dict(included=0, omitted_unchanged=0, rejected=1))
        self.assertEqual(result['imported']['counts']['invalid'], 1)
        self.assertEqual(self.uploads[0]['files'], 0)

    def test_usage_rights_confirmation_is_required_before_any_request(self):
        write_collection(self.collection, {'3007': webp()})
        opener = mock.Mock(side_effect=AssertionError('network access before rights confirmation'))
        with self.assertRaises(photo_import.PhotoImportError):
            photo_import.import_collection('df11/1', confirm_usage_rights=False,
                                           collections_root=self.root / 'collections',
                                           secret_file=self.secret_file, sports_data_url=self.server_url,
                                           opener=opener)
        self.assertEqual(self.requests, [])
        opener.assert_not_called()


if __name__ == '__main__':
    unittest.main()
