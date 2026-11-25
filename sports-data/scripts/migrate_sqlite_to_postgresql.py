"""Import the Sports Data SQLite 0008 snapshot into the empty Neon pg0001 database."""
from __future__ import annotations

import argparse
import hashlib
import heapq
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Iterator, Sequence

import psycopg
from psycopg import sql

from persistencia.postgresql import url_postgresql


NEON_PROJECT_ID = 'red-leaf-83438333'
NEON_BRANCH = 'development'
# Pinned to the direct endpoint verified for this project and branch in Hito 00.
NEON_ENDPOINT_HOST = 'ep-soft-glade-bh8z74nm.c-10.eu-central-1.aws.neon.tech'
TABLAS_ESPERADAS = (
    'call_up', 'call_up_change', 'call_up_stat', 'competition', 'competition_external_id',
    'content', 'discrepancy', 'discrepancy_observation', 'discrepancy_reason', 'discrepancy_value',
    'file_team_declaration', 'identity_decision', 'identity_decision_subject', 'import_run',
    'import_run_file', 'local_replacement', 'match', 'match_external_id', 'match_state',
    'match_version_issue', 'narrative_comment', 'narrative_comment_capture',
    'narrative_comment_enrichment', 'narrative_comment_enrichment_participant',
    'narrative_comment_selection', 'observation', 'player', 'player_attribute_observation',
    'player_external_id', 'rule_set', 'season', 'season_external_id', 'section_selection',
    'section_selection_history', 'source_file', 'stage', 'stage_external_id', 'team',
    'team_external_id', 'team_name',
)
TRIGGERS_ESPERADOS = {
    'narrative_comment_capture_immutable_update',
    'narrative_comment_capture_immutable_delete',
    'narrative_comment_immutable_update',
    'narrative_comment_immutable_delete',
}
TAMANO_LOTE = 2_000


class MigrationError(RuntimeError):
    """A source or destination check rejected the migration."""


@dataclass(frozen=True)
class SequenceInfo:
    table: str
    column: str
    schema: str
    name: str

    @property
    def qualified_name(self) -> str:
        return f'{self.schema}.{self.name}'


def _connect_postgres(url) -> psycopg.Connection:
    """Conecta con los campos de URL, sin serializar la contraseña en un DSN."""
    params = {
        'host': url.host,
        'dbname': url.database,
        'user': url.username,
        'password': url.password,
        'sslmode': url.query['sslmode'],
        'sslrootcert': url.query['sslrootcert'],
        'channel_binding': url.query['channel_binding'],
        'connect_timeout': 10,
    }
    if url.port is not None:
        params['port'] = url.port
    return psycopg.connect(**params, autocommit=True)


def _quote_sqlite(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _count_query_sqlite(connection: sqlite3.Connection) -> dict[str, int]:
    selects = [
        f"SELECT '{table}' AS table_name, count(*) AS row_count FROM {_quote_sqlite(table)}"
        for table in TABLAS_ESPERADAS
    ]
    return {name: count for name, count in connection.execute(' UNION ALL '.join(selects))}


def _count_query_postgres(cursor: psycopg.Cursor, tables: Sequence[str]) -> dict[str, int]:
    selects = [
        sql.SQL('SELECT %s::text AS table_name, count(*) AS row_count FROM {}.{}').format(
            sql.Identifier('public'), sql.Identifier(table)
        )
        for table in tables
    ]
    query = sql.SQL(' UNION ALL ').join(selects)
    params = [table for table in tables]
    return {name: count for name, count in cursor.execute(query, params).fetchall()}


def _source_tables(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "AND name <> 'alembic_version'"
        )
    }


def _sqlite_columns_and_keys(connection: sqlite3.Connection, table: str) -> tuple[list[str], tuple[str, ...]]:
    info = connection.execute(f'PRAGMA table_info({_quote_sqlite(table)})').fetchall()
    columns = [row[1] for row in info]
    primary_key = tuple(row[1] for row in sorted((r for r in info if r[5]), key=lambda r: r[5]))
    if not columns or not primary_key:
        raise MigrationError(f'La tabla {table} no contiene columnas o clave primaria')
    return columns, primary_key


def _foreign_keys(connection: sqlite3.Connection, table: str) -> list[tuple[str, tuple[str, ...], tuple[str, ...]]]:
    grouped: dict[int, list[tuple[int, str, str, str]]] = {}
    for row in connection.execute(f'PRAGMA foreign_key_list({_quote_sqlite(table)})'):
        grouped.setdefault(row[0], []).append((row[1], row[2], row[3], row[4]))
    result = []
    for rows in grouped.values():
        rows.sort()
        result.append((rows[0][1], tuple(r[2] for r in rows), tuple(r[3] for r in rows)))
    return result


def _table_order(connection: sqlite3.Connection) -> list[str]:
    parents = {table: set() for table in TABLAS_ESPERADAS}
    children: dict[str, set[str]] = {table: set() for table in TABLAS_ESPERADAS}
    for child in TABLAS_ESPERADAS:
        for parent, _from_columns, _to_columns in _foreign_keys(connection, child):
            if parent != child:
                if parent not in parents:
                    raise MigrationError(f'La FK de {child} apunta a una tabla inesperada')
                parents[child].add(parent)
    for child, required in parents.items():
        for parent in required:
            children[parent].add(child)
    ready = list(sorted(table for table, required in parents.items() if not required))
    heapq.heapify(ready)
    ordered = []
    while ready:
        parent = heapq.heappop(ready)
        ordered.append(parent)
        for child in sorted(children[parent]):
            parents[child].remove(parent)
            if not parents[child]:
                heapq.heappush(ready, child)
    if len(ordered) != len(TABLAS_ESPERADAS):
        raise MigrationError('El grafo de claves foráneas entre tablas contiene un ciclo')
    return ordered


def _rows_for_table(
    connection: sqlite3.Connection, table: str, columns: Sequence[str], primary_key: Sequence[str]
) -> Iterator[tuple]:
    order = ', '.join(_quote_sqlite(column) for column in primary_key)
    cursor = connection.execute(
        f'SELECT {", ".join(_quote_sqlite(c) for c in columns)} '
        f'FROM {_quote_sqlite(table)} ORDER BY {order}'
    )
    self_fks = [fk for fk in _foreign_keys(connection, table) if fk[0] == table]
    if not self_fks:
        while rows := cursor.fetchmany(TAMANO_LOTE):
            for row in rows:
                yield tuple(row)
        return

    rows = [tuple(row) for row in cursor.fetchall()]
    indexes = {name: index for index, name in enumerate(columns)}
    pk_indexes = tuple(indexes[name] for name in primary_key)
    rows_by_key = {tuple(row[index] for index in pk_indexes): row for row in rows}
    dependencies: dict[tuple, set[tuple]] = {key: set() for key in rows_by_key}
    children: dict[tuple, set[tuple]] = {key: set() for key in rows_by_key}

    for parent, from_columns, to_columns in self_fks:
        if parent != table:
            continue
        target_to_key = {
            tuple(row[indexes[name]] for name in to_columns): key
            for key, row in rows_by_key.items()
        }
        for key, row in rows_by_key.items():
            source_key = tuple(row[indexes[name]] for name in from_columns)
            if any(value is None for value in source_key):
                continue
            parent_key = target_to_key.get(source_key)
            if parent_key is None:
                raise MigrationError(f'La autorreferencia de {table} no encuentra su destino')
            if parent_key != key:
                dependencies[key].add(parent_key)
                children[parent_key].add(key)

    ready = [(repr(key), key) for key, required in dependencies.items() if not required]
    heapq.heapify(ready)
    ordered = []
    while ready:
        _sort_key, parent_key = heapq.heappop(ready)
        ordered.append(rows_by_key[parent_key])
        for child_key in children[parent_key]:
            dependencies[child_key].remove(parent_key)
            if not dependencies[child_key]:
                heapq.heappush(ready, (repr(child_key), child_key))
    if len(ordered) != len(rows):
        raise MigrationError(f'La tabla {table} contiene un ciclo de claves foráneas autorreferenciales')
    yield from ordered


def _canonical_value(value, postgres_type: str) -> tuple[bytes, bytes]:
    if value is None:
        return b'n', b''
    if postgres_type == 'bool':
        if value not in (0, 1, False, True):
            raise MigrationError('Una bandera SQLite no contiene 0, 1 o NULL')
        return b'b', b'1' if bool(value) else b'0'
    if postgres_type in {'numeric', 'decimal'}:
        return b'n', format(Decimal(str(value)).normalize(), 'f').encode('ascii')
    if postgres_type in {'float4', 'float8'}:
        return b'f', float(value).hex().encode('ascii')
    if postgres_type == 'date':
        normalized = value.date().isoformat() if isinstance(value, datetime) else (
            value.isoformat() if isinstance(value, date) else date.fromisoformat(str(value)).isoformat()
        )
        return b'd', normalized.encode('utf-8')
    if postgres_type in {'timestamp', 'timestamptz'}:
        if isinstance(value, datetime):
            normalized = value.isoformat()
        else:
            normalized = datetime.fromisoformat(str(value).replace('Z', '+00:00')).isoformat()
        return b't', normalized.encode('utf-8')
    if isinstance(value, bool):
        return b'b', b'1' if value else b'0'
    if isinstance(value, int):
        return b'i', str(value).encode('ascii')
    if isinstance(value, Decimal):
        return b'n', format(value, 'f').encode('ascii')
    if isinstance(value, float):
        return b'f', value.hex().encode('ascii')
    if isinstance(value, (bytes, bytearray, memoryview)):
        return b'x', bytes(value)
    if isinstance(value, str):
        return b's', value.encode('utf-8')
    raise MigrationError('La migración encontró un tipo de valor no soportado')


def _row_digest(row: Sequence, columns: Sequence[str], types: dict[str, str]) -> bytes:
    digest = hashlib.sha256()
    for name, value in zip(columns, row, strict=True):
        tag, payload = _canonical_value(value, types[name])
        digest.update(len(name).to_bytes(2, 'big'))
        digest.update(name.encode('utf-8'))
        digest.update(tag)
        digest.update(len(payload).to_bytes(8, 'big'))
        digest.update(payload)
    return digest.digest()


def _table_digest(table: str, row_hashes: list[bytes]) -> str:
    digest = hashlib.sha256()
    digest.update(table.encode('utf-8'))
    digest.update(len(row_hashes).to_bytes(8, 'big'))
    for row_hash in sorted(row_hashes):
        digest.update(row_hash)
    return digest.hexdigest()


def _copy_to_postgres(
    connection: psycopg.Connection,
    source: sqlite3.Connection,
    table: str,
    columns: Sequence[str],
    primary_key: Sequence[str],
    bool_columns: set[str],
    pg_types: dict[str, str],
) -> tuple[int, str]:
    row_hashes: list[bytes] = []
    count = 0
    copy_statement = sql.SQL('COPY {}.{} ({}) FROM STDIN').format(
        sql.Identifier('public'), sql.Identifier(table),
        sql.SQL(', ').join(sql.Identifier(column) for column in columns),
    )
    try:
        with connection.cursor() as cursor:
            with cursor.copy(copy_statement) as copy:
                for row in _rows_for_table(source, table, columns, primary_key):
                    adapted = tuple(
                        bool(value) if column in bool_columns and value is not None else value
                        for column, value in zip(columns, row, strict=True)
                    )
                    copy.write_row(adapted)
                    row_hashes.append(_row_digest(row, columns, pg_types))
                    count += 1
    except MigrationError:
        raise
    except Exception as error:
        code = getattr(error, 'sqlstate', None)
        detail = f' SQLSTATE {code}' if code else ''
        raise MigrationError(f'Falló COPY de {table}{detail}; PostgreSQL revertirá la transacción') from None
    return count, _table_digest(table, row_hashes)


def _verify_postgres_table(
    connection: psycopg.Connection,
    table: str,
    columns: Sequence[str],
    primary_key: Sequence[str],
    pg_types: dict[str, str],
) -> tuple[int, str]:
    statement = sql.SQL('SELECT {} FROM {}.{} ORDER BY {}').format(
        sql.SQL(', ').join(sql.Identifier(column) for column in columns),
        sql.Identifier('public'), sql.Identifier(table),
        sql.SQL(', ').join(sql.Identifier(column) for column in primary_key),
    )
    hashes = []
    count = 0
    with connection.cursor(name=f'verify_{table}') as cursor:
        cursor.execute(statement)
        while rows := cursor.fetchmany(TAMANO_LOTE):
            for row in rows:
                hashes.append(_row_digest(row, columns, pg_types))
                count += 1
    return count, _table_digest(table, hashes)


def _sequences(cursor: psycopg.Cursor) -> list[SequenceInfo]:
    rows = cursor.execute(
        "SELECT tbl.relname, att.attname, seq_ns.nspname, seq.relname "
        'FROM pg_class AS seq '
        'JOIN pg_namespace AS seq_ns ON seq_ns.oid=seq.relnamespace '
        "JOIN pg_depend AS dep ON dep.objid=seq.oid AND dep.deptype IN ('a','i') "
        'JOIN pg_class AS tbl ON tbl.oid=dep.refobjid '
        'JOIN pg_namespace AS tbl_ns ON tbl_ns.oid=tbl.relnamespace '
        'JOIN pg_attribute AS att ON att.attrelid=tbl.oid AND att.attnum=dep.refobjsubid '
        "WHERE seq.relkind='S' AND seq_ns.nspname='public' AND tbl_ns.nspname='public' "
        'AND tbl.relname = ANY(%s) ORDER BY tbl.relname, att.attnum',
        (list(TABLAS_ESPERADAS),),
    ).fetchall()
    return [SequenceInfo(*row) for row in rows]


def _sequence_states(cursor: psycopg.Cursor, sequences: Sequence[SequenceInfo]) -> dict[str, tuple[int, bool]]:
    if not sequences:
        return {}
    selects = [
        sql.SQL('SELECT %s::text, last_value, is_called FROM {}.{}').format(
            sql.Identifier(sequence.schema), sql.Identifier(sequence.name)
        )
        for sequence in sequences
    ]
    rows = cursor.execute(sql.SQL(' UNION ALL ').join(selects), [s.qualified_name for s in sequences]).fetchall()
    return {name: (value, called) for name, value, called in rows}


def _set_sequence_states(cursor: psycopg.Cursor, values: Sequence[tuple[str, int, bool]]) -> None:
    if not values:
        return
    row_sql = sql.SQL('(%s::text, %s::bigint, %s::boolean)')
    query = sql.SQL(
        'SELECT setval(seq_name::regclass, seq_value, seq_called) '
        'FROM (VALUES {}) AS sequences(seq_name, seq_value, seq_called)'
    ).format(sql.SQL(', ').join(row_sql for _ in values))
    params = [item for value in values for item in value]
    cursor.execute(query, params)


def _sequence_maxima(cursor: psycopg.Cursor, sequences: Sequence[SequenceInfo]) -> dict[str, int | None]:
    if not sequences:
        return {}
    selects = [
        sql.SQL('SELECT %s::text, MAX({}) FROM {}.{}').format(
            sql.Identifier(sequence.column), sql.Identifier('public'), sql.Identifier(sequence.table)
        )
        for sequence in sequences
    ]
    rows = cursor.execute(sql.SQL(' UNION ALL ').join(selects), [s.qualified_name for s in sequences]).fetchall()
    return {name: value for name, value in rows}


def _sequence_actual(cursor: psycopg.Cursor, sequences: Sequence[SequenceInfo]) -> dict[str, tuple[int | None, int, bool]]:
    if not sequences:
        return {}
    selects = [
        sql.SQL(
            'SELECT %s::text, MAX({}), (SELECT last_value FROM {}.{}), '
            '(SELECT is_called FROM {}.{}) FROM {}.{}'
        ).format(
            sql.Identifier(sequence.column),
            sql.Identifier(sequence.schema), sql.Identifier(sequence.name),
            sql.Identifier(sequence.schema), sql.Identifier(sequence.name),
            sql.Identifier('public'), sql.Identifier(sequence.table),
        )
        for sequence in sequences
    ]
    rows = cursor.execute(sql.SQL(' UNION ALL ').join(selects), [s.qualified_name for s in sequences]).fetchall()
    return {name: (maximum, last_value, called) for name, maximum, last_value, called in rows}


def _desired_sequence_states(sequences: Sequence[SequenceInfo], maxima: dict[str, int | None]) -> list[tuple[str, int, bool]]:
    result = []
    for sequence in sequences:
        maximum = maxima[sequence.qualified_name]
        if maximum is None or maximum <= 0:
            result.append((sequence.qualified_name, 1, False))
        else:
            result.append((sequence.qualified_name, maximum, True))
    return result


def _preflight_source(path: Path) -> tuple[sqlite3.Connection, dict[str, int], dict[str, tuple[list[str], tuple[str, ...]]], list[str], os.stat_result]:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise MigrationError(f'No existe la SQLite indicada: {path}')
    before = path.stat()
    connection = sqlite3.connect(f'{path.as_uri()}?mode=ro', uri=True)
    connection.execute('PRAGMA query_only=ON')
    connection.execute('BEGIN')

    version_table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='alembic_version'"
    ).fetchone()
    if not version_table:
        connection.close()
        raise MigrationError('El origen no contiene alembic_version')
    revision = connection.execute('SELECT version_num FROM alembic_version').fetchall()
    if revision != [('0008',)]:
        connection.close()
        raise MigrationError('El origen debe estar exactamente en SQLite 0008')
    tables = _source_tables(connection)
    if tables != set(TABLAS_ESPERADAS):
        connection.close()
        raise MigrationError('Las 40 tablas de SQLite no coinciden con el inventario 0008')
    integrity = connection.execute('PRAGMA integrity_check').fetchone()[0]
    fk_issue = connection.execute('PRAGMA foreign_key_check').fetchone()
    if integrity != 'ok' or fk_issue:
        connection.close()
        raise MigrationError('La SQLite de origen no supera integrity_check o foreign_key_check')

    columns = {table: _sqlite_columns_and_keys(connection, table) for table in TABLAS_ESPERADAS}
    order = _table_order(connection)
    counts = _count_query_sqlite(connection)
    return connection, counts, columns, order, before


def _preflight_destination(connection: psycopg.Connection, url) -> tuple[
    dict[str, dict[str, str]], dict[str, set[str]], dict[str, tuple[str, ...]],
    list[SequenceInfo], dict[str, tuple[int, bool]],
]:
    if url.host != NEON_ENDPOINT_HOST:
        raise MigrationError(
            f'El endpoint no coincide con el proyecto {NEON_PROJECT_ID}, rama {NEON_BRANCH} verificados'
        )
    with connection.cursor() as cursor:
        database, role, version, revision = cursor.execute(
            "SELECT current_database(), current_user, current_setting('server_version_num')::integer, "
            "(SELECT version_num FROM public.alembic_version)"
        ).fetchone()
        if database != 'sports_data' or role != 'neondb_owner' or version < 180000 or revision != 'pg0001':
            raise MigrationError('El destino no es sports_data, PostgreSQL 18, neondb_owner y revisión pg0001')

        tables = {
            row[0] for row in cursor.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_type='BASE TABLE' AND table_name <> 'alembic_version'"
            )
        }
        if tables != set(TABLAS_ESPERADAS):
            raise MigrationError('El inventario de tablas destino no coincide con pg0001')
        view_ok = cursor.execute("SELECT to_regclass('public.call_up_vigente') IS NOT NULL").fetchone()[0]
        trigger_names = {
            row[0] for row in cursor.execute(
                "SELECT trigger_name FROM information_schema.triggers WHERE trigger_schema='public'"
            )
        }
        if not view_ok or not TRIGGERS_ESPERADOS.issubset(trigger_names):
            raise MigrationError('Falta la vista o los triggers esperados del esquema pg0001')

        pg_columns: dict[str, set[str]] = {table: set() for table in TABLAS_ESPERADAS}
        pg_types: dict[str, dict[str, str]] = {table: {} for table in TABLAS_ESPERADAS}
        for table, column, data_type, udt_name in cursor.execute(
            "SELECT table_name, column_name, data_type, udt_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name = ANY(%s)",
            (list(TABLAS_ESPERADAS),),
        ):
            pg_columns[table].add(column)
            pg_types[table][column] = udt_name

        pg_primary_keys: dict[str, list[tuple[int, str]]] = {table: [] for table in TABLAS_ESPERADAS}
        for table, column, position in cursor.execute(
            "SELECT kcu.table_name, kcu.column_name, kcu.ordinal_position "
            "FROM information_schema.table_constraints AS tc "
            "JOIN information_schema.key_column_usage AS kcu "
            'ON kcu.constraint_schema=tc.constraint_schema AND kcu.constraint_name=tc.constraint_name '
            'AND kcu.table_name=tc.table_name '
            "WHERE tc.table_schema='public' AND tc.constraint_type='PRIMARY KEY' "
            'AND tc.table_name = ANY(%s) ORDER BY kcu.table_name, kcu.ordinal_position',
            (list(TABLAS_ESPERADAS),),
        ):
            pg_primary_keys[table].append((position, column))

        if cursor.execute(
            "SELECT count(*) FROM pg_constraint WHERE connamespace='public'::regnamespace "
            "AND contype='f' AND NOT convalidated"
        ).fetchone()[0]:
            raise MigrationError('El destino contiene claves foráneas no validadas')

        if set().union(*(pg_types[t].keys() for t in TABLAS_ESPERADAS)) == set():
            raise MigrationError('No se pudo inspeccionar las columnas PostgreSQL')
        nonempty = {table: count for table, count in _count_query_postgres(cursor, TABLAS_ESPERADAS).items() if count}
        if nonempty:
            raise MigrationError('El destino no está vacío; no se sobrescriben datos existentes')

        sequences = _sequences(cursor)
        states = _sequence_states(cursor, sequences)
        return pg_types, pg_columns, {
            table: tuple(column for _position, column in sorted(pg_primary_keys[table]))
            for table in TABLAS_ESPERADAS
        }, sequences, states


def _restore_sequences_if_empty(connection: psycopg.Connection, prior: dict[str, tuple[int, bool]]) -> None:
    if not prior:
        return
    try:
        with connection.cursor() as cursor:
            counts = _count_query_postgres(cursor, TABLAS_ESPERADAS)
            if any(counts.values()):
                return
            _set_sequence_states(cursor, [(name, value, called) for name, (value, called) in prior.items()])
    except Exception:
        print('Aviso: la transacción no dejó datos parciales; no se pudo restaurar algún estado de secuencia.', file=sys.stderr)


def _read_postgres_table_hashes(
    connection: psycopg.Connection,
    table: str,
    columns: Sequence[str],
    primary_key: Sequence[str],
    pg_types: dict[str, str],
) -> tuple[int, str]:
    return _verify_postgres_table(connection, table, columns, primary_key, pg_types)


def migrate(source_path: Path, *, authorize_external: bool) -> dict[str, int]:
    if not authorize_external:
        raise MigrationError('Para una carga real se requiere --authorize-external-hosting')

    source, source_counts, source_schema, table_order, source_before = _preflight_source(source_path)
    url = url_postgresql('SPORTS_DATA_MIGRATION_DATABASE_URL')
    try:
        with _connect_postgres(url) as destination:
            pg_types, pg_columns, pg_primary_keys, sequences, old_sequence_states = _preflight_destination(
                destination, url
            )
            for table in TABLAS_ESPERADAS:
                sqlite_columns, sqlite_pk = source_schema[table]
                if pg_columns[table] != set(sqlite_columns):
                    raise MigrationError(f'Las columnas de {table} no coinciden entre motores')
                if pg_primary_keys[table] != sqlite_pk:
                    raise MigrationError(f'La clave primaria de {table} no coincide entre motores')

            sequence_states_saved = old_sequence_states
            try:
                with destination.transaction():
                    lock_tables = sql.SQL(', ').join(
                        sql.Identifier('public', table) for table in sorted(TABLAS_ESPERADAS)
                    )
                    with destination.cursor() as cursor:
                        cursor.execute(sql.SQL('LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE').format(lock_tables))
                        if any(_count_query_postgres(cursor, TABLAS_ESPERADAS).values()):
                            raise MigrationError('El destino dejó de estar vacío antes de adquirir los bloqueos')

                    source_digests: dict[str, str] = {}
                    loaded_counts: dict[str, int] = {}
                    for table in table_order:
                        columns, primary_key = source_schema[table]
                        bool_columns = {column for column, pg_type in pg_types[table].items() if pg_type == 'bool'}
                        loaded_counts[table], source_digests[table] = _copy_to_postgres(
                            destination, source, table, columns, primary_key, bool_columns, pg_types[table]
                        )

                    for table in TABLAS_ESPERADAS:
                        columns, primary_key = source_schema[table]
                        actual_count, actual_digest = _read_postgres_table_hashes(
                            destination, table, columns, primary_key, pg_types[table]
                        )
                        if actual_count != source_counts[table] or loaded_counts[table] != source_counts[table]:
                            raise MigrationError(f'El recuento de {table} no coincide con el origen')
                        if actual_digest != source_digests[table]:
                            raise MigrationError(f'La huella de contenido de {table} no coincide con el origen')

                    source_after = Path(source_path).expanduser().resolve().stat()
                    if (source_before.st_size, source_before.st_mtime_ns) != (source_after.st_size, source_after.st_mtime_ns):
                        raise MigrationError('La SQLite de origen cambió mientras se ejecutaba la migración')

                    if sequences:
                        with destination.cursor() as cursor:
                            maxima = _sequence_maxima(cursor, sequences)
                            desired_states = _desired_sequence_states(sequences, maxima)
                            _set_sequence_states(cursor, desired_states)
                            actual_states = _sequence_actual(cursor, sequences)
                            for name, maximum, last_value, called in (
                                (name, *state) for name, state in actual_states.items()
                            ):
                                expected = next(value for seq_name, value, _called in desired_states if seq_name == name)
                                expected_called = next(called for seq_name, _value, called in desired_states if seq_name == name)
                                if last_value != expected or called != expected_called or (maximum is not None and last_value < maximum):
                                    raise MigrationError('El estado de una secuencia no cubre los IDs importados')
            except Exception:
                _restore_sequences_if_empty(destination, sequence_states_saved)
                raise

            with destination.cursor() as cursor:
                size_bytes = cursor.execute('SELECT pg_database_size(current_database())').fetchone()[0]
            print(f'project={NEON_PROJECT_ID} branch={NEON_BRANCH} database=sports_data revision=pg0001')
            print(f'tables={len(TABLAS_ESPERADAS)} rows={sum(source_counts.values())}')
            for table in ('team', 'player', 'match', 'narrative_comment', 'narrative_comment_enrichment'):
                print(f'rows_{table}={source_counts[table]}')
            print(f'rows_narrative_comment_enrichment_participant={source_counts["narrative_comment_enrichment_participant"]}')
            print(f'sequences={len(sequences)} state=verified')
            print(f'postgres_database_size_bytes={size_bytes} quota_1_gb_percent={100*size_bytes/1_000_000_000:.2f}')
            print('pk_values_and_row_hashes=verified foreign_keys=enforced sqlite_source_unchanged=true')
            return source_counts
    finally:
        source.close()


def main() -> int:
    parser = argparse.ArgumentParser(description='Transfiere una SQLite 0008 a la Neon sports_data vacía.')
    parser.add_argument('source_sqlite', type=Path, help='Ruta explícita a la copia SQLite 0008 consistente')
    parser.add_argument('--authorize-external-hosting', action='store_true',
                        help='Confirma que los datos reales pueden alojarse en Neon dev')
    args = parser.parse_args()
    try:
        migrate(args.source_sqlite, authorize_external=args.authorize_external_hosting)
    except MigrationError as error:
        print(f'Error: {error}', file=sys.stderr)
        return 2
    except Exception as error:
        code = getattr(error, 'sqlstate', None)
        detail = f' SQLSTATE {code}' if code else ''
        print(f'Error inesperado ({type(error).__name__}){detail}; no se muestran valores ni credenciales.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
