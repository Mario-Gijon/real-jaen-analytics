"""Focused adopt/restore tests over disposable 0007 SQLite installations only."""
from contextlib import closing
import hashlib
import io
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import subprocess
import tarfile
import unittest
import uuid
from unittest import mock

from alembic import command

ROOT = Path(__file__).resolve().parents[1]
SOURCE_RUNTIME_COMMIT = "6812d080e7afbf6f95248afddf41c46c3a79af2d"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from persistencia import actualizacion, carga, sqlite  # noqa: E402
from scripts import transition_enrichment_0008 as prepare  # noqa: E402
from scripts import transition_enrichment_0008_admin as admin  # noqa: E402
from test_carga import AMBITO, Base as CargaBase  # noqa: E402
import test_transition_enrichment_0008 as preparation_tests  # noqa: E402


class EnrichmentAdminTransitionTests(unittest.TestCase):
    def setUp(self):
        self.workspace = Path(tempfile.mkdtemp(prefix="rj-enrichment-admin-tests-"))
        self.addCleanup(shutil.rmtree, self.workspace, True)
        self.source_runtime = self.workspace / "source-runtime"
        self.source_runtime.mkdir()
        archive = subprocess.run(
            ["git", "archive", SOURCE_RUNTIME_COMMIT], cwd=ROOT, check=True, capture_output=True,
        ).stdout
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as source_archive:
            for member in source_archive.getmembers():
                target = (self.source_runtime / member.name).resolve()
                if target != self.source_runtime and self.source_runtime not in target.parents:
                    self.fail(f"Unsafe historical runtime archive path: {member.name}")
            source_archive.extractall(self.source_runtime)
        environment = dict(__import__("os").environ)
        environment["PYTHONPATH"] = str(self.source_runtime)
        environment.pop("PYTHONHOME", None)
        old_runtime = subprocess.run(
            [sys.executable, "-c", "from persistencia import actualizacion; print(actualizacion.codigo())"],
            cwd=self.source_runtime, env=environment, check=True, capture_output=True, text=True,
        )
        self.source_code = old_runtime.stdout.strip().splitlines()[-1]
        self.target_code = actualizacion.codigo()
        self.fixture = CargaBase("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        repository_guard = carga.repositorio_que_contiene
        tmp_git_marker = Path("/tmp/.git")

        def ignore_incomplete_tmp_marker(path):
            resolved = Path(path).resolve()
            if ((resolved == Path("/tmp") or Path("/tmp") in resolved.parents)
                    and tmp_git_marker.is_dir() and not (tmp_git_marker / "HEAD").is_file()):
                return None
            return repository_guard(path)

        self.tmp_repo_guard = mock.patch.object(
            carga, "repositorio_que_contiene", side_effect=ignore_incomplete_tmp_marker,
        )
        self.tmp_repo_guard.start()
        self.addCleanup(self.tmp_repo_guard.stop)
        carga.cargar(self.fixture.importacion(), self.fixture.base, "admin-transition-fixture")
        if sqlite.revision(self.fixture.base) != "0007":
            command.downgrade(carga.configuracion_alembic(self.fixture.base), "0007")
        self.scope_bytes = AMBITO.read_bytes()
        self.scope_hash = hashlib.sha256(self.scope_bytes).hexdigest()
        self.generation = str(uuid.uuid4())
        self._insert_comment()
        self.description = sqlite.comprobar_copia(self.fixture.base)
        self.package = self.workspace / "coherent-package"
        builder = preparation_tests.TransitionEnrichment0008Tests("runTest")
        builder.workspace = self.workspace
        builder.source = self.fixture.base
        _bundle, verification = builder._write_coherent_package(
            self.package, scope_bytes=self.scope_bytes, code_hash=self.source_code,
        )
        self.generation = json.loads(verification.read_text(encoding="utf-8"))["generation"]
        self.candidate_dir = self.workspace / "candidate"
        self.candidate_report = prepare.prepare_from_coherent_package(self.package, self.candidate_dir)
        self.install = self.workspace / "installation"
        self.base, self.scope, self.acquisition = self._copy_installation()
        self.data_dir = self.base.parent
        (self.data_dir / "evidence.json").write_text('{"fixture":"preserve-me"}\n', encoding="utf-8")
        self.transition_dir = self.workspace / "maintenance" / "enrichment-0008"
        self.transition_dir.parent.mkdir(mode=0o700)

    def _insert_comment(self):
        with closing(sqlite3.connect(self.fixture.base)) as connection, connection:
            connection.execute("PRAGMA foreign_keys=ON")
            match_id, event_id = connection.execute(
                """SELECT match.id, external_id.external_id FROM match
                   JOIN match_external_id AS external_id ON external_id.entity_id=match.id
                   WHERE external_id.provider='sofascore' AND external_id.status='origin'
                   ORDER BY match.id LIMIT 1"""
            ).fetchone()
            body = json.dumps({"comments": [{"id": 7001, "text": "¡Gol! Autogol de Prueba.",
                                               "type": "scoreChange", "time": 42,
                                               "periodName": "1ST"}]},
                              ensure_ascii=False, separators=(",", ":"))
            digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
            capture_id = connection.execute(
                """INSERT INTO narrative_comment_capture
                   (match_id,provider,source_event_id,source_path,captured_at,http_status,availability,
                    comment_count,response_sha256,response_json,transport_report_json)
                   VALUES (?,'sofascore',?,?,'2026-11-08T10:00:00Z',200,
                           'available',1,?,?,'{}')""",
                (match_id, str(event_id), f"/event/{event_id}/comments/es-la", digest, body),
            ).lastrowid
            connection.execute(
                """INSERT INTO narrative_comment
                   (capture_id,source_comment_id,source_position,text,type,minute,period_name,
                    participants_json,context_json)
                   VALUES (?,'7001',0,'¡Gol! Autogol de Prueba.','scoreChange',42,'1ST','{}','{}')""",
                (capture_id,),
            )
            connection.execute(
                """INSERT INTO narrative_comment_selection
                   (match_id,capture_id,last_successful_source_path,selected_spanish_sha256,
                    last_attempt_state,last_attempt_at,last_http_status,last_error_code)
                   VALUES (?,?,'/event/100/comments/es-la',?,'available','2026-11-08T10:00:00Z',200,NULL)""",
                (match_id, capture_id, digest),
            )

    def _copy_installation(self):
        build = self.package / "build"
        packaged_base = build / "state" / "real-jaen.sqlite"
        packaged_updates = Path(str(packaged_base) + ".actualizaciones")
        target_dir = self.install / "state"
        target_dir.mkdir(parents=True)
        base = target_dir / "real-jaen.sqlite"
        sqlite.copia_consistente(packaged_base, base)
        updates = Path(str(base) + ".actualizaciones")
        (updates / "control").mkdir(parents=True)
        sqlite.copia_consistente(packaged_updates / "jobs.sqlite", updates / "jobs.sqlite")
        sqlite.copia_consistente(packaged_updates / "control" / "jobs.sqlite", updates / "control" / "jobs.sqlite")
        acquisition = self.install / "acquisition-jobs" / "jobs.sqlite"
        acquisition.parent.mkdir()
        sqlite.copia_consistente(build / "acquisition-jobs" / "jobs.sqlite", acquisition)
        scope = self.install / "ambito.json"
        scope.write_bytes(self.scope_bytes)
        return base, scope, acquisition

    def _confirm_adopt(self):
        return f"adopt enrichment 0007->0008 {self.generation} {self.target_code} services-stopped"

    def _adopt(self, checkpoint=lambda _phase: None):
        return admin.adopt(self.base, self.scope, self.acquisition, self.candidate_dir,
                           self.transition_dir, self._confirm_adopt(), _checkpoint=checkpoint)

    def _confirmed(self):
        with closing(sqlite3.connect(Path(str(self.base) + ".actualizaciones") / "jobs.sqlite")) as connection:
            return json.loads(connection.execute("SELECT detalle FROM confirmado WHERE id=1").fetchone()[0])

    def _job_rows(self):
        with closing(sqlite3.connect(Path(str(self.base) + ".actualizaciones") / "jobs.sqlite")) as connection:
            return connection.execute("SELECT id,estado,detalle FROM trabajo ORDER BY id").fetchall()

    def _restore(self, checkpoint=lambda _phase: None):
        manifest = admin._load_backup(self.transition_dir)
        confirm = f"restore enrichment transition {manifest['manifest_sha256']} services-stopped"
        return admin.restore(self.base, self.scope, self.acquisition, self.transition_dir,
                             self.source_runtime, confirm,
                             _checkpoint=checkpoint)

    def test_adopt_preserves_generation_histories_and_evidence_then_restore_reopens_source(self):
        original_description = sqlite.comprobar_copia(self.base)
        original_signature = admin._db_signature(self.base)
        original_jobs = self._job_rows()
        original_meta = self._confirmed()
        evidence_hash = admin._sha_file(self.data_dir / "evidence.json")

        result = self._adopt()

        self.assertEqual(result["status"], "adopted")
        self.assertEqual(sqlite.revision(self.base), "0008")
        self.assertEqual(self._confirmed()["generacion"], self.generation)
        self.assertEqual(self._confirmed()["ambito"], self.scope_hash)
        self.assertEqual(self._confirmed()["codigo"], self.target_code)
        self.assertEqual(self._confirmed()["descripcion"], sqlite.comprobar_copia(self.base))
        self.assertEqual(self._job_rows(), original_jobs)
        self.assertEqual(admin._sha_file(self.data_dir / "evidence.json"), evidence_hash)
        self.assertEqual(admin._db_signature(self.transition_dir / "backup/payload/databases/sports.sqlite"),
                         original_signature)
        self.assertEqual(original_meta["descripcion"], original_description)
        self.assertTrue((self.transition_dir / "adoption.json").is_file())

        restored = self._restore()
        self.assertEqual(restored["status"], "restored")
        self.assertEqual(sqlite.revision(self.base), "0007")
        self.assertEqual(sqlite.comprobar_copia(self.base), original_description)
        self.assertEqual(self._confirmed(), original_meta)
        self.assertEqual(self._job_rows(), original_jobs)
        self.assertEqual(admin._sha_file(self.data_dir / "evidence.json"), evidence_hash)
        self.assertTrue((self.transition_dir / "restore.json").is_file())

    def test_interruption_after_backup_and_after_sqlite_replace_is_resumable(self):
        before = admin._db_signature(self.base)

        def before_backup(phase):
            if phase == "before_backup":
                raise RuntimeError("simulated interruption before backup")

        with self.assertRaisesRegex(RuntimeError, "before backup"):
            self._adopt(before_backup)
        self.assertEqual(admin._db_signature(self.base), before)
        self.assertEqual(sqlite.revision(self.base), "0007")
        self.assertFalse(self.transition_dir.exists())

        def after_backup(phase):
            if phase == "backup_complete":
                raise RuntimeError("simulated interruption after complete backup")

        with self.assertRaisesRegex(RuntimeError, "complete backup"):
            self._adopt(after_backup)
        self.assertEqual(sqlite.revision(self.base), "0007")
        self.assertTrue((self.transition_dir / "backup/manifest.json").is_file())

        def before_replace(phase):
            if phase == "before_sqlite_replace":
                raise RuntimeError("simulated interruption before SQLite replacement")

        with self.assertRaisesRegex(RuntimeError, "before SQLite replacement"):
            self._adopt(before_replace)
        self.assertEqual(sqlite.revision(self.base), "0007")
        self.assertEqual(self._confirmed()["codigo"], self.source_code)
        self.assertEqual(self._adopt()["status"], "adopted")

        # Revert only this simulation to the same snapshot to exercise the SQLite-replaced/journal-old phase.
        self._restore()
        self.transition_dir = self.workspace / "maintenance" / "enrichment-0008-second"

        def after_sqlite(phase):
            if phase == "sqlite_replaced":
                raise RuntimeError("simulated interruption between SQLite and journal commit")

        with self.assertRaisesRegex(RuntimeError, "between SQLite and journal"):
            self._adopt(after_sqlite)
        self.assertEqual(sqlite.revision(self.base), "0008")
        self.assertEqual(self._confirmed()["codigo"], self.source_code)
        self.assertEqual(self._adopt()["status"], "adopted")
        self.assertEqual(self._confirmed()["descripcion"], sqlite.comprobar_copia(self.base))

    def test_interruption_after_journal_commit_resumes_without_duplicate_history(self):
        original_jobs = self._job_rows()

        def after_journal(phase):
            if phase == "journal_committed":
                raise RuntimeError("simulated interruption after journal commit")

        with self.assertRaisesRegex(RuntimeError, "after journal commit"):
            self._adopt(after_journal)
        self.assertEqual(sqlite.revision(self.base), "0008")
        self.assertEqual(self._confirmed()["codigo"], self.target_code)
        self.assertEqual(self._adopt()["status"], "adopted")
        self.assertEqual(self._job_rows(), original_jobs)

    def test_interruption_after_coordinator_open_before_receipt_is_resumable(self):
        def after_open(phase):
            if phase == "coordinator_opened":
                raise RuntimeError("simulated interruption before adoption receipt")

        with self.assertRaisesRegex(RuntimeError, "before adoption receipt"):
            self._adopt(after_open)
        self.assertEqual(sqlite.revision(self.base), "0008")
        self.assertEqual(self._confirmed()["codigo"], self.target_code)
        self.assertFalse((self.transition_dir / "adoption.json").exists())
        self.assertEqual(self._adopt()["status"], "adopted")
        self.assertTrue((self.transition_dir / "adoption.json").is_file())

    def test_restore_resumes_after_sqlite_replacement_and_refuses_new_work(self):
        self._adopt()

        def after_restore_sports(phase):
            if phase == "restore_sports_replaced":
                raise RuntimeError("simulated interruption during restore")

        with self.assertRaisesRegex(RuntimeError, "during restore"):
            self._restore(after_restore_sports)
        self.assertEqual(sqlite.revision(self.base), "0007")
        self.assertEqual(self._confirmed()["codigo"], self.target_code)
        self.assertEqual(self._restore()["status"], "restored")

        self._adopt()
        control = Path(str(self.base) + ".actualizaciones") / "control" / "jobs.sqlite"
        with closing(sqlite3.connect(control)) as connection, connection:
            ident = str(uuid.uuid4())
            connection.execute("INSERT INTO job VALUES(?,?,?,?)",
                               (ident, str(uuid.uuid4()), "completed",
                                json.dumps({"id": ident, "status": "completed"})))
        with self.assertRaisesRegex(admin.TransitionRejected, "historial http_control cambió"):
            self._restore()
        self.assertEqual(sqlite.revision(self.base), "0008")

    def test_active_work_bad_candidate_and_confirmation_are_rejected_before_installation(self):
        journal = Path(str(self.base) + ".actualizaciones") / "jobs.sqlite"
        with closing(sqlite3.connect(journal)) as connection, connection:
            ident = str(uuid.uuid4())
            connection.execute("INSERT INTO trabajo VALUES(?,?,?)",
                               (ident, "running", json.dumps({"id": ident, "estado": "running", "fase": "importing"})))
        with self.assertRaises(admin.TransitionRejected):
            self._adopt()
        self.assertEqual(sqlite.revision(self.base), "0007")
        self.assertFalse(self.transition_dir.exists())

        # A fresh install copy detects a report/candidate hash mismatch and leaves its source untouched.
        self._restore_fixture_install()
        report_path = self.candidate_dir / prepare.REPORT_NAME
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["candidate_sqlite_sha256"] = "0" * 64
        report_path.write_text(json.dumps(report), encoding="utf-8")
        before = admin._db_signature(self.base)
        with self.assertRaisesRegex(admin.TransitionRejected, "hash de candidate.sqlite"):
            self._adopt()
        self.assertEqual(admin._db_signature(self.base), before)

    def test_candidate_generation_code_and_scope_must_match_the_confirmed_source(self):
        report_path = self.candidate_dir / prepare.REPORT_NAME
        original = json.loads(report_path.read_text(encoding="utf-8"))
        before = admin._db_signature(self.base)
        for key, value in (
            ("confirmed_generation", str(uuid.uuid4())),
            ("confirmed_code_sha256", "a" * 64),
            ("confirmed_scope_sha256", "b" * 64),
        ):
            with self.subTest(provenance_field=key):
                changed = json.loads(json.dumps(original))
                changed["validated_source_provenance"][key] = value
                report_path.write_text(json.dumps(changed), encoding="utf-8")
                with self.assertRaises(admin.TransitionRejected):
                    self._adopt()
                self.assertEqual(sqlite.revision(self.base), "0007")
                self.assertEqual(admin._db_signature(self.base), before)
                self.assertFalse(self.transition_dir.exists())
                report_path.write_text(json.dumps(original), encoding="utf-8")

    def _restore_fixture_install(self):
        """Recreate the disposable 0007 fixture without touching the package or candidate."""
        if self.transition_dir.exists():
            shutil.rmtree(self.transition_dir)
        shutil.rmtree(self.install)
        self.base, self.scope, self.acquisition = self._copy_installation()
        self.data_dir = self.base.parent
        (self.data_dir / "evidence.json").write_text('{"fixture":"preserve-me"}\n', encoding="utf-8")
        self.transition_dir.parent.mkdir(mode=0o700, exist_ok=True)


if __name__ == "__main__":
    unittest.main()
