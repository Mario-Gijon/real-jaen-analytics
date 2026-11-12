"""Contract of the acquisition FastAPI and its backend client: loopback HTTP, fake runner, no SDK or network.

Runs in the acquisition test image (the service needs fcntl and persistencia.bundle)."""
import hashlib
import json
from pathlib import Path
import shutil
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid

try:
    import uvicorn
except ImportError as exc:
    raise unittest.SkipTest('instala requirements-fastapi.txt para las pruebas de acquisition FastAPI') from exc

from acquisition.app import create_app
from acquisition.service import Jobs, PREFIX, configuration
from persistencia.adquisicion import AdquisicionRechazada, Cliente
from persistencia.bundle import fingerprint

RECIPE = 'sofascore-pilot-16599363-v1'
GROUP_RECIPE = 'sofascore-group2-2627-v1'


def baseline(generation=None):
    value = dict(schema=1, generation_id=generation or str(uuid.uuid4()), teams=[2841, 135672, *range(90001, 90019)],
                 events=[dict(id=16599363, current=True, replaced_by=None, home=2841, away=135672, status='finished',
                              tournament_id=94191, season_id=97382)], records=[])
    return dict(value, sha256=fingerprint(value))


class Runner:
    """Writes what acquisition.job writes. mode: ok, fail, wait (until cancelled or released), writable, mismatch."""
    def __init__(self):
        self.mode, self.calls, self.release = 'ok', 0, threading.Event()

    def __call__(self, folder, spec, cancelled):
        self.calls += 1
        if self.mode == 'wait':
            while not cancelled() and not self.release.is_set():
                time.sleep(.02)
            if cancelled():
                return 1
        if self.mode == 'fail':
            (folder / 'report.json').write_text(json.dumps(dict(error='Stopped: 403', descriptor=None)))
            return 1
        body = json.dumps(dict(job=spec['job_id'], request=spec['request_id'])).encode()
        (folder / 'bundle.json').write_bytes(body)
        if self.mode != 'writable':
            (folder / 'bundle.json').chmod(0o400)
        descriptor = dict(bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
        if self.mode == 'mismatch':
            descriptor['sha256'] = '0' * 64
        (folder / 'report.json').write_text(json.dumps(dict(descriptor=descriptor, sends=[], requests=[],
                                                             budget_exhausted=False,
                                                             connections=dict(sockets=[]), browser=dict(total=0))))
        return 0


class Protocol(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.token = b'a' * 64
        self.file = self.root / 'secret'
        self.file.write_bytes(self.token)
        self.servers = []
        self.addCleanup(self.stop_all)
        self.runner = Runner()
        self.start('jobs')

    def start(self, name):
        self.jobs = Jobs(self.root / name, provider='fixture-site', runner=self.runner)
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        sock.listen(128)
        port = sock.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(create_app(jobs=self.jobs, token=self.token),
                                                    host='127.0.0.1', port=port,
                                                    log_level='critical', access_log=False))
        thread = threading.Thread(target=self.server.run, kwargs={'sockets': [sock]}, daemon=True)
        self.servers.append((self.server, thread, self.runner))
        thread.start()
        deadline = time.monotonic() + 5
        while not self.server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(self.server.started, 'Uvicorn did not start')
        self.url = f'http://127.0.0.1:{port}'
        self.client = Cliente(self.url, self.file, modo='prueba')

    def stop_all(self):
        for server, thread, runner in self.servers:
            runner.release.set()
            server.should_exit = True
            thread.join(timeout=10)

    def post(self, body, auth=None, raw=None):
        req = urllib.request.Request(self.url + PREFIX, data=raw if raw is not None else json.dumps(body).encode(),
                                     headers={'Authorization': auth or 'Bearer ' + self.token.decode()})
        return urllib.request.urlopen(req, timeout=5)

    def completed(self, inventory=None):
        request = str(uuid.uuid4())
        job = self.client.solicitar(request, RECIPE, inventory or baseline(), operation_id=request)
        return request, self.client.esperar(job['id'], timeout=10)

    def test_authorization_wrong_secret_and_no_echo(self):
        for auth in ('Bearer wrong-secret', 'Basic ' + self.token.decode()):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.post({}, auth)
            self.assertEqual(error.exception.code, 401)
            self.assertNotIn(self.token, error.exception.read())
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(self.url + '/health?probe=1', timeout=5)
        self.assertEqual(error.exception.code, 401)

    def test_health_is_public_and_internal_routes_are_not(self):
        with urllib.request.urlopen(self.url + '/health', timeout=5) as response:
            self.assertEqual((response.status, response.read()), (200, b'{"provider":"fixture-site","status":"ok"}'))
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(self.url + PREFIX, timeout=5)
        self.assertEqual(error.exception.code, 401)

    def test_arbitrary_parameters_and_invalid_inventory_rejected(self):
        good = dict(request_id=str(uuid.uuid4()), operation_id=str(uuid.uuid4()), recipe_id=RECIPE, inventory=baseline())
        tampered = baseline(); tampered['teams'] = tampered['teams'][:19]
        cases = [dict(good, command='x'), dict(good, url='http://x'), dict(good, recipe_id='fixture-complete-v1'),
                 dict(good, request_id='not-a-uuid'), dict(good, inventory=[]), dict(good, inventory=tampered)]
        for body in cases:
            with self.subTest(body=sorted(body)), self.assertRaises(urllib.error.HTTPError) as error:
                self.post(body)
            self.assertEqual(error.exception.code, 400)
        raw = json.dumps(good).encode().replace(b'{', b'{"request_id":"x",', 1)  # duplicate key
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.post(None, raw=raw)
        self.assertEqual(error.exception.code, 400)
        self.assertEqual(self.runner.calls, 0)

    def test_completed_bundle_served_sealed_with_etag(self):
        request, state = self.completed()
        self.assertEqual(state['state'], 'completed')
        req = urllib.request.Request(self.url + PREFIX + '/' + state['id'] + '/bundle',
                                     headers={'Authorization': 'Bearer ' + self.token.decode()})
        with urllib.request.urlopen(req, timeout=5) as response:
            raw_bundle = response.read()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers['Content-Type'], 'application/json')
            self.assertEqual(response.headers['ETag'], '"' + state['bundle']['sha256'] + '"')
            self.assertEqual(int(response.headers['Content-Length']), state['bundle']['bytes'])
        self.assertEqual(raw_bundle, (self.jobs.root / 'work' / state['id'] / 'bundle.json').read_bytes())
        body = self.client.descargar(state['id'], state['bundle'])
        self.assertEqual(json.loads(body)['request'], request)
        with self.assertRaises(AdquisicionRechazada):
            self.client.descargar(state['id'], dict(state['bundle'], sha256='1' * 64))
        report = self.client.informe(state['id'])
        self.assertEqual(set(report), {'provider', 'started_at', 'finished_at', 'error', 'cancelled', 'stopped', 'limits',
                                       'sends', 'requests', 'sandbox', 'launches', 'connections', 'browser',
                                       'operation_budget', 'budget_exhausted', 'continuation_allowed'})

    def test_failed_group_bundle_does_not_advertise_batch_continuation(self):
        from acquisition.job import may_continue_group
        self.assertFalse(may_continue_group(error='Rejected: Incompatible contract', descriptor=None,
                                            report=dict(stopped=None, budget_exhausted=False),
                                            operation_state=dict(remaining=9)))
        descriptor = dict(bytes=100, sha256='a' * 64)
        self.assertTrue(may_continue_group(error=None, descriptor=descriptor,
                                           report=dict(stopped=None, budget_exhausted=False),
                                           operation_state=dict(remaining=9)))
        self.assertFalse(may_continue_group(error=None, descriptor=descriptor,
                                            report=dict(stopped=None, budget_exhausted=True),
                                            operation_state=dict(remaining=0)))

    def test_duplicate_request_returns_original_and_conflicts(self):
        inventory = baseline()
        request, state = self.completed(inventory)
        again = self.client.solicitar(request, RECIPE, inventory)
        self.assertEqual((again['id'], again['bundle']), (state['id'], state['bundle']))
        with self.assertRaises(AdquisicionRechazada):
            self.client.solicitar(request, RECIPE, baseline())  # same key, other inventory
        with self.assertRaises(AdquisicionRechazada):
            self.client.solicitar(request, RECIPE, inventory, operation_id=str(uuid.uuid4()))
        self.assertEqual(self.runner.calls, 1)

    def test_group_operation_budget_is_initialized_before_send_and_survives_service_restart(self):
        from acquisition.operation_budget import OperationBudget
        from acquisition.service import Conflict
        root = self.root / 'manual-operation-jobs'
        operation_id = str(uuid.uuid4())
        runner = Runner()

        def completed(jobs, request_id):
            item, _ = jobs.submit(request_id, GROUP_RECIPE, baseline(), operation_id)
            deadline = time.monotonic() + 5
            while jobs.get(item['id'])['state'] not in ('completed', 'failed') and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertEqual(jobs.get(item['id'])['state'], 'completed')
            self.assertEqual(jobs.get(item['id'])['operation_id'], operation_id)

        first = Jobs(root, provider='fixture-site', runner=runner)
        try:
            completed(first, str(uuid.uuid4()))
            ledger = OperationBudget(root / 'operations', operation_id, 50)
            for index in range(47):
                self.assertTrue(ledger.reserve_send(job_id='batch-one', channel='curl', path=f'/event/{index}')['allowed'])
            self.assertEqual(ledger.snapshot()['used'], 47)
        finally:
            first.close()

        second = Jobs(root, provider='fixture-site', runner=runner)
        try:
            completed(second, str(uuid.uuid4()))
            state = OperationBudget(root / 'operations', operation_id, 50).snapshot()
            self.assertEqual((state['used'], state['remaining']), (47, 3))
            self.assertEqual(sum(OperationBudget(root / 'operations', operation_id, 50).reserve_send(
                job_id='last-batch', channel='browser-page', path='/captcha.html')['allowed'] for _ in range(4)), 3)
        finally:
            second.close()

        (root / 'operations' / (operation_id + '.json')).unlink()
        third = Jobs(root, provider='fixture-site', runner=runner)
        try:
            with self.assertRaises(Conflict):
                third.submit(str(uuid.uuid4()), GROUP_RECIPE, baseline(), operation_id)
        finally:
            third.close()

    def test_one_active_job_and_cancellation(self):
        self.runner.mode = 'wait'
        job = self.client.solicitar(str(uuid.uuid4()), RECIPE, baseline())
        with self.assertRaises(AdquisicionRechazada):
            self.client.solicitar(str(uuid.uuid4()), RECIPE, baseline())
        self.client.cancelar(job['id'])
        state = self.client.esperar(job['id'], timeout=10)
        self.assertEqual(state['state'], 'cancelled')
        self.assertEqual(self.client.cancelar(job['id'])['state'], 'cancelled')
        with self.assertRaises(AdquisicionRechazada):
            self.client.descargar(job['id'], dict(bytes=1, sha256='0' * 64))

    def test_failed_or_unsealed_jobs_never_provide_a_bundle(self):
        for mode, error in (('fail', 'provider_failed'), ('writable', 'bundle_not_sealed'), ('mismatch', 'bundle_not_sealed')):
            with self.subTest(mode=mode):
                self.runner.mode = mode
                _, state = self.completed()
                self.assertEqual((state['state'], state['error']), ('failed', error))
                with self.assertRaises(AdquisicionRechazada):
                    self.client.descargar(state['id'], dict(bytes=1, sha256='0' * 64))

    def test_sealed_file_corruption_not_served(self):
        _, state = self.completed()
        path = self.jobs.root / 'work' / state['id'] / 'bundle.json'
        path.chmod(0o600); path.write_bytes(path.read_bytes() + b'x')
        with self.assertRaises(AdquisicionRechazada):
            self.client.descargar(state['id'], state['bundle'])

    def test_restart_marks_interrupted_without_resume(self):
        self.runner.mode = 'wait'
        inventory = baseline()
        request = str(uuid.uuid4())
        job = self.client.solicitar(request, RECIPE, inventory)
        while self.client.estado(job['id'])['state'] == 'queued':
            time.sleep(.02)
        # Crash image: the durable store as it is while the job runs, opened by a new process.
        (self.root / 'restarted').mkdir()
        shutil.copyfile(self.jobs.root / 'jobs.sqlite', self.root / 'restarted' / 'jobs.sqlite')
        self.runner = Runner()
        self.start('restarted')
        self.assertEqual(self.client.estado(job['id'])['state'], 'interrupted')
        self.assertEqual(self.client.solicitar(request, RECIPE, inventory)['state'], 'interrupted')
        self.assertEqual(self.runner.calls, 0)

    def test_client_configuration(self):
        for url, modo in (('http://example.org:8002', 'real'), ('http://127.0.0.1:1', 'real'), ('https://acquisition', 'real'),
                          ('http://acquisition:8002', 'simulada'), ('http://user:p@acquisition:8002', 'real')):
            with self.subTest(url=url, modo=modo), self.assertRaises(AdquisicionRechazada):
                Cliente(url, self.file, modo=modo)
        Cliente('http://acquisition:8002', self.file)

    def test_service_requires_an_explicit_provider(self):
        for env in ({}, {'ACQ_PROVIDER': 'sofascore'}, {'ACQ_PROVIDER': 'sofascore', 'ACQ_ENABLE_REAL': '1', 'ACQ_TEST_MODE': '1'},
                    {'ACQ_PROVIDER': 'fixture-site', 'ACQ_TEST_MODE': '1'}, {'ACQ_PROVIDER': 'simulated-local-v1', 'ACQ_TEST_MODE': '1'}):
            with self.subTest(env=env), self.assertRaises(SystemExit):
                configuration(env)
        self.assertEqual(configuration({'ACQ_PROVIDER': 'sofascore', 'ACQ_ENABLE_REAL': '1'}),
                         ('sofascore', dict(recent_round_count=3, max_total_sends=50)))
        with self.assertRaises(ValueError):
            Jobs(self.root / 'other', provider='sofascore')  # a real job process needs the profile owner


class Producers(unittest.TestCase):
    def test_current_producer_is_approved(self):
        from acquisition.recipe import producer_rules
        from persistencia.adquisicion import productores_aprobados
        self.assertIn(producer_rules(), productores_aprobados())


if __name__ == '__main__':
    unittest.main(verbosity=2)
