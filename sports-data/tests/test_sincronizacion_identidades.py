"""Focused checks for synchronization of retained source observations after explicit identity merges."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from sqlalchemy import create_engine

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from persistencia import sincronizacion  # noqa: E402
from persistencia.carga import CargaRechazada  # noqa: E402


class _Filas:
    def __init__(self, **rows):
        self.rows = rows

    def de(self, table):
        return self.rows.get(table, [])


class _Mappings:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def __iter__(self):
        return iter(self.rows)


class _RootConnection:
    dialect = SimpleNamespace(name='postgresql')

    def __init__(self, players):
        self.info = {}
        self.players = players

    def execute(self, _statement):
        return _Mappings(self.players)


class SynchronizationIdentityAliases(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        self.connection = self.engine.connect()
        self.connection.exec_driver_sql(
            'CREATE TABLE player (id INTEGER PRIMARY KEY, status TEXT NOT NULL, merged_into_id INTEGER)')
        self.connection.exec_driver_sql(
            'INSERT INTO player (id,status,merged_into_id) VALUES (419,\'active\',NULL),(479,\'merged\',419)')
        self.connection.exec_driver_sql(
            'CREATE TABLE source_file (id INTEGER PRIMARY KEY, label TEXT, sha256 TEXT, kind TEXT)')
        self.connection.exec_driver_sql(
            'INSERT INTO source_file (id,label,sha256,kind) VALUES (23,\'E23\',:sha,\'player_profile\')',
            {'sha': 'a' * 64})
        self.connection.exec_driver_sql('CREATE TABLE content (hash TEXT PRIMARY KEY)')
        self.connection.exec_driver_sql('INSERT INTO content (hash) VALUES (:hash)', {'hash': 'b' * 64})
        self.connection.exec_driver_sql('''
            CREATE TABLE file_team_declaration (
                source_file_id INTEGER NOT NULL, external_team_id TEXT NOT NULL, status TEXT NOT NULL,
                PRIMARY KEY (source_file_id, external_team_id)
            )''')
        self.connection.exec_driver_sql('''
            CREATE TABLE observation (
                id INTEGER PRIMARY KEY, source_file_id INTEGER, line INTEGER, kind TEXT, provider TEXT,
                external_event_id TEXT, external_player_id TEXT, key TEXT, sub TEXT, endpoint TEXT,
                fetched_at_raw TEXT, fetched_instant TEXT, fetched_at_valid BOOLEAN, content_hash TEXT,
                envelope_extra TEXT, match_id INTEGER, player_id INTEGER
            )''')
        self.connection.exec_driver_sql('''
            INSERT INTO observation
                (id,source_file_id,line,kind,provider,external_player_id,key,sub,endpoint,
                 fetched_at_valid,content_hash,player_id)
            VALUES (2284,23,1,'player_profile','sofascore','2412442','player_profile','','/player/2412442',1,:hash,479)
        ''', {'hash': 'b' * 64})

    def tearDown(self):
        self.connection.close()
        self.engine.dispose()

    def _desired_rows(self):
        return _Filas(
            source_file=[{'id': 101, 'label': 'E23', 'sha256': 'a' * 64, 'kind': 'player_profile'}],
            content=[],
            observation=[{
                'id': 901, 'source_file_id': 101, 'line': 1, 'kind': 'player_profile',
                'provider': 'sofascore', 'external_event_id': None, 'external_player_id': '2412442',
                'key': 'player_profile', 'sub': '', 'endpoint': '/player/2412442',
                'fetched_at_raw': None, 'fetched_instant': None, 'fetched_at_valid': True,
                'content_hash': 'b' * 64, 'envelope_extra': None, 'match_id': None, 'player_id': 42,
            }],
            file_team_declaration=[],
        )

    def test_only_explicitly_merged_player_ids_share_a_root(self):
        connection = _RootConnection([
            {'id': 419, 'status': 'active', 'merged_into_id': None},
            {'id': 479, 'status': 'merged', 'merged_into_id': 419},
            {'id': 480, 'status': 'active', 'merged_into_id': None},
        ])
        self.assertTrue(sincronizacion.misma_identidad_jugador_postgresql(connection, 479, 419))
        self.assertFalse(sincronizacion.misma_identidad_jugador_postgresql(connection, 479, 480))

    def test_historical_profile_fk_is_retained_when_roots_match(self):
        plan, translation = sincronizacion.Plan(), sincronizacion.Traduccion(1)
        translation.ids['player'][42] = 419
        with mock.patch.object(sincronizacion, 'misma_identidad_jugador_postgresql', return_value=True) as same:
            sincronizacion.planificar_evidencia(self.connection, self._desired_rows(), plan, translation)
        same.assert_called_once_with(self.connection, 479, 419)
        self.assertTrue(plan.vacio)
        retained = self.connection.exec_driver_sql('SELECT player_id FROM observation WHERE id=2284').scalar_one()
        self.assertEqual(retained, 479)

    def test_unrelated_player_fk_difference_is_still_rejected(self):
        plan, translation = sincronizacion.Plan(), sincronizacion.Traduccion(1)
        translation.ids['player'][42] = 419
        with mock.patch.object(sincronizacion, 'misma_identidad_jugador_postgresql', return_value=False):
            with self.assertRaisesRegex(CargaRechazada, r'observación 2284.*player_id'):
                sincronizacion.planificar_evidencia(self.connection, self._desired_rows(), plan, translation)


if __name__ == '__main__':
    unittest.main()
