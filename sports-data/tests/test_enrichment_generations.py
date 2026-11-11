"""Generation-bound enrichment reads using temporary SQLite 0008 databases only."""

from contextlib import closing
from pathlib import Path
import sys
import sqlite3
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

try:
    from alembic import command
except ImportError:
    raise unittest.SkipTest("instala requirements-sql.txt para las pruebas de generaciones enrichment")

try:
    from fastapi import FastAPI, HTTPException
    from starlette.requests import Request
except ImportError:
    raise unittest.SkipTest("instala requirements-fastapi.txt para las pruebas de generaciones enrichment")


ROOT = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(TESTS))

from enrichment import router as enrichment_router  # noqa: E402
from enrichment.queries import open_readonly_connection, query_enriched_comments  # noqa: E402
from enrichment.store import persist_candidate_enrichments  # noqa: E402
from persistencia.actualizacion import Coordinador  # noqa: E402
from persistencia.enrichment_snapshot import MAX_LIVE_ENRICHMENT_SNAPSHOTS  # noqa: E402
from persistencia import sqlite as sqlite_persistence  # noqa: E402
from test_carga import AMBITO  # noqa: E402
from test_enrichment_queries import _alembic_config  # noqa: E402


class EnrichmentGenerationTests(unittest.TestCase):
    def setUp(self):
        from test_actualizacion import Actualizacion as UpdateFixture

        self.fixture = UpdateFixture("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        command.upgrade(_alembic_config(self.fixture.base), "head")
        self.fixture._seed_enrichment_scenario()
        self.match_id = self.fixture.comment_match_id
        self.event_id = self.fixture.comment_event_id
        persist_candidate_enrichments(self.fixture.base, "rules-v1")
        self.snapshot_root = self.fixture.base.with_name(
            self.fixture.base.name + ".actualizaciones"
        ) / "enrichment-snapshots"

    def _new_coordinator(self, *, frontier=None):
        return Coordinador(
            self.fixture.base,
            AMBITO,
            reserva=0,
            _frontera=frontier,
            enrichment_snapshots=True,
        )

    @staticmethod
    def _application(coordinator):
        application = FastAPI()
        application.state.control_mode = True
        # A regression that opens this path directly must be caught by each test.
        application.state.enrichment_database_path = coordinator.base
        application.state.coordinator = coordinator
        application.include_router(enrichment_router.router)
        return application

    def _request(self, application, **params):
        request = Request({
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/v1/enrichment/comments",
            "raw_path": b"/api/v1/enrichment/comments",
            "query_string": b"",
            "headers": [],
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 12345),
            "root_path": "",
            "app": application,
        })
        values = dict(
            match_id=None,
            source_event_id=str(self.event_id),
            player_id=None,
            category=None,
            original_type=None,
            role=None,
            attributes=None,
            limit=10,
            offset=0,
            database_path=None,
        )
        values.update(params)
        try:
            return 200, enrichment_router.enriched_comments(request, **values)
        except HTTPException as error:
            return error.status_code, {"detail": error.detail}

    def _publish_comment_with_team(self, coordinator, comment_id=7002):
        # The fixture module exports JAEN; use it to add a harmless sporting event
        # as well as the new selected Spanish comments capture.
        from test_importar import JAEN

        bundle = self.fixture._comments_bundle(self.event_id, [{
            "id": comment_id,
            "text": "Remate de prueba en la segunda generación.",
            "type": "shotSaved",
            "time": 51,
            "periodName": "2ND",
            "player": {"id": 1393276, "name": "Otro jugador"},
        }], 2)
        job = coordinator.solicitar(
            exportaciones=[(self.fixture.fixture, JAEN)],
            comentarios=[bundle],
        )
        result = coordinator.esperar(job)
        self.assertEqual(result["estado"], "completed", result)
        return job

    def test_prepared_generation_is_hidden_until_confirmed_then_becomes_current(self):
        prepared = threading.Event()
        continue_publication = threading.Event()

        def frontier(name):
            if name == "snapshot_prepared":
                prepared.set()
                if not continue_publication.wait(10):
                    raise TimeoutError("publication test timed out")

        from persistencia.lectura import cargar_desde_base
        import api

        expected_data = cargar_desde_base(self.fixture.base, AMBITO)
        coordinator = self._new_coordinator(frontier=frontier)
        self.addCleanup(coordinator.close)
        application = self._application(coordinator)
        opened = []
        original_open = enrichment_router.open_readonly_connection
        generation = coordinator.capturar()
        match_public_id = f"/api/v1/matches/{generation._datos['partidos'][0]['match_id']}"
        self.assertEqual(generation.consultar(match_public_id), api.consultar(expected_data, match_public_id))

        def record_open(path):
            opened.append(Path(path).resolve())
            connection = original_open(path)
            self.assertEqual(connection.execute("PRAGMA query_only").fetchone()[0], 1)
            return connection

        with mock.patch.object(enrichment_router, "open_readonly_connection", side_effect=record_open):
            old = coordinator.capturar(enrichment=True)
            old_path = old.path
            self.assertEqual(old.generation_id, coordinator.capturar().id)
            self.assertEqual(old_path.stat().st_mode & 0o777, 0o400)
            self.assertEqual(old_path.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(self.snapshot_root.stat().st_mode & 0o777, 0o700)
            old.release()
            status, before = self._request(application)
            self.assertEqual(status, 200)
            self.assertEqual([row["comment"]["source_comment_id"] for row in before["items"]], ["7001"])
            from test_importar import JAEN
            job = coordinator.solicitar(
                exportaciones=[(self.fixture.fixture, JAEN)],
                comentarios=[self.fixture._comments_bundle(self.event_id, [{
                    "id": 7002,
                    "text": "Remate de prueba en la segunda generación.",
                    "type": "shotSaved",
                    "time": 51,
                    "periodName": "2ND",
                    "player": {"id": 1393276, "name": "Otro jugador"},
                }], 2)],
            )
            self.assertTrue(prepared.wait(10))
            self.assertEqual(coordinator.estado(job)["fase"], "preparing")
            status, while_prepared = self._request(application)
            self.assertEqual(status, 200)
            self.assertEqual([row["comment"]["source_comment_id"] for row in while_prepared["items"]], ["7001"])
            continue_publication.set()
            self.assertEqual(coordinator.esperar(job)["estado"], "completed")
            status, after = self._request(application)

        self.assertEqual(status, 200)
        self.assertEqual([row["comment"]["source_comment_id"] for row in after["items"]], ["7002"])
        self.assertTrue(opened)
        self.assertTrue(all(path != self.fixture.base.resolve() for path in opened))
        self.assertTrue(all(path.parent == self.snapshot_root.resolve() / path.parent.name for path in opened))
        self.assertFalse(old_path.exists())

    def test_request_lease_keeps_old_snapshot_alive_across_publication(self):
        coordinator = self._new_coordinator()
        self.addCleanup(coordinator.close)
        application = self._application(coordinator)
        entered = threading.Event()
        release_query = threading.Event()
        original_query = enrichment_router.query_enriched_comments
        first = True
        guard = threading.Lock()

        def pause_first_query(*args, **kwargs):
            nonlocal first
            with guard:
                should_pause = first
                first = False
            if should_pause:
                entered.set()
                if not release_query.wait(10):
                    raise TimeoutError("held enrichment request timed out")
            return original_query(*args, **kwargs)

        with mock.patch.object(enrichment_router, "query_enriched_comments", side_effect=pause_first_query):
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending_request = pool.submit(self._request, application)
                self.assertTrue(entered.wait(10))
                old_path = next(self.snapshot_root.glob("*/generation.sqlite"))
                self._publish_comment_with_team(coordinator)
                self.assertTrue(old_path.exists(), "old snapshot must remain while its request is using it")
                status, new_response = self._request(application)
                self.assertEqual(status, 200)
                self.assertEqual([row["comment"]["source_comment_id"] for row in new_response["items"]], ["7002"])
                release_query.set()
                old_status, old_response = pending_request.result(timeout=10)

        self.assertEqual(old_status, 200)
        self.assertEqual([row["comment"]["source_comment_id"] for row in old_response["items"]], ["7001"])
        self.assertFalse(old_path.exists(), "retired snapshot should be removed after its last reader releases it")
        self.assertLessEqual(len(list(self.snapshot_root.glob("*/generation.sqlite"))),
                             MAX_LIVE_ENRICHMENT_SNAPSHOTS)

    def test_failed_unconfirmed_publication_keeps_generation_a_and_discards_b(self):
        def fail_before_confirmation(name):
            if name == "before_confirmation":
                raise RuntimeError("injected publication failure")

        coordinator = self._new_coordinator(frontier=fail_before_confirmation)
        self.addCleanup(coordinator.close)
        application = self._application(coordinator)
        generation_a = coordinator.capturar().id
        initial_snapshot = coordinator.capturar(enrichment=True)
        path_a = initial_snapshot.path
        initial_snapshot.release()

        bundle = self.fixture._comments_bundle(self.event_id, [{
            "id": 7002,
            "text": "No debe hacerse visible.",
            "type": "shotSaved",
            "time": 51,
            "periodName": "2ND",
            "player": {"id": 1393276, "name": "Otro jugador"},
        }], 2)
        from test_importar import JAEN
        job = coordinator.solicitar(exportaciones=[(self.fixture.fixture, JAEN)], comentarios=[bundle])
        failed = coordinator.esperar(job)
        self.assertEqual(failed["estado"], "failed", failed)
        self.assertEqual(coordinator.capturar().id, generation_a)
        status, response = self._request(application)
        self.assertEqual(status, 200)
        self.assertEqual([row["comment"]["source_comment_id"] for row in response["items"]], ["7001"])
        self.assertTrue(path_a.exists())
        self.assertEqual(len(list(self.snapshot_root.glob("*/generation.sqlite"))), 1)

    def test_filters_pagination_and_controlled_errors_use_the_captured_snapshot(self):
        coordinator = self._new_coordinator()
        self.addCleanup(coordinator.close)
        application = self._application(coordinator)

        status, player = self._request(
            application, player_id="1393275", role="own_goal_author", limit=1, offset=0
        )
        self.assertEqual(status, 200)
        self.assertEqual((player["total"], player["limit"], player["offset"]), (1, 1, 0))
        self.assertEqual(player["items"][0]["comment"]["source_comment_id"], "7001")

        status, invalid = self._request(application, attributes="[]")
        self.assertEqual((status, invalid["detail"]["reason"]), (422, "attributes must be a JSON object"))

        static_app = FastAPI()
        static_app.state.control_mode = False
        static_app.state.enrichment_database_path = self.fixture.base
        status, static_player = self._request(
            static_app, database_path=self.fixture.base,
            player_id="1393275", role="own_goal_author", limit=1, offset=0,
        )
        self.assertEqual((status, static_player["total"]), (200, 1))

        missing_app = FastAPI()
        missing_app.state.control_mode = False
        missing_app.state.enrichment_database_path = None
        status, unavailable = self._request(missing_app)
        self.assertEqual((status, unavailable["detail"]["reason"]), (503, "database_not_configured"))

        coordinator.close()
        no_snapshot = Coordinador(self.fixture.base, AMBITO, reserva=0)
        self.addCleanup(no_snapshot.close)
        unavailable_app = self._application(no_snapshot)
        status, unavailable = self._request(unavailable_app)
        self.assertEqual((status, unavailable["detail"]["reason"]), (503, "generation_unavailable"))
        no_snapshot.close()

        old_schema = self.fixture.tmp / "revision-0007.sqlite"
        sqlite_persistence.copia_consistente(self.fixture.base, old_schema)
        with closing(sqlite3.connect(old_schema)) as connection, connection:
            connection.execute("UPDATE alembic_version SET version_num = '0007'")
        old_schema_app = FastAPI()
        old_schema_app.state.control_mode = False
        old_schema_app.state.enrichment_database_path = old_schema
        status, unavailable = self._request(old_schema_app, database_path=old_schema)
        self.assertEqual((status, unavailable["detail"]["reason"]), (503, "migration_0008_required"))

    def test_restart_rebuilds_only_confirmed_snapshot_and_discards_stale_directories(self):
        coordinator = self._new_coordinator()
        generation_a = coordinator.capturar().id
        self._publish_comment_with_team(coordinator)
        generation_b = coordinator.capturar().id
        self.assertNotEqual(generation_a, generation_b)
        coordinator.close()

        stale = self.snapshot_root / str(uuid.uuid4())
        stale.mkdir(parents=True, mode=0o700)
        (stale / "stale.txt").write_text("stale", encoding="utf-8")
        restarted = self._new_coordinator()
        self.addCleanup(restarted.close)
        self.assertFalse(stale.exists())
        self.assertEqual(restarted.capturar().id, generation_b)
        lease = restarted.capturar(enrichment=True)
        try:
            self.assertEqual(lease.generation_id, generation_b)
            self.assertNotEqual(lease.path.resolve(), self.fixture.base.resolve())
            with closing(open_readonly_connection(lease.path)) as connection:
                page = query_enriched_comments(connection, source_event_id=str(self.event_id))
            self.assertEqual([row["comment"]["source_comment_id"] for row in page["items"]], ["7002"])
        finally:
            lease.release()

    def test_registry_bounds_retained_old_generations(self):
        # The operational assertion is that a publication cannot create another
        # file after the configured live-snapshot bound; no active data is used.
        from persistencia.enrichment_snapshot import EnrichmentSnapshotRegistry, SnapshotCapacityError

        root = self.fixture.tmp / "bounded-snapshots"
        registry = EnrichmentSnapshotRegistry(root, maximum=1)
        first_id = str(uuid.uuid4())
        first = registry.create(self.fixture.base, first_id)
        held = first.retain()
        first.release_owner()
        with self.assertRaises(SnapshotCapacityError):
            registry.create(self.fixture.base, str(uuid.uuid4()))
        held.release()
        second = registry.create(self.fixture.base, str(uuid.uuid4()))
        second.release_owner()
        self.assertEqual(list(root.glob("*/generation.sqlite")), [])


if __name__ == "__main__":
    unittest.main()
