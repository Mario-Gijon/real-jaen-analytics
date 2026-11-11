"""Flujo normal del SDK del scraper para la adquisición: curl_cffi y, ante un challenge, su BrowserBridge.

No modifica el scraper (revisión fijada): ajusta en memoria lo documentado en docs/adquisicion-sofascore.md.
- Navegador con verificación HTTPS, sin service workers ni permisos y con el sandbox de Chromium activo.
- Un único perfil, el indicado (en Linux, la generación de la sesión gestionada): nunca un perfil temporal.
- Tráfico reducido: página ligera del mismo origen, la del captcha aparcada tras obtener la cookie, dominios de
  terceros bloqueados en las páginas de Scrapling y sin Media Router.
- Cada envío físico (curl o fetch() del navegador) pasa por una lista cerrada de rutas y un presupuesto; un 403 sin
  challenge, un challenge repetido en la página, un 429/503, un fallo del navegador o un intento fuera de la lista
  detienen el adaptador y todo envío posterior se rechaza sin salir a la red.

`request(path)` devuelve (estado, cabeceras, cuerpo) para que el transporte que lo usa aplique su presupuesto durable,
sus reintentos y su política de paradas. Un proceso, un adaptador: el SDK guarda estado global (navegador, modo
«primero el navegador», token). configure_environment() debe llamarse antes de importar el SDK.
"""
import asyncio
import contextlib
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit

SITE = 'https://www.sofascore.com'
API = SITE + '/api/v1'
LIGHT_PATH = '/robots.txt'
PROBE_PATH = '/unique-tournament/17/seasons'  # comprobación propia del bridge tras resolver un challenge en la página
TLS = {'ignore_https_errors': False, 'service_workers': 'block', 'permissions': []}
# Terceros vistos el 27-10 (publicidad, analítica, mensajería). Scrapling los bloquea solo en las páginas de su fetch().
BLOCKED_DOMAINS = frozenset({
    'doubleclick.net', 'googlesyndication.com', 'googletagmanager.com', 'google-analytics.com',
    'fundingchoicesmessages.google.com', 'accounts.google.com', 'firebase.googleapis.com',
    'firebaseinstallations.googleapis.com', 'hbwrapper.com', 'digitaloceanspaces.com', 'confiant-integrations.net',
    'adverge.ai', 'liadm.com', 'jsdelivr.net', 'googleusercontent.com', 'fonts.googleapis.com', 'fonts.gstatic.com',
})
REDUCED_FLAGS = ('--disable-features=MediaRouter,GlobalMediaControls',)
# En un ensayo local, todo lo que no sea 127.0.0.1 va a un proxy inexistente: nada sale del equipo.
LOCAL_FLAGS = ('--proxy-server=http://127.0.0.1:9', '--proxy-bypass-list=127.0.0.1;localhost')
SETTINGS = {
    'SOFASCORE_CONFIG': 'none', 'SOFASCORE_CLIENT__RETRIES': '0', 'SOFASCORE_CLIENT__RATE': '1',
    'SOFASCORE_CLIENT__MAX_CONCURRENT': '1', 'SOFASCORE_CLIENT__TIMEOUT_SECONDS': '10',
    'SOFASCORE_CLIENT__USE_PROXY': 'false', 'SOFASCORE_CLIENT__CAPTCHA_TOKEN': '',
    'SOFASCORE_CLIENT__BROWSER_HEADED': 'false', 'SOFASCORE_DEFAULTS__SLICES': 'statistics,lineups,incidents',
}
_installed = False


class AdapterStopped(RuntimeError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def configure_environment(workdir):
    """Configuración propia del SDK bajo `workdir` (datos, log, throttle y configuración), nunca la del usuario ni la
    de otro proceso. Debe llamarse antes de importar el SDK: parte de sus ajustes se leen al importarlo."""
    if 'sofascore_scraper' in sys.modules:
        raise AdapterStopped('El entorno del SDK se configura antes de importarlo')
    workdir = Path(workdir)
    folders = {name: workdir / name for name in ('config', 'data', 'log', 'throttle')}
    for folder in folders.values():
        folder.mkdir(parents=True, exist_ok=True)
    (workdir / 'empty.env').touch()
    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'CURL_CA_BUNDLE', 'SSL_CERT_FILE', 'REQUESTS_CA_BUNDLE'):
        if os.environ.get(name) or os.environ.get(name.lower()):
            raise AdapterStopped(f'Variable de proxy o certificados presente: {name}')
    os.environ.update(SETTINGS, SOFASCORE_ENV_FILE=str(workdir / 'empty.env'), SOFASCORE_CONFIG_DIR=str(folders['config']),
                      SOFASCORE_STORAGE__DATA_DIR=str(folders['data']), SOFASCORE_LOG__DIR=str(folders['log']),
                      SOFASCORE_CLIENT__THROTTLE_DIR=str(folders['throttle']))


def loopback(host):
    return host in ('127.0.0.1', 'localhost', '::1') or (host or '').startswith('127.')


class SdkAdapter:
    def __init__(self, profile, paths, *, api=API, site=SITE, max_sends=None, require_sandbox=False, local_test=False):
        api, site = api.rstrip('/'), site.rstrip('/')
        if local_test:
            if not all(urlsplit(u).scheme in ('http', 'https') and loopback(urlsplit(u).hostname) for u in (api, site)):
                raise ValueError('Un ensayo local solo admite 127.0.0.1')
        elif (api, site) != (API, SITE):
            raise ValueError('Fuera de un ensayo local solo se admite el sitio de SofaScore')
        self.profile, self.api, self.site = Path(profile), api, site
        # paths: a fixed collection, or a predicate for a recipe whose allowlist grows with what it lists (its
        # rounds, then only the matches of its scope); max_sends is then required.
        if callable(paths):
            if max_sends is None:
                raise ValueError('Un predicado de rutas exige un presupuesto explícito')
            self.permits = paths
        else:
            allowed = frozenset(paths)
            self.permits = allowed.__contains__
            max_sends = max_sends if max_sends is not None else 2 * len(allowed) + 2
        self.max_sends = max_sends
        self.require_sandbox, self.local_test = require_sandbox, local_test
        parts = urlsplit(api)
        self.api_scheme, self.api_host, self.api_prefix = parts.scheme, parts.hostname, parts.path
        self.lock = threading.Lock()
        self.stopped = None
        self.sends, self.requests, self.browser, self.events, self.sessions = [], [], [], [], []
        # Exact bodies are kept only for the current logical SDK request, then persisted by SdkTransport.
        self._captured_responses = []
        self.processes_remaining = []
        self.launches = 0
        self.page_challenges = 0
        self.sandbox = None

    # --- estado ------------------------------------------------------------------------------------------
    def stop(self, reason):
        with self.lock:
            if self.stopped is None:
                self.stopped = reason
                self.events.append(dict(at=now(), kind='stopped', detail=reason))

    def event(self, kind, detail=None):
        with self.lock:
            self.events.append(dict(at=now(), kind=kind, detail=detail))

    def remaining_sends(self):
        """Physical sends this adapter may still make (curl and the browser's fetch(), sent or not refused)."""
        with self.lock:
            return self.max_sends - len([s for s in self.sends if s.get('sent')])

    def report(self):
        with self.lock:
            hosts = {}
            for b in self.browser:
                key = f"{b['scheme']}://{b['host']}"
                hosts[key] = hosts.get(key, 0) + 1
            return dict(api=self.api, site=self.site, profile=str(self.profile), stopped=self.stopped,
                        sends=list(self.sends), requests=list(self.requests), launches=self.launches,
                        sessions=list(self.sessions), sandbox=self.sandbox, events=list(self.events),
                        browser=dict(total=len(self.browser), by_host=hosts,
                                     api=sum(1 for b in self.browser if b['api']),
                                     documents=[(b['host'], b['path'], b.get('status'), b.get('failed'))
                                                for b in self.browser if b['navigation']]),
                        processes_remaining=list(self.processes_remaining))

    # --- envíos físicos ------------------------------------------------------------------------------------
    def relative(self, url):
        p = urlsplit(url)
        if p.scheme != self.api_scheme or p.hostname != self.api_host or not p.path.startswith(self.api_prefix + '/'):
            return None
        return p.path[len(self.api_prefix):] if not p.query else None

    def before_send(self, channel, url):
        rel = self.relative(url)
        entry = dict(at=now(), channel=channel, path=rel or urlsplit(url).netloc + urlsplit(url).path)
        refused = None
        with self.lock:
            if self.stopped is not None:
                refused = 'adaptador detenido'
            elif rel is None or not (self.permits(rel) or (channel == 'bridge' and rel == PROBE_PATH)):
                refused = 'ruta no permitida'
            elif len([s for s in self.sends if s.get('sent')]) >= self.max_sends:
                refused = 'presupuesto de envíos agotado'
            entry.update(sent=refused is None, refused=refused)
            self.sends.append(entry)
        if refused is not None and refused != 'adaptador detenido':
            self.stop(f'{refused}: {entry["path"]}')
        return entry

    def after_send(self, entry, status, text=None, error=None):
        entry.update(finished_at=now(), status=status, error=error)
        challenge = status == 403 and 'challenge' in (text or '')
        entry['challenge'] = challenge
        if error is not None:
            self.stop(f'error de red ({entry["channel"]}): {error}')
        elif status == 200 or (challenge and entry['channel'] == 'curl'):
            pass  # flujo normal: un challenge de curl pasa al navegador
        elif challenge:
            with self.lock:
                self.page_challenges += 1
                repeated = self.page_challenges > 1
            if repeated:
                self.stop('challenge repetido dentro del navegador')
        elif status in (403, 429, 503):
            self.stop(f'{status} ({entry["channel"]}): {entry["path"]}')
        elif status == 0:
            self.stop(f'fallo de fetch() en el navegador: {entry["path"]}')

    def capture_response(self, url, status, body):
        rel = self.relative(url)
        if rel is None:
            return
        with self.lock:
            self._captured_responses.append((rel, status, bytes(body)))

    # --- instalación -------------------------------------------------------------------------------------
    def install(self):
        """Ajusta el SDK de este proceso. Una sola vez por proceso."""
        global _installed
        if _installed:
            raise AdapterStopped('El adaptador ya está instalado en este proceso')
        _installed = True
        from curl_cffi import requests as cffi_requests
        from curl_cffi.requests import AsyncSession
        from sofascore_scraper.client import bridge, profile_lock, transport

        transport.API_BASE_URL = self.api  # también la comprobación del bridge (api_url)
        bridge.HOME_URL = self.site + LIGHT_PATH
        bridge.CAPTCHA_URL = self.site + '/captcha.html?redirectUrl=' + quote(self.site + LIGHT_PATH, safe='')
        adapter = self

        async def no_async_curl(session, url, *a, **kw):
            # La adquisición usa el camino síncrono; la sesión asíncrona (con su warm-up) no se espera aquí.
            adapter.stop(f'sesión curl asíncrona no prevista: {urlsplit(url).netloc}')
            raise AdapterStopped('Sesión curl asíncrona no prevista')
        AsyncSession.get = no_async_curl

        original_get = cffi_requests.get

        def guarded_get(url, *a, **kw):
            if kw.get('verify') is False:
                adapter.stop('curl con verify=False')
                raise AdapterStopped('verify=False')
            entry = adapter.before_send('curl', url)
            if not entry['sent']:
                raise AdapterStopped(entry['refused'])
            try:
                response = original_get(url, *a, **kw)
            except Exception as e:
                adapter.after_send(entry, None, error=f'{type(e).__name__}: {str(e)[:160]}')
                raise
            adapter.capture_response(url, response.status_code, response.content)
            adapter.after_send(entry, response.status_code, text=response.text if response.status_code >= 400 else None)
            return response
        cffi_requests.get = guarded_get

        original_fetch = bridge.BrowserBridge._api_fetch

        async def guarded_fetch(self, url, cache_mode):
            entry = adapter.before_send('bridge', url)
            if not entry['sent']:
                return {'status': 0, 'ok': False, 'data': None, 'text': f'refused by acquisition adapter: {entry["refused"]}'}
            try:
                res = await original_fetch(self, url, cache_mode)
            except Exception as e:
                adapter.after_send(entry, None, error=f'{type(e).__name__}: {str(e)[:160]}')
                raise
            text = res.get('text')
            if isinstance(text, str):
                adapter.capture_response(url, res.get('status'), text.encode('utf-8'))
            adapter.after_send(entry, res.get('status'), text=res.get('text'))
            return res
        bridge.BrowserBridge._api_fetch = guarded_fetch

        original_launch = bridge.BrowserBridge._launch

        async def guarded_launch(self):
            with adapter.lock:
                adapter.launches += 1
                count = adapter.launches
            if adapter.stopped is not None or count > 1:
                adapter.stop('segundo arranque del navegador' if count > 1 else adapter.stopped)
                raise AdapterStopped('Arranque del navegador rechazado')
            launch_dir = self._launch_dir or self.profile_dir
            if Path(launch_dir) != adapter.profile:
                adapter.stop('el navegador no usa el perfil indicado')
                raise AdapterStopped('Perfil distinto del indicado')
            before = None
            if adapter.require_sandbox:
                from .browser import processes
                before = set(processes())
            await original_launch(self)
            adapter.event('browser-launch', str(launch_dir))
            if adapter.require_sandbox:
                from .browser import verify_sandbox
                try:
                    adapter.sandbox = verify_sandbox(before, launch_dir)
                except Exception as e:
                    adapter.stop(f'sandbox no acreditado: {e}')
                    raise
        bridge.BrowserBridge._launch = guarded_launch

        original_solve = bridge.BrowserBridge._solve_on_captcha_page

        async def guarded_solve(self):
            if adapter.stopped is not None:
                return None
            adapter.event('captcha-page')
            token = await original_solve(self)
            # Tras la espera de la cookie, la página del captcha (del pool de Scrapling) deja de ejecutar la página a
            # la que redirige. La página propia del bridge no se toca.
            for page in list(self.context.pages if self.context is not None else []):
                if page is not self.page and not page.is_closed():
                    with contextlib.suppress(Exception):
                        await page.goto('about:blank')
                        adapter.event('captcha-page-parked')
            return token
        bridge.BrowserBridge._solve_on_captcha_page = guarded_solve

        def no_secondary_profile(profile):
            adapter.stop('perfil ocupado: perfil secundario rechazado')
            raise OSError('secondary profiles are disabled by the acquisition adapter')
        profile_lock.secondary_profile = no_secondary_profile

        original_session_class = bridge._session_class

        def session_class():
            base = original_session_class()

            class AcquisitionSession(base):
                def __init__(self, **kw):
                    extra = dict(kw.pop('additional_args', None) or {}, **TLS, chromium_sandbox=True)
                    flags = list(kw.get('extra_flags') or []) + list(REDUCED_FLAGS)
                    if adapter.local_test:
                        flags += list(LOCAL_FLAGS)
                    kw.update(extra_flags=flags, blocked_domains=set(BLOCKED_DOMAINS))
                    super().__init__(additional_args=extra, **kw)
                    options = self._context_options
                    record = dict(at=now(), user_data_dir=kw.get('user_data_dir'), extra_flags=flags,
                                  chromium_sandbox=options.get('chromium_sandbox'),
                                  **{k: options.get(k) for k in TLS})
                    with adapter.lock:
                        adapter.sessions.append(record)
                    if options.get('ignore_https_errors') is not False or options.get('chromium_sandbox') is not True:
                        adapter.stop('sesión del navegador sin verificación HTTPS o sin sandbox')
                        raise AdapterStopped('Sesión del navegador no segura')

                async def start(self):
                    await super().start()
                    self.context.on('request', adapter.on_browser_request)
                    self.context.on('response', adapter.on_browser_response)
                    self.context.on('requestfailed', adapter.on_browser_failed)
            return AcquisitionSession
        bridge._session_class = session_class

        if bridge.BrowserBridge._instance is not None:
            raise AdapterStopped('El navegador del SDK ya estaba creado')
        bridge.BrowserBridge.get_instance(profile_dir=str(self.profile))
        return self

    # --- navegador -----------------------------------------------------------------------------------------
    def on_browser_request(self, request):
        p = urlsplit(request.url)
        entry = dict(at=now(), method=request.method, type=request.resource_type, scheme=p.scheme, host=p.hostname,
                     path=(p.path or '/')[:96], navigation=request.is_navigation_request(),
                     api=self.relative(request.url) is not None)
        with self.lock:
            self.browser.append(entry)
        if self.local_test and p.scheme in ('http', 'https', 'ws', 'wss') and not loopback(p.hostname):
            self.stop(f'el navegador intentó salir de loopback: {p.hostname}')

    def _match(self, url):
        p = urlsplit(url)
        for entry in reversed(self.browser):
            if entry['host'] == p.hostname and entry['path'] == (p.path or '/')[:96] and 'status' not in entry and 'failed' not in entry:
                return entry
        return None

    def on_browser_response(self, response):
        with self.lock:
            entry = self._match(response.url)
            if entry is not None:
                entry['status'] = response.status

    def on_browser_failed(self, request):
        with self.lock:
            entry = self._match(request.url)
            if entry is not None:
                entry['failed'] = (request.failure or '')[:120]

    # --- peticiones ----------------------------------------------------------------------------------------
    def request(self, path):
        """Una petición lógica por el flujo normal del SDK: (estado, cabeceras, cuerpo JSON). Errores de red como
        OSError; un adaptador detenido o una ruta fuera de la lista, 403 sin enviar nada."""
        from sofascore_scraper.client import transport
        from sofascore_scraper.exceptions import (APIError, DataParsingError, RateLimitError, ResourceNotFoundError,
                                                  SofaScoreScraperError)
        if self.stopped is not None:
            return 403, {}, b''
        if not self.permits(path):
            self.stop(f'ruta no permitida: {path}')
            return 403, {}, b''
        mark, started = len(self.sends), time.monotonic()
        response_mark = len(self._captured_responses)
        trace = transport.RequestTrace()
        body, error = b'', None
        try:
            data = transport.make_api_request(self.api + path, max_retries=1, raise_on_failure=True, trace=trace)
            status = 200
            body = json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
        except ResourceNotFoundError:
            status = 404
        except RateLimitError as e:
            status = e.status_code or 429
        except DataParsingError:
            status, body = 200, b'\x00'
        except APIError as e:
            status = e.status_code or 0
        except SofaScoreScraperError as e:
            status, error = None, f'{type(e).__name__}: {str(e)[:160]}'
        physical = self.sends[mark:]
        exact = [(code, raw) for captured_path, code, raw in self._captured_responses[response_mark:]
                 if captured_path == path]
        self._captured_responses[response_mark:] = []
        # Un 429/503 en cualquier envío físico (también el del navegador) prevalece sobre el resumen del SDK. Si el SDK
        # intentó salir por otro camino y el adaptador detenido lo rechazó, no es un fallo de red que reintentar.
        refusal = next((s['status'] for s in physical if s.get('status') in (429, 503)), None)
        if refusal is not None:
            status, error = refusal, None
        elif error is not None and any(not s['sent'] for s in physical):
            status, error = 403, None
        elif status == 403 and all(s.get('challenge') for s in physical if s.get('status') == 403):
            # The SDK's 403 summary can hide a server error: after a 5xx in the browser it retries by curl, meets the
            # normal challenge again and gives up with "403". Every 403 was a challenge: the answer is the 5xx.
            server = next((s['status'] for s in physical if isinstance(s.get('status'), int) and s['status'] >= 500), None)
            if server is not None:
                status = server
        exact_body = next((raw for code, raw in reversed(exact) if code == status), None)
        if exact_body is not None:
            body = exact_body
        with self.lock:
            self.requests.append(dict(path=path, status=status, via=trace.via, sends=len(physical), error=error,
                                      seconds=round(time.monotonic() - started, 3)))
        if error is not None:
            raise OSError(error)
        if status in (403, 429, 0):
            self.stop(f'{status} en {path}')
        return status, {}, body

    def close(self, timeout=20):
        """Cierra el navegador del SDK (antes de cerrar la sesión del perfil)."""
        from sofascore_scraper.client import bridge
        loop = bridge._loop
        if loop is not None and loop.is_running():
            future = asyncio.run_coroutine_threadsafe(bridge.BrowserBridge.get_instance().close(), loop)
            with contextlib.suppress(Exception):
                future.result(timeout)
            self.event('browser-closed')
        if self.require_sandbox:
            from .browser import processes
            left = [pid for pid, (values, args) in processes().items()
                    if any(a == ('--user-data-dir=' + str(self.profile)).encode() for a in args)]
            self.processes_remaining = left
            if left:
                self.stop(f'procesos del navegador sin cerrar: {left}')
