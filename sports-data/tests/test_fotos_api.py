"""Photo routes and photo_url in the sporting API, called in-process through ASGI (no network, no Acquisition)."""
import asyncio
from contextlib import closing
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

try:
    import fastapi  # noqa: F401
except ImportError:
    raise unittest.SkipTest('instala requirements-fastapi.txt para las pruebas de FastAPI')

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import api  # noqa: E402
from fotos import producers, serving, store  # noqa: E402
from fotos.players import FixedPlayers  # noqa: E402
from fotos_fixture import APPROVED, Bundle, photo, webp  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'datos'
DATA = api.cargar(FIXTURE)
PLAYERS = sorted(p['player_id'] for p in DATA['jugadores'])
EXCLUDED = sorted(DATA['jugadores_excluidos'])
TEAM = next(t['team_id'] for t in DATA['equipos']
            if api.consultar(DATA, f"/api/v1/teams/{t['team_id']}/players")[1]['data'])


def call(application, path, query=b'', *, method='GET', body=b'', headers=None):
    """One in-process ASGI request. Returns (status, headers, body bytes)."""
    async def run():
        messages = []

        async def receive():
            return {'type': 'http.request', 'body': body, 'more_body': False}

        async def send(message):
            messages.append(message)
        from urllib.parse import unquote
        request_headers = [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()]
        scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1', 'method': method,
                 'scheme': 'http', 'path': unquote(path), 'raw_path': path.encode(), 'query_string': query,
                 'root_path': '', 'headers': request_headers, 'client': ('test', 1), 'server': ('test', 80)}
        await application(scope, receive, send)
        start = messages[0]
        return (start['status'], {k.decode().lower(): v.decode() for k, v in start['headers']},
                b''.join(m.get('body', b'') for m in messages[1:]))
    return application.state.test_loop.run_until_complete(run())


class PhotoAPI(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix='rj-fotos-api-')
        self.root = Path(self._tmp.name) / 'fotos'
        self.env = mock.patch.dict(os.environ, {'SPORTS_DATA_PHOTOS_DIR': str(self.root), 'SPORTS_DATA_CONTROL': '0'})
        self.env.start()
        os.environ.pop('SPORTS_DATA_PHOTO_SOURCES', None)

    def tearDown(self):
        self.env.stop()
        self._tmp.cleanup()

    def app(self, **environ):
        environment = mock.patch.dict(os.environ, environ)
        environment.start()
        import app as entry
        application = entry.create_app(DATA, control_mode=False)
        # One event loop per application for its lifespan and every request made to it.
        loop = asyncio.new_event_loop()
        lifespan = application.router.lifespan_context(application)
        loop.run_until_complete(lifespan.__aenter__())
        application.state.test_loop = loop

        def close():
            try:
                loop.run_until_complete(lifespan.__aexit__(None, None, None))
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.close()
            finally:
                environment.stop()
        self.addCleanup(close)
        return application

    def importar(self, specs, *, source='df11', known=None):
        photo_store = store.PhotoStore(self.root)
        return photo_store.import_bundle(Bundle(specs, source=source).verify(),
                                         known or FixedPlayers(PLAYERS + EXCLUDED + [999999]))

    def get_json(self, application, path, query=b''):
        status, headers, body = call(application, path, query)
        return status, headers, json.loads(body)


class ServingPhotos(PhotoAPI):
    def test_existing_photo_bytes_type_and_cache(self):
        data = webp(payload=b'jugador uno')
        self.importar([photo(PLAYERS[0], data)])
        application = self.app()
        _, _, profile = self.get_json(application, f'/api/v1/players/{PLAYERS[0]}')
        url = profile['data']['photo_url']
        self.assertEqual(url, f'/api/v1/players/{PLAYERS[0]}/photo/{hashlib.sha256(data).hexdigest()}')
        status, headers, body = call(application, url)
        self.assertEqual(status, 200)
        self.assertEqual(body, data)
        self.assertEqual(headers['content-type'], 'image/webp')
        self.assertEqual(headers['cache-control'], 'private, max-age=31536000, immutable')
        self.assertEqual(headers['etag'], f'"{hashlib.sha256(data).hexdigest()}"')
        self.assertEqual(headers['x-content-type-options'], 'nosniff')

    def test_player_without_photo(self):
        self.importar([photo(PLAYERS[0])])
        application = self.app()
        _, headers, profile = self.get_json(application, f'/api/v1/players/{PLAYERS[1]}')
        self.assertIsNone(profile['data']['photo_url'])
        status, headers, body = self.get_json(application, f'/api/v1/players/{PLAYERS[1]}/photo/{"a" * 64}')
        self.assertEqual((status, body, headers['cache-control']), (404, {'error': 'Fotografía no encontrada'},
                                                                     'no-store'))

    def test_unknown_or_out_of_scope_player_is_never_served(self):
        # Photos stored for a player the sporting data do not serve (unknown id, or excluded from the scope).
        self.importar([photo(999999), photo(EXCLUDED[0])])
        application = self.app()
        for player in (999999, EXCLUDED[0]):
            sha = store.PhotoStore(self.root).active_photos(player)[0]['sha256']
            with self.subTest(player=player):
                status, _, body = self.get_json(application, f'/api/v1/players/{player}/photo/{sha}')
                self.assertEqual((status, body), (404, {'error': 'Fotografía no encontrada'}))

    def test_wrong_or_malformed_versions(self):
        self.importar([photo(PLAYERS[0])])
        application = self.app()
        real = store.PhotoStore(self.root).active_photos(PLAYERS[0])[0]['sha256']
        for version in ('b' * 64, real.upper(), real[:63], real + '0', real[:12], 'latest', '0'):
            with self.subTest(version=version):
                status, headers, body = self.get_json(application, f'/api/v1/players/{PLAYERS[0]}/photo/{version}')
                self.assertEqual((status, body['error']), (404, 'Fotografía no encontrada'))
                self.assertEqual(headers['cache-control'], 'no-store')

    def test_old_version_after_replacement(self):
        old, new = webp(payload=b'v1'), webp(payload=b'v2')
        self.importar([photo(PLAYERS[0], old)])
        application = self.app()
        old_url = self.get_json(application, f'/api/v1/players/{PLAYERS[0]}')[2]['data']['photo_url']
        self.assertEqual(call(application, old_url)[2], old)
        self.importar([photo(PLAYERS[0], new)])  # same running app: the index is read per request
        new_url = self.get_json(application, f'/api/v1/players/{PLAYERS[0]}')[2]['data']['photo_url']
        self.assertNotEqual(new_url, old_url)
        self.assertEqual(call(application, old_url)[0], 404)  # never the new bytes under the old URL
        self.assertEqual(call(application, new_url)[2], new)

    def test_no_unversioned_route_and_routing_compatibility(self):
        self.importar([photo(PLAYERS[0])])
        application = self.app()
        player = PLAYERS[0]
        self.assertEqual(self.get_json(application, f'/api/v1/players/{player}/photo')[:3:2],
                         (404, {'error': 'Ruta inexistente'}))
        self.assertEqual(self.get_json(application, f'/api/v1/players/{player}/matches')[0], 200)
        url = self.get_json(application, f'/api/v1/players/{player}')[2]['data']['photo_url']
        status, headers, body = self.get_json(application, url, query=b'size=small')
        self.assertEqual((status, body), (400, {'error': 'Esta versión no admite filtros de consulta.'}))
        self.assertEqual(self.get_json(application, f'/api/v1/players/abc/photo/{"a" * 64}')[0], 400)
        self.assertEqual(self.get_json(application, f'/api/v1/players/0{player}/photo/{"a" * 64}')[0], 400)
        self.assertEqual(self.get_json(application, url + '/extra')[:3:2], (404, {'error': 'Ruta inexistente'}))

    def test_no_arbitrary_file_system_access(self):
        self.importar([photo(PLAYERS[0])])
        application = self.app()
        index_sha = hashlib.sha256((self.root / 'fotos.sqlite').read_bytes()).hexdigest()
        stray = webp(payload=b'not indexed')
        (self.root / 'blobs' / f'{hashlib.sha256(stray).hexdigest()}.webp').write_bytes(stray)
        player = PLAYERS[0]
        for path in (f'/api/v1/players/{player}/photo/..%2F..%2Ffotos.sqlite',
                     f'/api/v1/players/{player}/photo/%2E%2E',
                     f'/api/v1/players/{player}/photo/{index_sha}',
                     f'/api/v1/players/{player}/photo/{hashlib.sha256(stray).hexdigest()}',
                     f'/api/v1/players/..%2F{player}/photo/{"a" * 64}'):
            with self.subTest(path=path):
                status, headers, body = call(application, path)
                self.assertIn(status, (400, 404))
                self.assertTrue(headers['content-type'].startswith('application/json'))
                self.assertNotIn(str(self.root).encode(), body)


class PhotoUrls(PhotoAPI):
    def setUp(self):
        super().setUp()
        self.team_players = [p['player_id'] for p in api.consultar(DATA, f'/api/v1/teams/{TEAM}/players')[1]['data']]
        self.with_photo = self.team_players[0]
        self.data = webp(payload=b'con foto')
        self.importar([photo(self.with_photo, self.data)])
        self.url = f'/api/v1/players/{self.with_photo}/photo/{hashlib.sha256(self.data).hexdigest()}'
        self.application = self.app()

    def compare(self, route, players_of):
        status, _, body = self.get_json(self.application, route)
        expected_status, expected = api.consultar(DATA, route)
        self.assertEqual(status, expected_status)
        players = players_of(body)
        self.assertTrue(players)
        for player in players:
            self.assertEqual(player['photo_url'], self.url if player['player_id'] == self.with_photo else None)
        for player in players_of(body):
            player.pop('photo_url')
        self.assertEqual(body, expected)  # every previous field, value and order of players unchanged
        return players

    def test_profile(self):
        self.compare(f'/api/v1/players/{self.with_photo}', lambda b: [b['data']])
        others = [p for p in PLAYERS if p != self.with_photo]
        self.compare(f'/api/v1/players/{others[0]}', lambda b: [b['data']])

    def test_team_players(self):
        players = self.compare(f'/api/v1/teams/{TEAM}/players', lambda b: b['data'])
        self.assertGreater(len(players), 1)

    def test_team_player_stats(self):
        self.compare(f'/api/v1/teams/{TEAM}/player-stats',
                     lambda b: [p for c in b['data']['competitions'] for p in c['players']])

    def test_trailing_slash_like_the_sporting_routes(self):
        status, _, body = self.get_json(self.application, f'/api/v1/players/{self.with_photo}/')
        self.assertEqual((status, body['data']['photo_url']), (200, self.url))

    def test_other_responses_have_no_photo_url(self):
        for route in ('/api/v1/scope', f'/api/v1/teams/{TEAM}', f'/api/v1/teams/{TEAM}/matches',
                      f'/api/v1/players/{self.with_photo}/matches', '/api/v1/players/1', '/health'):
            with self.subTest(route=route):
                self.assertEqual(self.get_json(self.application, route)[2], api.consultar(DATA, route)[1])

    def test_one_batch_lookup_per_response(self):
        with mock.patch.object(serving.PhotoReader, 'select', autospec=True,
                               side_effect=serving.PhotoReader.select) as spy:
            self.get_json(self.application, f'/api/v1/teams/{TEAM}/player-stats')
        self.assertEqual(spy.call_count, 1)
        self.assertEqual(sorted(spy.call_args.args[1]), sorted(
            p['player_id'] for c in api.consultar(DATA, f'/api/v1/teams/{TEAM}/player-stats')[1]['data']['competitions']
            for p in c['players']))

    def test_in_memory_sporting_data_is_not_mutated(self):
        before = json.dumps(DATA['jugadores'], sort_keys=True, default=str)
        self.get_json(self.application, f'/api/v1/players/{self.with_photo}')
        self.get_json(self.application, f'/api/v1/teams/{TEAM}/players')
        self.assertEqual(json.dumps(DATA['jugadores'], sort_keys=True, default=str), before)
        self.assertNotIn('photo_url', api.consultar(DATA, f'/api/v1/players/{self.with_photo}')[1]['data'])


class Absence(PhotoAPI):
    def assertAllNull(self, application):
        for route, players_of in ((f'/api/v1/players/{PLAYERS[0]}', lambda b: [b['data']]),
                                  (f'/api/v1/teams/{TEAM}/players', lambda b: b['data'])):
            status, _, body = self.get_json(application, route)
            self.assertEqual(status, 200)
            self.assertTrue(all(p['photo_url'] is None for p in players_of(body)))

    def test_missing_store_directory(self):
        application = self.app()
        self.assertAllNull(application)
        self.assertEqual(call(application, f'/api/v1/players/{PLAYERS[0]}/photo/{"a" * 64}')[0], 404)
        self.assertFalse(self.root.exists())  # reading never creates the store

    def test_empty_store(self):
        store.PhotoStore(self.root)
        application = self.app()
        self.assertAllNull(application)
        self.assertEqual(call(application, f'/api/v1/players/{PLAYERS[0]}/photo/{"a" * 64}')[0], 404)


class StoreFaults(PhotoAPI):
    """Corruption is not absence: the photo route answers 500; the sporting JSON keeps working (and logs it)."""

    def test_missing_or_altered_blob(self):
        self.importar([photo(PLAYERS[0])])
        application = self.app()
        url = self.get_json(application, f'/api/v1/players/{PLAYERS[0]}')[2]['data']['photo_url']
        blob = store.PhotoStore(self.root).active_photos(PLAYERS[0])[0]['path']
        for damage in (lambda: blob.write_bytes(b'altered'), blob.unlink):
            with self.subTest(damage=damage), mock.patch('sys.stderr', new_callable=io.StringIO) as err:
                damage()
                status, headers, body = self.get_json(application, url)
                self.assertEqual(status, 500)
                self.assertEqual(headers['cache-control'], 'no-store')
                self.assertNotIn(str(self.root), json.dumps(body))
                self.assertIn('[fotos]', err.getvalue())

    def test_unreadable_index(self):
        self.root.mkdir(parents=True)
        (self.root / 'fotos.sqlite').write_bytes(b'this is not a sqlite database' * 100)
        application = self.app()
        with mock.patch('sys.stderr', new_callable=io.StringIO) as err:
            status, _, body = self.get_json(application, f'/api/v1/players/{PLAYERS[0]}')
            self.assertEqual((status, body['data']['photo_url']), (200, None))
            self.assertIn('[fotos] índice de fotografías ilegible', err.getvalue())
            self.assertEqual(call(application, f'/api/v1/players/{PLAYERS[0]}/photo/{"a" * 64}')[0], 500)


class SourceSelection(PhotoAPI):
    def setUp(self):
        super().setUp()
        self.df11, self.web = webp(payload=b'df11'), webp(payload=b'web')
        self.importar([photo(PLAYERS[0], self.web)], source='web-club')  # imported first on purpose
        self.importar([photo(PLAYERS[0], self.df11)], source='df11')
        self.importar([photo(PLAYERS[1], self.web)], source='web-club')  # only in a source not enabled by default

    def served(self, application, player):
        url = self.get_json(application, f'/api/v1/players/{player}')[2]['data']['photo_url']
        return None if url is None else call(application, url)[2]

    def test_df11_is_the_default_and_only_enabled_source(self):
        application = self.app()
        self.assertEqual(self.served(application, PLAYERS[0]), self.df11)
        self.assertIsNone(self.served(application, PLAYERS[1]))
        web_sha = hashlib.sha256(self.web).hexdigest()
        self.assertEqual(call(application, f'/api/v1/players/{PLAYERS[1]}/photo/{web_sha}')[0], 404)

    def test_explicit_priority_list(self):
        application = self.app(SPORTS_DATA_PHOTO_SOURCES='web-club,df11')
        self.assertEqual(self.served(application, PLAYERS[0]), self.web)
        self.assertEqual(self.served(application, PLAYERS[1]), self.web)
        application = self.app(SPORTS_DATA_PHOTO_SOURCES='df11,web-club')
        self.assertEqual(self.served(application, PLAYERS[0]), self.df11)

    def test_selection_is_deterministic_and_ignores_superseded(self):
        reader = serving.PhotoReader(self.root, ('df11', 'web-club'))
        first = reader.select(PLAYERS[:2])
        for _ in range(5):
            self.assertEqual(reader.select(reversed(PLAYERS[:2])), first)
        self.importar([photo(PLAYERS[0], webp(payload=b'df11 v2'))], source='df11')
        self.assertEqual(reader.select([PLAYERS[0]])[PLAYERS[0]].sha256,
                         hashlib.sha256(webp(payload=b'df11 v2')).hexdigest())

    def test_invalid_priority_configuration(self):
        for value in ('', 'df11,,web', 'df11,df11', '../x'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                serving.source_priority({'SPORTS_DATA_PHOTO_SOURCES': value})
        self.assertEqual(serving.source_priority({}), ('df11',))


class BatchLookup(PhotoAPI):
    def test_many_players_in_chunks(self):
        ids = list(range(100000, 101200))
        photo_store = store.PhotoStore(self.root)
        photo_store.import_bundle(Bundle([photo(i, webp(payload=b'shared')) for i in ids]).verify(), FixedPlayers(ids))
        reader = serving.PhotoReader(self.root)
        statements = []
        real_connect = reader._connect

        def traced():
            connection = real_connect()
            connection.set_trace_callback(lambda sql: statements.append(sql) if sql.startswith('SELECT') else None)
            return connection
        with mock.patch.object(reader, '_connect', traced):
            urls = reader.urls(ids + [5, 6])
        self.assertEqual(sorted(urls), ids)
        self.assertEqual(len(statements), 3)  # 1200 players in batches of 500


class ControlMode(PhotoAPI):
    """In control mode both the JSON and the photo route use the generation captured for the request."""

    def test_uses_the_captured_generation(self):
        self.importar([photo(PLAYERS[0]), photo(999999)])
        application = self.app()
        captured = []

        class Generation:
            def consultar(self, route):
                captured.append(route)
                return api.consultar(DATA, route)
        application.state.control_mode = True
        application.state.coordinator = mock.Mock(capturar=lambda: Generation())
        url = self.get_json(application, f'/api/v1/players/{PLAYERS[0]}')[2]['data']['photo_url']
        self.assertIsNotNone(url)
        self.assertEqual(call(application, url)[0], 200)
        sha = store.PhotoStore(self.root).active_photos(999999)[0]['sha256']
        self.assertEqual(call(application, f'/api/v1/players/999999/photo/{sha}')[0], 404)
        self.assertEqual(captured, [f'/api/v1/players/{PLAYERS[0]}'] * 2 + ['/api/v1/players/999999'])
        application.state.coordinator = mock.Mock(capturar=mock.Mock(side_effect=RuntimeError('lock')))
        with mock.patch('sys.stderr', new_callable=io.StringIO):
            self.assertEqual(call(application, url)[0], 503)


class InternalImport(PhotoAPI):
    def setUp(self):
        super().setUp()
        self.token = 'photo-import-test-' + ('x' * 40)
        self.token_file = self.root.parent / 'acquisition-token'
        self.token_file.write_text(self.token, encoding='ascii')
        self.application = self.app(RJ_ACQUISITION_SECRET_FILE=str(self.token_file))

    def headers_for(self, bundle, descriptor):
        return {
            'Authorization': 'Bearer ' + self.token,
            'Content-Type': 'application/json',
            'X-Photo-Bundle-Bytes': str(descriptor['bytes']),
            'X-Photo-Bundle-SHA256': descriptor['sha256'],
            'X-Acquisition-Job-ID': bundle.job_id,
            'X-Acquisition-Request-ID': bundle.request_id,
        }

    def test_internal_inventory_and_import_require_auth_and_bind_exact_player_ids(self):
        path = '/internal/v1/player-photos/inventory'
        status, _, body = call(self.application, path, b'source_id=df11')
        self.assertEqual((status, json.loads(body)['error']), (401, 'unauthorized'))

        headers = {'Authorization': 'Bearer ' + self.token}
        status, _, body = call(self.application, path, b'source_id=df11', headers=headers)
        inventory = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual((inventory['source_id'], inventory['photos']), ('df11', []))
        self.assertFalse(self.root.exists())  # inventory is a read and does not initialize the catalog

        data = webp(payload=b'only this exact player id')
        bundle = Bundle([photo(PLAYERS[0], data), photo(999999, webp(payload=b'unknown id'))])
        raw, descriptor = bundle.body()
        with mock.patch.object(producers, 'load', return_value=APPROVED):
            status, _, body = call(self.application, '/internal/v1/player-photos/import', method='POST', body=raw,
                                   headers=self.headers_for(bundle, descriptor))
        result = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(result['counts']['new'], 1)
        self.assertEqual(result['counts']['unknown_player'], 1)
        with mock.patch.object(producers, 'load', side_effect=AssertionError('replay must use its saved receipt')):
            replay_status, _, replay_body = call(self.application, '/internal/v1/player-photos/import',
                                                  method='POST', body=raw,
                                                  headers=self.headers_for(bundle, descriptor))
        self.assertEqual(replay_status, 200)
        self.assertTrue(json.loads(replay_body)['reused'])
        profile = self.get_json(self.application, f'/api/v1/players/{PLAYERS[0]}')[2]['data']
        self.assertEqual(profile['photo_url'], f'/api/v1/players/{PLAYERS[0]}/photo/{hashlib.sha256(data).hexdigest()}')
        self.assertIsNone(self.get_json(self.application, '/api/v1/players/999999')[2].get('data', {}).get('photo_url'))

    def test_invalid_bundle_is_rejected_before_storage(self):
        photo_bundle = Bundle([photo(PLAYERS[0])])
        raw, descriptor = photo_bundle.body()
        tampered = raw + b' '
        with mock.patch.object(producers, 'load', return_value=APPROVED):
            status, _, body = call(self.application, '/internal/v1/player-photos/import', method='POST', body=tampered,
                                   headers=self.headers_for(photo_bundle, descriptor))
        self.assertEqual((status, json.loads(body)['error']), (422, 'Paquete de fotografías rechazado'))
        self.assertFalse(self.root.exists())


class Isolation(unittest.TestCase):
    def test_app_and_photo_code_are_outside_the_coordinator_fingerprint(self):
        from persistencia import carga
        files = carga.archivos_de_codigo()
        self.assertNotIn('app.py', files)
        self.assertNotIn('fotos/serving.py', files)
        self.assertFalse([f for f in files if f.startswith('fotos/')])

    def test_reader_never_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'fotos'
            photo_store = store.PhotoStore(root)
            photo_store.import_bundle(Bundle([photo(3007)]).verify(), FixedPlayers([3007]))
            before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
            reader = serving.PhotoReader(root)
            sha = reader.select([3007])[3007].sha256
            reader.serve(3007, sha)
            with closing(sqlite3.connect(f'{(root / "fotos.sqlite").resolve().as_uri()}?mode=ro', uri=True)) as c:
                self.assertEqual(c.execute('SELECT COUNT(*) FROM photo').fetchone()[0], 1)
            self.assertEqual({p: p.read_bytes() for p in root.rglob('*') if p.is_file()}, before)


if __name__ == '__main__':
    unittest.main()
