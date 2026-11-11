"""Isolated maintenance tests: use synthetic data and temporary SQLite copies only."""
import base64
import hashlib
import json
import sqlite3
import sys
import unittest
import uuid
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_carga import AMBITO, Base as CargaBase, CON_SQL  # noqa: E402

if CON_SQL:
    from alembic import command

    from persistencia import actualizacion, carga, comentarios, sincronizacion, sqlite
    from scripts import adopt_coordinator_fingerprint as maintenance


@unittest.skipUnless(
    CON_SQL and actualizacion.codigo() == maintenance.CURRENT_COORDINATOR_CODE,
    'La transición histórica 0004 → 0007 solo aplica a su huella de destino fijada',
)
class CoordinatorFingerprintMaintenance(CargaBase):
    def setUp(self):
        super().setUp()
        carga.cargar(self.importacion(), self.base)
        command.downgrade(carga.configuracion_alembic(self.base), '0004')
        self._set_historical_import_hash()
        self.description_before = sqlite.comprobar_copia(self.base)
        self.generation = 'fcca54bb-7ca8-4c07-bd00-aa459041fb3d'
        self.updates = self.base.with_name(self.base.name + '.actualizaciones')
        self.updates.mkdir()
        self.journal = self.updates / 'jobs.sqlite'
        self.control_journal = self.updates / 'control' / 'jobs.sqlite'
        self.control_journal.parent.mkdir()
        self._create_coordinator_history()
        self._create_control_history()
        self.backup = self.tmp / 'external-backups' / 'fingerprint-transition'
        self.work_before = self._work_rows()
        self.control_before = maintenance._db_signature(self.control_journal)
        self.source_inventory = maintenance._table_inventory(self.base)

    def _set_historical_import_hash(self):
        old_hashes = sorted(maintenance.HISTORICAL_IMPORT_CODES)
        with closing(sqlite3.connect(self.base)) as connection, connection:
            rows = connection.execute("SELECT id, summary FROM import_run WHERE status IN ('committed','no_changes')").fetchall()
            for index, (ident, summary_text) in enumerate(rows):
                if not summary_text:
                    continue
                summary = json.loads(summary_text)
                if isinstance(summary.get('codigo'), dict):
                    summary['codigo']['sha256_agregada'] = old_hashes[index % len(old_hashes)]
                    connection.execute('UPDATE import_run SET summary=? WHERE id=?',
                                       (json.dumps(summary, ensure_ascii=False), ident))

    def _create_coordinator_history(self):
        with closing(sqlite3.connect(self.journal)) as connection, connection:
            connection.executescript("""
                CREATE TABLE trabajo(id TEXT PRIMARY KEY, estado TEXT NOT NULL, detalle TEXT NOT NULL);
                CREATE UNIQUE INDEX unico_activo ON trabajo((1)) WHERE estado IN ('queued','running');
                CREATE TABLE confirmado(id INTEGER PRIMARY KEY CHECK(id=1), detalle TEXT NOT NULL);
            """)
            for _ in range(3):
                ident = str(uuid.uuid4())
                detail = dict(id=ident, estado='completed', fase='finished', entradas={
                    'exportaciones': [], 'calendarios': [], 'fichas': [], 'comentarios': []},
                    generacion=self.generation)
                connection.execute('INSERT INTO trabajo VALUES(?,?,?)',
                                   (ident, 'completed', json.dumps(detail, sort_keys=True)))
            confirmed = dict(codigo=maintenance.LEGACY_COORDINATOR_CODE,
                             ambito=hashlib.sha256(AMBITO.read_bytes()).hexdigest(),
                             generacion=self.generation, descripcion=self.description_before)
            connection.execute('INSERT INTO confirmado VALUES(1,?)',
                               (json.dumps(confirmed, ensure_ascii=False, sort_keys=True),))

    def _create_control_history(self):
        with closing(sqlite3.connect(self.control_journal)) as connection, connection:
            connection.execute('CREATE TABLE job(id TEXT PRIMARY KEY, idem TEXT UNIQUE, state TEXT, data TEXT)')
            for _ in range(3):
                ident, idem = str(uuid.uuid4()), str(uuid.uuid4())
                item = dict(id=ident, idem=idem, status='completed', phase='finished')
                connection.execute('INSERT INTO job VALUES(?,?,?,?)',
                                   (ident, idem, 'completed', json.dumps(item, sort_keys=True)))

    def _work_rows(self):
        with closing(sqlite3.connect(self.journal)) as connection:
            return connection.execute('SELECT id,estado,detalle FROM trabajo ORDER BY id').fetchall()

    def _confirmed(self):
        with closing(sqlite3.connect(self.journal)) as connection:
            return json.loads(connection.execute('SELECT detalle FROM confirmado WHERE id=1').fetchone()[0])

    def _adopt(self, checkpoint=lambda _name: None):
        return maintenance.adopt(self.base, AMBITO, self.backup, maintenance.CONFIRMATION, _checkpoint=checkpoint)

    def test_transition_preserves_sports_history_and_publishes_fixture_comments(self):
        result = self._adopt()
        self.assertEqual(result['status'], 'adopted')
        self.assertEqual((result['source_revision'], result['target_revision']), ('0004', '0007'))
        self.assertEqual(result['coordinator_jobs_preserved'], 3)
        self.assertEqual(result['coordinator_completed_jobs_preserved'], 3)
        self.assertEqual(result['generation'], self.generation)
        self.assertEqual(sqlite.revision(self.base), '0007')
        self.assertEqual(self._work_rows(), self.work_before)
        self.assertEqual(maintenance._db_signature(self.control_journal), self.control_before)
        self.assertEqual(self._confirmed()['codigo'], actualizacion.codigo())
        after_inventory = maintenance._table_inventory(self.base)
        for table, signature in self.source_inventory.items():
            self.assertEqual(after_inventory[table], signature, table)
        self.assertEqual(maintenance._import_hashes(self.base), result['historical_import_code_sha256'])
        self.assertTrue(sincronizacion.verificar(self.base)['ok'])

        source_event_id = str(self.consulta(
            "SELECT external_id FROM match_external_id WHERE provider='sofascore' AND status='origin' ORDER BY entity_id LIMIT 1"
        )[0][0])
        raw = json.dumps({'comments': [{'id': 77, 'text': 'Gol de prueba', 'type': 'scoreChange',
                                        'time': 42, 'periodName': '1ST'}]},
                         ensure_ascii=False, separators=(',', ':')).encode()
        record = dict(event_id=int(source_event_id), status=200, raw_b64=base64.b64encode(raw).decode('ascii'),
                      raw_sha256=hashlib.sha256(raw).hexdigest(), reason=None,
                      captured_at='2026-11-04T12:00:00+00:00',
                      transport_report={'provider': 'fixture-site', 'mode': 'offline-fixture'})
        attempt = self.tmp / 'fixture-comments.jsonl'
        attempt.write_text(json.dumps(record, ensure_ascii=False) + '\n', encoding='utf-8')
        with actualizacion.Coordinador(self.base, AMBITO, reserva=0) as coordinator:
            request_id = coordinator.solicitar(comentarios=[attempt])
            finished = coordinator.esperar(request_id)
            self.assertEqual(finished['estado'], 'completed', finished)
            status, body = coordinator.capturar().consultar_comentarios(f'sofascore-match-{source_event_id}')
            self.assertEqual(status, 200)
            self.assertEqual(body['availability'], 'available')
            self.assertEqual(body['comments'][0]['text'], 'Gol de prueba')
        self.assertEqual(sqlite.revision(self.base), '0007')
        current_work = self._work_rows()
        self.assertTrue(all(row in current_work for row in self.work_before))
        self.assertEqual(len(current_work), 4)
        self.assertTrue(sincronizacion.verificar(self.base)['ok'])

    def test_interruption_after_migration_resumes_from_same_snapshot(self):
        def interrupt(name):
            if name == 'migrated':
                raise RuntimeError('simulated process interruption')

        with self.assertRaisesRegex(RuntimeError, 'simulated process interruption'):
            self._adopt(interrupt)
        self.assertEqual(sqlite.revision(self.base), '0007')
        self.assertEqual(self._confirmed()['codigo'], maintenance.LEGACY_COORDINATOR_CODE)
        self.assertEqual(self._work_rows(), self.work_before)
        result = self._adopt()
        self.assertEqual(result['status'], 'adopted')
        self.assertEqual(self._confirmed()['generacion'], self.generation)
        self.assertEqual(self._work_rows(), self.work_before)

    def test_interruption_after_journal_commit_is_idempotent(self):
        def interrupt(name):
            if name == 'adopted':
                raise RuntimeError('simulated lost response after commit')

        with self.assertRaisesRegex(RuntimeError, 'lost response'):
            self._adopt(interrupt)
        self.assertEqual(self._confirmed()['codigo'], actualizacion.codigo())
        self.assertFalse((self.backup / 'adoption.json').exists())
        confirmed = self._confirmed()
        confirmed['unexpected'] = 'must be rejected'
        with closing(sqlite3.connect(self.journal)) as connection, connection:
            connection.execute('UPDATE confirmado SET detalle=? WHERE id=1', (json.dumps(confirmed),))
        with self.assertRaises(maintenance.MaintenanceRejected):
            self._adopt()
        confirmed.pop('unexpected')
        with closing(sqlite3.connect(self.journal)) as connection, connection:
            connection.execute('UPDATE confirmado SET detalle=? WHERE id=1', (json.dumps(confirmed),))
        result = self._adopt()
        self.assertEqual(result['status'], 'adopted')
        self.assertTrue((self.backup / 'adoption.json').is_file())
        self.assertEqual(self._work_rows(), self.work_before)

    def test_unknown_fingerprint_and_active_work_are_rejected_without_migration(self):
        with closing(sqlite3.connect(self.journal)) as connection, connection:
            confirmed = self._confirmed()
            confirmed['codigo'] = '0' * 64
            connection.execute('UPDATE confirmado SET detalle=? WHERE id=1', (json.dumps(confirmed),))
        with self.assertRaises(maintenance.MaintenanceRejected):
            self._adopt()
        self.assertEqual(sqlite.revision(self.base), '0004')
        self.assertFalse((self.backup / 'manifest.json').exists())

        # Restore a fresh known coordinator state, then demonstrate an active update is never adopted.
        confirmed['codigo'] = maintenance.LEGACY_COORDINATOR_CODE
        confirmed['descripcion'] = self.description_before
        self.backup = self.tmp / 'external-backups' / 'active-case'
        with closing(sqlite3.connect(self.journal)) as connection, connection:
            connection.execute('UPDATE confirmado SET detalle=? WHERE id=1', (json.dumps(confirmed),))
            row = connection.execute('SELECT id,detalle FROM trabajo ORDER BY id LIMIT 1').fetchone()
            detail = json.loads(row[1])
            detail.update(estado='running', fase='preparing')
            connection.execute('UPDATE trabajo SET estado=?,detalle=? WHERE id=?',
                               ('running', json.dumps(detail), row[0]))
        with self.assertRaises(maintenance.MaintenanceRejected):
            self._adopt()
        self.assertEqual(sqlite.revision(self.base), '0004')
        self.assertEqual(self._confirmed()['codigo'], maintenance.LEGACY_COORDINATOR_CODE)

    def test_verified_snapshot_restores_database_and_both_journals_as_a_pair(self):
        self._adopt()
        result = maintenance.restore(
            self.base, AMBITO, self.backup,
            f'restore coordinator snapshot {maintenance._load_manifest(self.backup)["manifest_sha256"]}')
        self.assertEqual(result['status'], 'restored')
        self.assertEqual(sqlite.revision(self.base), '0004')
        self.assertEqual(sqlite.comprobar_copia(self.base), self.description_before)
        self.assertEqual(self._confirmed()['codigo'], maintenance.LEGACY_COORDINATOR_CODE)
        self.assertEqual(self._confirmed()['generacion'], self.generation)
        self.assertEqual(self._work_rows(), self.work_before)
        self.assertEqual(maintenance._db_signature(self.control_journal), self.control_before)
        self.assertTrue(Path(result['preserved_current_dir']).is_dir())
        again = maintenance.restore(
            self.base, AMBITO, self.backup,
            f'restore coordinator snapshot {maintenance._load_manifest(self.backup)["manifest_sha256"]}')
        self.assertEqual(again['status'], 'already_restored')


if __name__ == '__main__':
    unittest.main()
