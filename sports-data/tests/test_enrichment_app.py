"""Focused tests for opt-in enrichment router registration in the main FastAPI app."""

import asyncio
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock
from urllib.parse import urlencode

try:
    from fastapi import FastAPI
except ImportError:
    raise unittest.SkipTest("instala requirements-fastapi.txt para las pruebas de la aplicación")

try:
    from alembic import command  # noqa: F401
except ImportError:
    raise unittest.SkipTest("instala requirements-sql.txt para las pruebas de la aplicación")


ROOT = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(TESTS))

import api  # noqa: E402
import app as fastapi_entry  # noqa: E402
from app import create_app  # noqa: E402
from enrichment.router import router as enrichment_router  # noqa: E402
from test_enrichment_queries import EnrichmentQueryTests as QueryDatabaseFixture  # noqa: E402
from test_enrichment_router import _asgi_get  # noqa: E402


async def _request_with_lifespan(application: FastAPI, target: str):
    async with application.router.lifespan_context(application):
        return await _asgi_get(application, target)


class EnrichmentAppTests(unittest.TestCase):
    def setUp(self):
        fixture = QueryDatabaseFixture()
        fixture.setUp()
        fixture.connection.close()
        self.fixture = fixture
        self.database = fixture.database
        self.sports_data = api.cargar(TESTS / "fixtures" / "datos")
        self.addCleanup(fixture.doCleanups)

    @staticmethod
    def _target(**params):
        path = "/api/v1/enrichment/comments"
        return path + ("?" + urlencode(params) if params else "")

    @staticmethod
    def _registered_paths(application):
        paths = []
        for route in application.routes:
            included_router = getattr(route, "original_router", None)
            if included_router is None:
                path = getattr(route, "path", None)
                if path is not None:
                    paths.append(path)
                continue
            paths.extend(
                registered.path
                for registered in included_router.routes
                if getattr(registered, "path", None) is not None
            )
        return paths

    def _request(self, application, target):
        return asyncio.run(_request_with_lifespan(application, target))

    def test_disabled_by_default_keeps_fallback_and_sports_comments_contract(self):
        with mock.patch.dict(os.environ, {
            "SPORTS_DATA_CONTROL": "0",
            "SPORTS_DATA_STORAGE": "json",
        }, clear=True):
            application = create_app(data=self.sports_data)

        paths = self._registered_paths(application)
        self.assertNotIn("/api/v1/enrichment/comments", paths)
        self.assertEqual(paths.count("/{path:path}"), 1)
        self.assertFalse(hasattr(application.state, "enrichment_database_path"))

        status, fallback = self._request(application, self._target())
        self.assertEqual((status, fallback), (404, {"error": "Ruta inexistente"}))

        match_id = self.sports_data["partidos"][0]["match_id"]
        status, comments = self._request(
            application, f"/api/v1/matches/sofascore-match-{match_id}/comments"
        )
        self.assertEqual(status, 200)
        self.assertEqual(comments["availability"], "not_collected")

    def test_enabled_router_uses_explicit_read_only_database_and_preserves_route_order(self):
        with mock.patch.dict(os.environ, {
            "SPORTS_DATA_CONTROL": "0",
            "SPORTS_DATA_STORAGE": "json",
            "SPORTS_DATA_ENRICHMENT_ENABLED": "1",
            "SPORTS_DATA_ENRICHMENT_DB": str(self.database),
        }, clear=True):
            application = create_app(data=self.sports_data)

        paths = self._registered_paths(application)
        enrichment_index = paths.index("/api/v1/enrichment/comments")
        self.assertEqual(paths.count("/api/v1/enrichment/comments"), 1)
        self.assertLess(enrichment_index, paths.index("/{path:path}"))

        status, player = self._request(
            application,
            self._target(source_event_id="16600011", player_id="50", role="assistant", limit=5),
        )
        self.assertEqual(status, 200)
        self.assertEqual((player["total"], player["items"][0]["comment"]["source_comment_id"]),
                         (1, "goal-with-assist"))

        status, page = self._request(
            application, self._target(match_id=1, limit=2, offset=2)
        )
        self.assertEqual(status, 200)
        self.assertEqual((page["total"], page["limit"], page["offset"], len(page["items"])), (7, 2, 2, 2))

        match_id = self.sports_data["partidos"][0]["match_id"]
        status, comments = self._request(
            application, f"/api/v1/matches/sofascore-match-{match_id}/comments"
        )
        self.assertEqual((status, comments["availability"]), (200, "not_collected"))

    def test_enabled_without_database_returns_controlled_503(self):
        with mock.patch.dict(os.environ, {
            "SPORTS_DATA_CONTROL": "0",
            "SPORTS_DATA_STORAGE": "json",
            "SPORTS_DATA_ENRICHMENT_ENABLED": "1",
        }, clear=True):
            application = create_app(data=self.sports_data)

        self.assertIsNone(application.state.enrichment_database_path)
        status, body = self._request(application, self._target())
        self.assertEqual(status, 503)
        self.assertEqual(body["detail"]["reason"], "database_not_configured")

    def test_enabled_with_0007_database_returns_controlled_503(self):
        with tempfile.TemporaryDirectory(prefix="rj-enrichment-app-0007-") as directory:
            database = Path(directory) / "revision-0007.sqlite"
            shutil.copy2(self.database, database)
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute("UPDATE alembic_version SET version_num = '0007'")

            with mock.patch.dict(os.environ, {
                "SPORTS_DATA_CONTROL": "0",
                "SPORTS_DATA_STORAGE": "json",
                "SPORTS_DATA_ENRICHMENT_ENABLED": "1",
                "SPORTS_DATA_ENRICHMENT_DB": str(database),
            }, clear=True):
                application = create_app(data=self.sports_data)

            status, body = self._request(application, self._target())
            self.assertEqual(status, 503)
            self.assertEqual(body["detail"]["reason"], "migration_0008_required")

    def test_control_and_enrichment_routes_register_without_opening_coordinator(self):
        with mock.patch.dict(os.environ, {
            "SPORTS_DATA_ENRICHMENT_ENABLED": "1",
            "SPORTS_DATA_ENRICHMENT_DB": str(self.database),
        }, clear=True):
            application = create_app(control_mode=True)

        included_routers = [
            route.original_router
            for route in application.routes
            if getattr(route, "original_router", None) is not None
        ]
        self.assertEqual(included_routers.count(enrichment_router), 1)
        self.assertEqual(included_routers.count(fastapi_entry.updates_router), 1)
        self.assertLess(included_routers.index(enrichment_router), included_routers.index(fastapi_entry.router))
        self.assertEqual(
            sum(route.path == "/api/v1/enrichment/comments" for route in enrichment_router.routes),
            1,
        )
        self.assertFalse(hasattr(application.state, "coordinator"))


if __name__ == "__main__":
    unittest.main()
