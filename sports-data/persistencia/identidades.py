"""Decisiones explícitas, auditadas y reversibles para identidades de jugadores en PostgreSQL."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import uuid

from sqlalchemy import bindparam, text

from persistencia import carga, sincronizacion


PROVEEDOR = 'sofascore'
NAMESPACE = ''
UID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, 'urn:real-jaen-analytics:player-identity-decision:v1')


class IdentityDecisionRejected(RuntimeError):
    """La resolución solicitada no es inequívoca o no puede publicarse de forma segura."""


def _players(connection):
    return {int(row['id']): dict(row) for row in connection.execute(text(
        'SELECT id, status, merged_into_id FROM player ORDER BY id')).mappings()}


def _root(player_id, players):
    seen = set()
    current = player_id
    while True:
        if current in seen:
            raise IdentityDecisionRejected(f'Ciclo de identidad de jugador detectado en {current}')
        seen.add(current)
        row = players.get(current)
        if row is None:
            raise IdentityDecisionRejected(f'La identidad de jugador {current} no existe')
        target = row['merged_into_id']
        if row['status'] == 'merged':
            if target is None:
                raise IdentityDecisionRejected(f'La identidad fusionada {current} no tiene destino')
            current = int(target)
            continue
        if target is not None:
            raise IdentityDecisionRejected(f'La identidad activa {current} tiene un destino de fusión')
        return current


def _projection(connection, root_overrides=None):
    """(external IDs -> public canonical IDs, internal IDs -> root, ext rows, players)."""
    players = _players(connection)
    roots = {player_id: _root(player_id, players) for player_id in players}
    root_overrides = root_overrides or {}
    ext_rows = [dict(row) for row in connection.execute(text("""
        SELECT id, provider, namespace, external_id, status, entity_id, decision_id
        FROM player_external_id ORDER BY id
    """)).mappings()]
    external_ids = {}
    origin_by_root = {}
    for row in ext_rows:
        if row['provider'] == PROVEEDOR and row['namespace'] == NAMESPACE and row['status'] == 'origin':
            source_root = roots[int(row['entity_id'])]
            if source_root in root_overrides:
                continue
            root = root_overrides.get(source_root, source_root)
            origin_by_root.setdefault(root, []).append(str(row['external_id']))
    for root, values in origin_by_root.items():
        if len(values) != 1:
            raise IdentityDecisionRejected(f'La identidad canónica interna {root} no tiene un ID SofaScore único')
    for row in ext_rows:
        if row['provider'] != PROVEEDOR or row['namespace'] != NAMESPACE or row['status'] == 'rejected':
            continue
        root = root_overrides.get(roots[int(row['entity_id'])], roots[int(row['entity_id'])])
        canonical = origin_by_root.get(root)
        if canonical is not None:
            external_ids[str(row['external_id'])] = canonical[0]
    return external_ids, roots, ext_rows, players


def aliases_publicos(connection):
    """Alias SofaScore a ID público canónico; no devuelve entradas idénticas."""
    external, _roots, _rows, _players_by_id = _projection(connection)
    aliases = {}
    for source, canonical in external.items():
        try:
            source_id, canonical_id = int(source), int(canonical)
        except ValueError:
            continue
        if source_id != canonical_id:
            aliases[source_id] = canonical_id
    return aliases


def _concurrent_alias_conflicts(connection, external_projection):
    grouped = {}
    for row in connection.execute(text("""
        SELECT match_id, side, external_player_id
        FROM call_up_vigente ORDER BY match_id, side, external_player_id
    """)).mappings():
        source = str(row['external_player_id'])
        canonical = external_projection.get(source, source)
        grouped.setdefault((int(row['match_id']), canonical), []).append((source, row['side']))
    conflicts = []
    for (match_id, canonical), entries in grouped.items():
        source_ids = sorted({source for source, _side in entries})
        if len(source_ids) > 1:
            conflicts.append({'match_id': match_id, 'canonical_player_id': canonical,
                              'external_player_ids': source_ids,
                              'sides': sorted({side for _source, side in entries})})
    return conflicts


def conflictos_identidad(connection):
    """Diagnósticos de IDs alias simultáneos en convocatorias vigentes."""
    projection, _roots, _ext, _players_by_id = _projection(connection)
    return _concurrent_alias_conflicts(connection, projection)


def _require_text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise IdentityDecisionRejected(f'{field} es obligatorio')
    return value.strip()


def _id(value, field):
    if type(value) is not int or value <= 0:
        raise IdentityDecisionRejected(f'{field} debe ser un ID SofaScore positivo')
    return str(value)


def _external_index(rows):
    return {str(row['external_id']): row for row in rows
            if row['provider'] == PROVEEDOR and row['namespace'] == NAMESPACE}


def previsualizar(connection, canonical_id, alias_ids):
    """Valida en solo lectura una fusión propuesta y devuelve su alcance y conflictos."""
    canonical_id = _id(canonical_id, 'canonical_id')
    alias_ids = [_id(value, 'alias_id') for value in alias_ids]
    if not alias_ids or canonical_id in alias_ids or len(set(alias_ids)) != len(alias_ids):
        raise IdentityDecisionRejected('Indica alias distintos y no repitas el ID canónico')

    _external, roots, rows, players = _projection(connection)
    by_external = _external_index(rows)
    canonical_row = by_external.get(canonical_id)
    if canonical_row is None or canonical_row['status'] != 'origin':
        raise IdentityDecisionRejected('El ID canónico debe ser un ID SofaScore origin existente')
    canonical_root = roots[int(canonical_row['entity_id'])]
    if players[canonical_root]['status'] != 'active':
        raise IdentityDecisionRejected('La identidad canónica no está activa')

    source_roots = []
    for alias_id in alias_ids:
        row = by_external.get(alias_id)
        if row is None or row['status'] == 'rejected':
            raise IdentityDecisionRejected(f'El ID alias {alias_id} no tiene una asociación SofaScore vigente')
        root = roots[int(row['entity_id'])]
        if root == canonical_root:
            raise IdentityDecisionRejected(f'El ID alias {alias_id} ya pertenece a la identidad canónica')
        if players[root]['status'] != 'active':
            raise IdentityDecisionRejected(f'La identidad del alias {alias_id} no está activa')
        if root in source_roots:
            raise IdentityDecisionRejected('Dos IDs alias pertenecen ya a la misma identidad de origen')
        source_roots.append(root)

    simulated_roots = {source_root: canonical_root for source_root in source_roots}
    projection, _simulated, _rows, _players_by_id = _projection(connection, simulated_roots)
    conflicts = _concurrent_alias_conflicts(connection, projection)
    if conflicts:
        first = conflicts[0]
        raise IdentityDecisionRejected(
            'La resolución produciría dos convocatorias del mismo jugador en el mismo partido '
            f'(partido {first["match_id"]}, IDs {", ".join(first["external_player_ids"])}); '
            'resuelve el conflicto de origen antes de fusionar'
        )

    source_entities = {player_id for player_id, root in roots.items() if root in source_roots}
    affected_external = [row for row in rows
                         if int(row['entity_id']) in source_entities and row['status'] != 'rejected']
    return {
        'canonical': {'sofascore_id': int(canonical_id), 'player_id': canonical_root},
        'aliases': [{'sofascore_id': int(alias), 'player_id': roots[int(by_external[alias]['entity_id'])],
                     'source_root': roots[int(by_external[alias]['entity_id'])]} for alias in alias_ids],
        'affected_player_entities': sorted(source_entities),
        'affected_external_ids': [{'id': row['id'], 'provider': row['provider'], 'external_id': row['external_id'],
                                   'status': row['status']} for row in affected_external],
        'conflicts': [],
        '_canonical_root': canonical_root,
        '_source_roots': source_roots,
        '_source_entities': sorted(source_entities),
        '_external_rows': affected_external,
    }


def _rows_by_ids(connection, table, ids, columns):
    if not ids:
        return []
    keys = {f'id_{index}': value for index, value in enumerate(sorted(set(ids)))}
    names = ', '.join(':' + name for name in keys)
    return [dict(row) for row in connection.execute(text(
        f'SELECT {columns} FROM {table} WHERE id IN ({names}) ORDER BY id'), keys).mappings()]


def _dependent_rows(connection, internal_ids):
    output = {}
    for table, key in (('call_up', ('id',)), ('player_attribute_observation', ('observation_id', 'entry_index', 'field')),
                       ('discrepancy', ('id',))):
        params = {f'p_{i}': value for i, value in enumerate(internal_ids)}
        where = ', '.join(':' + name for name in params)
        order = ', '.join(key)
        rows = connection.execute(text(
            f'SELECT {order}, player_id FROM {table} WHERE player_id IS NOT NULL AND player_id IN ({where}) '
            f'ORDER BY {order}'), params).mappings().all() if internal_ids else []
        output[table] = [dict(row) for row in rows]
    return output


def _dependent_keys(table):
    return {'call_up': ('id',), 'player_attribute_observation': ('observation_id', 'entry_index', 'field'),
            'discrepancy': ('id',)}[table]


def _update_dependent_row(connection, table, row):
    keys = _dependent_keys(table)
    params = {'player_id': row['player_id']}
    where = []
    for index, key in enumerate(keys):
        name = f'key_{index}'
        params[name] = row[key]
        where.append(f'"{key}"=:{name}')
    result = connection.execute(text(f'UPDATE {table} SET player_id=:player_id WHERE {" AND ".join(where)}'), params)
    if result.rowcount != 1:
        raise IdentityDecisionRejected(f'La referencia de {table} cambió durante la operación')


def _dependent_current(connection, table, rows):
    keys = _dependent_keys(table)
    output = []
    for row in rows:
        params, where = {}, []
        for index, key in enumerate(keys):
            name = f'key_{index}'
            params[name] = row[key]
            where.append(f'"{key}"=:{name}')
        current = connection.execute(text(f'SELECT {", ".join(keys)}, player_id FROM {table} '
                                          f'WHERE {" AND ".join(where)}'), params).mappings().one_or_none()
        if current is not None:
            output.append(dict(current))
    return output


def _snapshot_after_entities(before, canonical_root):
    return [{'id': row['id'], 'status': 'merged', 'merged_into_id': canonical_root} for row in before]


def _snapshot_after_external(before, canonical_root, decision_id):
    output = []
    for row in before:
        output.append({'id': row['id'], 'entity_id': canonical_root,
                       'status': 'verified' if row['status'] == 'origin' else row['status'],
                       'decision_id': decision_id if row['status'] == 'origin' else row['decision_id']})
    return output


def _subject(connection, decision_id, ordinal, role, *, player_id=None, external_id_id=None):
    fields = {'decision_id': decision_id, 'ordinal': ordinal, 'role': role}
    fields['player_id'] = player_id
    fields['player_external_id_id'] = external_id_id
    columns = ', '.join(f'"{key}"' for key in fields)
    values = ', '.join(':' + key for key in fields)
    connection.execute(text(f'INSERT INTO identity_decision_subject ({columns}) VALUES ({values})'), fields)


def _request_result(connection, uid):
    prior = _find_decision_uid(connection, uid)
    if prior is None:
        return None
    return {'id': int(prior['id']), 'kind': prior['kind'], 'idempotent': True,
            'reverts_decision_id': prior['reverts_decision_id']}


def _decision_uid(kind, payload):
    encoded = json.dumps({'kind': kind, **payload}, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return str(uuid.uuid5(UID_NAMESPACE, encoded))


def _find_decision_uid(connection, uid):
    return connection.execute(text('SELECT id, kind, reverts_decision_id FROM identity_decision WHERE uid=:uid'),
                              {'uid': uid}).mappings().first()


def _check_admin_environment(connection, scope_path):
    from sqlalchemy.exc import SQLAlchemyError

    try:
        db, role, version = connection.execute(text(
            "SELECT current_database(), current_user, current_setting('server_version_num')::integer")).one()
        if db != 'sports_data' or role != 'sports_data_owner' or not 180000 <= int(version) < 190000:
            raise IdentityDecisionRejected('Las decisiones requieren sports_data, PostgreSQL 18 y sports_data_owner')
        required = connection.execute(text("""
            SELECT to_regclass('public.identity_decision') IS NOT NULL
               AND to_regclass('public.identity_decision_subject') IS NOT NULL
               AND to_regclass('public.player_external_id') IS NOT NULL
               AND to_regclass('public.sports_data_publication_state') IS NOT NULL
               AND to_regclass('public.sports_data_update_state') IS NOT NULL
               AND to_regclass('public.sports_data_update_job') IS NOT NULL
        """)).scalar_one()
        if not required:
            raise IdentityDecisionRejected('Falta el esquema PostgreSQL de identidades, publicación o trabajos')
        if scope_path is not None:
            from pathlib import Path
            if not Path(scope_path).is_file():
                raise IdentityDecisionRejected('No existe el archivo de ámbito indicado')
    except SQLAlchemyError:
        raise IdentityDecisionRejected('El esquema PostgreSQL no corresponde a Sports Data pg0005') from None


def _lock_publication(connection):
    """Serialize with acquisition publication and job creation using the established lock order."""
    sincronizacion.bloquear_escritura_postgresql(connection)
    connection.execute(text('SELECT fencing_token FROM sports_data_update_state WHERE singleton_id=1 FOR UPDATE'))
    active = connection.execute(text("""
        SELECT job_id, phase FROM sports_data_update_job
        WHERE status IN ('queued','running') ORDER BY requested_at LIMIT 1
    """)).mappings().first()
    if active is not None:
        raise IdentityDecisionRejected(
            f'Hay un trabajo de Acquisition activo ({active["phase"]}); vuelve a intentarlo cuando termine'
        )


def _publish(connection, scope_path, result, reference):
    """Build the next normal Sports Data snapshot and record a durable generation without Acquisition."""
    from pathlib import Path
    from persistencia.actualizacion import preparar_postgresql

    scope_hash = hashlib.sha256(Path(scope_path).read_bytes()).hexdigest()
    state = connection.execute(text("""
        SELECT generation_id, generation_number, scope_sha256
        FROM sports_data_publication_state WHERE singleton_id=1 FOR UPDATE
    """)).mappings().one_or_none()
    if state is None:
        raise IdentityDecisionRejected('Falta el estado durable de publicación pg0002')
    if state['scope_sha256'] is not None and state['scope_sha256'] != scope_hash:
        raise IdentityDecisionRejected('Cambió el ámbito guardado; requiere mantenimiento explícito')
    generation_id = uuid.uuid4()
    generation_number = int(state['generation_number']) + 1
    publication_id = uuid.uuid4()
    published_at = datetime.now(timezone.utc)
    result = dict(result, generation_id=str(generation_id), generation_number=generation_number,
                  source_generation=str(state['generation_id']), published_at=published_at.isoformat())
    sincronizacion.exigir_coherencia(connection)
    # Same preparation path used by ordinary publications: read checks and response contracts are validated before
    # the durable pointer can change. The generated object is intentionally not installed in this CLI process.
    preparar_postgresql(connection, scope_path, str(generation_id), generation_number)
    connection.execute(text("""
        INSERT INTO sports_data_publication
            (publication_id, source_generation, result_generation, input_sha256, published_at, run_status, result_json)
        VALUES (:publication_id, :source_generation, :generation_id, :input_sha256, :published_at,
                'committed', CAST(:result_json AS jsonb))
    """), {'publication_id': publication_id, 'source_generation': state['generation_id'],
           'generation_id': generation_id, 'input_sha256': hashlib.sha256(
               json.dumps(result, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest(),
           'published_at': published_at, 'result_json': json.dumps(result, ensure_ascii=False, allow_nan=False)})
    connection.execute(text("""
        UPDATE sports_data_publication_state
        SET generation_id=:generation_id, generation_number=:generation_number,
            code_sha256=:code_sha256, scope_sha256=:scope_sha256,
            last_publication_id=:publication_id, updated_at=:published_at
        WHERE singleton_id=1
    """), {'generation_id': generation_id, 'generation_number': generation_number,
           'code_sha256': reference['sha256_agregada'], 'scope_sha256': scope_hash,
           'publication_id': publication_id, 'published_at': published_at})
    return result


def aplicar(connection, canonical_id, alias_ids, *, actor, reason, evidence, scope_path, command=''):
    actor = _require_text(actor, 'decided_by')
    reason = _require_text(reason, 'reason')
    evidence = _require_text(evidence, 'evidence')
    request = {'canonical_id': canonical_id, 'alias_ids': sorted(set(alias_ids)), 'actor': actor,
               'reason': reason, 'evidence': evidence}
    uid = _decision_uid('merge', request)
    with connection.begin():
        _check_admin_environment(connection, scope_path)
        prior = _request_result(connection, uid)
        if prior is not None:
            return prior
        _lock_publication(connection)
        prior = _request_result(connection, uid)
        if prior is not None:
            return prior
        sincronizacion.exigir_coherencia(connection)
        preview = previsualizar(connection, canonical_id, alias_ids)
        target = preview['_canonical_root']
        sources = preview['_source_roots']
        source_entities = preview['_source_entities']
        ext_ids = [row['id'] for row in preview['_external_rows']]
        entities_before = _rows_by_ids(connection, 'player', sources, 'id, status, merged_into_id')
        external_before = _rows_by_ids(connection, 'player_external_id', ext_ids,
                                       'id, entity_id, status, decision_id, provider, namespace, external_id')
        dependents_before = _dependent_rows(connection, source_entities)
        alias_external_ids = sorted({str(row['external_id']) for row in external_before
                                     if row['provider'] == PROVEEDOR and row['namespace'] == NAMESPACE})
        alias_callups = []
        alias_attributes = []
        if alias_external_ids:
            query = text('SELECT id FROM call_up WHERE external_player_id IN :aliases ORDER BY id').bindparams(
                bindparam('aliases', expanding=True))
            alias_callups = [int(row[0]) for row in connection.execute(query, {'aliases': alias_external_ids})]
            query = text('SELECT observation_id, entry_index, field FROM player_attribute_observation '
                         'WHERE external_player_id IN :aliases ORDER BY observation_id, entry_index, field').bindparams(
                             bindparam('aliases', expanding=True))
            alias_attributes = [dict(row) for row in connection.execute(query, {'aliases': alias_external_ids}).mappings()]
        timestamp = datetime.now(timezone.utc).date()
        evidence_data = {
            'format': 'rja-player-identity-decision-v1', 'supplied_evidence': evidence,
            'canonical_sofascore_id': canonical_id, 'alias_sofascore_ids': sorted(set(alias_ids)),
            'snapshot': {
                'entities_before': entities_before,
                'entities_after': _snapshot_after_entities(entities_before, target),
                'external_ids_before': external_before,
                'external_ids_after': _snapshot_after_external(external_before, target, None),
                'dependent_references_before': dependents_before,
                'dependent_references_after': {
                    table: [{**{key: row[key] for key in _dependent_keys(table)}, 'player_id': target}
                            for row in rows]
                    for table, rows in dependents_before.items()},
                'alias_callup_ids_before': alias_callups,
                'alias_attribute_keys_before': alias_attributes,
            },
        }
        reference = carga.referencia_del_codigo()
        run = sincronizacion.nueva_ejecucion(connection, 'identity_decisions', reference,
                                               command or 'persistencia identity apply')
        result = connection.execute(text("""
            INSERT INTO identity_decision
                (uid, kind, entity_type, reason, decided_by, decided_on, evidence, run_id)
            VALUES (:uid, 'merge', 'player', :reason, :actor, :decided_on, :evidence, :run_id)
            RETURNING id
        """), {'uid': uid, 'reason': reason, 'actor': actor, 'decided_on': timestamp,
               'evidence': json.dumps(evidence_data, ensure_ascii=False, separators=(',', ':'), allow_nan=False),
               'run_id': run}).scalar_one()
        ordinal = 0
        _subject(connection, result, ordinal, 'target', player_id=target)
        ordinal += 1
        for source in sorted(source_entities):
            _subject(connection, result, ordinal, 'source', player_id=source)
            ordinal += 1
        for row in external_before:
            _subject(connection, result, ordinal, 'external_id', external_id_id=row['id'])
            ordinal += 1

        origin_ids = [row['id'] for row in external_before if row['status'] == 'origin']
        if origin_ids:
            keys = {f'e_{i}': value for i, value in enumerate(origin_ids)}
            connection.execute(text('UPDATE player_external_id SET status=\'verified\', decision_id=:decision '
                                    f'WHERE id IN ({", ".join(":" + key for key in keys)})'),
                               {'decision': result, **keys})
        all_external_keys = {f'e_{i}': value for i, value in enumerate(ext_ids)}
        if all_external_keys:
            connection.execute(text('UPDATE player_external_id SET entity_id=:target '
                                    f'WHERE id IN ({", ".join(":" + key for key in all_external_keys)})'),
                               {'target': target, **all_external_keys})
        for source in sources:
            connection.execute(text("UPDATE player SET status='merged', merged_into_id=:target WHERE id=:source"),
                               {'target': target, 'source': source})
        for table, rows in dependents_before.items():
            for row in rows:
                _update_dependent_row(connection, table, dict(row, player_id=target))

        # The after snapshot records the actual decision ID for original external IDs.
        evidence_data['snapshot']['external_ids_after'] = _snapshot_after_external(external_before, target, result)
        connection.execute(text('UPDATE identity_decision SET evidence=:evidence WHERE id=:id'),
                           {'evidence': json.dumps(evidence_data, ensure_ascii=False, separators=(',', ':'),
                                                   allow_nan=False), 'id': result})
        summary = {'decision_id': result, 'canonical_sofascore_id': canonical_id,
                   'alias_sofascore_ids': sorted(set(alias_ids)), 'changed': True}
        sincronizacion.cerrar_ejecucion(connection, run, 'committed', summary)
        publication = _publish(connection, scope_path, summary, reference)
        return {'id': result, 'kind': 'merge', 'canonical_sofascore_id': canonical_id,
                'alias_sofascore_ids': sorted(set(alias_ids)), 'run_id': run, 'publication': publication}


def _decision_snapshot(connection, decision_id):
    row = connection.execute(text('SELECT id, uid, kind, entity_type, reason, decided_by, decided_on, evidence '
                                  'FROM identity_decision WHERE id=:id'), {'id': decision_id}).mappings().one_or_none()
    if row is None:
        raise IdentityDecisionRejected(f'No existe la decisión {decision_id}')
    if row['kind'] != 'merge' or row['entity_type'] != 'player':
        raise IdentityDecisionRejected('Solo se pueden revertir fusiones explícitas de jugadores')
    try:
        payload = json.loads(row['evidence'])
        snapshot = payload['snapshot']
    except (TypeError, ValueError, KeyError):
        raise IdentityDecisionRejected('La decisión no conserva una instantánea reversible') from None
    return dict(row), payload, snapshot


def _reverted_decision_ids(connection):
    return {int(row[0]) for row in connection.execute(
        text("SELECT reverts_decision_id FROM identity_decision WHERE kind='revert'"))}


def _later_decision_conflicts(connection, decision_id):
    original_subjects = connection.execute(text("""
        SELECT player_id, player_external_id_id FROM identity_decision_subject
        WHERE decision_id=:decision_id
    """), {'decision_id': decision_id}).mappings().all()
    player_ids = {int(row['player_id']) for row in original_subjects if row['player_id'] is not None}
    ext_ids = {int(row['player_external_id_id']) for row in original_subjects
               if row['player_external_id_id'] is not None}
    reverted = _reverted_decision_ids(connection)
    later = connection.execute(text("""
        SELECT DISTINCT d.id, s.player_id, s.player_external_id_id
        FROM identity_decision d JOIN identity_decision_subject s ON s.decision_id=d.id
        WHERE d.id > :decision_id AND d.kind='merge'
    """), {'decision_id': decision_id}).mappings().all()
    for row in later:
        if int(row['id']) in reverted:
            continue
        if row['player_id'] is not None and int(row['player_id']) in player_ids:
            return int(row['id'])
        if row['player_external_id_id'] is not None and int(row['player_external_id_id']) in ext_ids:
            return int(row['id'])
    return None


def _validate_current_snapshot(connection, snapshot):
    for expected in snapshot['entities_after']:
        actual = _rows_by_ids(connection, 'player', [expected['id']], 'id, status, merged_into_id')
        if not actual or actual[0] != expected:
            raise IdentityDecisionRejected('La identidad cambió después de la decisión; la reversión no sería exacta')
    for expected in snapshot['external_ids_after']:
        actual = _rows_by_ids(connection, 'player_external_id', [expected['id']],
                              'id, entity_id, status, decision_id')
        if not actual or actual[0] != expected:
            raise IdentityDecisionRejected('Un ID externo cambió después de la decisión; la reversión no sería exacta')
    for table, expected_rows in snapshot['dependent_references_after'].items():
        actual_rows = _dependent_current(connection, table, expected_rows)
        if actual_rows != expected_rows:
            raise IdentityDecisionRejected(f'Las referencias de {table} cambiaron; la reversión no sería exacta')


def _reject_new_alias_references(connection, snapshot):
    aliases = sorted({str(row['external_id']) for row in snapshot['external_ids_before']
                      if row['provider'] == PROVEEDOR and row['namespace'] == NAMESPACE})
    if not aliases:
        return
    callups_before = {int(value) for value in snapshot.get('alias_callup_ids_before', [])}
    query = text('SELECT id FROM call_up WHERE external_player_id IN :aliases').bindparams(
        bindparam('aliases', expanding=True))
    for (row_id,) in connection.execute(query, {'aliases': aliases}):
        if int(row_id) not in callups_before:
            raise IdentityDecisionRejected('Hay nuevas convocatorias de un alias desde la decisión; '
                                            'la reversión no sería exacta')
    attributes_before = {tuple(row[key] for key in _dependent_keys('player_attribute_observation'))
                         for row in snapshot.get('alias_attribute_keys_before', [])}
    query = text('SELECT observation_id, entry_index, field FROM player_attribute_observation '
                 'WHERE external_player_id IN :aliases').bindparams(bindparam('aliases', expanding=True))
    for row in connection.execute(query, {'aliases': aliases}).mappings():
        key = tuple(row[field] for field in _dependent_keys('player_attribute_observation'))
        if key not in attributes_before:
            raise IdentityDecisionRejected('Hay nuevas observaciones personales de un alias desde la decisión; '
                                            'la reversión no sería exacta')


def revertir(connection, decision_id, *, actor, reason, evidence, scope_path, command=''):
    actor = _require_text(actor, 'decided_by')
    reason = _require_text(reason, 'reason')
    evidence = _require_text(evidence, 'evidence')
    request = {'decision_id': decision_id, 'actor': actor, 'reason': reason, 'evidence': evidence}
    uid = _decision_uid('revert', request)
    with connection.begin():
        _check_admin_environment(connection, scope_path)
        prior = _request_result(connection, uid)
        if prior is not None:
            return prior
        _lock_publication(connection)
        prior = _request_result(connection, uid)
        if prior is not None:
            return prior
        original, original_evidence, snapshot = _decision_snapshot(connection, decision_id)
        if connection.execute(text("SELECT 1 FROM identity_decision WHERE kind='revert' AND reverts_decision_id=:id"),
                              {'id': decision_id}).first() is not None:
            raise IdentityDecisionRejected('La decisión ya fue revertida')
        later = _later_decision_conflicts(connection, decision_id)
        if later is not None:
            raise IdentityDecisionRejected(f'La decisión posterior {later} aún depende de esta identidad')
        _validate_current_snapshot(connection, snapshot)
        _reject_new_alias_references(connection, snapshot)
        sincronizacion.exigir_coherencia(connection)
        reference = carga.referencia_del_codigo()
        run = sincronizacion.nueva_ejecucion(connection, 'identity_decisions', reference,
                                               command or 'persistencia identity revert')
        revert_evidence = {
            'format': 'rja-player-identity-revert-v1', 'supplied_evidence': evidence,
            'reverts_decision_id': decision_id, 'original_uid': original['uid'],
            'original_evidence': original_evidence,
            'restore_snapshot': snapshot['entities_before'],
        }
        revert_id = connection.execute(text("""
            INSERT INTO identity_decision
                (uid, kind, entity_type, reason, decided_by, decided_on, evidence, run_id, reverts_decision_id)
            VALUES (:uid, 'revert', 'player', :reason, :actor, :decided_on, :evidence, :run_id, :original_id)
            RETURNING id
        """), {'uid': uid, 'reason': reason, 'actor': actor,
               'decided_on': datetime.now(timezone.utc).date(),
               'evidence': json.dumps(revert_evidence, ensure_ascii=False, separators=(',', ':'), allow_nan=False),
               'run_id': run, 'original_id': decision_id}).scalar_one()
        subjects = connection.execute(text("""
            SELECT role, player_id, player_external_id_id FROM identity_decision_subject
            WHERE decision_id=:decision_id ORDER BY ordinal
        """), {'decision_id': decision_id}).mappings().all()
        for ordinal, row in enumerate(subjects):
            _subject(connection, revert_id, ordinal, row['role'], player_id=row['player_id'],
                     external_id_id=row['player_external_id_id'])

        # Restore the source entities first so the original origin IDs can be reattached safely.
        for row in snapshot['entities_before']:
            connection.execute(text('UPDATE player SET status=:status, merged_into_id=:merged_into_id WHERE id=:id'), row)
        for row in snapshot['external_ids_before']:
            connection.execute(text('UPDATE player_external_id SET entity_id=:entity_id, status=:status, '
                                    'decision_id=:decision_id WHERE id=:id'),
                               {key: row[key] for key in ('entity_id', 'status', 'decision_id', 'id')})
        for table, rows in snapshot['dependent_references_before'].items():
            for row in rows:
                _update_dependent_row(connection, table, row)
        sincronizacion.exigir_coherencia(connection)
        summary = {'decision_id': revert_id, 'reverts_decision_id': decision_id, 'changed': True}
        sincronizacion.cerrar_ejecucion(connection, run, 'committed', summary)
        publication = _publish(connection, scope_path, summary, reference)
        return {'id': revert_id, 'kind': 'revert', 'reverts_decision_id': decision_id,
                'run_id': run, 'publication': publication}


def listar(connection):
    _check_admin_environment(connection, None)
    rows = connection.execute(text("""
        SELECT id, uid, kind, entity_type, reason, decided_by, decided_on, evidence, reverts_decision_id
        FROM identity_decision ORDER BY id
    """)).mappings().all()
    reverted = _reverted_decision_ids(connection)
    output = []
    for row in rows:
        try:
            evidence = json.loads(row['evidence'])
        except (TypeError, ValueError):
            evidence = {}
        output.append({
            'id': int(row['id']), 'uid': row['uid'], 'kind': row['kind'], 'entity_type': row['entity_type'],
            'reason': row['reason'], 'decided_by': row['decided_by'], 'decided_on': row['decided_on'].isoformat(),
            'status': ('reverted' if int(row['id']) in reverted else 'active') if row['kind'] == 'merge' else 'audit',
            'reverts_decision_id': row['reverts_decision_id'],
            'evidence': evidence.get('supplied_evidence'),
            'canonical_sofascore_id': evidence.get('canonical_sofascore_id'),
            'alias_sofascore_ids': evidence.get('alias_sofascore_ids'),
        })
    return output
