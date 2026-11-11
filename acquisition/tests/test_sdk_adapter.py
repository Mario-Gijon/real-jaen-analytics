"""Pruebas del adaptador del SDK (acquisition.sdk_adapter) contra el sitio de prueba de 127.0.0.1, sin red externa.

Cada escenario corre en un proceso nuevo (el SDK guarda estado global) con el scraper y el navegador reales:
    python test_sdk_adapter.py                         todas las pruebas
    python test_sdk_adapter.py scenario NOMBRE CARPETA  un escenario; imprime su informe JSON
Entorno: PYTHONPATH con la API y el scraper; RJ_SDK_DATA (carpeta con round_1.json y raw-*.jsonl de una descarga
real; sin ella se usan cuerpos mínimos que no son datos deportivos); RJ_REQUIRE_SANDBOX=1 en Linux para exigir el
sandbox de Chromium; RJ_TEST_CERT y RJ_TEST_KEY (certificado no confiable) para las pruebas TLS, que sin ellos usan
openssl si existe o se omiten.
"""
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sdk_fixture import SERVER_ERROR, SERVER_ERROR_THEN_FORBIDDEN, FixtureSite, load_downloaded  # noqa: E402

PATHS = ('/unique-tournament/17073/season/97382/events/round/1', '/event/16599363', '/event/16599363/lineups',
         '/event/16599363/statistics', '/event/16599363/incidents')
MINIMAL = {PATHS[0]: b'{"events":[]}', PATHS[1]: b'{"event":{"id":16599363}}', PATHS[2]: b'{"confirmed":true}',
           PATHS[3]: b'{"statistics":[]}', PATHS[4]: b'{"incidents":[]}'}


def routes():
    folder = os.environ.get('RJ_SDK_DATA')
    return load_downloaded(folder) if folder else dict(MINIMAL)


def certificate(folder):
    if os.environ.get('RJ_TEST_CERT'):
        return os.environ['RJ_TEST_CERT'], os.environ['RJ_TEST_KEY']
    if not shutil.which('openssl'):
        return None
    cert, key = Path(folder) / 'cert.pem', Path(folder) / 'key.pem'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '2', '-subj', '/CN=127.0.0.1',
                    '-addext', 'subjectAltName=IP:127.0.0.1', '-keyout', str(key), '-out', str(cert)],
                   check=True, capture_output=True)
    return str(cert), str(key)


def run_scenario(name, folder):
    """Un escenario en este proceso: configura el SDK, instala el adaptador, pide las rutas y cierra."""
    folder = Path(folder)
    from acquisition.sdk_adapter import SdkAdapter, configure_environment
    configure_environment(folder / 'sdk')
    profile = folder / 'profile'
    profile.mkdir()
    sandbox = os.environ.get('RJ_REQUIRE_SANDBOX') == '1'
    cert = certificate(folder) if name.startswith('tls') else None
    if name.startswith('tls') and cert is None:
        return dict(skipped='sin certificado de prueba')
    kind = 'challenge' if name in ('tls-navegador', 'tls-curl', 'perfil-ocupado', 'ruta-no-permitida', 'servidor-500',
                                 'servidor-500-prohibido') else name
    served = routes()
    if name == 'servidor-500':
        served[PATHS[1]] = SERVER_ERROR
    if name == 'servidor-500-prohibido':
        served[PATHS[1]] = SERVER_ERROR_THEN_FORBIDDEN
    statuses, errors = [], []
    with contextlib.ExitStack() as stack:
        # tls-curl: la API usa el certificado no confiable; tls-navegador: solo las páginas del sitio lo usan.
        api_site = stack.enter_context(FixtureSite(served, scenario=kind, cert=cert if name == 'tls-curl' else None))
        page_site = stack.enter_context(FixtureSite({}, scenario=kind, cert=cert)) if name == 'tls-navegador' else None
        origin = page_site.origin if page_site else api_site.origin
        adapter = SdkAdapter(profile, PATHS, api=api_site.api, site=origin, require_sandbox=sandbox, local_test=True)
        adapter.install()
        if name == 'perfil-ocupado':
            from sofascore_scraper.client import profile_lock
            profile_lock.profile_owner = lambda path: profile_lock.ProfileOwner()
        try:
            for path in ['/event/1'] if name == 'ruta-no-permitida' else PATHS:
                try:
                    status, _, _ = adapter.request(path)
                except OSError as e:
                    errors.append(str(e)[:200])
                    break
                statuses.append(status)
                if status != 200:
                    break
        finally:
            adapter.close()
    siblings = sorted(p.name for p in folder.iterdir() if p.name.startswith('profile-'))
    return dict(statuses=statuses, errors=errors, report=adapter.report(), api_hits=api_site.api_hits(),
                site_hits=api_site.hits, page_hits=page_site.hits if page_site else [],
                tls_rejected=api_site.tls_rejected + (page_site.tls_rejected if page_site else []), siblings=siblings)


class Adaptador(unittest.TestCase):
    def scenario(self, name):
        with tempfile.TemporaryDirectory(prefix='sdk-' + name + '-') as folder:
            env = dict(os.environ, PYTHONUTF8='1')
            p = subprocess.run([sys.executable, '-B', __file__, 'scenario', name, folder], capture_output=True,
                               text=True, encoding='utf-8', errors='replace', timeout=240, env=env)
            self.assertEqual(p.returncode, 0, p.stderr[-3000:])
            return json.loads(p.stdout.strip().splitlines()[-1])

    def sends(self, r):
        return [(s['channel'], s['path'], s['status']) for s in r['report']['sends'] if s['sent']]

    def test_challenge_resuelto_con_trafico_reducido(self):
        r = self.scenario('challenge')
        rep = r['report']
        self.assertEqual(r['statuses'], [200] * 5)
        self.assertIsNone(rep['stopped'])
        self.assertEqual(self.sends(r)[:2], [('curl', PATHS[0], 403), ('bridge', PATHS[0], 200)])
        self.assertEqual(len(r['api_hits']), len(self.sends(r)), 'cada envío anotado llegó al sitio y nada más')
        self.assertEqual({h['path'] for h in r['api_hits']}, {'/api/v1' + p for p in PATHS})
        [session] = rep['sessions']
        self.assertEqual((session['ignore_https_errors'], session['service_workers'], session['permissions'],
                          session['chromium_sandbox']), (False, 'block', [], True))
        self.assertEqual(Path(session['user_data_dir']).name, 'profile')
        self.assertEqual((rep['launches'], r['siblings']), (1, []))
        self.assertEqual(rep['browser']['api'], sum(1 for s in self.sends(r) if s[0] == 'bridge'),
                         'solo los fetch() propios; la página no pide la API')
        self.assertEqual({d[1] for d in rep['browser']['documents']}, {'/captcha.html', '/robots.txt'})
        self.assertIn('captcha-page-parked', [e['kind'] for e in rep['events']])
        if os.environ.get('RJ_REQUIRE_SANDBOX') == '1':
            self.assertEqual(rep['sandbox'], {'namespaces': True, 'seccomp': True, 'proc_verified': True})

    def test_error_del_servidor_tras_el_challenge_no_es_un_rechazo(self):
        # The SDK retries a browser 5xx by curl, meets the normal challenge and reports 403: it is still a 500.
        r = self.scenario('servidor-500')
        self.assertEqual(r['statuses'], [200, 500])
        self.assertIsNone(r['report']['stopped'])
        self.assertIn(('bridge', PATHS[1], 500), self.sends(r))

    def test_403_sin_challenge_tras_el_error_del_servidor_detiene(self):
        # Same 500 in the browser, but the retry meets a refusal without a challenge: everything stops there.
        r = self.scenario('servidor-500-prohibido')
        self.assertEqual(r['statuses'], [200, 403])
        self.assertIsNotNone(r['report']['stopped'])
        sends = [(s['channel'], s['path'], s['status'], s['challenge']) for s in r['report']['sends'] if s['sent']]
        self.assertIn(('bridge', PATHS[1], 500, False), sends)
        self.assertEqual(sends[-1], ('curl', PATHS[1], 403, False), 'the last send is the refusal, nothing after it')
        self.assertEqual(len(r['api_hits']), len(sends))

    def test_403_sin_challenge_detiene_sin_navegador(self):
        r = self.scenario('prohibido')
        self.assertEqual((r['statuses'], len(r['api_hits']), r['report']['launches']), ([403], 1, 0))
        self.assertIsNotNone(r['report']['stopped'])

    def test_challenge_irresoluble(self):
        r = self.scenario('irresoluble')
        self.assertEqual(r['statuses'], [403])
        self.assertLessEqual(len(r['api_hits']), 3)
        self.assertEqual(r['report']['launches'], 1)

    def test_429_en_el_navegador_detiene(self):
        r = self.scenario('limite-navegador')
        self.assertEqual(r['statuses'], [200, 429])
        self.assertEqual(sum(1 for h in r['api_hits'] if h['status'] == 429), 1)
        self.assertEqual(r['api_hits'][-1]['status'], 429, 'nada sale después del 429')

    def test_ruta_no_permitida_no_sale(self):
        r = self.scenario('ruta-no-permitida')
        self.assertEqual((r['statuses'], r['api_hits'], r['site_hits']), ([403], [], []))

    def test_perfil_ocupado_sin_perfil_temporal(self):
        r = self.scenario('perfil-ocupado')
        self.assertEqual((r['statuses'], r['report']['launches'], r['siblings']), ([403], 0, []))
        self.assertIn('perfil secundario', r['report']['stopped'])

    def test_tls_del_navegador_verificado(self):
        r = self.scenario('tls-navegador')
        if r.get('skipped'):
            self.skipTest(r['skipped'])
        self.assertEqual(r['statuses'], [403])
        self.assertEqual(r['page_hits'], [], 'el sitio HTTPS no confiable no recibe ninguna petición HTTP')
        self.assertTrue(r['tls_rejected'])

    def test_tls_de_curl_verificado(self):
        r = self.scenario('tls-curl')
        if r.get('skipped'):
            self.skipTest(r['skipped'])
        self.assertTrue(r['errors'])
        self.assertEqual(r['site_hits'], [])


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == 'scenario':
        print(json.dumps(run_scenario(sys.argv[2], sys.argv[3]), ensure_ascii=False, default=str))
    else:
        unittest.main(verbosity=2)
