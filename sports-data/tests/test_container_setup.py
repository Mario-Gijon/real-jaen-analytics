"""Container initialization uses only temporary fixtures; never reads the analyst database."""
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_carga import Base, CON_SQL

if CON_SQL:
    from persistencia import sqlite
    from persistencia.carga import cargar
    from scripts.initialize_snapshot import initialize


@unittest.skipUnless(CON_SQL, 'requiere dependencias SQL')
class InitialSnapshot(Base):
    def setUp(self):
        super().setUp()
        cargar(self.importacion(), self.base)
        self.snapshot = Path(sqlite.crear_copia(self.base, 'initialization')['ruta'])
        self.target = self.tmp / 'container' / 'real-jaen.sqlite'
        self.target.parent.mkdir()

    def test_verified_snapshot_and_existing_destination(self):
        expected = sqlite.describir(self.snapshot)
        result = initialize(self.snapshot, self.target)
        self.assertEqual(result['huella'], expected['huella'])
        self.assertEqual(sqlite.describir(self.target)['huella'], expected['huella'])
        with self.assertRaisesRegex(ValueError, 'ya existen'):
            initialize(self.snapshot, self.target)
        self.assertEqual(sqlite.describir(self.snapshot), expected)

    def test_failure_leaves_no_database_and_preserves_snapshot(self):
        expected = sqlite.describir(self.snapshot)
        with mock.patch.object(sqlite, 'copia_consistente', side_effect=RuntimeError('fallo simulado')):
            with self.assertRaises(RuntimeError):
                initialize(self.snapshot, self.target)
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.target.parent.glob('.initialize-*')), [])
        self.assertEqual(sqlite.describir(self.snapshot), expected)
        initialize(self.snapshot, self.target)

    def test_live_wal_input_is_rejected(self):
        wal = self.snapshot.with_name(self.snapshot.name + '-wal')
        wal.write_bytes(b'not a finished backup')
        with self.assertRaisesRegex(ValueError, 'sin WAL'):
            initialize(self.snapshot, self.target)
        self.assertFalse(self.target.exists())
