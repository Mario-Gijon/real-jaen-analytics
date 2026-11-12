"""Backend side of the internal acquisition protocol (docs/contrato-adquisicion-interno.md). No scraper or browser.

publicar(): inventory of the coordinator's current generation → request to the acquisition service with a durable
request key → sealed download (bounded, ETag) → private immutable copy → verification of inventory, coverage,
provenance, producer and generation → handoff to the existing coordinator with the expected generation.

Every step is recorded under <base>.actualizaciones/adquisiciones/<request_id>/ before it happens: repeating the same
request after a crash or a failure reuses its inventory, its acquisition job and its verified bundle; it never queries
the provider again. A coordinator failure that published nothing can be retried with the same bundle while the
generation is still the one the bundle was taken from.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .bundle import MAX_WIRE, Rejected, coverage_summary, encode, inventory, parse, verify

RECIPES = {'sofascore-pilot-16599363-v1', 'sofascore-group2-2627-v1'}
MAX_STATUS = 1024 * 1024
TEST_MARKER = '.acquisition-test-installation'
TEST_CONTENT = 'isolated-acquisition-test\n'
# Outside persistencia/: approving a producer must not change the rule fingerprint the coordinator requires.
PRODUCERS = Path(__file__).resolve().parents[1] / 'productores-adquisicion.json'


class AdquisicionRechazada(RuntimeError):
    pass


def productores_aprobados():
    """Producer fingerprints (acquisition.recipe.producer_rules) approved by review, with their optional generation
    restriction; any other bundle is rejected."""
    return {p['rules_sha256']: p.get('generacion') for p in json.loads(PRODUCERS.read_text(encoding='utf-8'))['aprobados']}


def _sync(directory):
    fd = os.open(directory, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _immutable(path, body):
    """Write once (fsync); an existing copy must be byte-identical."""
    if path.is_symlink():
        raise AdquisicionRechazada('Staging inseguro')
    if path.exists():
        if path.read_bytes() != body:
            raise AdquisicionRechazada('Copia privada modificada')
        return
    temporary = path.with_name(path.name + '.tmp-' + uuid.uuid4().hex)
    with temporary.open('xb') as out:
        out.write(body); out.flush(); os.fsync(out.fileno())
    os.replace(temporary, path)
    _sync(path.parent)


def _record(path, value):
    temporary = path.with_name(path.name + '.tmp-' + uuid.uuid4().hex)
    with temporary.open('xb') as out:
        out.write(encode(value)); out.flush(); os.fsync(out.fileno())
    os.replace(temporary, path)
    _sync(path.parent)


def _esperar_publicacion(coordinador, ident):
    """Wait for the durable publication to reach a terminal state.

    The coordinator's timeout bounds one polling window; it does not cancel its worker. Retrying that window avoids
    reporting a failed acquisition while the coordinator can still publish the same generation in the background.
    """
    while True:
        try:
            return coordinador.esperar(ident, timeout=300)
        except TimeoutError as error:
            # Only the coordinator's own timeout (which carries the awaited ID) is a polling-window expiry.
            if error.args != (ident,):
                raise


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


class Cliente:
    """modo='real': the Compose service `acquisition` and the real provider. modo='prueba': only an installation
    marked by its test fixture (never by an HTTP response) and bundles from the loopback test transport."""

    def __init__(self, url, secret_file, *, modo='real', restriccion=None):
        parsed = urllib.parse.urlsplit(url)
        hosts = {'real': ('acquisition',), 'prueba': ('acquisition', '127.0.0.1')}.get(modo, ())
        if (parsed.scheme != 'http' or parsed.hostname not in hosts or parsed.username or parsed.password
                or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
            raise AdquisicionRechazada('Configuracion interna invalida')
        self.modo = modo
        # dict(limits=dict(attempts, sends), detail_rounds=[...]): a bundle must declare exactly this (control.py).
        self.restriccion = restriccion
        self.url = url.rstrip('/') + '/internal/v1/acquisitions'
        self.health_url = url.rstrip('/') + '/health'
        self._secret = Path(secret_file).read_bytes().strip()
        if not 32 <= len(self._secret) <= 256 or not self._secret.isascii() or any(c <= 32 for c in self._secret):
            raise AdquisicionRechazada('Secreto interno no configurado')
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.guard = threading.Lock()

    def _http(self, method, suffix='', data=None, *, limit=MAX_STATUS, timeout=10, base=None):
        body = encode(data) if data is not None else (b'' if method == 'POST' else None)
        request = urllib.request.Request((base or self.url) + suffix, data=body, method=method, headers={
            'Authorization': 'Bearer ' + self._secret.decode('ascii'), 'Content-Type': 'application/json'})
        try:
            with self.opener.open(request, timeout=timeout) as response:
                raw = response.read(limit + 1)
                if len(raw) > limit:
                    raise AdquisicionRechazada('Respuesta interna demasiado grande')
                return raw, response.headers.get('ETag', '')
        except urllib.error.HTTPError as error:
            error.close()
            raise AdquisicionRechazada('Respuesta interna HTTP ' + str(error.code)) from None
        except (OSError, ValueError):
            raise AdquisicionRechazada('Adquisicion no disponible') from None

    def _json(self, *args, **kwargs):
        return json.loads(self._http(*args, **kwargs)[0])

    def disponible(self, coordinador):
        """Readiness is live, mode-matched and test-installation-aware; it never queries a sports provider."""
        try:
            self._instalacion(coordinador)
            health = self._json('GET', base=self.health_url, limit=1024, timeout=2)
            expected_provider = 'fixture-site' if self.modo == 'prueba' else 'sofascore'
            return health == {'status': 'ok', 'provider': expected_provider}
        except Exception:
            # Availability is advisory for the UI and a fail-closed precondition for starting a durable job.
            return False

    @staticmethod
    def _id(ident):
        if not isinstance(ident, str) or str(uuid.UUID(ident)) != ident:
            raise AdquisicionRechazada('ID invalido')
        return ident

    def solicitar(self, request_id, recipe_id, baseline):
        if recipe_id not in RECIPES:
            raise AdquisicionRechazada('Receta invalida')
        return self._json('POST', data=dict(request_id=self._id(request_id), recipe_id=recipe_id, inventory=baseline),
                          timeout=60)

    def estado(self, ident):
        return self._json('GET', '/' + self._id(ident))

    def informe(self, ident):
        return self._json('GET', '/' + self._id(ident) + '/report')

    def cancelar(self, ident):
        return self._json('POST', '/' + self._id(ident) + '/cancel')

    def esperar(self, ident, timeout=600, avance=None):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.estado(ident)
            if state['state'] not in ('queued', 'running', 'cancelling'):
                return state
            if avance is not None:
                avance('acquiring', acquisition_id=ident, acquisition_state=state['state'])
            time.sleep(.5)
        raise AdquisicionRechazada('Adquisicion sigue en curso')

    def descargar(self, ident, descriptor):
        if set(descriptor) != {'bytes', 'sha256'} or not re.fullmatch('[0-9a-f]{64}', descriptor['sha256']):
            raise AdquisicionRechazada('Descriptor de bundle invalido')
        body, etag = self._http('GET', '/' + self._id(ident) + '/bundle', limit=MAX_WIRE, timeout=60)
        if etag != '"' + descriptor['sha256'] + '"':
            raise AdquisicionRechazada('ETag del bundle incoherente')
        if len(body) != descriptor['bytes'] or hashlib.sha256(body).hexdigest() != descriptor['sha256']:
            raise AdquisicionRechazada('Descarga distinta del descriptor')
        return body

    def _instalacion(self, coordinador):
        marker = coordinador.base.with_name(coordinador.base.name + TEST_MARKER)
        if self.modo == 'prueba':
            if not marker.is_file() or marker.read_text() != TEST_CONTENT:
                raise AdquisicionRechazada('Instalacion de prueba no identificada')
        elif marker.exists():
            raise AdquisicionRechazada('Instalacion de prueba: proveedor real deshabilitado')

    def publicar(self, coordinador, request_id, recipe_id, *, timeout=600, avance=None):
        """`avance(fase, **datos)`, if given, is told each phase (acquiring, verifying, publishing) before it starts;
        an exception it raises stops the request there, before any import if raised before 'publishing'."""
        avance = avance or (lambda fase, **datos: None)
        self._instalacion(coordinador)
        self._id(request_id)
        if recipe_id not in RECIPES:
            raise AdquisicionRechazada('Receta invalida')
        with self.guard:
            folder = coordinador.directorio / 'adquisiciones' / request_id
            folder.mkdir(parents=True, exist_ok=True)
            if folder.is_symlink():
                raise AdquisicionRechazada('Staging inseguro')
            path = folder / 'solicitud.json'
            if path.exists():
                record = parse(path.read_bytes())
                baseline = parse((folder / 'inventory.json').read_bytes())
                if record['recipe_id'] != recipe_id or baseline['sha256'] != record['inventory_sha256']:
                    raise AdquisicionRechazada('Solicitud reutilizada con otra receta o inventario')
            else:
                baseline = inventory(coordinador.capturar())
                _immutable(folder / 'inventory.json', encode(baseline))
                record = dict(request_id=request_id, recipe_id=recipe_id, inventory_sha256=baseline['sha256'],
                              generation_id=baseline['generation_id'], acquisition_id=None, bundle=None, intentos=[])
                _record(path, record)
            if record['bundle'] is None:
                job = self.solicitar(request_id, recipe_id, baseline)
                record['acquisition_id'] = job['id']
                _record(path, record)
                avance('acquiring', acquisition_id=job['id'], acquisition_state=job['state'])
                state = self.esperar(job['id'], timeout, avance)
                if state['state'] != 'completed':
                    raise AdquisicionRechazada(f"Adquisicion {state['state']}: no se importa nada ({state.get('error')})")
                avance('verifying')
                body = self.descargar(job['id'], state['bundle'])
                _immutable(folder / 'bundle.json', body)
                record['bundle'] = state['bundle']
                _record(path, record)
            else:
                avance('verifying')
            published = self.recibir(coordinador, folder, record, baseline, avance)
            if recipe_id == 'sofascore-group2-2627-v1' and published.get('coverage') is not None:
                # A stopped transport (403/429, repeated challenge, browser or transport refusal) must not
                # trigger another batch. If the internal report cannot be read, fail closed and leave coverage
                # published for the next manual update.
                try:
                    report = self.informe(record['acquisition_id'])
                    published['_continuation_allowed'] = isinstance(report, dict) and report.get('stopped') is None
                except Exception:
                    published['_continuation_allowed'] = False
            return published

    def recibir(self, coordinador, folder, record, baseline, avance=None):
        """Verify the private copy and hand it to the coordinator. Records each coordinator attempt before it starts."""
        from .actualizacion import CoordinacionRechazada
        descriptor, job = record['bundle'], record['acquisition_id']
        body = (folder / 'bundle.json').read_bytes()
        manifest, files = verificar_sellado(body, descriptor, baseline, (job, record['request_id']),
                                            test_transport=self.modo == 'prueba')
        if manifest['recipe_id'] != record['recipe_id']:
            raise Rejected('Bundle of another recipe')
        comprobar_restriccion(manifest, self.restriccion)
        path = folder / 'solicitud.json'
        last = record['intentos'][-1] if record['intentos'] else None
        state = None
        if last is not None:
            try:
                state = coordinador.estado(last)
            except KeyError:
                state = None  # recorded before the coordinator accepted it: the same key again
        if state is None or state['estado'] not in ('queued', 'running', 'completed'):
            if last is None or state is not None:  # first attempt, or a failed one that published nothing
                last = str(uuid.uuid5(uuid.NAMESPACE_URL,
                                      f"urn:rj:acquisition:{job}:{descriptor['sha256']}:{len(record['intentos']) + 1}"))
                record['intentos'].append(last)
                _record(path, record)
            if inventory(coordinador.capturar())['sha256'] != baseline['sha256']:
                raise Rejected('La generacion cambio durante la adquisicion')
            if avance is not None:
                avance('publishing')
            exports = []
            comments = []
            for name, raw in files.items():
                _immutable(folder / name, raw)
                if name.startswith('team_'):
                    exports.append((folder / name, int(name[5:-6])))
                elif name == 'comments.jsonl':
                    comments.append(folder / name)
            try:
                coordinador.solicitar(exportaciones=exports, calendarios=[folder / 'calendar.jsonl'], identificador=last,
                                      generacion_esperada=baseline['generation_id'],comentarios=comments)
            except CoordinacionRechazada as e:
                raise Rejected(f'Coordinador: {e}') from None
        result = _esperar_publicacion(coordinador, last)
        if result['estado'] != 'completed':
            raise AdquisicionRechazada('Publicacion fallida: se conserva la generacion valida')
        published = dict(state='published', generation_id=result['generacion'], job_id=last, acquisition_id=job,
                         request_id=record['request_id'], bundle=descriptor, attempts=len(record['intentos']))
        if manifest['contract'] == 'group-v1':
            # Partial coverage: what is new, what was kept from before and what stays pending (never absence).
            plan = manifest['plan']
            published['coverage'] = dict(coverage_summary(manifest), new_matches=plan['new_matches'], rounds=plan['rounds'],
                                         deferred=sorted(int(i) for i, why in plan['deferred'].items() if why == 'budget'),
                                         restricted=sorted(int(i) for i, why in plan['deferred'].items() if why == 'restricted'),
                                         limits=plan['limits'], detail_rounds=plan['detail_rounds'])
        elif manifest['contract'] == 'group-v2':
            plan=manifest['plan']
            coverage=dict(coverage_summary(manifest),new_matches=plan['new_matches'],rounds=plan['rounds'],
                          deferred=sorted(int(i) for i,why in plan['deferred'].items() if why=='budget'),
                          restricted=sorted(int(i) for i,why in plan['deferred'].items() if why=='restricted'),
                          limits=plan['limits'],detail_rounds=plan['detail_rounds'])
            persisted=(result.get('resultado') or {}).get('comments_persisted') or {}
            failed_persisted=set(persisted.get('failed_ids',[]))
            if failed_persisted:
                comments_coverage=coverage['comments']
                failed_ids={entry['id'] for entry in comments_coverage['failed']}
                retained=[]
                for event_id in comments_coverage['new']:
                    if event_id in failed_persisted:
                        if event_id not in failed_ids:
                            comments_coverage['failed'].append(dict(id=event_id,reason='persistence_error'))
                    else:
                        retained.append(event_id)
                comments_coverage['new']=retained
                comments_coverage['failed'].sort(key=lambda entry:entry['id'])
            published['coverage']=coverage
            # Internal continuation accounting only; it is not part of the REST bundle or dashboard contract.
            attempted, new, kept = set(), set(), set()
            for row in manifest['coverage']:
                if row['resource'] == 'calendar':
                    continue
                key = f"{row['event_id']}:{row['resource']}"
                if row['status'] == 'data' and row['origin'] == 'attempt':
                    attempted.add(key)
                    new.add(key)
                elif row['status'] == 'data' and row['origin'] == 'inventory':
                    kept.add(key)
                elif row['status'] == 'pending' and row['checked_at'] is not None:
                    attempted.add(key)
            comments=coverage.get('comments') or {}
            attempted.update(f"{ident}:comments" for ident in comments.get('new', ()))
            attempted.update(f"{row['id']}:comments" for row in comments.get('failed', ()))
            published['_batch_resources'] = dict(attempted=sorted(attempted), new=sorted(new), kept=sorted(kept))
        return published


def comprobar_restriccion(manifest, restriccion):
    """Group bundles must declare this installation's exact limits; the fixed pilot has no group plan."""
    if restriccion is None or manifest.get('contract') == 'pilot-v2':
        return
    plan = manifest.get('plan') or {}
    if dict(limits=plan.get('limits'), detail_rounds=plan.get('detail_rounds')) != restriccion:
        raise Rejected('Bundle outside the restriction of this installation')


def verificar_sellado(body, descriptor, baseline, context, *, test_transport=False):
    """Trust-boundary verification of a sealed bundle, without the coordinator: approved producer, inventory,
    coverage, provenance, generation and transport. Returns (manifest, files)."""
    try:
        rules = parse(body)['manifest']['rules_sha256']
    except (ValueError, KeyError, TypeError):
        raise Rejected('Malformed bundle') from None
    approved = productores_aprobados()
    if rules not in approved:
        raise Rejected('Unapproved producer rules')
    if approved[rules] is not None and approved[rules] != baseline.get('generation_id'):
        raise Rejected('Producer approved only for another generation')
    return verify(body, descriptor, baseline, context=context, expected_rules=rules, test_transport=test_transport)
