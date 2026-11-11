"""FastAPI control mode against SQL fixtures and simulated acquisition only; no services or provider access."""
from contextlib import ExitStack, contextmanager
import asyncio
import json
import os
import socket
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

try:
    import uvicorn
except ImportError:
    raise unittest.SkipTest('instala requirements-fastapi.txt para las pruebas de FastAPI')

import test_actualizacion as fixtures
from test_control import FakeCliente, phase, wait
from persistencia.actualizacion import Coordinador
from persistencia.bloqueo import bloquear


@unittest.skipUnless(fixtures.CON_SQL, 'requiere requirements-sql.txt (.venv)')
class FastAPIControl(fixtures.Base):
    def setUp(self):
        self.fixture = fixtures.Actualizacion()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.client_impl = FakeCliente(self.fixture)
        self.token = 'control-test-token-' + ('x' * 40)
        self.control_secret = self.fixture.tmp / 'control-token'
        self.acquisition_secret = self.fixture.tmp / 'acquisition-token'
        self.control_secret.write_text(self.token, encoding='ascii')
        self.acquisition_secret.write_text('acquisition-test-token-' + ('y' * 40), encoding='ascii')

    def _environment(self):
        return {
            'SPORTS_DATA_CONTROL': '1',
            'SPORTS_DATA_WORKERS': '1',
            'WEB_CONCURRENCY': '1',
            'SPORTS_DATA_STORAGE': 'sql',
            'SPORTS_DATA_DB': str(self.fixture.base),
            'SPORTS_DATA_SCOPE': str(fixtures.AMBITO),
            'RJ_CONTROL_SECRET_FILE': str(self.control_secret),
            'RJ_ACQUISITION_SECRET_FILE': str(self.acquisition_secret),
            'RJ_ACQUISITION_URL': 'http://127.0.0.1:8002',
            'RJ_ACQUISITION_TEST': '1',
        }

    @contextmanager
    def running(self, *, frontier=None):
        import app as fastapi_entry

        def coordinator_factory(base, scope):
            coordinator = Coordinador(base, scope, reserva=0, _frontera=frontier)
            self.addCleanup(coordinator.close)
            return coordinator

        with (mock.patch.dict(os.environ, self._environment(), clear=False),
              mock.patch('persistencia.actualizacion.Coordinador', side_effect=coordinator_factory),
              mock.patch('persistencia.adquisicion.Cliente', return_value=self.client_impl)):
            application = fastapi_entry.create_app(control_mode=True)
            with self.serve(application) as base:
                yield base, application

    @staticmethod
    @contextmanager
    def serve(application):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(application, host='127.0.0.1', port=port,
                                               log_level='critical', access_log=False))
        thread = threading.Thread(target=lambda: asyncio.run(server.serve()), daemon=True)
        thread.start()
        deadline = time.monotonic() + 15
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.02)
        if not server.started:
            server.should_exit = True
            thread.join(5)
            raise RuntimeError('FastAPI no completó su ciclo de arranque')
        try:
            yield f'http://127.0.0.1:{port}'
        finally:
            server.should_exit = True
            thread.join(15)
            if thread.is_alive():
                raise RuntimeError('FastAPI no liberó sus recursos al detenerse')

    @staticmethod
    def request(base, path, method='GET', headers=None):
        request = urllib.request.Request(base + path, method=method,
                                         data=b'' if method == 'POST' else None, headers=headers or {})
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read()), response.headers

    def test_control_desactivado_por_defecto_y_configuracion_obligatoria(self):
        import api
        import app as fastapi_entry

        fixture_data = api.cargar(Path(__file__).with_name('fixtures') / 'datos')
        with mock.patch.dict(os.environ, {}, clear=True):
            application = fastapi_entry.create_app(data=fixture_data)

            async def exercise_lifespan():
                async with application.router.lifespan_context(application):
                    self.assertFalse(application.state.control_mode)
                    self.assertFalse(hasattr(application.state, 'updates'))

            asyncio.run(exercise_lifespan())
            self.assertFalse(application.state.control_mode)

        settings = {
            'SPORTS_DATA_CONTROL': '1',
            'SPORTS_DATA_STORAGE': 'sql',
            'SPORTS_DATA_DB': str(self.fixture.base),
            'SPORTS_DATA_WORKERS': '1',
        }
        with mock.patch.dict(os.environ, settings, clear=True):
            with self.assertRaisesRegex(ValueError, 'RJ_CONTROL_SECRET_FILE'):
                fastapi_entry._load_control(ExitStack())

        settings.update(WEB_CONCURRENCY='2', RJ_CONTROL_SECRET_FILE=str(self.control_secret))
        with mock.patch.dict(os.environ, settings, clear=True):
            with self.assertRaisesRegex(ValueError, 'un worker'):
                fastapi_entry._load_control(ExitStack())

        settings.update(WEB_CONCURRENCY='1', SPORTS_DATA_WORKERS='1')
        with (mock.patch.dict(os.environ, settings, clear=True),
              mock.patch('multiprocessing.parent_process', return_value=object())):
            with self.assertRaisesRegex(ValueError, 'procesos worker'):
                fastapi_entry._load_control(ExitStack())

    def test_http_actualizaciones_autenticacion_idempotencia_cancelacion_y_generaciones(self):
        from app import UPDATE_PREFIX as PREFIX

        auth = {'Authorization': 'Bearer ' + self.token}
        with self.running() as (base, application):
            for headers in ({}, {'Authorization': 'Bearer invalid'}):
                response = self.request(base, PREFIX + '/current', headers=headers)
                self.assertEqual((response[0], response[1]), (401, {'error': 'unauthorized'}))
            self.assertEqual(self.request(base, PREFIX, 'POST', auth)[0], 400)
            invalid = dict(auth, **{'Idempotency-Key': 'not-a-uuid'})
            self.assertEqual(self.request(base, PREFIX, 'POST', invalid)[:2],
                             (400, {'error': 'invalid_request'}))

            self.client_impl.gate.clear()
            key = str(uuid.uuid4())
            headers = dict(auth, **{'Idempotency-Key': key})
            started = self.request(base, PREFIX, 'POST', headers)
            self.assertEqual(started[0], 202)
            ident = started[1]['job']['id']
            phase(application.state.updates, ident, 'acquiring')
            self.assertEqual(self.request(base, PREFIX + '/' + ident, headers=auth)[1]['status'], 'running')
            repeated = self.request(base, PREFIX, 'POST', headers)
            self.assertEqual((repeated[0], repeated[1]['job']['id'], repeated[1]['reused']), (200, ident, True))
            self.assertEqual(self.request(base, PREFIX, 'POST',
                                          dict(auth, **{'Idempotency-Key': str(uuid.uuid4())}))[:2],
                             (200, repeated[1]))
            self.client_impl.gate.set()
            finished = wait(application.state.updates, ident)
            self.assertEqual((finished['status'], finished['published']), ('completed', True))
            current = self.request(base, PREFIX + '/current', headers=auth)
            self.assertEqual((current[0], current[1]['last_job']['status']), (200, 'completed'))
            self.assertEqual(self.request(base, PREFIX + '/' + ident + '/cancel', 'POST', auth)[0], 409)
            self.assertEqual(self.request(base, PREFIX + '/' + str(uuid.uuid4()), headers=auth)[0], 404)
            self.assertEqual(self.request(base, '/internal/v1/updates/invalid', headers=auth)[0], 404)
            self.assertEqual(self.request(base, '/outside', 'POST', auth)[:2],
                             (404, {'error': 'Ruta inexistente'}))

            self.client_impl.gate.clear()
            cancel_headers = dict(auth, **{'Idempotency-Key': str(uuid.uuid4())})
            queued = self.request(base, PREFIX, 'POST', cancel_headers)
            self.assertEqual(queued[0], 202)
            cancel_id = queued[1]['job']['id']
            phase(application.state.updates, cancel_id, 'acquiring')
            cancel_path = PREFIX + '/' + cancel_id + '/cancel'
            self.assertEqual(self.request(base, cancel_path, 'POST', auth)[0], 202)
            cancelled = wait(application.state.updates, cancel_id)
            self.assertEqual((cancelled['status'], cancelled['error']['code'], cancelled['published']),
                             ('cancelled', 'CANCELLED', False))

        with bloquear(self.fixture.base):
            self.assertTrue(self.fixture.base.is_file())

    def test_fallos_y_consultas_durante_publicacion_conservan_una_generacion(self):
        from app import UPDATE_PREFIX as PREFIX

        paused, resume = threading.Event(), threading.Event()

        def frontier(point):
            if point == 'principal_replaced' and not paused.is_set():
                paused.set()
                if not resume.wait(10):
                    raise TimeoutError('release publication fixture')

        auth = {'Authorization': 'Bearer ' + self.token}
        with self.running(frontier=frontier) as (base, application):
            for mode, expected in (('refused', 'ACQUISITION_FAILED'), ('tampered', 'BUNDLE_REJECTED')):
                self.client_impl.mode = mode
                started = self.request(base, PREFIX, 'POST', dict(auth, **{'Idempotency-Key': str(uuid.uuid4())}))
                self.assertEqual(started[0], 202)
                done = wait(application.state.updates, started[1]['job']['id'])
                self.assertEqual((done['status'], done['error']['code']), ('failed', expected))
                self.assertEqual(self.request(base, '/api/v1/matches/999')[0], 404)

            self.client_impl.mode = 'ok'
            started = self.request(base, PREFIX, 'POST', dict(auth, **{'Idempotency-Key': str(uuid.uuid4())}))
            self.assertEqual(started[0], 202)
            self.assertTrue(paused.wait(10))
            self.assertEqual(self.request(base, '/api/v1/matches/999')[0], 404)

            def read_new_match(_):
                return self.request(base, '/api/v1/matches/999')[0]

            with ThreadPoolExecutor(max_workers=4) as pool:
                statuses = list(pool.map(read_new_match, range(8)))
            self.assertEqual(statuses, [404] * 8)
            resume.set()
            done = wait(application.state.updates, started[1]['job']['id'])
            self.assertEqual((done['status'], done['published']), ('completed', True))
            self.assertEqual(self.request(base, '/api/v1/matches/999')[0], 200)

    def test_reintenta_un_bundle_verificado_tras_fallo_de_publicacion(self):
        from app import UPDATE_PREFIX as PREFIX

        injected = []

        def frontier(point):
            if point == 'candidate_committed' and not injected:
                injected.append(point)
                raise RuntimeError('fallo de publicación inyectado')

        auth = {'Authorization': 'Bearer ' + self.token}
        with self.running(frontier=frontier) as (base, application):
            first = self.request(base, PREFIX, 'POST', dict(auth, **{'Idempotency-Key': str(uuid.uuid4())}))
            first_job = wait(application.state.updates, first[1]['job']['id'])
            self.assertEqual((first_job['status'], first_job['error']['code'], first_job['resumable']),
                             ('failed', 'PUBLICATION_FAILED', True))
            request_id = first_job['request_id']
            self.assertEqual(self.request(base, '/api/v1/matches/999')[0], 404)

            retry = self.request(base, PREFIX, 'POST', dict(auth, **{'Idempotency-Key': str(uuid.uuid4())}))
            retry_job = wait(application.state.updates, retry[1]['job']['id'])
            self.assertEqual(retry_job['request_id'], request_id)
            self.assertEqual((retry_job['status'], retry_job['result']['reused_bundle']), ('completed', True))
            self.assertEqual(self.request(base, '/api/v1/matches/999')[0], 200)

    def test_lifespan_reconcilia_publicacion_confirmada_tras_reinicio(self):
        from app import UPDATE_PREFIX as PREFIX

        auth = {'Authorization': 'Bearer ' + self.token}
        with self.running() as (base, application):
            started = self.request(base, PREFIX, 'POST', dict(auth, **{'Idempotency-Key': str(uuid.uuid4())}))
            item = wait(application.state.updates, started[1]['job']['id'])
            self.assertEqual(item['status'], 'completed')
            generation = application.state.coordinator.capturar().id

            # Simulate a process exit after the coordinator durably confirmed its generation but before
            # Actualizaciones persisted the final response for the job.
            interrupted = dict(item, status='running', phase='publishing', finished_at=None)
            application.state.updates.save(interrupted)

        with self.running() as (base, application):
            recovered = application.state.updates.get(item['id'])
            self.assertEqual((recovered['status'], recovered['published']), ('completed', True))
            self.assertIsNone(application.state.updates.actual()['active_job'])
            self.assertEqual(application.state.coordinator.capturar().id, generation)
            self.assertEqual(self.request(base, '/api/v1/matches/999')[0], 200)


if __name__ == '__main__':
    unittest.main()
