"""Shared update-job state for PostgreSQL-backed Sports Data installations."""
from datetime import datetime, timezone
import json
import math
import os
import uuid

from sqlalchemy import text


LEASE_ENV = 'SPORTS_DATA_UPDATE_LEASE_SECONDS'
HEARTBEAT_ENV = 'SPORTS_DATA_UPDATE_HEARTBEAT_SECONDS'


def _seconds(environ, name, default):
    raw = environ.get(name, str(default))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f'{name} debe ser un número positivo') from None
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f'{name} debe ser un número positivo')
    return value


def lease_configuration(environ=None):
    environ = os.environ if environ is None else environ
    lease = _seconds(environ, LEASE_ENV, 60)
    heartbeat = _seconds(environ, HEARTBEAT_ENV, 15)
    if heartbeat >= lease:
        raise ValueError(f'{HEARTBEAT_ENV} debe ser menor que {LEASE_ENV}')
    return lease, heartbeat


def _uuid(value):
    return uuid.UUID(str(value))


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def _decode(row):
    if row is None:
        return None
    value = row['data']
    if isinstance(value, str):
        value = json.loads(value)
    item = dict(value)
    publication_id = str(row['publication_id']) if row['publication_id'] else None
    last_publication_id = str(row['last_publication_id']) if row['last_publication_id'] else None
    item.update(id=str(row['job_id']), status=row['status'], phase=row['phase'],
                request_id=str(row['request_id']), acquisition_id=(str(row['acquisition_id'])
                                                                    if row['acquisition_id'] else None),
                owner_id=str(row['owner_id']) if row['owner_id'] else item.get('owner_id'),
                fencing_token=int(row['fencing_token']),
                cancel_requested=bool(row['cancel_requested']), published=bool(row['published']),
                requested_at=row['requested_at'].isoformat(),
                finished_at=row['finished_at'].isoformat() if row['finished_at'] else item.get('finished_at'))
    item['publication_pending_id'] = publication_id
    if last_publication_id is not None:
        item['confirmed_publication_id'] = last_publication_id
    return item


class LeaseLost(RuntimeError):
    """The caller no longer owns the shared update job."""


class SharedUpdateJobs:
    """Small transaction-oriented repository; no transaction spans acquisition or local file work."""

    def __init__(self, engine, owner_id, lease_seconds=60):
        self.engine = engine
        self.owner_id = _uuid(owner_id)
        self.lease_seconds = float(lease_seconds)

    def check_schema(self):
        try:
            with self.engine.connect() as connection:
                connection.execute(text('SELECT singleton_id FROM sports_data_update_state WHERE singleton_id = 1'))
        except Exception:
            raise RuntimeError('El esquema PostgreSQL de trabajos compartidos pg0003 no está aplicado') from None

    @staticmethod
    def _lock_counter(connection):
        connection.execute(text('SELECT fencing_token FROM sports_data_update_state WHERE singleton_id = 1 FOR UPDATE'))

    @staticmethod
    def _next_token(connection):
        return int(connection.execute(text("""
            UPDATE sports_data_update_state SET fencing_token = fencing_token + 1
            WHERE singleton_id = 1 RETURNING fencing_token
        """)).scalar_one())

    @staticmethod
    def _job(connection, ident, *, lock=False):
        suffix = ' FOR UPDATE' if lock else ''
        return connection.execute(text('SELECT * FROM sports_data_update_job WHERE job_id=:id' + suffix),
                                  {'id': _uuid(ident)}).mappings().first()

    @staticmethod
    def _attach_idempotency(connection, idem, job_id):
        connection.execute(text("""
            INSERT INTO sports_data_update_idempotency (idempotency_key, job_id)
            VALUES (:idem, :job) ON CONFLICT (idempotency_key) DO NOTHING
        """), {'idem': _uuid(idem), 'job': _uuid(job_id)})

    def find_or_link(self, idem):
        """Return an idempotent or currently active job and associate this key atomically."""
        with self.engine.begin() as connection:
            self._lock_counter(connection)
            mapped = connection.execute(text("""
                SELECT job.* FROM sports_data_update_idempotency AS key
                JOIN sports_data_update_job AS job ON job.job_id = key.job_id
                WHERE key.idempotency_key = :idem
            """), {'idem': _uuid(idem)}).mappings().first()
            if mapped is not None:
                return _decode(mapped)
            active = connection.execute(text("""
                SELECT * FROM sports_data_update_job
                WHERE status IN ('queued','running') ORDER BY requested_at LIMIT 1 FOR UPDATE
            """)).mappings().first()
            if active is None:
                return None
            self._attach_idempotency(connection, idem, active['job_id'])
            return _decode(active)

    def create_or_reuse(self, item, idem):
        """Serialize competing creators through the singleton fencing counter."""
        with self.engine.begin() as connection:
            self._lock_counter(connection)
            mapped = connection.execute(text("""
                SELECT job.* FROM sports_data_update_idempotency AS key
                JOIN sports_data_update_job AS job ON job.job_id = key.job_id
                WHERE key.idempotency_key = :idem
            """), {'idem': _uuid(idem)}).mappings().first()
            if mapped is not None:
                return _decode(mapped), False
            active = connection.execute(text("""
                SELECT * FROM sports_data_update_job
                WHERE status IN ('queued','running') ORDER BY requested_at LIMIT 1 FOR UPDATE
            """)).mappings().first()
            if active is not None:
                self._attach_idempotency(connection, idem, active['job_id'])
                return _decode(active), False
            token = self._next_token(connection)
            item.update(owner_id=str(self.owner_id), fencing_token=token)
            connection.execute(text("""
                INSERT INTO sports_data_update_job
                    (job_id,status,phase,request_id,owner_id,fencing_token,lease_expires_at,heartbeat_at,
                     cancel_requested,published,requested_at,updated_at,data)
                VALUES (:id,:status,:phase,:request_id,:owner,:token,
                        clock_timestamp() + make_interval(secs => CAST(:lease AS double precision)),
                        clock_timestamp(),false,false,clock_timestamp(),clock_timestamp(),CAST(:data AS jsonb))
            """), {'id': _uuid(item['id']), 'status': item['status'], 'phase': item['phase'],
                   'request_id': _uuid(item['request_id']), 'owner': self.owner_id, 'token': token,
                   'lease': self.lease_seconds, 'data': _json(item)})
            self._attach_idempotency(connection, idem, item['id'])
            return dict(item), True

    def get(self, ident):
        with self.engine.connect() as connection:
            row = self._job(connection, ident)
        if row is None:
            raise KeyError(ident)
        return _decode(row)

    def active(self):
        with self.engine.connect() as connection:
            row = connection.execute(text("""
                SELECT * FROM sports_data_update_job WHERE status IN ('queued','running')
                ORDER BY requested_at LIMIT 1
            """)).mappings().first()
        return _decode(row)

    def latest_finished(self):
        with self.engine.connect() as connection:
            row = connection.execute(text("""
                SELECT * FROM sports_data_update_job WHERE status NOT IN ('queued','running')
                ORDER BY requested_at DESC LIMIT 1
            """)).mappings().first()
        return _decode(row)

    def latest_published(self):
        with self.engine.connect() as connection:
            row = connection.execute(text("""
                SELECT * FROM sports_data_update_job WHERE published
                ORDER BY published_at DESC NULLS LAST LIMIT 1
            """)).mappings().first()
            item = _decode(row)
            publication_id = (row['last_publication_id'] or row['publication_id']) if row is not None else None
            if item is not None and not item.get('result') and publication_id is not None:
                publication = connection.execute(text("""
                    SELECT result_generation,published_at,run_status,result_json
                    FROM sports_data_publication WHERE publication_id=:id
                """), {'id': publication_id}).mappings().first()
                if publication is not None:
                    stored = publication['result_json'] or {}
                    if isinstance(stored, str):
                        stored = json.loads(stored)
                    item['result'] = dict(published_at=publication['published_at'].isoformat(),
                                          generation=str(publication['result_generation']),
                                          reused_bundle=bool(item.get('reused_bundle')),
                                          coverage=stored.get('coverage'),
                                          changed=publication['run_status'] != 'no_changes')
        return item

    def save(self, item):
        active = item['status'] in ('queued', 'running')
        with self.engine.begin() as connection:
            result = connection.execute(text("""
                UPDATE sports_data_update_job SET
                    status=:status, phase=:phase, request_id=:request_id,
                    acquisition_id=:acquisition_id, publication_id=:publication_id,
                    last_publication_id=:last_publication_id,
                    cancel_requested=cancel_requested OR :cancel, published=published OR :published,
                    data=CAST(:data AS jsonb),
                    updated_at=clock_timestamp(),
                    heartbeat_at=CASE WHEN :active THEN clock_timestamp() ELSE heartbeat_at END,
                    lease_expires_at=CASE WHEN :active
                        THEN clock_timestamp() + make_interval(secs => CAST(:lease AS double precision))
                        ELSE NULL END,
                    finished_at=CASE WHEN :active THEN NULL
                        WHEN CAST(:finished AS timestamptz) IS NULL THEN clock_timestamp()
                        ELSE CAST(:finished AS timestamptz) END,
                    published_at=COALESCE(published_at, CASE WHEN :published THEN clock_timestamp() END),
                    owner_id=CASE WHEN :active THEN :owner ELSE NULL END
                WHERE job_id=:id AND owner_id=:owner AND fencing_token=:token
                  AND status IN ('queued','running') AND lease_expires_at > clock_timestamp()
                RETURNING job_id
            """), {'status': item['status'], 'phase': item['phase'], 'request_id': _uuid(item['request_id']),
                   'acquisition_id': _uuid(item['acquisition_id']) if item.get('acquisition_id') else None,
                   'publication_id': _uuid(item['publication_pending_id'])
                   if item.get('publication_pending_id') else None,
                   'last_publication_id': _uuid(item['confirmed_publication_id'])
                   if item.get('confirmed_publication_id') else None,
                   'cancel': bool(item.get('cancel_requested')), 'published': bool(item.get('published')),
                   'data': _json(item), 'active': active, 'lease': self.lease_seconds,
                   'finished': item.get('finished_at'), 'owner': self.owner_id, 'id': _uuid(item['id']),
                   'token': int(item['fencing_token'])})
            if result.first() is None:
                raise LeaseLost('Propiedad del trabajo perdida')

    def heartbeat(self, ident, token):
        with self.engine.begin() as connection:
            row = connection.execute(text("""
                UPDATE sports_data_update_job SET heartbeat_at=clock_timestamp(), updated_at=clock_timestamp(),
                    lease_expires_at=clock_timestamp() + make_interval(secs => CAST(:lease AS double precision))
                WHERE job_id=:id AND owner_id=:owner AND fencing_token=:token
                  AND status IN ('queued','running') AND lease_expires_at > clock_timestamp()
                RETURNING job_id
            """), {'lease': self.lease_seconds, 'id': _uuid(ident), 'owner': self.owner_id,
                   'token': int(token)}).first()
        return row is not None

    def request_cancel(self, ident):
        with self.engine.begin() as connection:
            row = self._job(connection, ident, lock=True)
            if row is None:
                raise KeyError(ident)
            item = _decode(row)
            if item['status'] == 'cancelled':
                return item
            if item['status'] not in ('queued', 'running') or item['phase'] not in ('queued', 'acquiring'):
                from control import NotCancellable
                raise NotCancellable()
            item['cancel_requested'] = True
            connection.execute(text("""
                UPDATE sports_data_update_job SET cancel_requested=true, data=CAST(:data AS jsonb),
                    updated_at=clock_timestamp()
                WHERE job_id=:id AND status IN ('queued','running') AND phase IN ('queued','acquiring')
            """), {'data': _json(item), 'id': _uuid(ident)})
            return item

    def recover_expired(self):
        """Reconcile expired owners; only untouched queued jobs can be safely transferred."""
        with self.engine.connect() as connection:
            expired = connection.execute(text("""
                SELECT 1 FROM sports_data_update_job
                WHERE status IN ('queued','running') AND lease_expires_at <= clock_timestamp() LIMIT 1
            """)).first()
        if expired is None:
            return []
        reclaimed = []
        with self.engine.begin() as connection:
            self._lock_counter(connection)
            rows = connection.execute(text("""
                SELECT * FROM sports_data_update_job
                WHERE status IN ('queued','running') AND lease_expires_at <= clock_timestamp()
                ORDER BY requested_at FOR UPDATE
            """)).mappings().all()
            for row in rows:
                item = _decode(row)
                publication_id = row['publication_id']
                durable = None
                if publication_id is not None:
                    durable = connection.execute(text("""
                        SELECT result_generation,published_at,run_status,result_json
                        FROM sports_data_publication WHERE publication_id=:id
                    """), {'id': publication_id}).mappings().first()
                if durable is not None:
                    coverage = (item.get('result') or {}).get('coverage')
                    item.update(status='completed', phase='finished', published=True, finished_at=now_utc(),
                                resumable=False, confirmed_publication_id=str(publication_id),
                                result=dict(published_at=durable['published_at'].isoformat(),
                                            generation=str(durable['result_generation']),
                                            reused_bundle=bool(item.get('reused_bundle')), coverage=coverage,
                                            changed=durable['run_status'] != 'no_changes'),
                                reconciliation=dict(coordinator_state='completed', reconciled_at=now_utc()))
                    self._write_recovered(connection, item, owner=None, lease=None)
                    continue
                if item['phase'] == 'publishing' and publication_id is None:
                    # No durable ID means the COMMIT outcome cannot safely be inferred.
                    continue
                if item['status'] == 'queued' and item['phase'] == 'queued' \
                        and not item.get('acquisition_id') and publication_id is None:
                    token = self._next_token(connection)
                    item.update(owner_id=str(self.owner_id), fencing_token=token)
                    connection.execute(text("""
                        UPDATE sports_data_update_job SET owner_id=:owner, fencing_token=:token,
                            heartbeat_at=clock_timestamp(),
                            lease_expires_at=clock_timestamp() + make_interval(secs => CAST(:lease AS double precision)),
                            updated_at=clock_timestamp(), data=CAST(:data AS jsonb)
                        WHERE job_id=:id
                    """), {'owner': self.owner_id, 'token': token, 'lease': self.lease_seconds,
                           'data': _json(item), 'id': row['job_id']})
                    reclaimed.append(item)
                    continue
                item.update(status='interrupted', phase='finished', finished_at=now_utc(), resumable=False,
                            error=dict(code='INTERRUPTED',
                                       message='El propietario perdió el lease antes de completar el trabajo.',
                                       retryable=True))
                self._write_recovered(connection, item, owner=None, lease=None)
        return reclaimed

    @staticmethod
    def _write_recovered(connection, item, *, owner, lease):
        connection.execute(text("""
            UPDATE sports_data_update_job SET status=:status, phase=:phase, owner_id=:owner,
                lease_expires_at=:lease, acquisition_id=:acquisition, publication_id=:publication,
                last_publication_id=:last_publication,
                cancel_requested=:cancel, published=:published, data=CAST(:data AS jsonb),
                updated_at=clock_timestamp(), finished_at=COALESCE(CAST(:finished AS timestamptz), clock_timestamp()),
                published_at=COALESCE(published_at, CASE WHEN :published THEN clock_timestamp() END)
            WHERE job_id=:id
        """), {'status': item['status'], 'phase': item['phase'], 'owner': owner, 'lease': lease,
               'acquisition': _uuid(item['acquisition_id']) if item.get('acquisition_id') else None,
               'publication': _uuid(item['publication_pending_id']) if item.get('publication_pending_id') else None,
               'last_publication': _uuid(item['confirmed_publication_id'])
               if item.get('confirmed_publication_id') else None,
               'cancel': bool(item.get('cancel_requested')), 'published': bool(item.get('published')),
               'data': _json(item), 'finished': item.get('finished_at'), 'id': _uuid(item['id'])})


def now_utc():
    return datetime.now(timezone.utc).isoformat()
