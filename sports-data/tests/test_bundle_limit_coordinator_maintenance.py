"""Isolated 0007 coordinator fingerprint transition for the comments bundle limit change."""
import json
import shutil
import sqlite3
import tempfile
import unittest
import uuid
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

from test_carga import AMBITO, Base as CargaBase, CON_SQL

if CON_SQL:
    from persistencia import actualizacion, carga, sqlite, sincronizacion
    from scripts import adopt_bundle_limit_fingerprint as maintenance


# This maintenance tool is pinned to one reviewed code fingerprint. Its transition tests only
# apply while the current source still has that exact target fingerprint.
@unittest.skipUnless(
    CON_SQL and actualizacion.codigo() == maintenance.TARGET,
    'requires requirements-sql.txt and the exact reviewed 0007 target fingerprint',
)
class BundleLimitCoordinatorMaintenance(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='rj-bundle-limit-maintenance-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.base = self.tmp / 'state' / 'real-jaen.sqlite'
        fixture = SimpleNamespace(tmp=self.tmp, base=self.base)
        carga.cargar(CargaBase.importacion(fixture), self.base)
        self.description = sqlite.comprobar_copia(self.base)
        self.generation = str(uuid.uuid4())
        self.updates = self.base.with_name(self.base.name + '.actualizaciones')
        self.updates.mkdir()
        self.journal = self.updates / 'jobs.sqlite'
        self.control = self.updates / 'control' / 'jobs.sqlite'
        self.control.parent.mkdir()
        self._create_journals()
        self.backup = self.tmp / 'external-snapshots' / 'bundle-limit-transition'
        self.base_before = maintenance._db_signature(self.base)
        self.tables_before = maintenance._table_inventory(self.base)
        self.jobs_before = self._jobs()
        self.control_before = maintenance._db_signature(self.control)
        self.confirmed_before = self._confirmed()

    def _create_journals(self):
        scope_hash = maintenance._sha(AMBITO.read_bytes())
        with closing(sqlite3.connect(self.journal)) as connection, connection:
            connection.executescript("""
                CREATE TABLE trabajo(id TEXT PRIMARY KEY, estado TEXT NOT NULL, detalle TEXT NOT NULL);
                CREATE UNIQUE INDEX unico_activo ON trabajo((1)) WHERE estado IN ('queued','running');
                CREATE TABLE confirmado(id INTEGER PRIMARY KEY CHECK(id=1), detalle TEXT NOT NULL);
            """)
            for _ in range(2):
                ident = str(uuid.uuid4())
                detail = dict(id=ident, estado='completed', fase='finished', entradas={'comentarios': []},
                              generacion=self.generation)
                connection.execute('INSERT INTO trabajo VALUES(?,?,?)',
                                   (ident, 'completed', json.dumps(detail, sort_keys=True)))
            meta = dict(codigo=maintenance.SOURCE, ambito=scope_hash, generacion=self.generation,
                        descripcion=self.description)
            connection.execute('INSERT INTO confirmado VALUES(1,?)',
                               (json.dumps(meta, ensure_ascii=False, sort_keys=True),))
        with closing(sqlite3.connect(self.control)) as connection, connection:
            connection.execute('CREATE TABLE job(id TEXT PRIMARY KEY, idem TEXT UNIQUE, state TEXT, data TEXT)')
            ident, idem = str(uuid.uuid4()), str(uuid.uuid4())
            data = dict(id=ident, idem=idem, status='completed', phase='finished')
            connection.execute('INSERT INTO job VALUES(?,?,?,?)',
                               (ident, idem, 'completed', json.dumps(data, sort_keys=True)))

    def _jobs(self):
        with closing(sqlite3.connect(self.journal)) as connection:
            return connection.execute('SELECT id,estado,detalle FROM trabajo ORDER BY id').fetchall()

    def _confirmed(self):
        with closing(sqlite3.connect(self.journal)) as connection:
            return json.loads(connection.execute('SELECT detalle FROM confirmado WHERE id=1').fetchone()[0])

    def _adopt(self, checkpoint=lambda _name: None):
        return maintenance.adopt(self.base, AMBITO, self.backup, maintenance.ADOPT_CONFIRM,
                                 _checkpoint=checkpoint)

    def test_adopts_fingerprint_without_migration_or_sports_changes(self):
        result = self._adopt()
        self.assertEqual(result['action'], 'adopted')
        self.assertEqual((sqlite.revision(self.base), result['source_code'], result['target_code']),
                         ('0007', maintenance.SOURCE, actualizacion.codigo()))
        self.assertEqual(self.base_before, maintenance._db_signature(self.base))
        self.assertEqual(self.tables_before, maintenance._table_inventory(self.base))
        self.assertEqual(self.jobs_before, self._jobs())
        self.assertEqual(self.control_before, maintenance._db_signature(self.control))
        self.assertEqual(self._confirmed(), dict(self.confirmed_before, codigo=maintenance.TARGET))
        self.assertTrue(sincronizacion.verificar(self.base)['ok'])
        self.assertTrue((self.backup / 'manifest.json').is_file())
        self.assertTrue((self.backup / 'adoption.json').is_file())

    def test_interrupted_commit_resumes_and_explicit_restore_is_verified(self):
        def interrupt(name):
            if name == 'adopted':
                raise RuntimeError('simulated interruption after journal commit')

        with self.assertRaisesRegex(RuntimeError, 'simulated interruption'):
            self._adopt(interrupt)
        self.assertEqual(self._confirmed()['codigo'], maintenance.TARGET)
        result = self._adopt()
        self.assertEqual(result['action'], 'adopted')
        restored = maintenance.restore(self.base, AMBITO, self.backup, maintenance.RESTORE_CONFIRM)
        self.assertEqual(restored['action'], 'restored')
        self.assertEqual(self._confirmed(), self.confirmed_before)
        self.assertEqual(self.jobs_before, self._jobs())
        self.assertEqual(self.control_before, maintenance._db_signature(self.control))
        self.assertEqual(self.base_before, maintenance._db_signature(self.base))
        self.assertTrue(sincronizacion.verificar(self.base)['ok'])

    def test_active_job_is_rejected_before_snapshot_or_metadata_change(self):
        ident = str(uuid.uuid4())
        with closing(sqlite3.connect(self.journal)) as connection, connection:
            connection.execute('INSERT INTO trabajo VALUES(?,?,?)',
                               (ident, 'running', json.dumps(dict(id=ident, estado='running', fase='acquiring'))))
        with self.assertRaisesRegex(maintenance.MaintenanceRejected, 'Active or inconsistent'):
            self._adopt()
        self.assertFalse(self.backup.exists())
        self.assertEqual(self._confirmed(), self.confirmed_before)

    def test_restore_requires_a_matching_adoption_receipt(self):
        self._adopt()
        (self.backup / 'adoption.json').unlink()
        with self.assertRaisesRegex(maintenance.MaintenanceRejected, 'No valid adoption receipt'):
            maintenance.restore(self.base, AMBITO, self.backup, maintenance.RESTORE_CONFIRM)
        self.assertEqual(self._confirmed()['codigo'], maintenance.TARGET)


if __name__ == '__main__':
    unittest.main()
