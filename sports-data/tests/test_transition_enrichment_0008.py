"""Focused, disposable-SQLite tests for preparing (not installing) Alembic 0008 candidates."""

import hashlib
import json
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import stat
import sys
import tempfile
import tarfile
import unittest
import uuid
from unittest import mock

from alembic import command

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from scripts import transition_enrichment_0008 as transition  # noqa: E402
from persistencia import sqlite  # noqa: E402


class TransitionEnrichment0008Tests(unittest.TestCase):
    def setUp(self):
        from test_enrichment_persistence import EnrichmentPersistenceTests, alembic_config

        self.workspace = Path(tempfile.mkdtemp(prefix="rj-transition-0008-tests-"))
        self.addCleanup(shutil.rmtree, self.workspace, True)
        self.fixture = EnrichmentPersistenceTests("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.alembic_config = alembic_config
        self.source = self.fixture.database
        self.fixture.connection.close()
        command.downgrade(self.alembic_config(self.source), "0007")
        self.assertEqual(sqlite.revision(self.source), "0007")
        self.output = self.workspace / "candidate-output"
        self.updates = Path(str(self.source) + ".actualizaciones")

    def _prepare(self):
        return transition.prepare_candidate(self.source, self.output)

    def _assert_source_unchanged(self, original_description, original_file_hash):
        self.assertEqual(sqlite.describir(self.source), original_description)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), original_file_hash)

    def test_prepare_migra_enriquece_preserva_y_emite_informe_privado(self):
        original_description = sqlite.describir(self.source)
        original_file_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()

        report = self._prepare()

        candidate = self.output / transition.CANDIDATE_NAME
        report_path = self.output / transition.REPORT_NAME
        self.assertEqual(report["status"], "candidate_not_installed")
        self.assertFalse(report["candidate_installed"])
        self.assertEqual(report["source_revision"], "0007")
        self.assertEqual(report["target_revision"], "0008")
        self.assertEqual(sqlite.revision(candidate), "0008")
        self.assertEqual(report["enrichment"]["selected_spanish_captures"], 2)
        self.assertEqual(report["enrichment"]["selected_empty_captures"], 1)
        self.assertEqual(report["enrichment"]["selected_available_comments"], 3)
        self.assertEqual(report["enrichment"]["first_run"]["inserted"], 3)
        self.assertEqual(report["enrichment"]["idempotency_run"]["inserted"], 0)
        self.assertEqual(report["enrichment"]["idempotency_run"]["unchanged"], 3)
        self.assertEqual(report["enrichment"]["selected_annotations"], 3)
        self.assertTrue(report["enrichment"]["coverage_complete"])
        self.assertTrue(report["preexisting_tables_preserved"])
        self.assertEqual(report["candidate_semantic_description"]["integridad"], "ok")
        self.assertEqual(report["candidate_semantic_description"]["claves_foraneas"], [])
        self.assertEqual(report["source_snapshot_semantic_sha256"], original_description["huella"])
        self.assertEqual(report["candidate_sqlite_sha256"], hashlib.sha256(candidate.read_bytes()).hexdigest())
        self.assertEqual(json.loads(report_path.read_text(encoding="utf-8")), report)
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(candidate.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(report_path.stat().st_mode), 0o600)
        self.assertFalse(any(Path(str(candidate) + suffix).exists() for suffix in sqlite.ACOMPANANTES))
        self.assertFalse(self.updates.exists())
        self._assert_source_unchanged(original_description, original_file_hash)

    def test_rechaza_origen_invalido_revision_incorrecta_y_salida_existente(self):
        invalid_source = self.workspace / "invalid" / "not-sqlite.db"
        invalid_source.parent.mkdir()
        invalid_source.write_bytes(b"not a sqlite database")
        invalid_output = self.workspace / "invalid-output"
        with self.assertRaises(transition.PreparationRejected):
            transition.prepare_candidate(invalid_source, invalid_output)
        self.assertFalse(invalid_output.exists())

        wrong_source_dir = self.workspace / "wrong-source"
        wrong_source_dir.mkdir()
        wrong_revision = wrong_source_dir / "wrong-revision.sqlite"
        sqlite.copia_consistente(self.source, wrong_revision)
        command.upgrade(self.alembic_config(wrong_revision), "0008")
        wrong_revision_output = self.workspace / "wrong-revision-output"
        with self.assertRaisesRegex(transition.PreparationRejected, "0007"):
            transition.prepare_candidate(wrong_revision, wrong_revision_output)
        self.assertFalse(wrong_revision_output.exists())

        self.output.mkdir()
        with self.assertRaisesRegex(transition.PreparationRejected, "ya existe"):
            self._prepare()

    def test_rechaza_salida_dentro_del_arbol_de_origen_o_del_repositorio(self):
        inside_source = self.source.parent / "nested-candidate"
        with self.assertRaisesRegex(transition.PreparationRejected, "árbol"):
            transition.prepare_candidate(self.source, inside_source)

        inside_repository = ROOT / "not-a-real-candidate-output-for-test"
        with self.assertRaisesRegex(transition.PreparationRejected, "repositorio"):
            transition.prepare_candidate(self.source, inside_repository)
        self.assertFalse(inside_repository.exists())

    def test_fallo_en_migracion_o_enrichment_limpia_candidata_y_no_toca_origen(self):
        original_description = sqlite.describir(self.source)
        original_file_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        for phase in ("migration", "enrichment"):
            with self.subTest(phase=phase):
                output = self.workspace / f"failed-{phase}"
                if phase == "migration":
                    patcher = mock.patch.object(transition.command, "upgrade", side_effect=RuntimeError("injected"))
                else:
                    patcher = mock.patch.object(
                        transition, "persist_candidate_enrichments", side_effect=RuntimeError("injected"),
                    )
                with patcher, self.assertRaisesRegex(RuntimeError, "injected"):
                    transition.prepare_candidate(self.source, output)
                self.assertFalse(output.exists())
                self._assert_source_unchanged(original_description, original_file_hash)

    def test_rechaza_paquete_con_manifest_y_journal_sin_tocarlos(self):
        package = self.workspace / "coordinator-backup"
        payload = package / "payload"
        payload.mkdir(parents=True)
        packaged_source = payload / self.source.name
        sqlite.copia_consistente(self.source, packaged_source)
        manifest = package / "manifest.json"
        manifest.write_text('{"complete": true}', encoding="utf-8")
        journal_dir = Path(str(packaged_source) + ".actualizaciones")
        journal_dir.mkdir()
        journal = journal_dir / "jobs.sqlite"
        with closing(sqlite3.connect(journal)) as connection:
            connection.execute("CREATE TABLE marker (value TEXT NOT NULL)")
            connection.execute("INSERT INTO marker VALUES ('coordinator-history')")
            connection.commit()
        protected_hashes = {
            path: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (packaged_source, manifest, journal)
        }
        output = self.workspace / "refused-package-output"

        with self.assertRaisesRegex(transition.PreparationRejected, "journal o manifiesto"):
            transition.prepare_candidate(packaged_source, output)

        self.assertFalse(output.exists())
        for path, digest in protected_hashes.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)

    def _write_coherent_package(self, package, *, coordinator_state="completed", phase="completed",
                                description_mismatch=False, code_mismatch=False, scope_mismatch=False,
                                missing_control=False, sidecar=False, incomplete_manifest=False,
                                scope_bytes=None, code_hash=None):
        package.mkdir(parents=True)
        bundle = package / transition.COHERENT_BUNDLE_NAME
        state = package / "build"
        base = state / "state" / "real-jaen.sqlite"
        base.parent.mkdir(parents=True)
        sqlite.copia_consistente(self.source, base)
        updates = Path(str(base) + ".actualizaciones")
        updates.mkdir(parents=True)
        generation = str(uuid.uuid4())
        scope_hash = hashlib.sha256(scope_bytes).hexdigest() if scope_bytes is not None else "b" * 64
        code_hash = code_hash or "a" * 64
        confirmed_code_hash = "c" * 64 if code_mismatch else code_hash
        manifest_scope_hash = "d" * 64 if scope_mismatch else scope_hash
        description = sqlite.describir(base)

        coordinator = updates / "jobs.sqlite"
        with closing(sqlite3.connect(coordinator)) as connection:
            connection.executescript(
                "CREATE TABLE trabajo(id TEXT PRIMARY KEY, estado TEXT NOT NULL, detalle TEXT NOT NULL);"
                "CREATE UNIQUE INDEX unico_activo ON trabajo((1)) WHERE estado IN ('queued','running');"
                "CREATE TABLE confirmado(id INTEGER PRIMARY KEY CHECK(id=1), detalle TEXT NOT NULL);"
            )
            detail = {"id": "coord-1", "estado": coordinator_state, "fase": phase}
            connection.execute("INSERT INTO trabajo VALUES (?, ?, ?)",
                               ("coord-1", coordinator_state, json.dumps(detail, sort_keys=True)))
            confirmed_description = dict(description)
            if description_mismatch:
                confirmed_description["huella"] = "e" * 64
            confirmed = {"codigo": confirmed_code_hash, "ambito": scope_hash, "generacion": generation,
                         "descripcion": confirmed_description}
            connection.execute("INSERT INTO confirmado VALUES (1, ?)",
                               (json.dumps(confirmed, sort_keys=True),))
            connection.commit()

        control = updates / "control" / "jobs.sqlite"
        control.parent.mkdir(parents=True)
        with closing(sqlite3.connect(control)) as connection:
            connection.execute("CREATE TABLE job(id TEXT PRIMARY KEY, idem TEXT, state TEXT, data TEXT)")
            data = {"id": "http-1", "status": "completed"}
            connection.execute("INSERT INTO job VALUES ('http-1', 'idem-1', 'completed', ?)",
                               (json.dumps(data, sort_keys=True),))
            connection.commit()

        acquisition = state / "acquisition-jobs" / "jobs.sqlite"
        acquisition.parent.mkdir(parents=True)
        with closing(sqlite3.connect(acquisition)) as connection:
            connection.execute("CREATE TABLE job(id TEXT PRIMARY KEY, request TEXT UNIQUE, state TEXT, data TEXT)")
            data = {"id": "acq-1"}
            connection.execute("INSERT INTO job VALUES ('acq-1', 'request-1', 'completed', ?)",
                               (json.dumps(data, sort_keys=True),))
            connection.commit()

        paths = {
            "state/real-jaen.sqlite": base,
            "state/real-jaen.sqlite.actualizaciones/jobs.sqlite": coordinator,
            "state/real-jaen.sqlite.actualizaciones/control/jobs.sqlite": control,
            "acquisition-jobs/jobs.sqlite": acquisition,
        }
        if missing_control:
            del paths["state/real-jaen.sqlite.actualizaciones/control/jobs.sqlite"]
        if sidecar:
            paths["state/real-jaen.sqlite-wal"] = None
        files = []
        for relative, path in paths.items():
            if path is None:
                content = b""
                digest = hashlib.sha256(content).hexdigest()
                size = 0
            else:
                content = path.read_bytes()
                digest = hashlib.sha256(content).hexdigest()
                size = len(content)
            files.append({"kind": "file", "path": relative, "sha256": digest, "size": size})

        from scripts import adopt_batches_fingerprint as batch_maintenance

        coord_rows = [("coord-1", coordinator_state, json.dumps(
            {"id": "coord-1", "estado": coordinator_state, "fase": phase}, sort_keys=True))]
        control_rows = [("http-1", "idem-1", "completed", json.dumps(
            {"id": "http-1", "status": "completed"}, sort_keys=True))]
        acquisition_rows = [("acq-1", "request-1", "completed", json.dumps({"id": "acq-1"}, sort_keys=True))]
        summary = lambda rows, state_column: {
            "rows": len(rows),
            "states": {rows[0][state_column]: len(rows)},
            "rows_sha256": hashlib.sha256(json.dumps(
                rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ).encode()).hexdigest(),
        }
        comments_captures, comments_count = transition._selected_spanish_source_counts(base)
        manifest = {
            "format": transition.COHERENT_PACKAGE_FORMAT,
            "created_at_utc": "2026-11-06T19:24:17.644430+00:00",
            "source": {"database": "/state/real-jaen.sqlite", "project": "rj-test"},
            "database": {
                "revision": "0007", "description": description,
                "code_fingerprint": code_hash, "scope_fingerprint": manifest_scope_hash,
                "confirmed_generation": generation,
                "comments": {"selected_spanish_captures_with_comments": comments_captures,
                             "selected_spanish_comments": comments_count},
            },
            "journals": {
                "coordinator": {**summary(coord_rows, 1), "active_or_transitioning": [],
                                "integrity": "ok", "foreign_key_violations": 0},
                "http_control": {**summary(control_rows, 2), "active_or_transitioning": [],
                                 "integrity": "ok", "foreign_key_violations": 0},
                "acquisition": {**summary(acquisition_rows, 2), "active": [],
                                "integrity": "ok", "foreign_key_violations": 0},
            },
            "files": files,
        }
        if incomplete_manifest:
            del manifest["journals"]
        raw_manifest = transition._json_bytes(manifest)
        with tarfile.open(bundle, "w:") as archive:
            dirs = sorted({
                "/".join(relative.split("/")[:index])
                for relative in paths for index in range(1, len(relative.split("/")))
            })
            for dirname in dirs:
                info = tarfile.TarInfo(dirname + "/")
                info.type = tarfile.DIRTYPE
                info.mode = 0o700
                archive.addfile(info)
            for relative, path in paths.items():
                payload = b"" if path is None else path.read_bytes()
                info = tarfile.TarInfo(relative)
                info.size = len(payload)
                info.mode = 0o600
                import io
                archive.addfile(info, io.BytesIO(payload))
            info = tarfile.TarInfo("manifest.json")
            info.size = len(raw_manifest)
            info.mode = 0o600
            import io
            archive.addfile(info, io.BytesIO(raw_manifest))
        bundle_hash = hashlib.sha256(bundle.read_bytes()).hexdigest()
        journals = manifest.get("journals", {})
        verification = {
            "status": "verified", "bundle_sha256": bundle_hash, "bundle_size": bundle.stat().st_size,
            "manifest_entries": len(files), "verified_file_entries": len(files),
            "database_sha256": description["huella"], "generation": generation, "revision": "0007",
            "selected_spanish_captures_with_comments": comments_captures,
            "selected_spanish_comments": comments_count,
            "coordinator": {"active": [], **{k: journals.get("coordinator", {}).get(k) for k in (
                "rows", "states", "rows_sha256", "integrity", "foreign_key_violations")}},
            "http_control": {"active": [], **{k: journals.get("http_control", {}).get(k) for k in (
                "rows", "states", "rows_sha256", "integrity", "foreign_key_violations")}},
            "acquisition": {"active": [], **{k: journals.get("acquisition", {}).get(k) for k in (
                "rows", "states", "rows_sha256", "integrity", "foreign_key_violations")}},
        }
        (package / transition.COHERENT_VERIFICATION_NAME).write_text(
            json.dumps(verification, sort_keys=True), encoding="utf-8",
        )
        return bundle, package / transition.COHERENT_VERIFICATION_NAME

    def test_validates_coherent_package_and_keeps_confirmed_and_destination_code_separate(self):
        package = self.workspace / "valid-package"
        bundle, verification = self._write_coherent_package(package)
        original = (bundle.read_bytes(), verification.read_bytes())

        report = transition.validate_coherent_backup_package(package)

        self.assertEqual(report["status"], "validated_coherent_package")
        self.assertEqual(report["confirmed_code_sha256"], "a" * 64)
        self.assertNotEqual(report["confirmed_code_sha256"], report["current_destination_code_sha256"])
        self.assertEqual(report["confirmed_scope_sha256"], "b" * 64)
        self.assertEqual(report["selected_spanish_comments"], 3)
        self.assertEqual((bundle.read_bytes(), verification.read_bytes()), original)

    def test_rejects_incomplete_manifest_missing_journal_and_changed_journal(self):
        incomplete = self.workspace / "incomplete"
        bundle, _ = self._write_coherent_package(incomplete, incomplete_manifest=True)
        with self.assertRaises(transition.PreparationRejected):
            transition.validate_coherent_backup_package(incomplete)

        tampered = self.workspace / "tampered-manifest"
        bundle, _ = self._write_coherent_package(tampered)
        with tarfile.open(bundle, "r:") as archive:
            manifest_offset = archive.getmember("manifest.json").offset_data
        with bundle.open("r+b") as stream:
            stream.seek(manifest_offset)
            stream.write(b"[")
        with self.assertRaisesRegex(transition.PreparationRejected, "SHA-256 del bundle"):
            transition.validate_coherent_backup_package(tampered)

        missing = self.workspace / "missing-journal"
        bundle, _ = self._write_coherent_package(missing, missing_control=True)
        with self.assertRaisesRegex(transition.PreparationRejected, "journals requeridos"):
            transition.validate_coherent_backup_package(missing)

        modified = self.workspace / "modified-journal"
        bundle, verification = self._write_coherent_package(modified)
        with tarfile.open(bundle, "r:") as archive:
            journal_offset = archive.getmember(transition.COHERENT_COORDINATOR_PATH).offset_data
        with bundle.open("r+b") as stream:
            stream.seek(journal_offset)
            byte = stream.read(1)
            stream.seek(journal_offset)
            stream.write(bytes([byte[0] ^ 1]))
        # Simulate an updated outer bundle checksum; the per-file manifest must still catch the edited journal.
        verification_data = json.loads(verification.read_text(encoding="utf-8"))
        verification_data["bundle_sha256"] = hashlib.sha256(bundle.read_bytes()).hexdigest()
        verification.write_text(json.dumps(verification_data, sort_keys=True), encoding="utf-8")
        with self.assertRaisesRegex(transition.PreparationRejected, "contenido no coincide con el manifiesto"):
            transition.validate_coherent_backup_package(modified)

    def test_rejects_description_active_publication_and_code_or_scope_mismatch(self):
        cases = (
            ("description", {"description_mismatch": True}, "confirmado.detalle.descripcion"),
            ("active", {"coordinator_state": "running", "phase": "importing"}, "coordinator job"),
            ("publishing", {"coordinator_state": "completed", "phase": "publishing"}, "coordinator job"),
            ("code", {"code_mismatch": True}, "huella de código"),
            ("scope", {"scope_mismatch": True}, "huella de ámbito"),
        )
        for name, options, message in cases:
            with self.subTest(name=name):
                package = self.workspace / f"bad-{name}"
                self._write_coherent_package(package, **options)
                with self.assertRaisesRegex(transition.PreparationRejected, message):
                    transition.validate_coherent_backup_package(package)

    def test_rejects_unresolved_sqlite_sidecar(self):
        package = self.workspace / "sidecar"
        self._write_coherent_package(package, sidecar=True)
        with self.assertRaisesRegex(transition.PreparationRejected, "acompañantes SQLite"):
            transition.validate_coherent_backup_package(package)

    def test_prepare_package_uses_validated_autonomous_source_and_preserves_originals_on_success_or_failure(self):
        package = self.workspace / "prepare-package"
        bundle, verification = self._write_coherent_package(package)
        package_before = (bundle.read_bytes(), verification.read_bytes())
        source_description = sqlite.describir(self.source)
        source_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        output = self.workspace / "package-candidate"

        report = transition.prepare_from_coherent_package(package, output)

        self.assertEqual(report["source_kind"], "validated_coherent_backup_package")
        self.assertEqual(report["validated_source_provenance"]["confirmed_generation"],
                         json.loads(verification.read_text())["generation"])
        self.assertEqual(sqlite.revision(output / transition.CANDIDATE_NAME), "0008")
        self.assertEqual((bundle.read_bytes(), verification.read_bytes()), package_before)
        self._assert_source_unchanged(source_description, source_hash)

        failed_output = self.workspace / "package-candidate-failed"
        with mock.patch.object(transition.command, "upgrade", side_effect=RuntimeError("injected")):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                transition.prepare_from_coherent_package(package, failed_output)
        self.assertFalse(failed_output.exists())
        self.assertEqual((bundle.read_bytes(), verification.read_bytes()), package_before)
        self._assert_source_unchanged(source_description, source_hash)


if __name__ == "__main__":
    unittest.main()
