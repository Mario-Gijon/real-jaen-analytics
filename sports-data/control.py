"""Update jobs and configuration for the optional FastAPI control mode.

The process owns the coordinator (and so the base lock) for its whole life; every sporting GET captures the current
generation once and a publication swaps it without cutting requests in flight. Contract:
../rj-analyst/docs/contrato-actualizaciones.md.

Environment: RJ_CONTROL_SECRET_FILE (bearer secret of /internal/v1/updates, shared only with Next's server),
RJ_ACQUISITION_URL and RJ_ACQUISITION_SECRET_FILE (the acquisition service), RJ_UPDATE_RECIPE (the group's by
default; the pilot's for its tests), RJ_ACQUISITION_TEST=1 only on an
installation marked by its test fixture (persistencia.adquisicion, modo 'prueba'). Without the control secret the
mode refuses to start. This module is outside persistencia/: it does not change the coordinator's rule fingerprint.
"""
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import threading
import uuid

PILOT = 'sofascore-pilot-16599363-v1'
GROUP = 'sofascore-group2-2627-v1'
# What each recipe really consults, as the interface must say it (never more than the recipe covers).
SCOPES = {
    PILOT: dict(kind='pilot', recipe=PILOT, round=1, match_ids=[16599363], action='Actualizar piloto',
                label='Actualización piloto: calendario de la jornada 1 y un partido',
                description='Consulta el calendario de la jornada 1 y un partido. No actualiza el resto de la jornada '
                            'ni de la temporada: los demás partidos se conservan tal como están publicados.'),
    GROUP: dict(kind='group', recipe=GROUP, round=None, match_ids=[], action='Actualizar Grupo 2',
                label='Actualización del Grupo 2 (Primera Federación 26/27)',
                description='Consulta los partidos finalizados que faltan, los aplazados, los cambios de calendario de '
                            'las jornadas próximas y, por turnos, de las lejanas, y revisa las correcciones de las '
                            'últimas 72 horas. Continúa en lotes seguros mientras quede trabajo elegible y el proveedor '
                            'lo permita. Si se detiene, lo pendiente conserva los datos anteriores; nunca se sustituye '
                            'por ceros.'),
}
RECIPE = GROUP
ACTIVE = ('queued', 'running')
CANCELLABLE_PHASES = ('queued', 'acquiring')
POLL_SECONDS = 3
PUBLICATION_RECONCILE_SECONDS = 5
PUBLICATION_ATTENTION_SECONDS = 900
# A single click is finite even if an invalid coverage report keeps claiming that work remains.
MAX_GROUP_BATCHES = 64
# Safe public messages per error code; internal reasons stay in the backend log, keyed by job id.
MESSAGES = {
    'ACQUISITION_FAILED': ('La consulta al proveedor no se completó o fue rechazada. No se reintenta sola.', True),
    'ACQUISITION_INTERRUPTED': ('La adquisición se interrumpió antes de terminar.', True),
    'ACQUISITION_UNAVAILABLE': ('El servicio de adquisición no está disponible.', True),
    'CANCELLED': ('Actualización cancelada antes de importar nada.', True),
    'BUNDLE_REJECTED': ('Los datos recibidos no superaron la verificación.', False),
    'GENERATION_CHANGED': ('Los datos publicados cambiaron durante la actualización.', True),
    'PUBLICATION_FAILED': ('No se pudo publicar. Se puede reintentar sin volver a consultar.', True),
    'INTERRUPTED': ('El servidor se reinició durante la actualización.', True),
    'INTERNAL_ERROR': ('Error interno durante la actualización.', False),
}


def now():
    return datetime.now(timezone.utc).isoformat()


def secret(path):
    value = Path(path).read_bytes().strip()
    if not 32 <= len(value) <= 256 or not value.isascii() or any(c <= 32 for c in value):
        raise ValueError('Invalid control secret configuration')
    return value


def restriccion(environ):
    """The restriction a bundle of this installation must declare, if any: RJ_ACQUISITION_MAX_ATTEMPTS,
    RJ_ACQUISITION_MAX_SENDS and RJ_ACQUISITION_DETAIL_ROUNDS, all three or none (the acquisition service has the
    same values; this side refuses a bundle that does not declare exactly them)."""
    names = ('RJ_ACQUISITION_MAX_ATTEMPTS', 'RJ_ACQUISITION_MAX_SENDS', 'RJ_ACQUISITION_DETAIL_ROUNDS')
    values = [environ.get(n) for n in names]
    if not any(values):
        return None
    if not all(values) or not values[0].isdigit() or not values[1].isdigit():
        raise SystemExit('Set the three of ' + ', '.join(names) + ' or none')
    rounds = sorted({int(p) for p in values[2].split(',')})
    return dict(limits=dict(attempts=int(values[0]), sends=int(values[1])), detail_rounds=rounds)


class Unavailable(Exception): pass
class NotCancellable(Exception): pass
class Cancelled(Exception): pass


class Actualizaciones:
    """One active update at a time, durable jobs and idempotency keys under <base>.actualizaciones/control/."""

    def __init__(self, coordinador, cliente, *, recipe=RECIPE):
        if recipe not in SCOPES:
            raise ValueError('Receta no aprobada')
        self.c, self.cliente, self.recipe = coordinador, cliente, recipe
        self.root = coordinador.directorio / 'control'
        self.root.mkdir(exist_ok=True)
        self.guard = threading.RLock()
        self.threads = []
        self.live_jobs = set()
        self.stop_reconciler = threading.Event()
        with closing(self.db()) as c, c:
            c.execute('CREATE TABLE IF NOT EXISTS job(id TEXT PRIMARY KEY, idem TEXT UNIQUE, state TEXT, data TEXT)')
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_active ON job((1)) WHERE state IN ('queued','running')")
        self._reconcile()
        self.reconciler_thread = threading.Thread(target=self._reconcile_loop, daemon=True,
                                                  name='publication-reconciler')
        self.reconciler_thread.start()

    def db(self):
        c = sqlite3.connect(self.root / 'jobs.sqlite', timeout=10)
        c.execute('PRAGMA synchronous=FULL')
        return c

    def _rows(self, where='', args=()):
        with closing(self.db()) as c:
            return [json.loads(r[0]) for r in c.execute('SELECT data FROM job ' + where, args).fetchall()]

    def get(self, ident):
        rows = self._rows('WHERE id=?', (ident,))
        if not rows:
            raise KeyError(ident)
        return rows[0]

    def save(self, item):
        with closing(self.db()) as c, c:
            c.execute('UPDATE job SET state=?, data=? WHERE id=?', (item['status'], json.dumps(item), item['id']))

    def _attempt(self, item):
        """Coordinator state of the request's last import attempt (the coordinator's journal is the authority)."""
        ident = self._publication_id(item)
        if ident is None:
            return None
        try:
            return self.c.estado(ident)
        except KeyError:
            return None

    def _publication_id(self, item):
        ident = item.get('publication_pending_id')
        if ident:
            return ident
        path = self.c.directorio / 'adquisiciones' / item['request_id'] / 'solicitud.json'
        if not path.is_file() or path.is_symlink():
            return None
        try:
            attempts = json.loads(path.read_bytes()).get('intentos') or []
            return attempts[-1] if attempts else None
        except (OSError, ValueError, TypeError):
            return None

    def _reconcile_publication(self, item, *, coordinator_id=None):
        try:
            return self._reconcile_publication_once(item, coordinator_id=coordinator_id)
        except Exception as error:
            ident = coordinator_id or self._publication_id(item)
            # A mismatch or unreadable confirmation is an unknown outcome, not grounds to release the active lock.
            with self.guard:
                current = self.get(item['id'])
                current.update(status='running', phase='publishing',
                               publication_pending_id=ident,
                               reconciliation_error=dict(type=type(error).__name__, message=str(error)[:300]),
                               reconciliation_checked_at=now())
                self._annotate_pending_age(current)
                current['publication_attention_required'] = True
                self.save(current)
            print(f'[control] publication reconciliation {item["id"]}: {type(error).__name__}: {str(error)[:300]}',
                  file=sys.stderr, flush=True)
            return False

    @staticmethod
    def _annotate_pending_age(item):
        started = item.get('publication_pending_since')
        if not started:
            return
        try:
            age = max(0, int((datetime.now(timezone.utc) - datetime.fromisoformat(started)).total_seconds()))
        except (TypeError, ValueError):
            return
        item['publication_age_seconds'] = age
        item['publication_attention_required'] = age >= PUBLICATION_ATTENTION_SECONDS

    def _reconcile_publication_once(self, item, *, coordinator_id=None):
        """Check and verify one durable publication; this path never starts Acquisition or a new batch."""
        ident = coordinator_id or self._publication_id(item)
        if ident is None:
            return False
        try:
            result = self.cliente.reconciliar_publicacion(self.c, item['request_id'], self.recipe, ident)
        except Exception as error:
            # An uncertain verification is not evidence of failure. Retain the active lock and retry this same ID.
            with self.guard:
                current = self.get(item['id'])
                current.update(status='running', phase='publishing',
                               publication_pending_id=ident,
                               reconciliation_error=dict(type=type(error).__name__, message=str(error)[:300]),
                               reconciliation_checked_at=now())
                self._annotate_pending_age(current)
                current['publication_attention_required'] = True
                self.save(current)
            print(f'[control] publication reconciliation {item["id"]}: {type(error).__name__}: {str(error)[:300]}',
                  file=sys.stderr, flush=True)
            return False

        if result.get('job_id') != ident:
            raise ValueError('Publication reconciliation returned a different coordinator ID')
        if result.get('state') == 'pending':
            with self.guard:
                current = self.get(item['id'])
                current.update(status='running', phase='publishing',
                               publication_pending_id=ident,
                               reconciliation_checked_at=now())
                self._annotate_pending_age(current)
                current.pop('reconciliation_error', None)
                self.save(current)
            return False
        if result.get('state') == 'failed':
            with self.guard:
                current = self.get(item['id'])
                self._fail(current, 'PUBLICATION_FAILED', resumable=True)
                current['publication_pending_id'] = ident
                current['reconciliation'] = dict(coordinator_id=ident, coordinator_state='failed',
                                                  reconciled_at=now())
                self.save(current)
            return True
        if result.get('state') != 'published' or result.get('job_id') != ident:
            raise ValueError('Publication reconciliation returned an inconsistent result')

        confirmed = self.c.estado(ident)
        current_generation = self.c.capturar().id
        generation = result.get('generation_id')
        if (confirmed.get('estado') != 'completed' or confirmed.get('generacion') != generation
                or current_generation != generation):
            raise ValueError('Publication is not the confirmed current generation')

        coverage = result.get('coverage')
        with self.guard:
            current = self.get(item['id'])
            progress = current.get('batch_progress')
            if self.recipe == GROUP and coverage is not None:
                progress = self._merge_batch(progress, coverage, result.get('_batch_resources'))
                coverage = self._combine_coverage(coverage, progress)
                coverage = self._named(coverage)
                current['batch_progress'] = progress
            import_result = confirmed.get('resultado') or {}
            run_status, published_at = self._publication_run(import_result)
            outcome = 'no_changes' if run_status == 'no_changes' else None
            current.update(status='completed', phase='finished', published=True, finished_at=now(), resumable=False,
                           batch_count=max(current.get('batch_count', 0), current.get('pending_batch_sequence', 1)),
                           continuation_pending=False,
                           result=dict(published_at=published_at, generation=generation,
                                       reused_bundle=bool(current.get('reused_bundle', False)), coverage=coverage,
                                       **({'publication_result': outcome} if outcome else {})),
                           reconciliation=dict(coordinator_id=ident, coordinator_state='completed',
                                               reconciled_at=now(), generation=generation))
            current.pop('publication_pending_id', None)
            current.pop('pending_batch_sequence', None)
            current.pop('reconciliation_error', None)
            current.pop('publication_age_seconds', None)
            current.pop('publication_attention_required', None)
            self.save(current)
        return True

    def _publication_run(self, result):
        """Return the coordinator import run's terminal status and timestamp when available."""
        run_id = result.get('run')
        if isinstance(run_id, int):
            try:
                with closing(sqlite3.connect(self.c.base.as_uri() + '?mode=ro', uri=True, timeout=2)) as c:
                    row = c.execute('SELECT status, finished_at FROM import_run WHERE id=?', (run_id,)).fetchone()
                if row and row[0]:
                    return row[0], row[1] or now()
            except (OSError, sqlite3.Error):
                pass
        return None, now()

    def _reconcile_loop(self):
        while not self.stop_reconciler.wait(PUBLICATION_RECONCILE_SECONDS):
            for item in self._rows("WHERE state='running'"):
                if item.get('phase') != 'publishing':
                    continue
                with self.guard:
                    if item['id'] in self.live_jobs:
                        continue
                self._reconcile_publication(item)

    def _reconcile(self):
        """Reconcile durable publication IDs; only work before publication is treated as interrupted on restart."""
        for item in self._rows("WHERE state IN ('queued','running')"):
            if item.get('phase') == 'publishing' or item.get('publication_pending_id'):
                publication_id = self._publication_id(item)
                if publication_id is not None:
                    self._reconcile_publication(item, coordinator_id=publication_id)
                else:
                    # Publishing phase without its receipt is uncertain; do not free the one-active-job lock.
                    item.update(status='running', phase='publishing',
                                reconciliation_error=dict(type='MissingPublicationId',
                                                          message='No durable coordinator ID in request receipt'),
                                reconciliation_checked_at=now(), publication_attention_required=True)
                    self._annotate_pending_age(item)
                    self.save(item)
                continue
            attempt = self._attempt(item)
            if item.get('batch_mode'):
                if (not item.get('continuation_pending') and attempt is not None
                        and attempt['estado'] == 'completed'):
                    item['published'] = True
                    item['result'] = dict(published_at=now(), generation=attempt['generacion'],
                                          reused_bundle=item.get('reused_bundle', False),
                                          coverage=(item.get('result') or {}).get('coverage'))
                    item.update(status='completed', phase='finished', finished_at=now())
                else:
                    self._fail(item, 'INTERRUPTED', resumable=False, status='interrupted')
            elif attempt is not None and attempt['estado'] == 'completed':
                item.update(status='completed', phase='finished', published=True, finished_at=now(),
                            result=dict(published_at=now(), generation=attempt['generacion'],
                                        reused_bundle=item.get('reused_bundle', False)))
            else:
                self._fail(item, 'INTERRUPTED', resumable=False)
            self.save(item)

    def _fail(self, item, code, *, resumable, status='failed'):
        message, retryable = MESSAGES[code]
        item.update(status=status, phase='finished', finished_at=now(), resumable=resumable,
                    error=dict(code=code, message=message, retryable=retryable))

    def _last_finished(self):
        rows = [r for r in self._rows("WHERE state NOT IN ('queued','running')")]
        return max(rows, key=lambda r: r['requested_at']) if rows else None

    def solicitar(self, idem):
        if not isinstance(idem, str) or str(uuid.UUID(idem)) != idem:
            raise ValueError('Idempotency-Key must be a UUID')
        with self.guard:
            known = self._rows('WHERE idem=?', (idem,))
            if known:
                return known[0], True
            active = self._rows("WHERE state IN ('queued','running')")
            if active:
                return active[0], True
            if not self.cliente.disponible(self.c):
                raise Unavailable('Acquisition is not ready for this installation')
            try:
                self.c.capturar()
            except Exception as e:
                raise Unavailable(str(e)) from None
            last = self._last_finished()
            # A failed publication with a verified bundle is retried with the same request: no new provider query.
            reuse = last is not None and last['status'] == 'failed' and last.get('resumable')
            request_id = last['request_id'] if reuse else str(uuid.uuid4())
            item = dict(id=str(uuid.uuid4()), idem=idem, request_id=request_id,
                        status='queued', phase='queued', published=False, requested_at=now(), finished_at=None,
                        acquisition_id=None, cancel_requested=False, reused_bundle=bool(reuse), resumable=False,
                        error=None, result=None, batch_mode=self.recipe == GROUP, batch_count=0,
                        batch_request_ids=[request_id], batch_progress=None,
                        continuation_pending=self.recipe == GROUP)
            with closing(self.db()) as c, c:
                c.execute('INSERT INTO job VALUES(?,?,?,?)', (item['id'], idem, item['status'], json.dumps(item)))
        worker = threading.Thread(target=self.run, args=(item['id'],), daemon=True)
        with self.guard:
            self.live_jobs.add(item['id'])
        self.threads.append(worker)
        worker.start()
        return item, False

    def cancelar(self, ident):
        with self.guard:
            item = self.get(ident)
            if item['status'] == 'cancelled':
                return item
            if item['status'] not in ACTIVE or item['phase'] not in CANCELLABLE_PHASES:
                raise NotCancellable()
            item['cancel_requested'] = True
            self.save(item)
            acquisition = item['acquisition_id']
        if acquisition:
            self._cancel_acquisition(acquisition)
        return item

    def _cancel_acquisition(self, acquisition):
        try:
            self.cliente.cancelar(acquisition)
        except Exception as e:  # the acquisition may have just finished; the job still stops before importing
            print(f'[control] cancel {acquisition}: {e}', file=sys.stderr, flush=True)

    def run(self, ident):
        def avance(fase, **datos):
            with self.guard:
                item = self.get(ident)
                cancel = item['cancel_requested']
                first = datos.get('acquisition_id') and item['acquisition_id'] is None
                if first:
                    item['acquisition_id'] = datos['acquisition_id']
                if cancel and fase in ('verifying', 'publishing'):
                    raise Cancelled()
                item.update(status='running', phase=fase)
                self.save(item)
            if cancel and first:
                self._cancel_acquisition(datos['acquisition_id'])
        from persistencia.adquisicion import AdquisicionRechazada
        from persistencia.bundle import Rejected
        try:
            if self.get(ident)['cancel_requested']:
                raise Cancelled()
            batch_count = 0
            previous_work = None
            progress = None
            result = None
            while True:
                item = self.get(ident)
                if item['cancel_requested']:
                    raise Cancelled()
                result = self.cliente.publicar(self.c, item['request_id'], self.recipe, avance=avance)
                if result.get('state') == 'pending':
                    coordinator_id = result.get('job_id')
                    if not coordinator_id or result.get('request_id') != item['request_id']:
                        raise ValueError('Pending publication is missing its durable identifiers')
                    with self.guard:
                        item = self.get(ident)
                        item.update(status='running', phase='publishing',
                                    publication_pending_id=coordinator_id,
                                    pending_batch_sequence=item.get('batch_count', 0) + 1,
                                    publication_pending_since=now(), reconciliation_checked_at=now())
                        self._annotate_pending_age(item)
                        self.save(item)
                    return
                batch_count += 1
                coverage = result.get('coverage')
                if self.recipe == GROUP and coverage is not None:
                    progress = self._merge_batch(progress, coverage, result.get('_batch_resources'))
                    combined = self._combine_coverage(coverage, progress)
                    work = self._batch_work(combined)
                else:
                    combined, work = coverage, set()
                with self.guard:
                    item = self.get(ident)
                    item.update(published=True, batch_count=batch_count)
                    if progress is not None:
                        item['batch_progress'] = progress
                    item['result'] = dict(published_at=now(), generation=result['generation_id'],
                                          reused_bundle=bool(item['reused_bundle'] and batch_count == 1),
                                          coverage=self._named(combined))
                    self.save(item)

                # Continue only for budget-deferred work within the declared scope, after measurable progress, and
                # while the acquisition adapter confirms that its protective transport breaker remains closed.
                attempted = (result.get('_batch_resources') or {}).get('attempted') or ()
                if (self.recipe != GROUP or not work or not attempted or
                        not result.get('_continuation_allowed') or batch_count >= MAX_GROUP_BATCHES):
                    with self.guard:
                        item = self.get(ident)
                        item['continuation_pending'] = False
                        self.save(item)
                    break
                if previous_work is not None and not (previous_work - work):
                    with self.guard:
                        item = self.get(ident)
                        item['continuation_pending'] = False
                        self.save(item)
                    break
                previous_work = work
                next_request = str(uuid.uuid4())
                with self.guard:
                    item = self.get(ident)
                    if item['cancel_requested']:
                        raise Cancelled()
                    item.update(request_id=next_request, acquisition_id=None, phase='acquiring', reused_bundle=False,
                                batch_request_ids=item['batch_request_ids'] + [next_request], continuation_pending=True)
                    item['result']['reused_bundle'] = False
                    self.save(item)
            with self.guard:
                item = self.get(ident)
                item.update(status='completed', phase='finished', published=True, finished_at=now(),
                            resumable=False)
                self.save(item)
        except Exception as error:
            text = str(error)
            if isinstance(error, Cancelled) or (isinstance(error, AdquisicionRechazada) and 'cancelled' in text):
                code, resumable, status = 'CANCELLED', False, 'cancelled'
            elif isinstance(error, Rejected):
                code, resumable, status = ('GENERATION_CHANGED' if 'generacion' in text.lower() else 'BUNDLE_REJECTED'), False, 'failed'
            elif isinstance(error, AdquisicionRechazada) and 'Publicacion fallida' in text:
                code, resumable, status = 'PUBLICATION_FAILED', True, 'failed'
            elif isinstance(error, AdquisicionRechazada) and 'no se importa' in text:  # the acquisition job ended unsuccessfully
                code = 'ACQUISITION_INTERRUPTED' if 'interrupted' in text else 'ACQUISITION_FAILED'
                resumable, status = False, 'failed'
            elif isinstance(error, AdquisicionRechazada):
                code, resumable, status = 'ACQUISITION_UNAVAILABLE', False, 'failed'
            else:
                code, resumable, status = 'INTERNAL_ERROR', False, 'failed'
            print(f'[control] job {ident}: {code}: {type(error).__name__}: {text[:300]}', file=sys.stderr, flush=True)
            with self.guard:
                item = self.get(ident)
                self._fail(item, code, resumable=resumable, status=status)
                self.save(item)
        finally:
            with self.guard:
                self.live_jobs.discard(ident)

    def _named(self, coverage):
        """The coverage with the names of the matches it mentions, from the generation just published (the interface
        shows them without asking the data API)."""
        if not coverage:
            return coverage
        ids = {p['event_id'] for p in coverage['pending']} | set(coverage['deferred']) | set(coverage.get('restricted', ()))
        ids |= {m['id'] for m in coverage['new_matches']} | {r for m in coverage['new_matches'] for r in m['replaces']}
        comments=coverage.get('comments') or {}
        ids |= set(comments.get('new',())) | set(comments.get('kept',())) | set(comments.get('pending',()))
        ids |= {item['id'] for item in comments.get('failed',())}
        generation = self.c.capturar()
        data = generation._datos
        teams = {e['team_id']: e['name'] for e in data['equipos']}
        names = {}
        for m in data['partidos']:
            if m['match_id'] in ids:
                names[str(m['match_id'])] = f"{teams.get(m['home_team_id']) or '?'} – {teams.get(m['away_team_id']) or '?'}"
        if comments:
            empty = sorted(ident for ident in comments.get('new', ())
                           if (generation._comentarios.get(f'sofascore-match-{ident}') or {}).get('availability') == 'empty')
            return dict(coverage, comments=dict(comments, empty=empty), names=names)
        return dict(coverage, names=names)

    @staticmethod
    def _round_number(value):
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, dict):
            return Actualizaciones._round_number(value.get('number'))
        if isinstance(value, str):
            try:
                return Actualizaciones._round_number(json.loads(value))
            except (ValueError, TypeError):
                return None
        return None

    def _batch_work(self, coverage):
        """Budget-deferred work only, filtered to the installation's configured detail rounds."""
        if not coverage:
            return set()
        work = {f"deferred:{ident}" for ident in coverage.get('deferred', ())}
        for match in coverage.get('pending', ()):
            for resource in match.get('resources', ()):
                if resource.get('reason') == 'budget':
                    work.add(f"sport:{match['event_id']}:{resource['resource']}")
        comments = coverage.get('comments') or {}
        pending_comments = {int(ident) for ident in comments.get('pending', ())}
        detail_rounds = coverage.get('detail_rounds')
        if pending_comments:
            matches = self.c.capturar()._datos.get('partidos', ())
            round_by_id = {int(match['match_id']): self._round_number(match.get('round')) for match in matches}
            for ident in pending_comments:
                if ident not in round_by_id:
                    continue
                if detail_rounds is None or round_by_id[ident] in detail_rounds:
                    work.add(f"comments:{ident}")
        return work

    @staticmethod
    def _merge_batch(progress, coverage, resources):
        """Accumulate unique changes while preserving the final pending/kept state of the published generation."""
        progress = progress or dict(new_resources=[], latest_kept_resources=[], new_comments=[],
                                   latest_kept_comments=[], failed_comments={}, new_matches={},
                                   near_rounds=[], far_rounds=[], failed_rounds=[])
        resources = resources or {}
        progress['new_resources'] = sorted(set(progress['new_resources']) | set(resources.get('new', ())))
        progress['latest_kept_resources'] = sorted(set(resources.get('kept', ())))
        comments = coverage.get('comments') or {}
        progress['new_comments'] = sorted(set(progress['new_comments']) | set(comments.get('new', ())))
        progress['latest_kept_comments'] = sorted(set(comments.get('kept', ())))
        failures = progress['failed_comments']
        for row in comments.get('failed', ()):
            failures[str(row['id'])] = row['reason']
        for ident in set(progress['new_comments']) | set(progress['latest_kept_comments']):
            failures.pop(str(ident), None)
        for row in coverage.get('new_matches', ()):
            progress['new_matches'][str(row['id'])] = row
        rounds = coverage.get('rounds') or {}
        for key, field in (('near', 'near_rounds'), ('far', 'far_rounds'), ('failed', 'failed_rounds')):
            progress[field] = sorted(set(progress[field]) | set(rounds.get(key, ())))
        return progress

    @staticmethod
    def _combine_coverage(latest, progress):
        if latest is None or progress is None:
            return latest
        combined = dict(latest)
        new_resources = set(progress['new_resources'])
        kept_resources = set(progress['latest_kept_resources']) - new_resources
        combined['new'] = len(new_resources)
        combined['kept'] = len(kept_resources)
        combined['new_matches'] = [progress['new_matches'][key] for key in sorted(progress['new_matches'])]
        combined['rounds'] = dict(near=progress['near_rounds'], far=progress['far_rounds'],
                                  failed=progress['failed_rounds'])
        comments = dict(latest.get('comments') or {})
        new_comments = set(progress['new_comments'])
        kept_comments = set(progress['latest_kept_comments']) - new_comments
        comments.update(new=sorted(new_comments), kept=sorted(kept_comments),
                        failed=[dict(id=int(ident), reason=reason)
                                for ident, reason in sorted(progress['failed_comments'].items())
                                if int(ident) not in new_comments and int(ident) not in kept_comments])
        combined['comments'] = comments
        return combined

    def public(self, item):
        if item is None:
            return None
        return dict(id=item['id'], status=item['status'], phase=item['phase'],
                    cancellable=item['status'] in ACTIVE and item['phase'] in CANCELLABLE_PHASES and not item['cancel_requested'],
                    cancel_requested=item['cancel_requested'], published=item['published'],
                    requested_at=item['requested_at'], finished_at=item['finished_at'], poll_after_seconds=POLL_SECONDS,
                    publication_pending=(item['status'] == 'running' and item['phase'] == 'publishing'),
                    publication_pending_since=item.get('publication_pending_since'),
                    publication_age_seconds=item.get('publication_age_seconds'),
                    publication_attention_required=item.get('publication_attention_required', False),
                    publication_reconciliation_error=(
                        item['reconciliation_error'].get('type')
                        if isinstance(item.get('reconciliation_error'), dict)
                        else item.get('reconciliation_error')),
                    error=item['error'],
                    result=None if item['result'] is None else dict(published_at=item['result']['published_at'],
                                                                     reused_bundle=item['result']['reused_bundle'],
                                                                     coverage=item['result'].get('coverage')))

    def actual(self):
        active = self._rows("WHERE state IN ('queued','running')")
        last = self._last_finished()
        published = [r for r in self._rows() if r.get('published') and r.get('result')]
        latest = max(published, key=lambda r: (r.get('result') or {}).get('published_at') or r.get('finished_at') or '') if published else None
        return dict(available=self.cliente.disponible(self.c), scope=SCOPES[self.recipe],
                    active_job=self.public(active[0] if active else None), last_job=self.public(last),
                    # With the coverage of that publication: its pending resources and deferred refreshes stay visible
                    # while their previous data are served, even after a later update failed or was cancelled.
                    last_published=None if latest is None else dict(at=latest['result']['published_at'],
                                                                    coverage=latest['result'].get('coverage')))

    def close(self, timeout=5):
        self.stop_reconciler.set()
        if hasattr(self, 'reconciler_thread'):
            self.reconciler_thread.join(timeout)
        for thread in self.threads:
            thread.join(timeout)
