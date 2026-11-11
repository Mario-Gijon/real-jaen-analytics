"""Focused read-only query checks for currently selected Spanish enrichments."""

import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config

from enrichment.queries import (
    EnrichmentQueryError,
    MAX_PAGE_SIZE,
    open_readonly_connection,
    query_enriched_comments,
)


ROOT = Path(__file__).resolve().parent.parent


def _alembic_config(database: Path) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["base"] = str(database)
    config.attributes["configurar_registro"] = False
    return config


class EnrichmentQueryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="rj-enrichment-query-"))
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.database = self.temp_dir / "comments.sqlite"
        command.upgrade(_alembic_config(self.database), "head")
        connection = sqlite3.connect(self.database)
        connection.execute("PRAGMA foreign_keys = ON")
        self._seed(connection)
        connection.commit()
        connection.close()
        self.connection = open_readonly_connection(self.database)
        self.addCleanup(self.connection.close)

    def _seed(self, connection):
        connection.execute(
            """INSERT INTO import_run (id, kind, started_at, finished_at, status)
               VALUES (1, 'initial_load', '2026-11-01T10:00:00+00:00',
                       '2026-11-01T10:01:00+00:00', 'committed')"""
        )
        for match_id in (1, 2):
            connection.execute(
                "INSERT INTO match (id, uid, status, created_in_run) VALUES (?, ?, 'active', 1)",
                (match_id, f"00000000-0000-4000-8000-{match_id:012d}"),
            )
        self._capture(connection, 10, 1, "16600011", "/event/16600011/comments/es-la", "a" * 64, 7)
        self._capture(connection, 11, 1, "16600011", "/event/16600011/comments", "b" * 64, 1)
        self._capture(connection, 20, 2, "16600012", "/event/16600012/comments/es-la", "c" * 64, 1)
        for match_id, capture_id, source_path, digest, attempt_state in (
            (1, 10, "/event/16600011/comments/es-la", "a" * 64, "available"),
            # Deliberately stale selection hash: this capture must be invisible.
            (2, 20, "/event/16600012/comments/es-la", "d" * 64, "available"),
        ):
            connection.execute(
                """INSERT INTO narrative_comment_selection
                   (match_id, capture_id, last_successful_source_path, selected_spanish_sha256,
                    last_attempt_state, last_attempt_at, last_http_status)
                   VALUES (?, ?, ?, ?, ?, '2026-11-05T11:00:00+00:00', 200)""",
                (match_id, capture_id, source_path, digest, attempt_state),
            )

        comments = [
            (10, "own-goal", "scoreChange", "¡Gol! Autogol de Juan Pérez.",
             {"player": {"id": 42, "name": "Juan Pérez"}}, "goal",
             {"goal_status": "valid", "goal_kind": "own_goal"},
             [("player", "own_goal_author", "42", "scorer")]),
            (10, "foul-ambiguous", "freeKickLost", "El jugador ha simulado una falta.",
             {"player": {"id": "42", "name": "Juan Pérez"}}, "foul",
             {"infraction_type": None, "location_context": None, "flank": None},
             [("player", None, '"42"', "foul_committer")]),
            (10, "shot-right", "shotSaved", "Disparo con el pie derecho.",
             {"player": {"id": 42, "name": "Juan Pérez"}}, "shot",
             {"surface": "right_foot", "area_zone": "inside_area"},
             [("player", "shooter", "42", None)]),
            (10, "shot-leading-zero", "shotBlocked", "Disparo bloqueado.",
             {"player": {"id": "0042", "name": "Jugador con ID textual"}}, "shot",
             {"surface": None},
             [("player", "shooter", '"0042"', None)]),
            (10, "substitution", "substitution", "Sustitución.",
             {"playerIn": {"id": 88, "name": "Entrante"},
              "playerOut": {"id": 89, "name": "Saliente"}}, "substitution", {},
             [("playerIn", "incoming_player", "88", "substituted_in"),
              ("playerOut", "outgoing_player", "89", "substituted_out")]),
            (10, "goal-with-assist", "scoreChange", "¡Gol! Asiste otro jugador.",
             {"player": {"id": 51, "name": "Goleador"},
              "assist1": {"id": 50, "name": "Asistente"}}, "goal",
             {"goal_status": "valid", "goal_kind": "ordinary"},
             [("player", "scorer", "51", "scorer"),
              ("assist1", "assistant", "50", "assist")]),
            (10, "cancelled", "varCancelledGoal", "Gol anulado tras revisión del VAR.",
             {"player": {"id": 43, "name": "Jugador 43"}}, "goal_review",
             {"goal_status": "disallowed"},
             [("player", "disallowed_scorer", "43", None)]),
            (11, "english-history", "scoreChange", "Goal.", {}, "goal",
             {"goal_status": "valid"}, []),
            (20, "other-match", "yellowCard", "Tarjeta amarilla.",
             {"player": {"id": "42", "name": "Juan Pérez"}}, "discipline",
             {}, [("player", "booked_player", '"42"', None)]),
        ]
        for position, comment in enumerate(comments):
            self._comment(connection, position, *comment)

    @staticmethod
    def _capture(connection, capture_id, match_id, event_id, path, digest, comment_count):
        connection.execute(
            """INSERT INTO narrative_comment_capture
               (id, match_id, provider, source_event_id, captured_at, http_status, availability,
                comment_count, response_sha256, response_json, source_path)
               VALUES (?, ?, 'sofascore', ?, '2026-11-05T10:00:00+00:00', 200,
                       'available', ?, ?, '{\"comments\": []}', ?)""",
            (capture_id, match_id, event_id, comment_count, digest, path),
        )

    @staticmethod
    def _comment(connection, position, capture_id, source_id, original_type, text,
                 raw_participants, category, attributes, participants):
        connection.execute(
            """INSERT INTO narrative_comment
               (capture_id, source_comment_id, source_position, text, type, minute, period_name,
                is_home, home_score, away_score, incident_id, participants_json, context_json)
               VALUES (?, ?, ?, ?, ?, 20, '1st', 1, 1, 0, NULL, ?, '{}')""",
            (capture_id, source_id, position, text, original_type,
             json.dumps(raw_participants, ensure_ascii=False, separators=(",", ":"))),
        )
        connection.execute(
            """INSERT INTO narrative_comment_enrichment
               (capture_id, source_comment_id, enrichment_version, category, attributes_json)
               VALUES (?, ?, 'rules-v1', ?, ?)""",
            (capture_id, source_id, category,
             json.dumps(attributes, ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
        )
        for index, (source_role, role, source_id_json, normalizer_role) in enumerate(participants):
            connection.execute(
                """INSERT INTO narrative_comment_enrichment_participant
                   (capture_id, source_comment_id, enrichment_version, participant_position,
                    source_role, role, source_id_json, normalizer_role)
                   VALUES (?, ?, 'rules-v1', ?, ?, ?, ?, ?)""",
                (capture_id, source_id, index, source_role, role, source_id_json, normalizer_role),
            )

    def test_only_hash_matched_current_spanish_captures_are_returned(self):
        page = query_enriched_comments(self.connection, source_event_id="16600011", limit=10)
        self.assertEqual(page["total"], 7)
        self.assertEqual(len(page["items"]), 7)
        self.assertNotIn("english-history", {item["comment"]["source_comment_id"] for item in page["items"]})
        first = page["items"][0]
        self.assertEqual(first["provenance"]["source_path"], "/event/16600011/comments/es-la")
        self.assertEqual(first["provenance"]["response_sha256"], first["provenance"]["selected_spanish_sha256"])
        self.assertEqual(first["comment"]["participants"]["player"]["name"], "Juan Pérez")
        self.assertEqual(query_enriched_comments(self.connection, match_id=2)["total"], 0)

    def test_player_id_types_and_effective_roles_do_not_conflate_actions(self):
        numeric = query_enriched_comments(self.connection, match_id=1, player_id=42, limit=10)
        numeric_text = query_enriched_comments(self.connection, match_id=1, player_id="42", limit=10)
        self.assertEqual(numeric["total"], 3)
        self.assertEqual(
            {item["comment"]["source_comment_id"] for item in numeric["items"]},
            {item["comment"]["source_comment_id"] for item in numeric_text["items"]},
        )

        leading_zero = query_enriched_comments(self.connection, match_id=1, player_id="0042")
        self.assertEqual([item["comment"]["source_comment_id"] for item in leading_zero["items"]],
                         ["shot-leading-zero"])
        self.assertEqual(
            query_enriched_comments(self.connection, match_id=1, player_id="0042",
                                    role="shooter")["total"],
            1,
        )
        self.assertEqual(
            query_enriched_comments(self.connection, match_id=1, player_id="42",
                                    role="shooter")["total"],
            1,
        )
        scorers = query_enriched_comments(self.connection, match_id=1, role="scorer")
        self.assertEqual(scorers["total"], 1)
        self.assertEqual(
            [item["comment"]["source_comment_id"] for item in scorers["items"]],
            ["goal-with-assist"],
        )
        self.assertEqual(
            query_enriched_comments(self.connection, match_id=1, player_id=42,
                                    role="scorer")["total"],
            0,
        )
        own_goal = query_enriched_comments(self.connection, match_id=1, role="own_goal_author")
        self.assertEqual(own_goal["total"], 1)
        self.assertEqual(own_goal["items"][0]["enrichment"]["participants"][0]["normalizer_role"], "scorer")
        self.assertEqual(query_enriched_comments(self.connection, match_id=1, role="foul_committer")["total"], 0)
        ambiguous_foul = query_enriched_comments(self.connection, match_id=1, original_type="freeKickLost")
        foul_participant = ambiguous_foul["items"][0]["enrichment"]["participants"][0]
        self.assertIsNone(foul_participant["role"])
        self.assertEqual(foul_participant["normalizer_role"], "foul_committer")

    def test_category_type_attribute_filters_and_pagination(self):
        shot = query_enriched_comments(
            self.connection, match_id=1, category="shot", attributes={"surface": "right_foot"}
        )
        self.assertEqual([item["comment"]["source_comment_id"] for item in shot["items"]], ["shot-right"])

        cancelled = query_enriched_comments(
            self.connection, match_id=1, original_type="varCancelledGoal",
            attributes={"goal_status": "disallowed"},
        )
        self.assertEqual(cancelled["total"], 1)
        self.assertEqual(cancelled["items"][0]["enrichment"]["participants"][0]["role"], "disallowed_scorer")
        self.assertEqual(
            query_enriched_comments(self.connection, match_id=1, original_type="varCancelledGoal",
                                    role="scorer")["total"],
            0,
        )

        first = query_enriched_comments(self.connection, match_id=1, limit=2, offset=0)
        second = query_enriched_comments(self.connection, match_id=1, limit=2, offset=2)
        self.assertEqual(first["total"], 7)
        self.assertEqual(len(first["items"]), 2)
        self.assertEqual(len(second["items"]), 2)
        self.assertFalse(
            {item["comment"]["source_comment_id"] for item in first["items"]}
            & {item["comment"]["source_comment_id"] for item in second["items"]}
        )

    def test_player_and_role_filters_must_match_the_same_participant(self):
        self.assertEqual(
            query_enriched_comments(self.connection, match_id=1, player_id=88,
                                    role="outgoing_player")["total"],
            0,
        )
        self.assertEqual(
            query_enriched_comments(self.connection, match_id=1, player_id="88",
                                    role="incoming_player")["total"],
            1,
        )
        self.assertEqual(
            query_enriched_comments(self.connection, match_id=1, player_id=89,
                                    role="outgoing_player")["total"],
            1,
        )
        self.assertEqual(
            query_enriched_comments(self.connection, match_id=1, player_id=50,
                                    role="scorer")["total"],
            0,
        )
        assistant = query_enriched_comments(self.connection, match_id=1, player_id=50,
                                            role="assistant")
        self.assertEqual(assistant["total"], 1)
        self.assertEqual(assistant["items"][0]["comment"]["source_comment_id"], "goal-with-assist")
        self.assertEqual(
            query_enriched_comments(self.connection, match_id=1, player_id="50",
                                    role="assistant")["total"],
            1,
        )
        self.assertEqual(
            query_enriched_comments(self.connection, match_id=1, player_id=51,
                                    role="scorer")["total"],
            1,
        )

    def test_connection_and_filters_are_read_only_and_validated(self):
        with self.assertRaises(sqlite3.OperationalError):
            self.connection.execute("INSERT INTO narrative_comment_enrichment VALUES (1, 'x', 'rules-v1', NULL, '{}')")
        with self.assertRaises(EnrichmentQueryError):
            query_enriched_comments(self.connection, player_id=True)
        with self.assertRaises(EnrichmentQueryError):
            query_enriched_comments(self.connection, attributes={"name": "Juan Pérez"})
        with self.assertRaises(EnrichmentQueryError):
            query_enriched_comments(self.connection, limit=MAX_PAGE_SIZE + 1)


if __name__ == "__main__":
    unittest.main()
