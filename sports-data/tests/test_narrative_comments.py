"""Focused narrative-comment tests with temporary SQLite only; no provider access."""
import sys
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import closing

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import api  # noqa: E402
from persistencia.comentarios import (  # noqa: E402
    NarrativeCommentImportRejected, _comment_rows, load_current_comments, match_comments_response,
    record_http_attempt,
)


class NarrativeCommentNormalization(unittest.TestCase):
    def test_preserves_provider_fields_and_source_order_without_inventing_values(self):
        raw = [
            {
                "id": 2, "text": "Final", "type": "matchEnded", "time": None, "periodName": None,
                "isHome": None, "homeScore": None, "awayScore": None, "incidentId": None,
            },
            {
                "id": 1, "text": "Gol", "type": "scoreChange", "time": 97, "periodName": "2ND",
                "isHome": True, "homeScore": 3, "awayScore": 2, "incidentId": 55,
                "player": {"id": 1001, "name": "Jugador", "position": "F"},
                "footballPassingNetworkAction": {"action": "finish"},
            },
        ]
        rows = _comment_rows({"comments": raw})
        self.assertEqual([row[0] for row in rows], ["2", "1"])
        self.assertEqual([row[1] for row in rows], [0, 1])
        self.assertIsNone(rows[0][5])
        self.assertIsNone(rows[0][4])
        self.assertEqual(rows[1][4:10], (97, "2ND", True, 3, 2, "55"))
        self.assertIn('"id":1001', rows[1][10])
        self.assertIn("footballPassingNetworkAction", rows[1][11])

    def test_rejects_duplicate_ids_invalid_minutes_and_boolean_minutes(self):
        base = {"id": 1, "text": "Acción", "type": "shot"}
        with self.assertRaises(NarrativeCommentImportRejected):
            _comment_rows({"comments": [base, base]})
        with self.assertRaises(NarrativeCommentImportRejected):
            _comment_rows({"comments": [{**base, "time": -1}]})
        with self.assertRaises(NarrativeCommentImportRejected):
            _comment_rows({"comments": [{**base, "time": True}]})

    def test_public_endpoint_distinguishes_known_uncollected_from_unknown_match(self):
        fixture = Path(__file__).resolve().parent / "fixtures" / "datos"
        data = api.cargar(fixture)
        source_id = str(data["partidos"][0]["match_id"])
        public_id = f"sofascore-match-{source_id}"
        status, body = match_comments_response(data, {}, public_id)
        self.assertEqual(status, 200)
        self.assertEqual(body["availability"], "not_collected")
        self.assertEqual(body["comments"], [])
        status, body = match_comments_response(data, {}, f"sofascore-match-{int(source_id) + 999999}")
        self.assertEqual((status, body), (404, {"error": "Partido no encontrado"}))


class NarrativeCommentProvenance(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='rj-comments-provenance-')
        self.addCleanup(self.folder.cleanup)
        self.base = Path(self.folder.name) / 'comments.sqlite'
        self.data = api.cargar(Path(__file__).resolve().parent / 'fixtures' / 'datos')
        self.event_id = str(self.data['partidos'][0]['match_id'])
        self.public_id = f'sofascore-match-{self.event_id}'
        self.path = f'/event/{self.event_id}/comments/es-la'
        self._create_db()

    def _create_db(self):
        with closing(sqlite3.connect(self.base)) as connection, connection:
            connection.executescript("""
                CREATE TABLE match (id INTEGER PRIMARY KEY, status TEXT);
                CREATE TABLE match_state (match_id INTEGER, status TEXT, round INTEGER,
                                          external_stage_id TEXT, external_season_id TEXT);
                CREATE TABLE match_external_id (provider TEXT, namespace TEXT, external_id TEXT,
                                                entity_id INTEGER, status TEXT);
                CREATE TABLE local_replacement (replaced_match_id INTEGER, status TEXT);
                CREATE TABLE narrative_comment_capture (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, match_id INTEGER, provider TEXT, source_event_id TEXT,
                    source_path TEXT, captured_at TEXT, http_status INTEGER, availability TEXT, comment_count INTEGER,
                    response_sha256 TEXT, response_json TEXT, transport_report_json TEXT,
                    UNIQUE(match_id, response_sha256));
                CREATE TABLE narrative_comment (
                    capture_id INTEGER, source_comment_id TEXT, source_position INTEGER, text TEXT, type TEXT,
                    minute INTEGER, period_name TEXT, is_home INTEGER, home_score INTEGER, away_score INTEGER,
                    incident_id TEXT, participants_json TEXT, context_json TEXT);
                CREATE TABLE narrative_comment_selection (
                    match_id INTEGER PRIMARY KEY, capture_id INTEGER, last_successful_source_path TEXT,
                    selected_spanish_sha256 TEXT, last_attempt_state TEXT, last_attempt_at TEXT,
                    last_http_status INTEGER, last_error_code TEXT);
                INSERT INTO match VALUES (1, 'active');
                INSERT INTO match_state VALUES (1, 'finished', 10, '94191', '97382');
            """)
            connection.execute("INSERT INTO match_external_id VALUES ('sofascore', '', ?, 1, 'origin')",
                               (self.event_id,))

    def _record(self, document, *, status=200, error=None, at='2026-11-03T12:00:00+00:00'):
        raw = (json.dumps(document, ensure_ascii=False, separators=(',', ':')).encode()
               if document is not None else None)
        with closing(sqlite3.connect(self.base)) as connection, connection:
            return record_http_attempt(
                connection, event_id=self.event_id, scope_pairs={('94191', '97382')}, http_status=status,
                response=raw if status == 200 else None, source_path=self.path, captured_at=at,
                transport_report={'path': self.path}, error_code=error)

    def _seed_legacy_capture(self, *, text='Historical English narration', availability='available'):
        document = {'comments': [] if availability == 'empty' else [
            {'id': 7, 'text': text, 'type': 'goal', 'time': 42, 'periodName': '1ST'}]}
        raw = json.dumps(document, ensure_ascii=False, separators=(',', ':'))
        digest = hashlib.sha256(raw.encode()).hexdigest()
        with closing(sqlite3.connect(self.base)) as connection, connection:
            cursor = connection.execute("""
                INSERT INTO narrative_comment_capture
                    (match_id, provider, source_event_id, source_path, captured_at, http_status, availability,
                     comment_count, response_sha256, response_json, transport_report_json)
                VALUES (1, 'sofascore', ?, ?, '2026-11-02T12:00:00+00:00', 200, ?, ?, ?, ?, '{}')
            """, (self.event_id, f'/event/{self.event_id}/comments', availability,
                  len(document['comments']), digest, raw))
            capture_id = cursor.lastrowid
            if document['comments']:
                connection.execute("""
                    INSERT INTO narrative_comment
                        (capture_id, source_comment_id, source_position, text, type, minute, period_name,
                         participants_json, context_json)
                    VALUES (?, '7', 0, ?, 'goal', 42, '1ST', '{}', '{}')
                """, (capture_id, text))
            connection.execute("""
                INSERT INTO narrative_comment_selection
                    (match_id, capture_id, last_successful_source_path, selected_spanish_sha256,
                     last_attempt_state, last_attempt_at, last_http_status, last_error_code)
                VALUES (1, ?, ?, NULL, ?, '2026-11-02T12:00:00+00:00', 200, NULL)
            """, (capture_id, f'/event/{self.event_id}/comments', availability))
        return capture_id

    def _public_snapshot(self):
        snapshots = load_current_comments(self.base)
        status, body = match_comments_response(self.data, snapshots, self.public_id)
        self.assertEqual(status, 200)
        return snapshots[self.public_id], body

    def test_legacy_capture_is_preserved_but_never_exposed_as_spanish(self):
        self._seed_legacy_capture()
        snapshot, body = self._public_snapshot()
        self.assertEqual(snapshot['availability'], 'not_collected')
        self.assertFalse(snapshot['spanishComplete'])
        self.assertEqual(snapshot['comments'], [])
        self.assertEqual(body['availability'], 'not_collected')
        self.assertEqual(body['comments'], [])

    def test_empty_spanish_response_replaces_unverified_selection_without_comments(self):
        legacy_capture_id = self._seed_legacy_capture()
        self._record({'comments': []})
        snapshot, body = self._public_snapshot()
        self.assertEqual(snapshot['availability'], 'empty')
        self.assertTrue(snapshot['spanishComplete'])
        self.assertEqual(snapshot['comments'], [])
        self.assertEqual(body['availability'], 'empty')
        self.assertEqual(body['comments'], [])
        with closing(sqlite3.connect(self.base)) as connection:
            self.assertEqual(connection.execute(
                'SELECT source_path FROM narrative_comment_capture WHERE id = ?', (legacy_capture_id,)
            ).fetchone()[0], f'/event/{self.event_id}/comments')
            self.assertEqual(connection.execute('SELECT count(*) FROM narrative_comment_capture').fetchone()[0], 2)

    def test_empty_spanish_response_is_safe_when_hash_deduplicates_with_legacy_empty_capture(self):
        self._seed_legacy_capture(availability='empty')
        self._record({'comments': []})
        snapshot, body = self._public_snapshot()
        self.assertEqual(snapshot['availability'], 'empty')
        self.assertTrue(snapshot['spanishComplete'])
        self.assertEqual(snapshot['comments'], [])
        self.assertEqual(body['availability'], 'empty')

    def test_legacy_empty_attempt_never_exposes_the_still_selected_english_capture(self):
        self._seed_legacy_capture()
        with closing(sqlite3.connect(self.base)) as connection, connection:
            connection.execute("""
                UPDATE narrative_comment_selection
                SET last_successful_source_path = ?, last_attempt_state = 'empty',
                    last_attempt_at = '2026-11-03T12:05:00+00:00', last_http_status = 200
                WHERE match_id = 1
            """, (self.path,))
        snapshot, body = self._public_snapshot()
        self.assertEqual(snapshot['availability'], 'empty')
        self.assertEqual(snapshot['comments'], [])
        self.assertEqual(body['availability'], 'empty')
        self.assertEqual(body['comments'], [])

    def test_spanish_capture_remains_selected_after_later_empty_response_and_failure(self):
        spanish = {'comments': [
            {'id': 9, 'text': 'Gol', 'type': 'scoreChange', 'time': 42, 'periodName': '1ST'}]}
        self._record(spanish, at='2026-11-03T12:00:00+00:00')
        self._record({'comments': []}, at='2026-11-03T12:05:00+00:00')
        snapshot, body = self._public_snapshot()
        self.assertEqual(snapshot['availability'], 'available')
        self.assertEqual([item['id'] for item in snapshot['comments']], ['9'])
        self.assertEqual(snapshot['lastAttempt']['availability'], 'empty')
        self.assertEqual(body['comments'][0]['text'], 'Gol')

        self._record(None, status=None, error='transport_error', at='2026-11-03T12:10:00+00:00')
        snapshot, body = self._public_snapshot()
        self.assertEqual(snapshot['availability'], 'available')
        self.assertEqual(snapshot['lastAttempt']['availability'], 'error')
        self.assertEqual(body['comments'][0]['text'], 'Gol')

    def test_new_spanish_nonempty_capture_replaces_legacy_selection(self):
        self._seed_legacy_capture()
        self._record({'comments': [
            {'id': 8, 'text': 'Narración española', 'type': 'goal', 'time': 71, 'periodName': '2ND'}]})
        snapshot, body = self._public_snapshot()
        self.assertEqual(snapshot['availability'], 'available')
        self.assertEqual(body['comments'][0]['text'], 'Narración española')


if __name__ == "__main__":
    unittest.main()
