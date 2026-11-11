"""Focused persistence checks for versioned enrichment on disposable SQLite databases."""

import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config

from enrichment.store import EnrichmentConflict, persist_selected_enrichments


ROOT = Path(__file__).resolve().parent.parent
SPANISH_PATH = "/event/16600011/comments/es-la"


def alembic_config(database: Path) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["base"] = str(database)
    config.attributes["configurar_registro"] = False
    return config


class EnrichmentPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="rj-enrichment-store-"))
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.database = self.temp_dir / "comments.sqlite"
        command.upgrade(alembic_config(self.database), "head")
        self.connection = sqlite3.connect(self.database)
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.addCleanup(self.connection.close)
        self._seed_run_and_matches()

    def _seed_run_and_matches(self):
        self.connection.execute(
            """INSERT INTO import_run (id, kind, started_at, finished_at, status)
               VALUES (1, 'initial_load', '2026-11-01T10:00:00+00:00', '2026-11-01T10:01:00+00:00', 'committed')"""
        )
        for match_id in (1, 2):
            self.connection.execute(
                "INSERT INTO match (id, uid, status, created_in_run) VALUES (?, ?, 'active', 1)",
                (match_id, f"00000000-0000-4000-8000-{match_id:012d}"),
            )
        self._insert_capture(10, 1, "16600011", SPANISH_PATH, "a" * 64, "available", 3)
        self._insert_capture(11, 1, "16600011", "/event/16600011/comments", "b" * 64, "available", 1)
        self._insert_capture(12, 1, "16600011", SPANISH_PATH, "c" * 64, "available", 1)
        self._insert_capture(20, 2, "1002", "/event/1002/comments/es-la", "d" * 64, "empty", 0)

        self.connection.execute(
            """INSERT INTO narrative_comment_selection
               (match_id, capture_id, last_successful_source_path, selected_spanish_sha256,
                last_attempt_state, last_attempt_at, last_http_status, last_error_code)
               VALUES (1, 10, ?, ?, 'error', '2026-11-05T11:00:00+00:00', 503, 'transport_error')""",
            (SPANISH_PATH, "a" * 64),
        )
        self.connection.execute(
            """INSERT INTO narrative_comment_selection
               (match_id, capture_id, last_successful_source_path, selected_spanish_sha256,
                last_attempt_state, last_attempt_at, last_http_status, last_error_code)
               VALUES (2, 20, '/event/1002/comments/es-la', ?, 'empty', '2026-11-05T11:00:00+00:00', 200, NULL)""",
            ("d" * 64,),
        )

        self._insert_comment(10, "100", "scoreChange", "¡Gol! Autogol de Juan Durán.",
                             {"player": {"id": 1393275, "name": "Juan Durán"}})
        self._insert_comment(10, "101", "freeKickWon", "Javi Antón se ha ido al suelo, pero ha simulado una falta.",
                             {"player": {"id": "18", "name": "Javi Antón"}})
        self._insert_comment(10, "102", "substitution", "Cambio.", {
            "playerIn": {"id": "21", "name": "Entra"},
            "playerOut": {"id": 22, "name": "Sale"},
        })
        self._insert_comment(11, "english-history", "scoreChange", "Goal", {})
        self._insert_comment(12, "spanish-history", "yellowCard", "Tarjeta amarilla", {
            "player": {"id": 88, "name": "Histórico"},
        })
        self.connection.commit()

    def _insert_capture(self, capture_id, match_id, event_id, path, digest, availability, count):
        self.connection.execute(
            """INSERT INTO narrative_comment_capture
               (id, match_id, provider, source_event_id, source_path, captured_at, http_status, availability,
                comment_count, response_sha256, response_json)
               VALUES (?, ?, 'sofascore', ?, ?, '2026-11-05T10:00:00+00:00', 200, ?, ?, ?, '{"comments": []}')""",
            (capture_id, match_id, event_id, path, availability, count, digest),
        )

    def _insert_comment(self, capture_id, source_id, kind, text, participants):
        position = self.connection.execute(
            "SELECT count(*) FROM narrative_comment WHERE capture_id = ?", (capture_id,)
        ).fetchone()[0]
        self.connection.execute(
            """INSERT INTO narrative_comment
               (capture_id, source_comment_id, source_position, text, type, participants_json, context_json)
               VALUES (?, ?, ?, ?, ?, ?, '{}')""",
            (capture_id, source_id, position, text, kind,
             json.dumps(participants, ensure_ascii=False, separators=(",", ":"))),
        )

    def test_current_spanish_capture_is_persisted_idempotently_and_versioned(self):
        first = persist_selected_enrichments(self.connection)
        self.assertEqual(first, {
            "enrichment_version": "rules-v1",
            "eligible_captures": 2,
            "comments_seen": 3,
            "inserted": 3,
            "unchanged": 0,
            "participants_inserted": 4,
        })
        rows = self.connection.execute(
            """SELECT enrichment.source_comment_id, enrichment.category, enrichment.attributes_json,
                      participant.source_role, participant.role, participant.source_id_json,
                      participant.normalizer_role
               FROM narrative_comment_enrichment AS enrichment
               LEFT JOIN narrative_comment_enrichment_participant AS participant
                 ON participant.capture_id = enrichment.capture_id
                AND participant.source_comment_id = enrichment.source_comment_id
                AND participant.enrichment_version = enrichment.enrichment_version
               WHERE enrichment.enrichment_version = 'rules-v1'
               ORDER BY enrichment.source_comment_id, participant.participant_position"""
        ).fetchall()
        by_comment = {}
        for row in rows:
            by_comment.setdefault(row[0], []).append(row[1:])
        self.assertEqual(set(by_comment), {"100", "101", "102"})

        own_goal = by_comment["100"][0]
        self.assertEqual(own_goal[0], "goal")
        self.assertEqual(json.loads(own_goal[1])["goal_kind"], "own_goal")
        self.assertEqual(own_goal[2:], ("player", "own_goal_author", "1393275", "scorer"))

        ambiguous_foul = by_comment["101"][0]
        self.assertIsNone(ambiguous_foul[3])
        self.assertEqual(ambiguous_foul[4], '"18"')
        self.assertEqual(ambiguous_foul[5], "foul_recipient")

        substitution = by_comment["102"]
        self.assertEqual([participant[3] for participant in substitution], ["incoming_player", "outgoing_player"])
        self.assertEqual([participant[4] for participant in substitution], ['"21"', "22"])
        self.assertNotIn("name", {row[1] for row in self.connection.execute(
            "PRAGMA table_info(narrative_comment_enrichment_participant)")})

        repeated = persist_selected_enrichments(self.connection)
        self.assertEqual((repeated["inserted"], repeated["unchanged"]), (0, 3))
        next_version = persist_selected_enrichments(self.connection, "rules-v2")
        self.assertEqual((next_version["inserted"], next_version["unchanged"]), (3, 0))
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM narrative_comment_enrichment WHERE enrichment_version = 'rules-v1'"
        ).fetchone()[0], 3)
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM narrative_comment_enrichment WHERE enrichment_version = 'rules-v2'"
        ).fetchone()[0], 3)

    def test_existing_conflict_rolls_back_new_annotations_from_same_batch(self):
        self.connection.execute(
            """INSERT INTO narrative_comment_enrichment
               (capture_id, source_comment_id, enrichment_version, category, attributes_json)
               VALUES (10, '102', 'rules-v1', 'conflicting-category', '{}')"""
        )
        self.connection.commit()

        with self.assertRaises(EnrichmentConflict):
            persist_selected_enrichments(self.connection)
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM narrative_comment_enrichment WHERE enrichment_version = 'rules-v1'"
        ).fetchone()[0], 1)
        self.assertIsNone(self.connection.execute(
            """SELECT 1 FROM narrative_comment_enrichment
               WHERE capture_id = 10 AND source_comment_id IN ('100', '101') AND enrichment_version = 'rules-v1'"""
        ).fetchone())


if __name__ == "__main__":
    unittest.main()
