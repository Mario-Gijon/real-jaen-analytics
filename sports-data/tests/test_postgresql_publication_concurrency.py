"""Focused PostgreSQL 18 concurrency tests for shared update leases and publication ownership."""
import os
import re
import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest import mock


DATABASE_URL = os.environ.get('SPORTS_DATA_TEST_DATABASE_URL')


@unittest.skipUnless(DATABASE_URL, 'requires an isolated PostgreSQL 18 test database')
class PostgreSQLPublicationConcurrency(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from sqlalchemy import create_engine
        from sqlalchemy.engine import make_url

        url = make_url(DATABASE_URL)
        if not url.database or not url.database.startswith('rj_fix_'):
            raise RuntimeError('SPORTS_DATA_TEST_DATABASE_URL must target an rj_fix_* database')
        cls.bootstrap = create_engine(url, pool_size=1, max_overflow=0, pool_timeout=2)
        with cls.bootstrap.connect() as connection:
            database, version = connection.exec_driver_sql(
                'SELECT current_database(), current_setting(\'server_version_num\')::int').one()
        if database != url.database or version < 180000:
            raise RuntimeError('The isolated concurrency tests require PostgreSQL 18')

        cls.schema = 'rj_fix_' + uuid.uuid4().hex
        with cls.bootstrap.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{cls.schema}"')
        connect_args = {'options': f'-csearch_path={cls.schema}'}
        cls.engine = create_engine(url, connect_args=connect_args, pool_size=4, max_overflow=1, pool_timeout=2)
        cls.heartbeat_engine = create_engine(url, connect_args=connect_args, pool_size=1, max_overflow=0, pool_timeout=1)
        with cls.engine.begin() as connection:
            connection.exec_driver_sql("""
                CREATE TABLE sports_data_update_state (
                    singleton_id smallint PRIMARY KEY,
                    fencing_token bigint NOT NULL
                )
            """)
            connection.exec_driver_sql("INSERT INTO sports_data_update_state VALUES (1, 0)")
            connection.exec_driver_sql("""
                CREATE TABLE sports_data_update_job (
                    job_id uuid PRIMARY KEY,
                    status text NOT NULL,
                    phase text NOT NULL,
                    request_id uuid NOT NULL,
                    acquisition_id uuid,
                    publication_id uuid,
                    last_publication_id uuid,
                    owner_id uuid,
                    fencing_token bigint NOT NULL,
                    lease_expires_at timestamptz,
                    heartbeat_at timestamptz,
                    cancel_requested boolean NOT NULL DEFAULT false,
                    published boolean NOT NULL DEFAULT false,
                    requested_at timestamptz NOT NULL DEFAULT clock_timestamp(),
                    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
                    finished_at timestamptz,
                    published_at timestamptz,
                    data jsonb NOT NULL
                )
            """)
            connection.exec_driver_sql("""
                CREATE UNIQUE INDEX one_active_update ON sports_data_update_job ((1))
                WHERE status IN ('queued','running')
            """)
            connection.exec_driver_sql("""
                CREATE TABLE sports_data_update_idempotency (
                    idempotency_key uuid PRIMARY KEY,
                    job_id uuid NOT NULL
                )
            """)
            connection.exec_driver_sql("""
                CREATE TABLE sports_data_publication (
                    publication_id uuid PRIMARY KEY,
                    source_generation uuid NOT NULL,
                    result_generation uuid NOT NULL,
                    input_sha256 text NOT NULL,
                    published_at timestamptz NOT NULL,
                    run_status text NOT NULL,
                    result_json jsonb NOT NULL
                )
            """)
            connection.exec_driver_sql("""
                CREATE TABLE sports_data_publication_state (
                    singleton_id smallint PRIMARY KEY,
                    generation_id uuid NOT NULL,
                    generation_number bigint NOT NULL,
                    code_sha256 text,
                    scope_sha256 text,
                    last_publication_id uuid,
                    updated_at timestamptz NOT NULL
                )
            """)
            connection.exec_driver_sql("CREATE TABLE test_business_write (value text NOT NULL)")

    @classmethod
    def tearDownClass(cls):
        cls.heartbeat_engine.dispose()
        cls.engine.dispose()
        with cls.bootstrap.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{cls.schema}" CASCADE')
        cls.bootstrap.dispose()

    def setUp(self):
        from persistencia.trabajos_postgresql import SharedUpdateJobs

        self.owner = uuid.uuid4()
        self.other_owner = uuid.uuid4()
        self.jobs = SharedUpdateJobs(self.engine, self.owner, lease_seconds=3,
                                     heartbeat_engine=self.heartbeat_engine)
        self.other_jobs = SharedUpdateJobs(self.engine, self.other_owner, lease_seconds=3,
                                           heartbeat_engine=self.heartbeat_engine)
        self.created_job_id = None
        with self.engine.begin() as connection:
            connection.exec_driver_sql('DELETE FROM test_business_write')
            connection.exec_driver_sql('DELETE FROM sports_data_publication')
            connection.exec_driver_sql('DELETE FROM sports_data_publication_state')
            connection.exec_driver_sql("""
                INSERT INTO sports_data_publication_state
                    (singleton_id, generation_id, generation_number, updated_at)
                VALUES (1, '00000000-0000-4000-8000-000000000001', 0, clock_timestamp())
            """)

    def tearDown(self):
        if self.created_job_id is None:
            return
        with self.engine.begin() as connection:
            connection.exec_driver_sql("""
                UPDATE sports_data_update_job SET status='failed', phase='finished', owner_id=NULL,
                    lease_expires_at=NULL
                WHERE job_id=%s AND status IN ('queued','running')
            """, (uuid.UUID(self.created_job_id),))

    def create_job(self, *, phase='queued', acquisition_id=None, publication_id=None):
        item_id, request_id, idem = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        item = dict(
            id=str(item_id), request_id=str(request_id), idem=str(idem), status='queued', phase='queued',
            acquisition_id=str(acquisition_id) if acquisition_id else None,
            publication_pending_id=None, confirmed_publication_id=None, cancel_requested=False,
            published=False, requested_at='2026-11-26T00:00:00+00:00', finished_at=None,
            error=None, result=None,
        )
        created, is_new = self.jobs.create_or_reuse(item, str(idem))
        self.assertTrue(is_new)
        self.created_job_id = created['id']
        if phase != 'queued' or publication_id is not None:
            created.update(status='running', phase=phase,
                           publication_pending_id=str(publication_id) if publication_id else None)
            self.jobs.save(created)
        return created

    def test_heartbeat_and_status_reads_remain_available_during_long_writer_transaction(self):
        from persistencia.actualizacion import validar_propiedad_compartida
        from persistencia.sincronizacion import bloquear_escritura_postgresql

        publication_id = uuid.uuid4()
        item = self.create_job(phase='publishing', publication_id=publication_id)
        shared = dict(job_id=item['id'], owner_id=str(self.owner), fencing_token=item['fencing_token'])
        started = time.monotonic()
        with self.engine.connect() as writer:
            transaction = writer.begin()
            stop = threading.Event()
            renewals = []

            def heartbeat_loop():
                while not stop.wait(0.2):
                    renewals.append(self.jobs.renew_lease(item['id'], item['fencing_token']))

            heartbeat = threading.Thread(target=heartbeat_loop)
            heartbeat.start()
            try:
                bloquear_escritura_postgresql(writer)
                validar_propiedad_compartida(writer, shared, str(publication_id), bloquear=False)
                # Keep a publication transaction open beyond the three-second lease while heartbeats run
                # independently, and collect live monitoring-read latency samples.
                latencies = []
                deadline = started + 3.5
                while time.monotonic() < deadline:
                    read_started = time.monotonic()
                    self.assertEqual(self.jobs.get(item['id'])['phase'], 'publishing')
                    latencies.append(time.monotonic() - read_started)
                    time.sleep(0.15)
                self.assertGreater(time.monotonic() - started, self.jobs.lease_seconds)
                self.assertTrue(renewals)
                self.assertTrue(all(renewal is not None for renewal in renewals))
                self.assertLess(max(latencies), 1.0)
                self.assertEqual(self.other_jobs.recover_expired(), [])
                # Final confirmation takes the row lock only after the long preparation stage.
                validar_propiedad_compartida(writer, shared, str(publication_id), bloquear=True)
            finally:
                stop.set()
                heartbeat.join(timeout=2)
                transaction.rollback()

    def test_coordinator_commits_generation_and_publication_after_long_fixture_phase(self):
        from persistencia.actualizacion import CoordinadorPostgreSQL

        publication_id = uuid.uuid4()
        item = self.create_job(phase='publishing', publication_id=publication_id)
        old_generation_id = '00000000-0000-4000-8000-000000000001'
        new_generation = object()
        old_generation = object()
        shared = dict(job_id=item['id'], owner_id=str(self.owner), fencing_token=item['fencing_token'])
        coordinator = object.__new__(CoordinadorPostgreSQL)
        coordinator.motor = self.engine
        coordinator.ambito = 'synthetic-scope'
        coordinator._puerta = threading.Lock()
        journal = []
        coordinator._leer = lambda _ident: dict(
            id=str(publication_id), estado='running', fase='publishing', entradas={},
            generacion_base=old_generation_id, trabajo_compartido=shared,
        )
        coordinator._guardar = lambda job, *_args, **_kwargs: journal.append(
            (dict(job), self.engine.pool.checkedout()))
        coordinator._congelar_postgresql = lambda _job: ({}, 'a' * 64)
        coordinator._frontera = lambda _name: None
        coordinator._reglas = lambda: dict(codigo='b' * 64, ambito='c' * 64)
        coordinator._instalar_generacion = lambda _generation: old_generation
        coordinator._liberar_generacion = lambda _generation: None
        coordinator._numero_generacion = 0
        coordinator._ultima_comprobacion_generacion = 0
        coordinator._comprobacion_generacion_valida = True
        coordinator._bloqueado = None

        stop = threading.Event()
        renewals = []
        import_started = threading.Event()

        def heartbeat_loop():
            while not stop.wait(0.2):
                renewals.append(self.jobs.renew_lease(item['id'], item['fencing_token']))

        def slow_fixture_import(_connection, **_kwargs):
            import_started.set()
            time.sleep(3.4)
            return dict(estado='no_changes', cambios={}, ficheros=[])

        heartbeat = threading.Thread(target=heartbeat_loop)
        heartbeat.start()
        read_latencies = []
        started = time.monotonic()
        try:
            with mock.patch('persistencia.sincronizacion.importar_evidencia_postgresql',
                            side_effect=slow_fixture_import), \
                    mock.patch('persistencia.sincronizacion.exigir_coherencia'), \
                    mock.patch('persistencia.comentarios.comments_schema_ready', return_value=True), \
                    mock.patch('enrichment.store.persist_selected_enrichments_postgresql', return_value={'unchanged': 0}), \
                    mock.patch('enrichment.store.ensure_selected_enrichments_complete_postgresql'), \
                    mock.patch('persistencia.actualizacion.preparar_postgresql', return_value=new_generation), \
                    self.assertLogs('persistencia.actualizacion', level='INFO') as phase_logs:
                worker = threading.Thread(target=coordinator._ejecutar, args=(str(publication_id),))
                worker.start()
                self.assertTrue(import_started.wait(timeout=2))

                barrier = threading.Barrier(5)

                def concurrent_monitor_read():
                    barrier.wait()
                    read_started = time.monotonic()
                    job_state = self.jobs.get(item['id'])
                    with self.engine.connect() as connection:
                        connection.exec_driver_sql('SELECT pg_sleep(0.05)')
                        generation_state = connection.exec_driver_sql(
                            'SELECT generation_id,generation_number FROM sports_data_publication_state '
                            'WHERE singleton_id=1').one()
                    return (time.monotonic() - read_started, job_state['phase'],
                            str(generation_state[0]), generation_state[1])

                with ThreadPoolExecutor(max_workers=5) as pool:
                    parallel_reads = [future.result(timeout=3) for future in
                                      [pool.submit(concurrent_monitor_read) for _ in range(5)]]
                for latency, phase, generation_id, generation_number in parallel_reads:
                    self.assertEqual(phase, 'publishing')
                    self.assertEqual((generation_id, generation_number), (old_generation_id, 0))
                    read_latencies.append(latency)
                while worker.is_alive():
                    read_started = time.monotonic()
                    current_job = self.jobs.get(item['id'])
                    with self.engine.connect() as connection:
                        generation = connection.exec_driver_sql(
                            'SELECT generation_id,generation_number FROM sports_data_publication_state '
                            'WHERE singleton_id=1').one()
                    self.assertEqual(current_job['phase'], 'publishing')
                    self.assertEqual((str(generation[0]), generation[1]), (old_generation_id, 0))
                    read_latencies.append(time.monotonic() - read_started)
                    time.sleep(0.1)
                worker.join()
            duration = time.monotonic() - started
        finally:
            stop.set()
            heartbeat.join(timeout=2)

        self.assertGreater(duration, self.jobs.lease_seconds)
        self.assertTrue(renewals)
        self.assertTrue(all(renewal is not None for renewal in renewals))
        self.assertTrue(read_latencies)
        self.assertLess(max(read_latencies), 1.0)
        self.assertTrue(journal)
        self.assertEqual(journal[-1][0].get('estado'), 'completed', journal[-1][0].get('error'))
        self.assertEqual(journal[-1][1], 0, 'local journal must be written after releasing the PostgreSQL connection')
        with self.engine.connect() as connection:
            publication = connection.exec_driver_sql("""
                SELECT source_generation,result_generation,run_status FROM sports_data_publication
                WHERE publication_id=%s
            """, (publication_id,)).one()
            generation = connection.exec_driver_sql("""
                SELECT generation_id,generation_number,last_publication_id
                FROM sports_data_publication_state WHERE singleton_id=1
            """).one()
            confirmed_job = self.jobs.get(item['id'])
        self.assertEqual(str(publication[0]), old_generation_id)
        self.assertEqual(publication[2], 'no_changes')
        self.assertEqual(str(publication[1]), str(generation[0]))
        self.assertEqual(generation[1], 1)
        self.assertEqual(str(generation[2]), str(publication_id))
        self.assertEqual(confirmed_job['confirmed_publication_id'], str(publication_id))

        phases = {
            name: float(milliseconds)
            for record in phase_logs.output
            if (match := re.search(r'phase=([a-z_]+) duration_ms=([0-9.]+)', record))
            for name, milliseconds in [match.groups()]
        }
        self.assertIn('commit', phases)
        p95_index = min(len(read_latencies) - 1, int(len(read_latencies) * 0.95))
        print('synthetic_publication_seconds={:.3f} phase_ms={} monitoring_reads={} p95_ms={:.1f} max_ms={:.1f}'.format(
            duration, {name: round(phases[name], 1) for name in
                       ('sports_import', 'comments_import', 'enrichment', 'coherence', 'generation_build', 'commit')},
            len(read_latencies), sorted(read_latencies)[p95_index] * 1000, max(read_latencies) * 1000))

    def test_recovery_skips_an_inflight_publication_without_waiting_or_fencing_it(self):
        from persistencia.sincronizacion import bloquear_escritura_postgresql

        publication_id = uuid.uuid4()
        item = self.create_job(phase='publishing', publication_id=publication_id)
        with self.engine.begin() as connection:
            connection.exec_driver_sql("""
                UPDATE sports_data_update_job SET lease_expires_at=clock_timestamp()-interval '1 second'
                WHERE job_id=%s
            """, (uuid.UUID(item['id']),))

        with self.engine.connect() as writer:
            transaction = writer.begin()
            try:
                bloquear_escritura_postgresql(writer)
                started = time.monotonic()
                self.assertEqual(self.other_jobs.recover_expired(), [])
                self.assertLess(time.monotonic() - started, 0.5)
                current = self.jobs.get(item['id'])
                self.assertEqual(current['status'], 'running')
                self.assertEqual(current['fencing_token'], item['fencing_token'])
            finally:
                transaction.rollback()

        recovered = self.other_jobs.recover_expired()
        self.assertEqual(recovered, [])
        self.assertEqual(self.jobs.get(item['id'])['status'], 'interrupted')

    def test_lost_lease_rolls_back_all_uncommitted_business_changes(self):
        from persistencia.actualizacion import CoordinacionRechazada, validar_propiedad_compartida
        from persistencia.sincronizacion import bloquear_escritura_postgresql

        publication_id = uuid.uuid4()
        item = self.create_job(phase='publishing', publication_id=publication_id)
        shared = dict(job_id=item['id'], owner_id=str(self.owner), fencing_token=item['fencing_token'])
        with self.engine.connect() as writer:
            transaction = writer.begin()
            try:
                bloquear_escritura_postgresql(writer)
                validar_propiedad_compartida(writer, shared, str(publication_id), bloquear=False)
                writer.exec_driver_sql("INSERT INTO test_business_write VALUES ('must rollback')")
                # Simulate a genuine ownership change while no row lock is held.
                with self.engine.begin() as successor:
                    successor.exec_driver_sql("""
                        UPDATE sports_data_update_job SET owner_id=%s, fencing_token=fencing_token+1,
                            lease_expires_at=clock_timestamp()+interval '3 seconds'
                        WHERE job_id=%s
                    """, (uuid.uuid4(), uuid.UUID(item['id'])))
                with self.assertRaises(CoordinacionRechazada):
                    validar_propiedad_compartida(writer, shared, str(publication_id), bloquear=True)
                transaction.rollback()
            finally:
                if transaction.is_active:
                    transaction.rollback()
        with self.engine.connect() as connection:
            self.assertEqual(connection.exec_driver_sql('SELECT count(*) FROM test_business_write').scalar_one(), 0)

    def test_expired_lease_rejects_publication_and_rolls_back_business_changes(self):
        from persistencia.actualizacion import CoordinacionRechazada, validar_propiedad_compartida
        from persistencia.sincronizacion import bloquear_escritura_postgresql

        publication_id = uuid.uuid4()
        item = self.create_job(phase='publishing', publication_id=publication_id)
        shared = dict(job_id=item['id'], owner_id=str(self.owner), fencing_token=item['fencing_token'])
        with self.engine.connect() as writer:
            transaction = writer.begin()
            try:
                bloquear_escritura_postgresql(writer)
                validar_propiedad_compartida(writer, shared, str(publication_id), bloquear=False)
                writer.exec_driver_sql("INSERT INTO test_business_write VALUES ('expired lease')")
                with self.engine.begin() as successor:
                    successor.exec_driver_sql("""
                        UPDATE sports_data_update_job
                        SET lease_expires_at=clock_timestamp()-interval '1 second'
                        WHERE job_id=%s
                    """, (uuid.UUID(item['id']),))
                with self.assertRaises(CoordinacionRechazada):
                    validar_propiedad_compartida(writer, shared, str(publication_id), bloquear=True)
                transaction.rollback()
            finally:
                if transaction.is_active:
                    transaction.rollback()
        with self.engine.connect() as connection:
            self.assertEqual(connection.exec_driver_sql('SELECT count(*) FROM test_business_write').scalar_one(), 0)

    def test_expired_owner_never_adopts_successors_fencing_token(self):
        item = self.create_job()
        old_token = item['fencing_token']
        with self.engine.begin() as connection:
            connection.exec_driver_sql("""
                UPDATE sports_data_update_job SET lease_expires_at=clock_timestamp()-interval '1 second'
                WHERE job_id=%s
            """, (uuid.UUID(item['id']),))
        reclaimed = self.other_jobs.recover_expired()
        self.assertEqual(len(reclaimed), 1)
        self.assertNotEqual(reclaimed[0]['owner_id'], str(self.owner))
        self.assertGreater(reclaimed[0]['fencing_token'], old_token)
        self.assertIsNone(self.jobs.renew_lease(item['id'], old_token))
        self.assertIsNotNone(self.other_jobs.renew_lease(item['id'], reclaimed[0]['fencing_token']))

    def test_recovery_uses_durable_publication_after_uncertain_commit(self):
        publication_id = uuid.uuid4()
        item = self.create_job(phase='publishing', publication_id=publication_id)
        with self.engine.begin() as connection:
            connection.exec_driver_sql("""
                UPDATE sports_data_update_job SET lease_expires_at=clock_timestamp()-interval '1 second'
                WHERE job_id=%s
            """, (uuid.UUID(item['id']),))
            connection.exec_driver_sql("""
                INSERT INTO sports_data_publication
                    (publication_id,source_generation,result_generation,input_sha256,published_at,run_status,result_json)
                VALUES (%s,%s,%s,%s,clock_timestamp(),'no_changes','{}'::jsonb)
            """, (publication_id, uuid.uuid4(), uuid.uuid4(), 'a' * 64))
        self.assertEqual(self.other_jobs.recover_expired(), [])
        current = self.jobs.get(item['id'])
        self.assertEqual(current['status'], 'completed')
        self.assertTrue(current['published'])
        self.assertEqual(current['confirmed_publication_id'], str(publication_id))


if __name__ == '__main__':
    unittest.main()
