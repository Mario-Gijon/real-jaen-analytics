"""SQLite loading through FastAPI, compared with the same synthetic JSON fixtures."""
from contextlib import ExitStack, closing
import importlib.util
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
CON_SQL = importlib.util.find_spec('sqlalchemy') is not None and importlib.util.find_spec('alembic') is not None
CON_FASTAPI = importlib.util.find_spec('uvicorn') is not None

import api  # noqa: E402
from test_carga import AMBITO, Base as BaseCarga, respuestas  # noqa: E402

if CON_SQL:
    from alembic import command
    from persistencia.carga import cargar, configuracion_alembic
    from persistencia.decisiones import cargar_sustituciones
    from persistencia.lectura import BaseNoUtilizable, cargar_desde_base


def _free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@unittest.skipUnless(CON_SQL, 'requiere requirements-sql.txt (.venv)')
class EquivalenciaJsonSql(BaseCarga):
    def test_todas_las_rutas_iguales_con_conflictos_invalidos_y_ausencias(self):
        folder = self.importacion()
        cargar(folder, self.base)
        self.assertEqual(respuestas(cargar_desde_base(self.base, AMBITO)), respuestas(api.cargar(folder, AMBITO)))

    def test_todas_las_rutas_iguales_con_sustituciones_leidas_de_la_base(self):
        folder = self.importacion()
        cargar(folder, self.base)
        replacement = self.sustituciones((300, 301))
        cargar_sustituciones(self.base, replacement, AMBITO)
        sql = respuestas(cargar_desde_base(self.base, AMBITO))
        self.assertEqual(sql, respuestas(api.cargar(folder, AMBITO, replacement)))
        detail = json.loads(next(body for route, _, body in sql if route == '/api/v1/matches/300'))
        self.assertEqual(detail['data']['replaced_by']['match_id'], 301)

    def test_normalizacion_y_evidencia_desde_la_base(self):
        folder = self.importacion()
        cargar(folder, self.base)
        sql, json_data = cargar_desde_base(self.base, AMBITO), api.cargar(folder, AMBITO)
        self.assertEqual(api.actuaciones_del_partido(sql, 200), api.actuaciones_del_partido(json_data, 200))
        players = api.actuaciones_del_partido(sql, 200)
        reason = lambda player, metric: players[player]['stats'][metric]['reason'] or players[player]['stats'][metric]['source']
        self.assertEqual((reason(3, 'shots'), reason(3, 'passes')), ('version_conflict', 'reported'))
        self.assertTrue(any(item['issue'] == 'stat_version_conflict' for item in players[3]['evidence']))
        self.assertEqual((reason(2, 'fouls'), reason(5, 'shots'), reason(1, 'fouls')),
                         ('unused', 'participation_unknown', 'not_reported'))


@unittest.skipUnless(CON_SQL, 'requiere requirements-sql.txt (.venv)')
class ErroresDeCargaSql(BaseCarga):
    def cargar_desde_entorno(self, database, scope=AMBITO):
        import app
        with mock.patch.dict(os.environ, {
            'SPORTS_DATA_STORAGE': 'sql',
            'SPORTS_DATA_DB': str(database),
            'SPORTS_DATA_SCOPE': str(scope),
        }, clear=False), ExitStack() as resources:
            return app._load_data(resources)

    def test_base_inexistente_no_se_crea(self):
        from persistencia.lectura import BaseNoUtilizable
        with self.assertRaisesRegex(BaseNoUtilizable, 'nunca crea una'):
            self.cargar_desde_entorno(self.base)
        self.assertFalse(self.base.exists())

    def test_archivo_que_no_es_una_base(self):
        from persistencia.lectura import BaseNoUtilizable
        self.base.parent.mkdir(parents=True, exist_ok=True)
        self.base.write_text('no es una base', encoding='utf-8')
        with self.assertRaises(BaseNoUtilizable):
            self.cargar_desde_entorno(self.base)
        self.assertEqual(self.base.read_text(encoding='utf-8'), 'no es una base')

    def test_base_sqlite_ajena_o_de_otra_revision(self):
        from persistencia.lectura import BaseNoUtilizable
        self.base.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.base)) as connection, connection:
            connection.execute('CREATE TABLE otra (x INTEGER)')
        with self.assertRaisesRegex(BaseNoUtilizable, 'sin revisión de esquema'):
            self.cargar_desde_entorno(self.base)
        cargar(self.importacion(), self.tmp / 'valid.sqlite')
        shutil.copy(self.tmp / 'valid.sqlite', self.tmp / 'old.sqlite')
        with closing(sqlite3.connect(self.tmp / 'old.sqlite')) as connection, connection:
            connection.execute("UPDATE alembic_version SET version_num = '0001'")
        with self.assertRaisesRegex(BaseNoUtilizable, 'revisión de esquema 0001; este código necesita 0007'):
            cargar_desde_base(self.tmp / 'old.sqlite', AMBITO)

    def test_base_sin_carga_inicial(self):
        self.base.parent.mkdir(parents=True, exist_ok=True)
        command.upgrade(configuracion_alembic(self.base), 'head')
        with self.assertRaisesRegex(BaseNoUtilizable, 'carga inicial confirmada'):
            cargar_desde_base(self.base, AMBITO)

    def test_sustituciones_guardadas_fuera_del_ambito(self):
        cargar(self.importacion(), self.base)
        replacement = self.sustituciones((300, 301))
        cargar_sustituciones(self.base, replacement, AMBITO)
        scope = self.tmp / 'other-scope.json'
        scope.write_text(json.dumps({'competiciones': [{'nombre': 'Otro', 'unique_tournament_id': 1,
                                                        'tournament_id': 2, 'season_id': 3}]}), encoding='utf-8')
        with self.assertRaisesRegex(BaseNoUtilizable, 'fuera del ámbito'):
            self.cargar_desde_entorno(self.base, scope)


@unittest.skipUnless(CON_SQL and CON_FASTAPI, 'requiere requirements-sql.txt y requirements-fastapi.txt')
class FastAPISqlOverHttp(BaseCarga):
    def test_sql_served_by_fastapi_matches_json_and_releases_the_lock(self):
        from persistencia.bloqueo import bloquear
        folder = self.importacion()
        cargar(folder, self.base)
        replacement = self.sustituciones((300, 301))
        cargar_sustituciones(self.base, replacement, AMBITO)
        port = _free_port()
        env = dict(os.environ, SPORTS_DATA_CONTROL='0', SPORTS_DATA_STORAGE='sql', SPORTS_DATA_DB=str(self.base),
                   SPORTS_DATA_SCOPE=str(AMBITO), PYTHONUTF8='1')
        process = subprocess.Popen([sys.executable, '-B', '-m', 'uvicorn', 'app:app', '--host', '127.0.0.1',
                                    '--port', str(port), '--log-level', 'warning'], cwd=ROOT, env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        base_url = f'http://127.0.0.1:{port}'
        try:
            deadline = time.monotonic() + 30
            while True:
                try:
                    urllib.request.urlopen(base_url + '/health', timeout=1).close()
                    break
                except OSError:
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError(f'FastAPI SQL no arrancó: {process.stderr.read()}')
                    time.sleep(0.1)
            expected = {route: (status, json.loads(body)) for route, status, body in
                        respuestas(api.cargar(folder, AMBITO, replacement))}
            sample = ['/api/v1/scope', '/api/v1/teams/2841/matches', '/api/v1/teams/2841/performance',
                      '/api/v1/teams/2841/player-stats', '/api/v1/players/1/matches',
                      '/api/v1/matches/200/lineups', '/api/v1/matches/300', '/api/v1/matches/400',
                      '/api/v1/teams/1', '/api/v1/teams/abc']
            for route in sample:
                try:
                    with urllib.request.urlopen(base_url + route, timeout=10) as response:
                        status, body = response.status, json.loads(response.read().decode('utf-8'))
                except urllib.error.HTTPError as error:
                    with error:
                        status, body = error.code, json.loads(error.read().decode('utf-8'))
                self.assertEqual((status, body), expected[route], route)
        finally:
            process.terminate()
            process.wait(timeout=10)
            process.stderr.close()
        with bloquear(self.base):
            self.assertTrue(self.base.is_file())


if __name__ == '__main__':
    unittest.main()
