from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tarfile
import tempfile
import unittest
import uuid
from contextlib import closing, redirect_stderr
from unittest.mock import patch

from scripts import postgresql_backup as workflow
from scripts import postgresql_backup_helper as helper


class _CapturedStdout:
    def __init__(self):
        self.buffer = io.BytesIO()


class PostgreSQLBackupTests(unittest.TestCase):
    def test_ordinary_backup_rejects_runtime_image_mismatch(self):
        container = {"image_id": "sha256:" + "a" * 64}
        with patch.object(workflow, "_text", return_value="sha256:" + "b" * 64):
            with self.assertRaises(workflow.OperationError):
                workflow._check_runtime_image_identity("sports-data", "sports-data:runtime", container, set())

    def test_recovery_image_mismatch_requires_missing_original_object(self):
        container = {"image_id": "sha256:" + "a" * 64}
        allowed = {"sports-data", "video-service"}
        with patch.object(workflow, "_text", return_value="sha256:" + "b" * 64), \
                patch.object(workflow, "_image_object_available", return_value=False):
            workflow._check_runtime_image_identity("sports-data", "sports-data:runtime", container, allowed)
        with patch.object(workflow, "_text", return_value="sha256:" + "b" * 64), \
                patch.object(workflow, "_image_object_available", return_value=True):
            with self.assertRaises(workflow.OperationError):
                workflow._check_runtime_image_identity("sports-data", "sports-data:runtime", container, allowed)

    def test_recovery_rejects_an_unlisted_service_or_unrelated_admin_image(self):
        container = {"image_id": "sha256:" + "a" * 64}
        with patch.object(workflow, "_text", return_value="sha256:" + "b" * 64):
            with self.assertRaises(workflow.OperationError):
                workflow._check_runtime_image_identity("dashboard", "dashboard:runtime", container,
                                                       {"sports-data", "video-service"})
        valid = "sha256:" + "c" * 64 + "|rja-project|migrate-sports-data"
        self.assertEqual(workflow._parse_admin_image_identity(valid, "rja-project", "migrate-sports-data")[0],
                         "sha256:" + "c" * 64)
        with self.assertRaises(workflow.OperationError):
            workflow._parse_admin_image_identity(
                "sha256:" + "c" * 64 + "|rja-project|sports-data", "rja-project", "migrate-sports-data")

    def test_recovery_manifest_requires_verified_admin_provenance(self):
        runtime = {service: {"container_id": "a" * 64, "configured_reference": f"local/{service}:runtime",
                             "image_id": "sha256:" + "b" * 64, "image_object_available": True}
                   for service in workflow.SERVICES}
        for service, algorithm in (("sports-data", "sports-data-reference-v1"),
                                   ("video-service", "video-service-src-tree-v1")):
            runtime[service].update(image_object_available=False, code_fingerprint_algorithm=algorithm,
                                    code_sha256="c" * 64)
        administrative = {}
        for kind, service in (("sports", "migrate-sports-data"), ("video", "migrate-video-service")):
            administrative[kind] = {
                "reference": f"local/{kind}:admin", "image_id": "sha256:" + "d" * 64,
                "compose_project": "rja-project", "compose_service": service,
                "code_fingerprint_algorithm": ("sports-data-reference-v1" if kind == "sports"
                                                else "video-service-src-tree-v1"),
                "code_sha256": "e" * 64,
            }
        manifest = {
            "backup_tool": {"version": workflow.BACKUP_TOOL_VERSION, "commit": "f" * 40,
                            "working_tree_dirty": True, "script_sha256": "1" * 64,
                            "helper_sha256": "2" * 64},
            "source": {"project": "rja-project", "runtime_images": runtime,
                       "administrative_images": administrative},
        }
        workflow._verify_backup_provenance(manifest)
        runtime["sports-data"]["image_object_available"] = True
        workflow._verify_backup_provenance(manifest)
        runtime["video-service"]["image_object_available"] = True
        with self.assertRaises(workflow.OperationError):
            workflow._verify_backup_provenance(manifest)
        runtime["video-service"]["image_object_available"] = False
        manifest["source"]["administrative_images"]["sports"]["compose_project"] = "another-project"
        with self.assertRaises(workflow.OperationError):
            workflow._verify_backup_provenance(manifest)

    def test_run_streams_open_file_as_stdin(self):
        payload = b"custom archive stream"
        with tempfile.TemporaryFile() as stream:
            stream.write(payload)
            stream.seek(0)
            result = workflow._run(["cat"], stdin=stream)
        self.assertEqual(result.stdout, payload)

    def test_restore_uses_explicit_database_admin_user(self):
        with tempfile.NamedTemporaryFile() as archive, patch.object(workflow, "_run") as run:
            workflow._restore_dump("container-id", "sports_data", "sports_data_owner", Path(archive.name))
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--username") + 1], "postgres")
        self.assertEqual(command[command.index("--role") + 1], "sports_data_owner")

    def test_snapshot_canonicalizes_redundant_varchar_array_casts(self):
        source = {
            "constraints": [["table", "check", "c", "CHECK (side::text = ANY (ARRAY['home'::character varying, 'away'::character varying]::text[]))", True]],
            "indexes": [["public", "table", "ix", "CREATE INDEX ix ON public.table USING btree (state) WHERE ((state)::text <> ALL ((ARRAY['queued'::character varying, 'running'::character varying])::text[]))"]],
        }
        restored = {
            "constraints": [["table", "check", "c", "CHECK (side::text = ANY (ARRAY['home'::character varying::text, 'away'::character varying::text]))", True]],
            "indexes": [["public", "table", "ix", "CREATE INDEX ix ON public.table USING btree (state) WHERE ((state)::text <> ALL (ARRAY[('queued'::character varying)::text, ('running'::character varying)::text]))"]],
        }
        self.assertNotEqual(source, restored)
        self.assertEqual(workflow._canonical_snapshot(source), workflow._canonical_snapshot(restored))

    def _jobs_root(self, state: str = "completed") -> tuple[Path, str, dict]:
        root = Path(tempfile.mkdtemp())
        (root / "work").mkdir()
        (root / "operations").mkdir()
        job_id = str(uuid.uuid4())
        request_id = str(uuid.uuid4())
        inventory_hash = hashlib.sha256(b"inventory").hexdigest()
        bundle = b'{"manifest":{"contract":"test"},"items":[]}'
        descriptor = {"bytes": len(bundle), "sha256": hashlib.sha256(bundle).hexdigest()}
        job = {"id": job_id, "request_id": request_id, "state": state, "inventory_sha256": inventory_hash,
               "bundle": descriptor if state == "completed" else None,
               "operation_id": str(uuid.uuid4()), "profile": "/profiles/generations/private",
               "acquisition_token": "fixture-secret-must-not-be-exported"}
        with closing(sqlite3.connect(root / "jobs.sqlite")) as db, db:
            db.execute("CREATE TABLE job(id TEXT PRIMARY KEY,request TEXT UNIQUE,state TEXT,data TEXT)")
            db.execute("INSERT INTO job VALUES(?,?,?,?)", (job_id, request_id, state,
                                                              json.dumps(job, separators=(",", ":"))))
        if state == "completed":
            folder = root / "work" / job_id
            folder.mkdir()
            (folder / "job.json").write_text(json.dumps(job), encoding="utf-8")
            (folder / "inventory.json").write_text(json.dumps({"sha256": inventory_hash}), encoding="utf-8")
            (folder / "report.json").write_text(json.dumps({"descriptor": descriptor}), encoding="utf-8")
            (folder / "bundle.json").write_bytes(bundle)
            os.chmod(folder / "bundle.json", 0o444)
            ledger_id = str(uuid.uuid4())
            ledger = {"schema": 1, "operation_id": ledger_id, "maximum": 50,
                      "sends": [{"job_id": job_id, "channel": "fetch", "path": "/match",
                                 "status": "completed", "http_status": 500,
                                 "error": "postgresql://app:fixture-secret@db/private"}],
                      "history_matches": []}
            (root / "operations" / f"{ledger_id}.json").write_text(json.dumps(ledger), encoding="utf-8")
        return root, job_id, job

    def _export(self, root: Path) -> tuple[bytes, dict]:
        capture = _CapturedStdout()
        original = sys.stdout
        sys.stdout = capture
        try:
            with redirect_stderr(io.StringIO()):
                metadata = helper._snapshot_acquisition(root, root.parent / "snapshot.sqlite")
        finally:
            sys.stdout = original
        return capture.buffer.getvalue(), metadata

    def test_acquisition_export_keeps_reconciliation_but_strips_profile_and_secrets(self):
        root, job_id, _ = self._jobs_root()
        archive, metadata = self._export(root)
        summary = helper._validate_archive(io.BytesIO(archive), Path(tempfile.mkdtemp()), write=False)
        self.assertEqual(summary["jobs_completed"], 1)
        self.assertEqual(summary["operation_ledgers"], 1)
        self.assertEqual(summary["package_files"], 4)
        self.assertEqual(metadata["jobs_total"], 1)
        self.assertNotIn(b"/profiles/generations/private", archive)
        self.assertNotIn(b"fixture-secret-must-not-be-exported", archive)
        self.assertNotIn(b"fixture-secret@db", archive)
        target = Path(tempfile.mkdtemp())
        restored = helper._validate_archive(io.BytesIO(archive), target, write=True)
        self.assertEqual(restored["jobs_completed"], 1)
        with closing(sqlite3.connect(target / "jobs.sqlite")) as db, db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            saved = db.execute("SELECT data FROM job").fetchone()[0]
            self.assertNotIn("fixture-secret@db", saved)
        self.assertTrue((target / "work" / job_id / "bundle.json").is_file())

    def test_acquisition_export_rejects_active_job(self):
        root, _, _ = self._jobs_root("running")
        with self.assertRaises(RuntimeError):
            helper._snapshot_acquisition(root, root.parent / "snapshot.sqlite")

    def test_acquisition_archive_rejects_path_traversal(self):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            info = tarfile.TarInfo("../escaped")
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
        target = Path(tempfile.mkdtemp())
        with self.assertRaises(RuntimeError):
            helper._validate_archive(io.BytesIO(stream.getvalue()), target, write=True)
        self.assertEqual(list(target.iterdir()), [])

    def test_backup_verification_rejects_modified_payload(self):
        root, _, _ = self._jobs_root()
        archive, export_meta = self._export(root)
        backup = Path(tempfile.mkdtemp()) / "backup"
        backup.mkdir(mode=0o700)
        payloads = {"sports_data.dump": b"sports-dump", "video_service.dump": b"video-dump",
                    "acquisition-history.tar": archive}
        files = {}
        for name, content in payloads.items():
            path = backup / name
            path.write_bytes(content)
            os.chmod(path, 0o600)
            files[name] = {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        archive_summary = helper._validate_archive(io.BytesIO(archive), Path(tempfile.mkdtemp()), write=False)
        manifest = {"format": "real-jaen-postgresql-backup", "format_version": 3,
                    "files": files,
                    "acquisition": {**export_meta, "files": archive_summary["files"],
                                    "total_bytes": archive_summary["total_bytes"]}}
        (backup / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        os.chmod(backup / "manifest.json", 0o600)
        lines = []
        for name in (*workflow.PAYLOADS, "manifest.json"):
            digest = hashlib.sha256((backup / name).read_bytes()).hexdigest()
            lines.append(f"{digest}  {name}\n")
        (backup / "SHA256SUMS").write_text("".join(lines), encoding="ascii")
        os.chmod(backup / "SHA256SUMS", 0o600)
        workflow._verify_files(backup)
        with (backup / "sports_data.dump").open("ab") as output:
            output.write(b"tampered")
        with self.assertRaises(workflow.OperationError):
            workflow._verify_files(backup)

    def test_empty_destination_check_rejects_existing_file(self):
        target = Path(tempfile.mkdtemp())
        (target / "leftover").write_text("keep", encoding="ascii")
        self.assertEqual(helper._empty_check(target), "nonempty")

    def test_restore_failure_keeps_durable_marker_until_owner_clears_it(self):
        imports = Path(tempfile.mkdtemp())
        marker, token = workflow._restore_marker(imports, "rja-test", {"postgres-data": "rja-test-pg"}, "a" * 64)
        self.assertEqual(marker.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(workflow.OperationError):
            workflow._clear_restore_marker(marker, "wrong-token")
        self.assertTrue(marker.is_file())
        workflow._clear_restore_marker(marker, token)
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
