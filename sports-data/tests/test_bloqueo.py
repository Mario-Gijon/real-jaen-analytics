"""Coordinación real entre procesos; solo bases temporales, sin tocar servicios ajenos."""
import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / 'tests'))
from persistencia.bloqueo import BaseEnUso, archivo_de_bloqueo, bloquear
from test_carga import AMBITO, Base as BaseCarga, CON_SQL

if CON_SQL:
    from persistencia import sincronizacion, sqlite
    from persistencia.carga import cargar
    from persistencia.decisiones import cargar_sustituciones

ENV = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')


class Procesos:
    def proceso(self, codigo, *args):
        p = subprocess.Popen([sys.executable, '-B', '-u', '-c', codigo, *map(str, args)], cwd=RAIZ,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, encoding='utf-8', env=ENV)
        self.addCleanup(self.cerrar, p)
        return p

    @staticmethod
    def matar(p):
        if p.poll() is None:
            if os.name == 'nt':
                # El redirector de venv puede ejecutar Python en un hijo. Solo nuestro árbol de pruebas.
                subprocess.run(['taskkill', '/PID', str(p.pid), '/T', '/F'], capture_output=True, timeout=10)
            else:
                p.kill()
            p.wait(timeout=10)

    @classmethod
    def cerrar(cls, p):
        cls.matar(p)
        p.communicate(timeout=10)

    def poseedor(self, ruta):
        p = self.proceso("from persistencia.bloqueo import bloquear; import sys; "
                         "c=bloquear(sys.argv[1]); c.__enter__(); print('LISTO',flush=True); "
                         "sys.stdin.readline(); c.__exit__(None,None,None)", ruta)
        self.assertEqual(p.stdout.readline().strip(), 'LISTO')
        return p

    def soltar(self, p):
        p.stdin.write('fin\n')
        p.stdin.flush()
        p.wait(timeout=10)
        self.assertEqual(p.returncode, 0, p.stderr.read())


class BloqueoEntreProcesos(Procesos, unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name) / 'base.sqlite'

    def test_exclusion_normalizacion_reentrada_y_cierre_normal(self):
        p = self.poseedor(self.base)
        equivalente = self.base.parent / '.' / 'sub' / '..' / self.base.name
        (self.base.parent / 'sub').mkdir()
        with self.assertRaisesRegex(BaseEnUso, 'Base en uso'):
            with bloquear(equivalente):
                self.fail('Se adquirió dos veces entre procesos')
        self.soltar(p)
        with bloquear(equivalente), bloquear(self.base):
            self.assertTrue(archivo_de_bloqueo(self.base).exists())
        self.assertTrue(archivo_de_bloqueo(self.base).exists())
        self.soltar(self.poseedor(self.base))

    def test_liberacion_tras_terminacion_inesperada(self):
        p = self.poseedor(self.base)
        self.matar(p)
        with bloquear(self.base):
            pass
        self.assertTrue(archivo_de_bloqueo(self.base).exists())
        self.soltar(self.poseedor(self.base))

    def test_ruta_relativa_y_mayusculas_windows(self):
        p = self.poseedor(self.base)
        rutas = [Path(os.path.relpath(self.base, RAIZ))]
        if os.name == 'nt':
            rutas.append(Path(str(self.base).upper()))
        for ruta in rutas:
            with self.subTest(ruta=ruta), self.assertRaises(BaseEnUso), bloquear(ruta):
                pass
        self.soltar(p)

    def test_enlace_simbolico(self):
        self.base.touch()
        enlace = self.base.with_name('alias.sqlite')
        try:
            enlace.symlink_to(self.base)
        except OSError as e:
            self.skipTest(f'El sistema no permite crear symlinks: {e}')
        p = self.poseedor(enlace)
        with self.assertRaises(BaseEnUso), bloquear(self.base):
            pass
        self.soltar(p)

    @unittest.skipUnless(os.name == 'nt', 'junctions de Windows')
    def test_junction_windows(self):
        real = self.base.parent / 'real'
        real.mkdir()
        enlace = self.base.parent / 'junction'
        r = subprocess.run(['cmd', '/c', 'mklink', '/J', str(enlace), str(real)], capture_output=True, timeout=10)
        if r.returncode:
            self.skipTest(f'No se pudo crear junction: {r.stderr!r}')
        p = self.poseedor(real / 'base.sqlite')
        with self.assertRaises(BaseEnUso), bloquear(enlace / 'base.sqlite'):
            pass
        self.soltar(p)

    def test_excepcion_libera_el_bloqueo(self):
        with self.assertRaisesRegex(ValueError, 'fallo'), bloquear(self.base):
            raise ValueError('fallo')
        self.soltar(self.poseedor(self.base))


CON_FASTAPI = importlib.util.find_spec('uvicorn') is not None

@unittest.skipUnless(CON_SQL and CON_FASTAPI, 'requiere requirements-sql.txt y requirements-fastapi.txt')
class ApiYMantenimiento(Procesos, BaseCarga):
    def setUp(self):
        super().setUp()
        self.carpeta = self.importacion()
        cargar(self.carpeta, self.base)

    def api_environment(self, scope=AMBITO):
        env = dict(ENV)
        env.update({
            'SPORTS_DATA_CONTROL': '0',
            'SPORTS_DATA_STORAGE': 'sql',
            'SPORTS_DATA_DB': str(self.base),
            'SPORTS_DATA_SCOPE': str(scope),
        })
        return env

    def api(self, scope=AMBITO):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        p = subprocess.Popen([sys.executable, '-B', '-m', 'uvicorn', 'app:app', '--host', '127.0.0.1',
                              '--port', str(port), '--log-level', 'warning'], cwd=RAIZ,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, encoding='utf-8', env=self.api_environment(scope))
        self.addCleanup(self.cerrar, p)
        deadline = time.monotonic() + 20
        while True:
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=0.5) as response:
                    self.assertEqual(response.status, 200)
                return p
            except OSError:
                if p.poll() is not None or time.monotonic() > deadline:
                    self.fail(f'FastAPI no disponible, código {p.poll()}: {p.stderr.read()}')
                time.sleep(0.05)

    def cerrar_api(self, process):
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        self.cerrar(process)

    def test_api_rechaza_todas_las_operaciones_sin_cambios_y_despues_funcionan(self):
        p = self.api()
        antes = sqlite.describir(self.base)['huella']
        copias = list(self.base.parent.glob('copias/*'))
        archivo = self.sustituciones((300, 301))
        # Incluso con argumentos/rutas de entrada inválidos se rechaza antes de leerlos o crear copias.
        ordenes = [
            ['importar', '--exportacion', f'2841={self.tmp / "no-existe.jsonl"}'],
            ['reconstruir', '--confirmar'], ['migrar'],
            ['sustituciones', '--archivo', archivo, '--ambito', AMBITO],
            ['restaurar', '--copia', self.tmp / 'no-existe.sqlite', '--confirmar'],
            ['copiar'], ['verificar'], ['cargar', '--importacion', self.carpeta],
        ]
        for orden in ordenes:
            with self.subTest(orden=orden):
                r = subprocess.run([sys.executable, '-B', '-m', 'persistencia', *map(str, orden), '--base', str(self.base)],
                                   cwd=RAIZ, capture_output=True, text=True, encoding='utf-8', env=ENV, timeout=15)
                self.assertEqual(r.returncode, 1, r.stderr)
                self.assertIn('Base en uso', r.stderr)
                self.assertNotIn('Traceback', r.stderr)
        # Una segunda API tampoco puede servir una instantánea distinta de esta misma base.
        r = subprocess.run([sys.executable, '-B', '-m', 'uvicorn', 'app:app', '--host', '127.0.0.1', '--port', '0'],
                           cwd=RAIZ, capture_output=True, text=True, encoding='utf-8',
                           env=self.api_environment(), timeout=15)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('Base en uso', r.stderr)
        self.assertEqual(sqlite.describir(self.base)['huella'], antes)
        self.assertEqual(list(self.base.parent.glob('copias/*')), copias)
        self.cerrar_api(p)
        copia = sincronizacion.copiar(self.base)
        self.assertTrue(sincronizacion.verificar(self.base)['ok'])
        self.assertFalse(sincronizacion.migrar(self.base)['migrada'])
        sincronizacion.importar_evidencia(self.base, entradas=[self.tmp / 'propia.jsonl'])
        sincronizacion.reconstruir(self.base, confirmar=True)
        cargar_sustituciones(self.base, archivo, AMBITO)
        sincronizacion.restaurar(copia['ruta'], self.base)
        self.assertEqual(sqlite.describir(self.base)['huella'], antes)

    def test_api_terminada_inesperadamente_libera_la_base(self):
        p = self.api()
        self.matar(p)
        self.assertTrue(sincronizacion.verificar(self.base)['ok'])
        self.assertTrue(archivo_de_bloqueo(self.base).exists())
        self.cerrar_api(self.api())

    def test_api_fallo_al_cargar_libera_la_base(self):
        p = subprocess.run([sys.executable, '-B', '-m', 'uvicorn', 'app:app', '--host', '127.0.0.1', '--port', '0'],
                           cwd=RAIZ, capture_output=True, text=True, encoding='utf-8',
                           env=self.api_environment(self.tmp / 'no-existe.json'), timeout=15)
        self.assertNotEqual(p.returncode, 0)
        self.assertTrue(sincronizacion.verificar(self.base)['ok'])

    def test_caida_durante_escritura_no_deja_cambios_parciales(self):
        antes = sqlite.describir(self.base)['huella']
        p = self.proceso("""import sys
from persistencia.bloqueo import bloquear
from persistencia.sqlite import crear_motor
with bloquear(sys.argv[1]):
    motor = crear_motor(sys.argv[1])
    with motor.begin() as c:
        c.exec_driver_sql("UPDATE team_name SET name = 'CAMBIO SIN CONFIRMAR'")
        print('LISTO', flush=True)
        sys.stdin.readline()
""", self.base)
        self.assertEqual(p.stdout.readline().strip(), 'LISTO')
        with self.assertRaises(BaseEnUso):
            sincronizacion.verificar(self.base)
        self.matar(p)
        self.assertEqual(sqlite.describir(self.base)['huella'], antes)
        self.assertTrue(sincronizacion.verificar(self.base)['ok'])

    def test_alembic_directo_tambien_coordina(self):
        p = self.poseedor(self.base)
        from alembic import command
        from persistencia.carga import configuracion_alembic
        with self.assertRaises(BaseEnUso):
            command.downgrade(configuracion_alembic(self.base), '0002')
        self.soltar(p)
        self.assertEqual(sqlite.revision(self.base), '0007')
        command.downgrade(configuracion_alembic(self.base), '0002')
        self.assertEqual(sincronizacion.migrar(self.base)['revision'], '0007')

    def test_restauracion_fallida_preserva_destino_y_libera_bloqueo(self):
        copia = sincronizacion.copiar(self.base)
        cargar_sustituciones(self.base, self.sustituciones((300, 301)), AMBITO)
        antes = sqlite.describir(self.base)['huella']
        self.assertNotEqual(antes, copia['huella'])
        comprobar = sqlite.comprobar_copia
        def fallo_preparada(ruta, esperado=None):
            if '.restauracion-' in str(ruta):
                raise sqlite.CopiaNoValida('fallo de verificación de la imagen preparada')
            return comprobar(ruta, esperado)
        with mock.patch.object(sqlite, 'comprobar_copia', side_effect=fallo_preparada):
            with self.assertRaises(sqlite.CopiaNoValida):
                sincronizacion.restaurar(copia['ruta'], self.base)
        with mock.patch.object(sqlite.os, 'replace', side_effect=OSError('fallo al reemplazar')):
            with self.assertRaises(OSError):
                sincronizacion.restaurar(copia['ruta'], self.base)
        self.assertEqual(sqlite.describir(self.base)['huella'], antes)
        self.assertEqual(list(self.base.parent.glob('*.restauracion-*.sqlite')), [])
        self.soltar(self.poseedor(self.base))
        sincronizacion.restaurar(copia['ruta'], self.base)
        self.assertEqual(sqlite.describir(self.base)['huella'], copia['huella'])


if __name__ == '__main__':
    unittest.main()
