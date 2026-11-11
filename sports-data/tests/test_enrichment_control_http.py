"""HTTP-level ASGI checks for enrichment in coordinator mode, using temporary SQLite only."""

import asyncio
from contextlib import ExitStack, closing, contextmanager
import os
from pathlib import Path
import sqlite3
import sys
import threading
import unittest
from unittest import mock
from urllib.parse import urlencode

try:
    from alembic import command
except ImportError:
    raise unittest.SkipTest("instala requirements-sql.txt para las pruebas HTTP de enrichment")

try:
    import fastapi  # noqa: F401
except ImportError:
    raise unittest.SkipTest("instala requirements-fastapi.txt para las pruebas HTTP de enrichment")


ROOT = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(TESTS))

import app as fastapi_entry  # noqa: E402
from enrichment import router as enrichment_router  # noqa: E402
from enrichment.store import persist_candidate_enrichments  # noqa: E402
from persistencia.actualizacion import Coordinador as RealCoordinator  # noqa: E402
from persistencia import sqlite as sqlite_persistence  # noqa: E402
from test_carga import AMBITO  # noqa: E402
from test_enrichment_queries import _alembic_config  # noqa: E402
from test_enrichment_router import _asgi_get  # noqa: E402


class _UnusedAcquisitionClient:
    """A deliberately inert dependency: these tests never request an update."""


class _NoopUpdates:
    def __init__(self, *_args, **_kwargs):
        pass

    def close(self):
        pass


class EnrichmentControlHttpTests(unittest.TestCase):
    def setUp(self):
        from test_actualizacion import Actualizacion as UpdateFixture

        self.fixture = UpdateFixture("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        command.upgrade(_alembic_config(self.fixture.base), "head")
        self.fixture._seed_enrichment_scenario()
        persist_candidate_enrichments(self.fixture.base, "rules-v1")
        self.control_secret = self.fixture.tmp / "control-secret"
        self.acquisition_secret = self.fixture.tmp / "acquisition-secret"
        self.control_secret.write_bytes(b"c" * 48)
        self.acquisition_secret.write_bytes(b"a" * 48)

    @contextmanager
    def running(self, *, frontier=None, database=None):
        env = {
            "SPORTS_DATA_CONTROL": "1",
            "SPORTS_DATA_WORKERS": "1",
            "WEB_CONCURRENCY": "1",
            "SPORTS_DATA_STORAGE": "sql",
            "SPORTS_DATA_DB": str(database or self.fixture.base),
            "SPORTS_DATA_SCOPE": str(AMBITO),
            "SPORTS_DATA_ENRICHMENT_ENABLED": "1",
            "RJ_CONTROL_SECRET_FILE": str(self.control_secret),
            "RJ_ACQUISITION_SECRET_FILE": str(self.acquisition_secret),
            "RJ_ACQUISITION_URL": "http://127.0.0.1:9",
            "RJ_ACQUISITION_TEST": "1",
        }

        def coordinator_factory(base, scope, **kwargs):
            return RealCoordinator(
                base, scope, reserva=0, _frontera=frontier, **kwargs
            )

        stack = ExitStack()
        stack.enter_context(mock.patch.dict(os.environ, env, clear=True))
        stack.enter_context(mock.patch(
            "persistencia.actualizacion.Coordinador", side_effect=coordinator_factory
        ))
        stack.enter_context(mock.patch(
            "persistencia.adquisicion.Cliente", return_value=_UnusedAcquisitionClient()
        ))
        stack.enter_context(mock.patch("control.Actualizaciones", _NoopUpdates))
        application = fastapi_entry.create_app(control_mode=True)
        loop = asyncio.new_event_loop()
        lifespan = application.router.lifespan_context(application)
        try:
            loop.run_until_complete(lifespan.__aenter__())
            yield application, loop
        finally:
            try:
                loop.run_until_complete(lifespan.__aexit__(None, None, None))
            finally:
                loop.close()
                stack.close()

    @staticmethod
    def request(loop, application, **params):
        target = "/api/v1/enrichment/comments"
        if params:
            target += "?" + urlencode(params)
        return loop.run_until_complete(_asgi_get(application, target))

    def publish_comment(self, coordinator, comment_id=7002):
        from test_importar import JAEN

        bundle = self.fixture._comments_bundle(self.fixture.comment_event_id, [{
            "id": comment_id,
            "text": "Remate de prueba en la generación publicada.",
            "type": "shotSaved",
            "time": 51,
            "periodName": "2ND",
            "player": {"id": 1393276, "name": "Jugador de prueba"},
        }], 2)
        ident = coordinator.solicitar(
            exportaciones=[(self.fixture.fixture, JAEN)], comentarios=[bundle]
        )
        return coordinator.esperar(ident)

    def test_http_filters_paging_sports_route_and_resources(self):
        opened = []
        snapshot_path = None
        original_open = enrichment_router.open_readonly_connection

        def record_open(path):
            connection = original_open(path)
            self.assertEqual(connection.execute("PRAGMA query_only").fetchone()[0], 1)
            opened.append((Path(path).resolve(), connection))
            return connection

        with mock.patch.object(enrichment_router, "open_readonly_connection", side_effect=record_open):
            with self.running() as (application, loop):
                lease = application.state.coordinator.capturar(enrichment=True)
                snapshot_path = lease.path
                lease.release()
                status, page = self.request(
                    loop, application, source_event_id=str(self.fixture.comment_event_id),
                    player_id="1393275", role="own_goal_author", limit=1, offset=0,
                )
                self.assertEqual(status, 200)
                self.assertEqual(page["enrichment_version"], "rules-v1")
                self.assertEqual(page["total"], 1)
                self.assertEqual(page["items"][0]["comment"]["source_comment_id"], "7001")

                status, all_rows = self.request(
                    loop, application, match_id=self.fixture.comment_match_id, limit=1, offset=0
                )
                self.assertEqual(status, 200)
                self.assertEqual((all_rows["total"], all_rows["limit"], all_rows["offset"]), (1, 1, 0))
                status, next_page = self.request(
                    loop, application, match_id=self.fixture.comment_match_id, limit=1, offset=1
                )
                self.assertEqual(status, 200)
                self.assertEqual((next_page["total"], next_page["offset"], next_page["items"]), (1, 1, []))

                generation = application.state.coordinator.capturar()
                public_id = generation._datos["partidos"][0]["match_id"]
                status, sporting = self.request_sports(loop, application, public_id)
                self.assertEqual(status, 200)
                self.assertEqual(sporting["data"]["match_id"], public_id)

                self.assertTrue(opened)
                self.assertTrue(all(path != self.fixture.base.resolve() for path, _ in opened))
                for _path, connection in opened:
                    with self.assertRaises(sqlite3.ProgrammingError):
                        connection.execute("SELECT 1")

        self.assertFalse(snapshot_path.exists(), "coordinator close should remove an unleased current snapshot")
    @staticmethod
    def request_sports(loop, application, match_id):
        return loop.run_until_complete(_asgi_get(application, f"/api/v1/matches/{match_id}"))

    def test_http_request_keeps_generation_a_during_publication_and_reads_b_afterward(self):
        entered, release = threading.Event(), threading.Event()
        original_query = enrichment_router.query_enriched_comments
        call_lock = threading.Lock()
        first = True

        def pause_first_query(*args, **kwargs):
            nonlocal first
            with call_lock:
                pause = first
                first = False
            if pause:
                entered.set()
                if not release.wait(10):
                    raise TimeoutError("HTTP enrichment request was not released")
            return original_query(*args, **kwargs)

        with mock.patch.object(enrichment_router, "query_enriched_comments", side_effect=pause_first_query):
            with self.running() as (application, loop):
                coordinator = application.state.coordinator
                old_lease = coordinator.capturar(enrichment=True)
                old_path = old_lease.path
                old_lease.release()
                target = "/api/v1/enrichment/comments?source_event_id=" + str(self.fixture.comment_event_id)

                async def scenario():
                    pending = asyncio.create_task(_asgi_get(application, target))
                    try:
                        deadline = loop.time() + 5
                        while not entered.is_set() and loop.time() < deadline:
                            await asyncio.sleep(0.005)
                        self.assertTrue(entered.is_set(), "the HTTP request never entered the query")

                        published = self.publish_comment(coordinator)
                        self.assertEqual(published["estado"], "completed", published)
                        self.assertTrue(old_path.exists(), "generation A must stay retained while its request runs")

                        status, page_b = await _asgi_get(application, target)
                        self.assertEqual(status, 200)
                        self.assertEqual(
                            [item["comment"]["source_comment_id"] for item in page_b["items"]], ["7002"]
                        )

                        release.set()
                        status_a, page_a = await pending
                        self.assertEqual(status_a, 200)
                        self.assertEqual(
                            [item["comment"]["source_comment_id"] for item in page_a["items"]], ["7001"]
                        )
                        self.assertFalse(old_path.exists(), "generation A should be released after its last request")
                    finally:
                        release.set()
                        if not pending.done():
                            await asyncio.wait({pending}, timeout=2)
                            if not pending.done():
                                pending.cancel()
                                await asyncio.wait({pending}, timeout=1)
                        if pending.done() and not pending.cancelled():
                            pending.exception()

                loop.run_until_complete(scenario())

    def test_failed_publication_keeps_a_and_http_validation_errors_keep_their_contract(self):
        failed_once = threading.Event()

        def fail_confirmation(point):
            if point == "before_confirmation" and not failed_once.is_set():
                failed_once.set()
                raise RuntimeError("injected confirmation failure")

        with self.running(frontier=fail_confirmation) as (application, loop):
            coordinator = application.state.coordinator
            status, invalid = self.request(loop, application, attributes="[]")
            self.assertEqual(status, 422)
            self.assertEqual(invalid["detail"]["error"], "invalid_filter")

            generation_a = coordinator.capturar().id
            failed = self.publish_comment(coordinator)
            self.assertEqual(failed["estado"], "failed", failed)
            self.assertTrue(failed_once.is_set())
            self.assertEqual(coordinator.capturar().id, generation_a)
            status, page = self.request(
                loop, application, source_event_id=str(self.fixture.comment_event_id)
            )
            self.assertEqual(status, 200)
            self.assertEqual([item["comment"]["source_comment_id"] for item in page["items"]], ["7001"])

            coordinator.close()
            no_snapshot = RealCoordinator(self.fixture.base, AMBITO, reserva=0)
            application.state.coordinator = no_snapshot
            try:
                status, unavailable = self.request(loop, application)
                self.assertEqual(status, 503)
                self.assertEqual(unavailable["detail"]["reason"], "generation_unavailable")
            finally:
                no_snapshot.close()

    def test_selected_snapshot_without_rules_v1_returns_controlled_503(self):
        database = self.fixture.tmp / "without-rules.sqlite"
        sqlite_persistence.copia_consistente(self.fixture.base, database)
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("DELETE FROM narrative_comment_enrichment_participant")
            connection.execute("DELETE FROM narrative_comment_enrichment")

        with self.running(database=database) as (application, loop):
            status, unavailable = self.request(
                loop, application, source_event_id=str(self.fixture.comment_event_id)
            )
            self.assertEqual(status, 503)
            self.assertEqual(unavailable["detail"]["reason"], "rules_v1_not_found")


if __name__ == "__main__":
    unittest.main()
