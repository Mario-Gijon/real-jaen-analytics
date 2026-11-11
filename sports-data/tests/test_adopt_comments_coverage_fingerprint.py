"""Focused, disposable Alembic-0008 checks for the comments-coverage fingerprint transition."""

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT))

from scripts import adopt_comments_coverage_fingerprint as transition  # noqa: E402
from persistencia import actualizacion, sqlite  # noqa: E402
from test_sincronizacion import Base as SynchronizedDatabaseBase  # noqa: E402
from test_carga import AMBITO  # noqa: E402


class CommentsCoverageFingerprintTests(SynchronizedDatabaseBase):
    def setUp(self):
        super().setUp()
        self.workspace = self.tmp / "administrative-state"
        self.workspace.mkdir()
        self.workspace.chmod(0o700)
        self.assertEqual(sqlite.revision(self.base), "0008")
        self.scope = self.workspace / "ambito.json"
        self.scope.write_bytes(AMBITO.read_bytes())
        self.old_scope_sha = transition.SCOPE_SHA256
        transition.SCOPE_SHA256 = hashlib.sha256(self.scope.read_bytes()).hexdigest()
        self.addCleanup(setattr, transition, "SCOPE_SHA256", self.old_scope_sha)
        self.generation = transition.GENERATION
        self.updates = Path(str(self.base) + ".actualizaciones")
        self.updates.mkdir()
        self.journal = self.updates / "jobs.sqlite"
        self._create_coordinator_journal()
        self.control = self.updates / "control" / "jobs.sqlite"
        self.control.parent.mkdir()
        with closing(sqlite3.connect(self.control)) as connection:
            connection.execute("CREATE TABLE job(id TEXT PRIMARY KEY, idem TEXT, state TEXT, data TEXT)")
            connection.execute("INSERT INTO job VALUES ('http-1','idem-1','completed',?)",
                               (json.dumps({"id": "http-1", "status": "completed"}),))
            connection.commit()
        self.acquisition_root = self.workspace / "acquisition-jobs"
        self.acquisition_root.mkdir()
        self.acquisition = self.acquisition_root / "jobs.sqlite"
        with closing(sqlite3.connect(self.acquisition)) as connection:
            connection.execute("CREATE TABLE job(id TEXT PRIMARY KEY, request TEXT, state TEXT, data TEXT)")
            connection.execute("INSERT INTO job VALUES ('acq-1','request-1','completed',?)",
                               (json.dumps({"id": "acq-1"}),))
            connection.commit()
        (self.base.parent / "evidence").mkdir()
        (self.base.parent / "evidence" / "receipt.json").write_text('{"kept":true}\n', encoding="utf-8")
        (self.acquisition_root / "reports").mkdir()
        (self.acquisition_root / "reports" / "report.json").write_text('{"finished":true}\n', encoding="utf-8")
        self.backup = self.workspace / "backup-parent" / "transition"
        self.original_description = sqlite.describir(self.base)
        self.original_base_signature = transition._db_signature(self.base)

    def _create_coordinator_journal(self):
        meta = {
            "codigo": transition.SOURCE,
            "ambito": hashlib.sha256(self.scope.read_bytes()).hexdigest(),
            "generacion": self.generation,
            "descripcion": sqlite.describir(self.base),
        }
        with closing(sqlite3.connect(self.journal)) as connection:
            connection.executescript(
                "CREATE TABLE trabajo(id TEXT PRIMARY KEY, estado TEXT NOT NULL, detalle TEXT NOT NULL);"
                "CREATE UNIQUE INDEX unico_activo ON trabajo((1)) WHERE estado IN ('queued','running');"
                "CREATE TABLE confirmado(id INTEGER PRIMARY KEY CHECK(id=1), detalle TEXT NOT NULL);"
            )
            job = {"id": "coord-1", "estado": "completed", "fase": "finished"}
            connection.execute("INSERT INTO trabajo VALUES ('coord-1','completed',?)",
                               (json.dumps(job, sort_keys=True),))
            connection.execute("INSERT INTO confirmado VALUES (1,?)",
                               (json.dumps(meta, ensure_ascii=False, sort_keys=True, separators=(",", ":")),))
            connection.commit()

    def _confirmed(self):
        with closing(sqlite3.connect(self.journal)) as connection:
            return json.loads(connection.execute("SELECT detalle FROM confirmado WHERE id=1").fetchone()[0])

    def _transition(self):
        return transition.prepare(self.base, self.scope, self.acquisition, self.backup)

    def _source_runtime_mock(self, root, *, base=None, scope=None, open_coordinator=False):
        if not open_coordinator:
            return {"source_runtime_code": transition.SOURCE}
        with mock.patch.object(actualizacion, "codigo", return_value=transition.SOURCE):
            with actualizacion.Coordinador(base, scope) as coordinator:
                captured = coordinator.capturar().id
        return {"generation": captured, "code": transition.SOURCE}

    def _assert_sports_unchanged(self):
        self.assertEqual(sqlite.describir(self.base), self.original_description)
        self.assertEqual(transition._db_signature(self.base), self.original_base_signature)
        self.assertEqual(sqlite.revision(self.base), "0008")

    def test_prepare_preserves_eight_nested_candidate_wal_and_shm_pairs(self):
        opened = []
        expected = {}
        try:
            for index in range(8):
                candidate_dir = self.updates / f"generation-{index:02d}"
                candidate_dir.mkdir()
                candidate = candidate_dir / "candidata.sqlite"
                connection = sqlite3.connect(candidate)
                opened.append(connection)
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA wal_autocheckpoint=0")
                connection.execute("CREATE TABLE evidence (value TEXT NOT NULL)")
                connection.execute("INSERT INTO evidence VALUES (?)", (f"generation-{index}",))
                connection.commit()
                wal = Path(str(candidate) + "-wal")
                shm = Path(str(candidate) + "-shm")
                self.assertTrue(wal.is_file(), f"missing WAL pair {index}")
                self.assertTrue(shm.is_file(), f"missing SHM pair {index}")
                if index == 0:
                    self.assertGreater(wal.stat().st_size, 0)
                for artifact in (candidate, wal, shm):
                    expected[artifact.relative_to(self.updates).as_posix()] = artifact.read_bytes()

            self._transition()
            _, manifest = transition._load_snapshot(self.backup)
            saved_updates = (self.backup / "payload" / (self.base.name + ".actualizaciones"))
            self.assertEqual(set(manifest["updates_artifacts"]), set(expected))
            for relative, original_bytes in expected.items():
                saved = saved_updates / relative
                self.assertEqual(saved.read_bytes(), original_bytes, relative)
                self.assertEqual(transition._file_sha(saved), manifest["updates_artifacts"][relative]["sha256"])

            # Verify the non-empty WAL still contains a readable committed database, without
            # opening or changing any SQLite artifact inside the verified snapshot.
            validation_dir = self.workspace / "wal-validation"
            validation_dir.mkdir(mode=0o700)
            saved_candidate = saved_updates / "generation-00" / "candidata.sqlite"
            for suffix in ("", "-wal", "-shm"):
                shutil.copyfile(Path(str(saved_candidate) + suffix),
                                Path(str(validation_dir / "candidata.sqlite") + suffix))
            with closing(sqlite3.connect(f"file:{validation_dir / 'candidata.sqlite'}?mode=ro", uri=True)) as check:
                self.assertEqual(check.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(check.execute("SELECT value FROM evidence").fetchone()[0], "generation-0")
            self.assertEqual(transition._load_snapshot(self.backup)[1]["payload_sha256"], manifest["payload_sha256"])
            self._assert_sports_unchanged()
        finally:
            for connection in opened:
                connection.close()

    def test_prepare_adopt_and_restore_change_only_confirmed_code(self):
        prepared = self._transition()
        self.assertEqual(prepared["source_code"], transition.SOURCE)
        self.assertEqual(prepared["target_code"], transition.TARGET)
        self.assertEqual(prepared["generation"], self.generation)
        _, manifest = transition._load_snapshot(self.backup)
        self.assertEqual(manifest["source_meta"], self._confirmed())
        self._assert_sports_unchanged()

        adopted = transition.adopt(self.base, self.scope, self.acquisition, self.backup,
                                   transition.ADOPT_CONFIRM)
        self.assertEqual(adopted["action"], "adopted")
        self.assertEqual(self._confirmed()["codigo"], transition.TARGET)
        self.assertEqual(self._confirmed()["generacion"], self.generation)
        self.assertEqual(self._confirmed()["descripcion"], self.original_description)
        self._assert_sports_unchanged()

        source_runtime_root = os.environ.get("RJ_SOURCE_RUNTIME_ROOT")
        if source_runtime_root:
            restored = transition.restore(self.base, self.scope, self.acquisition, self.backup,
                                          source_runtime_root, transition.RESTORE_CONFIRM)
            self.assertEqual(restored["source_runtime_open_verified"], self.generation)
        else:
            with mock.patch.object(transition, "_source_runtime_probe", side_effect=self._source_runtime_mock) as probe:
                restored = transition.restore(self.base, self.scope, self.acquisition, self.backup,
                                              self.workspace, transition.RESTORE_CONFIRM)
            self.assertEqual(probe.call_args.kwargs["open_coordinator"], True)
        self.assertEqual(restored["action"], "restored")
        self.assertEqual(self._confirmed(), manifest["source_meta"])
        self._assert_sports_unchanged()
        self.assertTrue((self.backup / "manifest.json").is_file())
        self.assertTrue((self.backup / "adoption.json").is_file())
        self.assertTrue((self.backup / "restore.json").is_file())

    def test_restore_rejects_any_post_adoption_coordinator_work(self):
        self._transition()
        transition.adopt(self.base, self.scope, self.acquisition, self.backup, transition.ADOPT_CONFIRM)
        with closing(sqlite3.connect(self.journal)) as connection:
            detail = {"id": "coord-later", "estado": "completed", "fase": "finished"}
            connection.execute("INSERT INTO trabajo VALUES ('coord-later','completed',?)",
                               (json.dumps(detail, sort_keys=True),))
            connection.commit()
        with mock.patch.object(transition, "_source_runtime_probe", side_effect=self._source_runtime_mock):
            with self.assertRaisesRegex(transition.MaintenanceRejected, "history differs"):
                transition.restore(self.base, self.scope, self.acquisition, self.backup,
                                   self.workspace, transition.RESTORE_CONFIRM)
        self.assertEqual(self._confirmed()["codigo"], transition.TARGET)
        self._assert_sports_unchanged()

    def test_adopt_failure_after_metadata_change_leaves_recoverable_receipt(self):
        self._transition()

        def stop_after_metadata(point):
            if point == "adopted":
                raise RuntimeError("injected stop")

        with self.assertRaisesRegex(RuntimeError, "injected stop"):
            transition.adopt(self.base, self.scope, self.acquisition, self.backup,
                             transition.ADOPT_CONFIRM, _checkpoint=stop_after_metadata)
        self.assertEqual(self._confirmed()["codigo"], transition.TARGET)
        adoption = transition._read_receipt(self.backup / "adoption.json", "adopted",
                                             transition._load_snapshot(self.backup)[1])
        self.assertFalse(adoption["coordinator_open_verified"])

        with mock.patch.object(transition, "_source_runtime_probe", side_effect=self._source_runtime_mock):
            restored = transition.restore(self.base, self.scope, self.acquisition, self.backup,
                                          self.workspace, transition.RESTORE_CONFIRM)
        self.assertEqual(restored["action"], "restored")
        self.assertEqual(self._confirmed(), transition._load_snapshot(self.backup)[1]["source_meta"])
        self._assert_sports_unchanged()

    def test_receipt_write_failure_rolls_back_only_after_snapshot_revalidation(self):
        self._transition()
        with mock.patch.object(transition, "_write_receipt", side_effect=OSError("injected disk failure")):
            with self.assertRaisesRegex(transition.MaintenanceRejected, "source fingerprint restored"):
                transition.adopt(self.base, self.scope, self.acquisition, self.backup,
                                 transition.ADOPT_CONFIRM)
        self.assertEqual(self._confirmed()["codigo"], transition.SOURCE)
        self.assertFalse((self.backup / "adoption.json").exists())
        self._assert_sports_unchanged()

    def test_failed_restore_probe_only_returns_metadata_to_target_if_state_is_unchanged(self):
        self._transition()
        transition.adopt(self.base, self.scope, self.acquisition, self.backup, transition.ADOPT_CONFIRM)
        def fail_open(root, *, base=None, scope=None, open_coordinator=False):
            if open_coordinator:
                raise transition.MaintenanceRejected("injected old runtime failure")
            return self._source_runtime_mock(root, base=base, scope=scope, open_coordinator=False)

        with mock.patch.object(transition, "_source_runtime_probe", side_effect=fail_open):
            with self.assertRaisesRegex(transition.MaintenanceRejected, "target code restored"):
                transition.restore(self.base, self.scope, self.acquisition, self.backup,
                                   self.workspace, transition.RESTORE_CONFIRM)
        self.assertEqual(self._confirmed()["codigo"], transition.TARGET)
        self._assert_sports_unchanged()

    def test_prepare_rejects_wrong_generation_or_active_coordinator_work(self):
        with closing(sqlite3.connect(self.journal)) as connection:
            connection.execute("UPDATE confirmado SET detalle=json_set(detalle,'$.generacion',?) WHERE id=1",
                               ("00000000-0000-4000-8000-000000000001",))
            connection.commit()
        with self.assertRaisesRegex(transition.MaintenanceRejected, "generation"):
            self._transition()
        self.assertFalse(self.backup.exists())

        with closing(sqlite3.connect(self.journal)) as connection:
            row = connection.execute("SELECT detalle FROM confirmado WHERE id=1").fetchone()[0]
            meta = json.loads(row)
            meta["generacion"] = self.generation
            connection.execute("UPDATE confirmado SET detalle=? WHERE id=1", (json.dumps(meta),))
            job = {"id": "active", "estado": "running", "fase": "fetching"}
            connection.execute("INSERT INTO trabajo VALUES ('active','running',?)", (json.dumps(job),))
            connection.commit()
        with self.assertRaisesRegex(transition.MaintenanceRejected, "Active or inconsistent coordinator job"):
            self._transition()
        self.assertFalse(self.backup.exists())

    def test_prepare_rejects_active_acquisition_job(self):
        with closing(sqlite3.connect(self.acquisition)) as connection:
            connection.execute("UPDATE job SET state='running' WHERE id='acq-1'")
            connection.commit()
        with self.assertRaisesRegex(transition.MaintenanceRejected, "Active or inconsistent Acquisition job"):
            self._transition()
        self.assertFalse(self.backup.exists())


if __name__ == "__main__":
    unittest.main()
