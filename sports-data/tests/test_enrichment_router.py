"""Focused ASGI checks for the isolated read-only enrichment router."""

import asyncio
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from urllib.parse import urlencode

try:
    from fastapi import FastAPI
except ImportError:
    raise unittest.SkipTest("instala requirements-fastapi.txt para las pruebas del router")

try:
    from alembic import command
    from alembic.config import Config
except ImportError:
    raise unittest.SkipTest("instala requirements-sql.txt para las pruebas del router")


ROOT = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(TESTS))

from enrichment.router import router  # noqa: E402
from test_enrichment_queries import EnrichmentQueryTests as QueryDatabaseFixture  # noqa: E402


async def _asgi_get(application, target: str):
    path, separator, query = target.partition("?")
    sent = []
    received_request = False
    response_complete = asyncio.Event()

    async def receive():
        nonlocal received_request
        if not received_request:
            received_request = True
            return {"type": "http.request", "body": b"", "more_body": False}
        # An ASGI server reports a disconnect only after the client has gone away.
        # Returning it immediately races the synchronous endpoint and can cancel
        # middleware while the response is still being produced.
        await response_complete.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)
        if (message["type"] == "http.response.body"
                and not message.get("more_body", False)):
            response_complete.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": query.encode("ascii") if separator else b"",
        "headers": [(b"host", b"testserver")],
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
        "root_path": "",
        "app": application,
    }
    # ASGI servers schedule each connection as a task. In the restricted local
    # runner, a selector wakeup from AnyIO's worker thread can be missed while
    # the event loop is idle, so this small timer mirrors the next server I/O
    # wakeup without replacing the synchronous handler or its threadpool path.
    task = asyncio.create_task(application(scope, receive, send))

    async def event_loop_pulse():
        while not task.done():
            await asyncio.sleep(0.005)

    pulse = asyncio.create_task(event_loop_pulse())
    try:
        done, _ = await asyncio.wait({task}, timeout=5)
        if not done:
            raise TimeoutError(f"ASGI request did not complete: {target}")
        task.result()
    finally:
        response_complete.set()
        pulse.cancel()
        await asyncio.gather(pulse, return_exceptions=True)
        if not task.done():
            task.cancel()
            await asyncio.wait({task}, timeout=1)
    start = next(message for message in sent if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in sent
                    if message["type"] == "http.response.body")
    return start["status"], json.loads(body)


class EnrichmentRouterTests(unittest.TestCase):
    def setUp(self):
        fixture = QueryDatabaseFixture()
        fixture.setUp()
        fixture.connection.close()
        self.fixture = fixture
        self.database = fixture.database
        self.application = FastAPI()
        self.application.state.enrichment_database_path = self.database
        self.application.include_router(router)
        self.addCleanup(fixture.doCleanups)

    def request(self, **params):
        target = "/api/v1/enrichment/comments"
        if params:
            target += "?" + urlencode(params)
        return asyncio.run(_asgi_get(self.application, target))

    def test_player_combination_and_pagination(self):
        status, player_page = self.request(source_event_id="16600011", player_id="50", limit=10)
        self.assertEqual(status, 200)
        self.assertEqual(player_page["enrichment_version"], "rules-v1")
        self.assertEqual(player_page["total"], 1)
        self.assertEqual(player_page["items"][0]["comment"]["source_comment_id"], "goal-with-assist")

        status, wrong_role = self.request(match_id=1, player_id="50", role="scorer")
        self.assertEqual((status, wrong_role["total"]), (200, 0))
        status, correct_role = self.request(match_id=1, player_id="50", role="assistant")
        self.assertEqual((status, correct_role["total"]), (200, 1))

        status, first = self.request(match_id=1, limit=2, offset=0)
        next_status, second = self.request(match_id=1, limit=2, offset=2)
        self.assertEqual((status, next_status), (200, 200))
        self.assertEqual((first["total"], first["limit"], first["offset"]), (7, 2, 0))
        self.assertEqual((second["total"], second["limit"], second["offset"]), (7, 2, 2))
        self.assertEqual((len(first["items"]), len(second["items"])), (2, 2))

    def test_missing_selected_annotation_returns_503_until_copy_is_completed(self):
        with tempfile.TemporaryDirectory(prefix="rj-enrichment-incomplete-") as directory:
            database = Path(directory) / "incomplete.sqlite"
            shutil.copy2(self.database, database)
            self.application.state.enrichment_database_path = database

            with closing(sqlite3.connect(database)) as connection, connection:
                annotation = connection.execute(
                    """SELECT category, attributes_json FROM narrative_comment_enrichment
                       WHERE capture_id = 10 AND source_comment_id = 'own-goal'
                         AND enrichment_version = 'rules-v1'"""
                ).fetchone()
                participants = connection.execute(
                    """SELECT participant_position, source_role, role, source_id_json, normalizer_role
                       FROM narrative_comment_enrichment_participant
                       WHERE capture_id = 10 AND source_comment_id = 'own-goal'
                         AND enrichment_version = 'rules-v1'"""
                ).fetchall()
                connection.execute(
                    """DELETE FROM narrative_comment_enrichment_participant
                       WHERE capture_id = 10 AND source_comment_id = 'own-goal'
                         AND enrichment_version = 'rules-v1'"""
                )
                connection.execute(
                    """DELETE FROM narrative_comment_enrichment
                       WHERE capture_id = 10 AND source_comment_id = 'own-goal'
                         AND enrichment_version = 'rules-v1'"""
                )

            status, unavailable = self.request(match_id=1)
            self.assertEqual(status, 503)
            self.assertEqual(unavailable["detail"]["reason"], "enrichment_incomplete")

            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute(
                    """INSERT INTO narrative_comment_enrichment
                       (capture_id, source_comment_id, enrichment_version, category, attributes_json)
                       VALUES (10, 'own-goal', 'rules-v1', ?, ?)""",
                    annotation,
                )
                connection.executemany(
                    """INSERT INTO narrative_comment_enrichment_participant
                       (capture_id, source_comment_id, enrichment_version, participant_position,
                        source_role, role, source_id_json, normalizer_role)
                       VALUES (10, 'own-goal', 'rules-v1', ?, ?, ?, ?, ?)""",
                    [(position, source_role, role, source_id, normalizer_role)
                     for position, source_role, role, source_id, normalizer_role in participants],
                )

            status, complete = self.request(match_id=1)
            self.assertEqual(status, 200)
            self.assertEqual(complete["total"], 7)

    def test_newly_selected_comment_requires_enrichment_but_empty_capture_does_not(self):
        with tempfile.TemporaryDirectory(prefix="rj-enrichment-new-selection-") as directory:
            database = Path(directory) / "new-selection.sqlite"
            shutil.copy2(self.database, database)
            self.application.state.enrichment_database_path = database
            source_path = "/event/16600012/comments/es-la"
            digest = "e" * 64

            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute(
                    """INSERT INTO narrative_comment_capture
                       (id, match_id, provider, source_event_id, captured_at, http_status, availability,
                        comment_count, response_sha256, response_json, source_path)
                       VALUES (30, 2, 'sofascore', '16600012', '2026-11-06T10:00:00+00:00', 200,
                               'available', 1, ?, '{\"comments\":[{\"id\":\"new-comment\"}]}', ?)""",
                    (digest, source_path),
                )
                connection.execute(
                    """UPDATE narrative_comment_selection
                       SET capture_id = 30, selected_spanish_sha256 = ?,
                           last_successful_source_path = ?, last_attempt_state = 'available'
                       WHERE match_id = 2""",
                    (digest, source_path),
                )
                connection.execute(
                    """INSERT INTO narrative_comment
                       (capture_id, source_comment_id, source_position, text, type, minute, period_name,
                        is_home, home_score, away_score, incident_id, participants_json, context_json)
                       VALUES (30, 'new-comment', 0, 'Comentario nuevo', 'yellowCard', 12, '1st',
                               1, 0, 0, NULL, '{}', '{}')"""
                )

            status, unavailable = self.request(match_id=2)
            self.assertEqual(status, 503)
            self.assertEqual(unavailable["detail"]["reason"], "enrichment_incomplete")

            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute(
                    """INSERT INTO narrative_comment_enrichment
                       (capture_id, source_comment_id, enrichment_version, category, attributes_json)
                       VALUES (30, 'new-comment', 'rules-v1', 'discipline', '{}')"""
                )

            status, complete = self.request(match_id=2)
            self.assertEqual(status, 200)
            self.assertEqual(complete["total"], 1)
            self.assertEqual(complete["items"][0]["comment"]["source_comment_id"], "new-comment")

            empty_database = Path(directory) / "empty-selection.sqlite"
            shutil.copy2(self.database, empty_database)
            self.application.state.enrichment_database_path = empty_database
            empty_digest = "f" * 64
            with closing(sqlite3.connect(empty_database)) as connection, connection:
                connection.execute(
                    """INSERT INTO narrative_comment_capture
                       (id, match_id, provider, source_event_id, captured_at, http_status, availability,
                        comment_count, response_sha256, response_json, source_path)
                       VALUES (30, 2, 'sofascore', '16600012', '2026-11-06T10:00:00+00:00', 200,
                               'empty', 0, ?, '{\"comments\":[]}', ?)""",
                    (empty_digest, source_path),
                )
                connection.execute(
                    """UPDATE narrative_comment_selection
                       SET capture_id = 30, selected_spanish_sha256 = ?,
                           last_successful_source_path = ?, last_attempt_state = 'empty'
                       WHERE match_id = 2""",
                    (empty_digest, source_path),
                )

            status, empty = self.request(match_id=2)
            self.assertEqual(status, 200)
            self.assertEqual(empty["total"], 0)

    def test_filter_validation_and_missing_migration_or_rules_are_controlled(self):
        status, invalid = self.request(attributes="[1,2]")
        self.assertEqual(status, 422)
        self.assertEqual(invalid["detail"]["error"], "invalid_filter")

        with tempfile.TemporaryDirectory(prefix="rj-enrichment-router-") as directory:
            old_revision_copy = Path(directory) / "old-revision.sqlite"
            shutil.copy2(self.database, old_revision_copy)
            with closing(sqlite3.connect(old_revision_copy)) as connection, connection:
                connection.execute("UPDATE alembic_version SET version_num = '0007'")
            self.application.state.enrichment_database_path = old_revision_copy
            status, unavailable = self.request(match_id=1)
            self.assertEqual(status, 503)
            self.assertEqual(unavailable["detail"]["reason"], "migration_0008_required")

            no_rules_copy = Path(directory) / "no-rules.sqlite"
            shutil.copy2(self.database, no_rules_copy)
            with closing(sqlite3.connect(no_rules_copy)) as connection, connection:
                connection.execute("DELETE FROM narrative_comment_enrichment_participant")
                connection.execute("DELETE FROM narrative_comment_enrichment")
            self.application.state.enrichment_database_path = no_rules_copy
            status, unavailable = self.request(match_id=1)
            self.assertEqual(status, 503)
            self.assertEqual(unavailable["detail"]["reason"], "rules_v1_not_found")


if __name__ == "__main__":
    unittest.main()
