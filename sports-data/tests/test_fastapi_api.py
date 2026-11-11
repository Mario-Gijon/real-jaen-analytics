"""REST contract and SQLite lifecycle tests for the sports-data FastAPI app."""
import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

try:
    import uvicorn
except ImportError:
    raise unittest.SkipTest('instala requirements-fastapi.txt para las pruebas de FastAPI')

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'datos'
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import api  # noqa: E402
from test_carga import AMBITO, Base as BaseCarga  # noqa: E402


def _free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def _get(base, route):
    try:
        response = urllib.request.urlopen(base + route, timeout=5)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        headers = {name.lower(): response.headers[name] for name in ('Content-Type', 'Cache-Control')}
        return response.status, headers, json.loads(response.read().decode('utf-8'))


class FastAPIContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='sports-data-api-')
        cls.replacements = Path(cls.temp.name) / 'replacements.json'
        cls.replacements.write_text(json.dumps({'sustituciones': [{
            'sustituido': 1012,
            'vigente': 1006,
            'motivo': 'Aplazado y jugado en otra fecha con otro id',
            'procedencia': {
                'decidido_por': 'Analista (prueba)',
                'fecha': '2026-10-22',
                'evidencia': 'Mismo cruce y jornada',
            },
        }]}), encoding='utf-8')
        cls.data = api.cargar(FIXTURE, sustituciones=cls.replacements)
        cls.port = _free_port()
        env = dict(os.environ)
        env.update({
            'SPORTS_DATA_CONTROL': '0',
            'SPORTS_DATA_STORAGE': 'json',
            'SPORTS_DATA_DIR': str(FIXTURE),
            'SPORTS_DATA_REPLACEMENTS': str(cls.replacements),
            'PYTHONUTF8': '1',
        })
        cls.process = subprocess.Popen(
            [sys.executable, '-B', '-m', 'uvicorn', 'app:app', '--host', '127.0.0.1', '--port', str(cls.port),
             '--log-level', 'warning'],
            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        cls.url = f'http://127.0.0.1:{cls.port}'
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if cls.process.poll() is not None:
                error = cls.process.stderr.read().decode('utf-8', errors='replace')
                raise RuntimeError(f'FastAPI terminó al arrancar: {error}')
            try:
                _get(cls.url, '/health')
                return
            except OSError:
                time.sleep(0.05)
        cls.tearDownClass()
        raise RuntimeError('FastAPI no arrancó')

    @classmethod
    def tearDownClass(cls):
        process = getattr(cls, 'process', None)
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        temporary = getattr(cls, 'temp', None)
        if temporary is not None:
            temporary.cleanup()

    @classmethod
    def routes(cls):
        routes = ['/health', '/api/v1/scope']
        for team in cls.data['equipos']:
            ident = team['team_id']
            routes.extend((f'/api/v1/teams/{ident}', f'/api/v1/teams/{ident}/matches',
                           f'/api/v1/teams/{ident}/players', f'/api/v1/teams/{ident}/performance',
                           f'/api/v1/teams/{ident}/player-stats'))
        player_ids = {player['player_id'] for player in cls.data['jugadores']} | set(cls.data['jugadores_excluidos'])
        for ident in sorted(player_ids):
            routes.extend((f'/api/v1/players/{ident}', f'/api/v1/players/{ident}/matches'))
        match_ids = {match['match_id'] for match in cls.data['partidos']} | set(cls.data['excluidos'])
        for ident in sorted(match_ids):
            routes.extend((f'/api/v1/matches/{ident}',
                           *(f'/api/v1/matches/{ident}/{section}' for section in
                             ('statistics', 'lineups', 'incidents', 'unknown-section'))))
        routes.extend(('/', '/api/v1/teams/abc', '/api/v1/teams/1', '/api/v1/players/not-an-id',
                       '/api/v1/players/1', '/api/v1/matches/not-an-id', '/api/v1/matches/1',
                       '/api/v1/matches/1001/not-a-section', '/api/v1/unknown/1', '/api/v1/teams/2841/',
                       '/api/v1/matches/1012', '/api/v1/matches/1006'))
        return list(dict.fromkeys(routes))

    def test_fastapi_matches_the_existing_sports_rest_contract(self):
        routes = self.routes()
        self.assertGreaterEqual(len(routes), 135)
        for route in routes:
            with self.subTest(route=route):
                status, headers, body = _get(self.url, route)
                expected_status, expected_body = api.consultar(self.data, route)
                self.assertEqual(status, expected_status)
                self.assertEqual(headers['content-type'], 'application/json; charset=utf-8')
                self.assertEqual(headers['cache-control'], 'no-store')
                self.assertEqual(body, expected_body)

    def test_query_parameters_keep_the_documented_rejection(self):
        status, headers, body = _get(self.url, '/api/v1/teams/2841/matches?unsupported=true')
        self.assertEqual((status, headers['cache-control'], body),
                         (400, 'no-store', {'error': 'Esta versión no admite filtros de consulta.'}))

    def test_comments_use_public_match_ids_and_distinguish_not_collected(self):
        source_id = self.data['partidos'][0]['match_id']
        status, headers, body = _get(self.url, f'/api/v1/matches/sofascore-match-{source_id}/comments')
        self.assertEqual(status, 200)
        self.assertEqual(headers['cache-control'], 'no-store')
        self.assertEqual(body['matchId'], f'sofascore-match-{source_id}')
        self.assertEqual(body['availability'], 'not_collected')
        self.assertEqual(body['comments'], [])

        status, _headers, body = _get(self.url, '/api/v1/matches/sofascore-match-999999999/comments')
        self.assertEqual((status, body), (404, {'error': 'Partido no encontrado'}))

        status, _headers, body = _get(self.url, f'/api/v1/matches/sofascore-match-{source_id}/comments?x=1')
        self.assertEqual((status, body), (400, {'error': 'Esta versión no admite filtros de consulta.'}))


class FastAPISQLLifecycle(BaseCarga):
    """The ASGI lifespan loads one SQL snapshot and holds/releases its cooperative lock."""

    def test_loads_sql_once_and_keeps_the_lock_until_shutdown(self):
        from persistencia.bloqueo import BaseEnUso, bloquear
        from persistencia.carga import cargar
        import app as fastapi_entry

        cargar(self.importacion(), self.base)
        with mock.patch.dict(os.environ, {
            'SPORTS_DATA_STORAGE': 'sql',
            'SPORTS_DATA_DB': str(self.base),
            'SPORTS_DATA_SCOPE': str(AMBITO),
        }, clear=False):
            application = fastapi_entry.create_app()

            async def exercise_lifespan():
                async with application.router.lifespan_context(application):
                    self.assertTrue(application.state.data['partidos'])

                    def try_acquire_from_another_thread():
                        with bloquear(self.base):
                            pass

                    with self.assertRaises(BaseEnUso):
                        await asyncio.to_thread(try_acquire_from_another_thread)

            asyncio.run(exercise_lifespan())

        with bloquear(self.base):
            self.assertTrue(self.base.is_file())


if __name__ == '__main__':
    unittest.main()
