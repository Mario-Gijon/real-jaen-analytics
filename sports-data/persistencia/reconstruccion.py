"""Reconstrucción, desde la base, de los JSON que escribe el importador (fase 2). Sirve para validar una carga contra
su carpeta de origen y para validar decisiones locales con las reglas de api.py. Lee lo guardado (evidencia y estado
operativo) sin recalcular la selección; solo la ficha combinada se calcula, como hace la API, con ficha_combinada.
No es todavía la lectura de la API (fase 3)."""
import json

import importar

FORMATO = importar.FORMATO
# Orden de claves de un partido que solo está en el calendario: los campos de importar.partido_de_listado y, después,
# los que añade importar.ensamblar. No cambia el contenido; hace que las respuestas coincidan también como texto.
ORDEN_PARTIDO_DE_LISTADO = ('kickoff_utc', 'status', 'tournament_id', 'competition', 'season_id', 'round', 'home_team_id',
                            'away_team_id', 'home_score', 'away_score', 'source', 'match_id', 'season', 'fetched_at',
                            'available_sections', 'exports', 'version_conflict', 'version_issues', 'calendar_source')


def _json(valor):
    """Literal JSON guardado -> valor; NULL de SQL (ausente) -> None, como en los JSON del importador."""
    return None if valor is None else json.loads(valor)


def _entero(texto):
    """Id externo de SofaScore (texto en la base) -> entero, como en los JSON del importador."""
    return None if texto is None else int(texto)


def informe_de_carga(c):
    """El informe de la carga inicial (import_run.summary): origen, fecha de importación y resumen del importador."""
    fila = c.exec_driver_sql("SELECT summary FROM import_run WHERE kind = 'initial_load' AND status = 'committed' "
                             "ORDER BY id LIMIT 1").fetchone()
    if fila is None:
        raise ValueError('La base no tiene una carga inicial confirmada')
    return json.loads(fila[0])


def originales(c):
    """Registros de originales.json, en orden de ingestión, con la marca `selected` de la selección guardada."""
    filas = c.exec_driver_sql("""
        SELECT o.id, o.external_event_id, o.key, o.sub, o.fetched_at_raw, o.fetched_instant, o.fetched_at_valid,
               o.content_hash, o.envelope_extra, o.line, f.label, ct.payload_text,
               EXISTS (SELECT 1 FROM section_selection s WHERE s.observation_id = o.id) AS selected
        FROM observation o JOIN source_file f ON f.id = o.source_file_id JOIN content ct ON ct.hash = o.content_hash
        WHERE o.kind IN ('section', 'listing') ORDER BY o.id""").mappings().all()
    registros = []
    for f in filas:
        r = {'event_id': _entero(f['external_event_id']), 'key': f['key'], 'sub': f['sub'], 'fetched_at': f['fetched_at_raw'],
             'payload': json.loads(f['payload_text'])}
        r.update(_json(f['envelope_extra']) or {})
        r.update(export=f['label'], line=f['line'], content_hash=f['content_hash'], fetched_instant=f['fetched_instant'],
                 fetched_at_valid=bool(f['fetched_at_valid']), selected=bool(f['selected']))
        registros.append(r)
    return registros


def fichas_originales(c):
    filas = c.exec_driver_sql("""
        SELECT o.external_player_id, o.endpoint, o.fetched_at_raw, o.fetched_instant, o.fetched_at_valid, o.content_hash,
               o.envelope_extra, o.line, f.label, ct.payload_text
        FROM observation o JOIN source_file f ON f.id = o.source_file_id JOIN content ct ON ct.hash = o.content_hash
        WHERE o.kind = 'player_profile' ORDER BY o.id""").mappings().all()
    registros = []
    for f in filas:
        r = {'player_id': _entero(f['external_player_id']), 'endpoint': f['endpoint'], 'fetched_at': f['fetched_at_raw'],
             'payload': json.loads(f['payload_text'])}
        r.update(_json(f['envelope_extra']) or {})
        r.update(export=f['label'], line=f['line'], content_hash=f['content_hash'], fetched_instant=f['fetched_instant'],
                 fetched_at_valid=bool(f['fetched_at_valid']))
        registros.append(r)
    return registros


def ejecuciones_de_estado(c):
    """Ejecuciones que fijaron el estado operativo (carga inicial, importaciones, reconstrucciones), de la última a la
    primera, con su informe. Una `running` solo puede ser la de la transacción en curso."""
    filas = c.exec_driver_sql("""SELECT id, kind, finished_at, summary FROM import_run
                                 WHERE kind IN ('initial_load', 'import', 'rebuild') AND status <> 'failed'
                                 ORDER BY id DESC""").fetchall()
    return [(i, tipo, fin, json.loads(informe) if informe else {}) for i, tipo, fin, informe in filas]


def resumen_vigente(c):
    """El resumen del importador de la última ejecución que fijó el estado (resumen.json)."""
    for _i, tipo, _fin, informe in ejecuciones_de_estado(c):
        if 'resumen' in informe:
            return informe['resumen']
    return informe_de_carga(c)['resumen']


def importado_en(c):
    """imported_at del manifiesto: el de la última ejecución que añadió evidencia (la carga inicial conserva el de su
    carpeta). La API no lo publica."""
    for _i, tipo, _fin, informe in ejecuciones_de_estado(c):
        if tipo == 'import' and 'imported_at' in informe:
            return informe['imported_at']
    return informe_de_carga(c)['origen']['imported_at']


def exportaciones(c):
    """exportaciones.json: todos los ficheros de evidencia en orden de ingestión (source_file.id), con su etiqueta y el
    nombre de su primera presentación, y una entrada por cada declaración activa de equipo (en orden de declaración;
    sin declaraciones, una entrada sin equipo). Cada equipo lista así sus ficheros (api.equipos_con_exportacion)."""
    informe = informe_de_carga(c)
    filas = c.exec_driver_sql("""
        SELECT f.id, f.label, f.kind, f.sha256, f.records, f.events, f.players, f.invalid_fetched_at, f.without_profile,
               f.fetched_from, f.fetched_to,
               (SELECT rf.presented_name FROM import_run_file rf JOIN import_run r ON r.id = rf.run_id
                WHERE rf.source_file_id = f.id AND r.status <> 'failed' ORDER BY rf.run_id, rf.position LIMIT 1) AS presented_name
        FROM source_file f WHERE f.kind IN ('raw', 'listing', 'player_profile') ORDER BY f.id""").mappings().all()
    declaraciones = {}
    for archivo, equipo in c.exec_driver_sql("SELECT source_file_id, external_team_id FROM file_team_declaration "
                                             "WHERE status = 'active' ORDER BY id"):
        declaraciones.setdefault(archivo, []).append(int(equipo))
    exports = []
    for f in filas:
        comun = {'id': f['label'], 'file': f['presented_name'], 'sha256': f['sha256'], 'followed_team_id': None,
                 'records': f['records'], 'events': f['events'], 'invalid_fetched_at': f['invalid_fetched_at'],
                 'fetched_from': f['fetched_from'], 'fetched_to': f['fetched_to']}
        if f['kind'] == 'player_profile':
            comun.update(players=f['players'], without_profile=f['without_profile'])
        exports.extend(dict(comun, followed_team_id=equipo, kind=f['kind']) for equipo in declaraciones.get(f['id'], [None]))
    return {'format': informe['origen']['format'], 'imported_at': importado_en(c), 'exports': exports,
            'followed_team_ids': sorted({e['followed_team_id'] for e in exports if e['followed_team_id'] is not None})}


def equipos(c):
    filas = c.exec_driver_sql("""SELECT x.external_id, n.name FROM team_name n
                                 JOIN team_external_id x ON x.entity_id = n.team_id AND x.status = 'origin'""").fetchall()
    return sorted(({'source': 'Sofascore', 'team_id': int(e), 'name': n} for e, n in filas), key=lambda x: x['team_id'])


def partidos(c):
    secciones, exports = {}, {}
    for partido, clave in c.exec_driver_sql("SELECT match_id, key FROM section_selection WHERE key <> 'listing'"):
        secciones.setdefault(partido, set()).add(clave)
    for partido, etiqueta in c.exec_driver_sql("""SELECT DISTINCT o.match_id, f.label FROM observation o
                                                  JOIN source_file f ON f.id = o.source_file_id
                                                  WHERE o.kind IN ('section', 'listing') AND o.match_id IS NOT NULL"""):
        exports.setdefault(partido, set()).add(etiqueta)
    problemas = {}
    for partido, motivo in c.exec_driver_sql("SELECT match_id, reason FROM match_version_issue ORDER BY match_id, ordinal"):
        problemas.setdefault(partido, []).append(motivo)
    filas = c.exec_driver_sql("""
        SELECT s.*, x.external_id AS external_match_id, o.fetched_at_raw FROM match_state s
        JOIN match_external_id x ON x.entity_id = s.match_id AND x.status = 'origin'
        JOIN observation o ON o.id = s.source_observation_id""").mappings().all()
    salida = []
    for s in filas:
        p = {'source': 'Sofascore', 'match_id': int(s['external_match_id']), 'kickoff_utc': s['kickoff_utc'],
             'status': s['status'], 'tournament_id': _entero(s['external_stage_id']), 'competition': s['competition_name'],
             'season_id': _entero(s['external_season_id']), 'season': s['season_label'], 'round': _json(s['round']),
             'home_team_id': int(s['external_home_team_id']), 'away_team_id': int(s['external_away_team_id']),
             'home_score': _json(s['home_score']), 'away_score': _json(s['away_score']), 'fetched_at': s['fetched_at_raw'],
             'available_sections': sorted(secciones.get(s['match_id'], ())) if s['calendar_source'] == 'event' else [],
             'exports': sorted(exports.get(s['match_id'], ())), 'version_conflict': bool(s['version_conflict']),
             'version_issues': problemas.get(s['match_id'], []), 'calendar_source': s['calendar_source']}
        if s['calendar_source'] == 'listing':
            # El importador construye estos partidos con los campos del calendario delante: el mismo orden de claves
            # da las mismas respuestas, también como texto.
            p = {k: p[k] for k in ORDEN_PARTIDO_DE_LISTADO}
        salida.append(p)
    return sorted(salida, key=lambda x: x['match_id'])


def jugador_partido(c):
    """Las convocatorias vigentes. Se leen solo de la vista call_up_vigente: una retirada no cuenta en ningún partido,
    acumulado ni ficha (sus estadísticas siguen en call_up_stat, pero no se leen)."""
    estadisticas = {}
    for convocatoria, clave, valor in c.exec_driver_sql("""SELECT s.call_up_id, s.key, s.value_json FROM call_up_stat s
                                                           JOIN call_up_vigente u ON u.id = s.call_up_id
                                                           ORDER BY s.call_up_id, s.ordinal"""):
        estadisticas.setdefault(convocatoria, {})[clave] = json.loads(valor)
    filas = c.exec_driver_sql("""
        SELECT u.*, m.external_id AS external_match_id, t.external_id AS external_side_team_id, o.fetched_at_raw,
               ct.payload_text
        FROM call_up_vigente u
        JOIN match_external_id m ON m.entity_id = u.match_id AND m.status = 'origin'
        JOIN team_external_id t ON t.entity_id = u.team_id AND t.status = 'origin'
        JOIN observation o ON o.id = u.lineup_observation_id JOIN content ct ON ct.hash = o.content_hash""").mappings().all()
    salida = []
    for u in filas:
        if u['statistics_kind'] == 'object':
            stats = estadisticas.get(u['id'], {})
        elif u['statistics_kind'] == 'other':
            # Un statistics que no es objeto ni null: el original sigue en el payload, en su posición.
            entradas = [e for lado in ('home', 'away') for e in (json.loads(u['payload_text']).get(lado) or {}).get('players') or []]
            stats = entradas[u['entry_index']]['statistics']
        else:
            stats = None
        salida.append({'source': 'Sofascore', 'match_id': int(u['external_match_id']), 'team_id': int(u['external_side_team_id']),
                       'player_id': int(u['external_player_id']), 'side': u['side'],
                       'source_team_id': _entero(u['external_team_id']), 'substitute': _json(u['substitute']),
                       'match_position': _json(u['match_position']), 'shirt_number': _json(u['shirt_number']),
                       'minutes_played': _json(u['minutes_played']),
                       'played': None if u['played'] is None else bool(u['played']), 'statistics': stats,
                       'fetched_at': u['fetched_at_raw']})
    return sorted(salida, key=lambda x: (x['match_id'], x['team_id'], x['player_id']))


def jugadores(c):
    """La ficha combinada de cada jugador observado en alineaciones, con las mismas observaciones y en el mismo orden
    que el importador (alineaciones en orden de ingestión y, después, fichas de jugadores ya observados)."""
    fichas = {}
    filas = c.exec_driver_sql("""
        SELECT a.observation_id, a.entry_index, a.field, a.value_json, a.external_player_id, o.kind, o.fetched_at_raw
        FROM player_attribute_observation a JOIN observation o ON o.id = a.observation_id
        ORDER BY CASE o.kind WHEN 'player_profile' THEN 1 ELSE 0 END, a.observation_id, a.entry_index""").mappings().all()
    for f in filas:
        clave = (f['observation_id'], f['entry_index'])
        ficha = fichas.setdefault(clave, {'kind': f['kind'], 'fetched_at': f['fetched_at_raw'],
                                          'player_id': int(f['external_player_id']), 'campos': {}})
        ficha['campos'][f['field']] = json.loads(f['value_json'])
    observaciones = {}
    for ficha in fichas.values():
        jugador = ficha['player_id']
        if ficha['kind'] == 'player_profile' and jugador not in observaciones:
            continue  # una ficha solo enriquece a un jugador observado en alineaciones
        completa = {'source': 'Sofascore', 'player_id': jugador,
                    **{campo: ficha['campos'].get(campo) for campo in importar.CAMPOS_FICHA},
                    'fetched_at': ficha['fetched_at']}
        observaciones.setdefault(jugador, []).append((ficha['fetched_at'], completa))
    return sorted((importar.ficha_combinada(obs)[0] for obs in observaciones.values()), key=lambda x: x['player_id'])


def discrepancias(c):
    versiones = {}
    for d, etiqueta, linea, huella, fetched_at, instante, valida, clave, kind in c.exec_driver_sql("""
            SELECT dobs.discrepancy_id, f.label, o.line, o.content_hash, o.fetched_at_raw, o.fetched_instant,
                   o.fetched_at_valid, o.key, o.kind
            FROM discrepancy_observation dobs JOIN observation o ON o.id = dobs.observation_id
            JOIN source_file f ON f.id = o.source_file_id ORDER BY dobs.discrepancy_id, o.id"""):
        versiones.setdefault(d, []).append({'export': etiqueta, 'line': linea, 'content_hash': huella, 'fetched_at': fetched_at,
                                            'fetched_instant': instante, 'fetched_at_valid': bool(valida),
                                            'key': clave, 'kind': kind})
    motivos, valores = {}, {}
    for d, motivo in c.exec_driver_sql('SELECT discrepancy_id, reason FROM discrepancy_reason ORDER BY discrepancy_id, ordinal'):
        motivos.setdefault(d, []).append(motivo)
    for d, campo, valor in c.exec_driver_sql('SELECT discrepancy_id, field, value_json FROM discrepancy_value '
                                             'ORDER BY discrepancy_id, ordinal'):
        valores.setdefault(d, {}).setdefault(campo, []).append(json.loads(valor))
    filas = c.exec_driver_sql("""
        SELECT d.*, m.external_id AS external_match_id, p.external_id AS external_player_id,
               so.line AS sel_line, so.content_hash AS sel_hash, sf.label AS sel_label
        FROM discrepancy d
        LEFT JOIN match_external_id m ON m.entity_id = d.match_id AND m.status = 'origin'
        LEFT JOIN player_external_id p ON p.entity_id = d.player_id AND p.status = 'origin'
        LEFT JOIN observation so ON so.id = d.selected_observation_id LEFT JOIN source_file sf ON sf.id = so.source_file_id
        WHERE d.superseded_in_run IS NULL ORDER BY d.id""").mappings().all()
    salida = []
    for d in filas:
        vs = versiones.get(d['id'], [])
        if d['kind'] == 'player_attribute':
            salida.append({'player_id': int(d['external_player_id']), 'key': d['key'], 'reasons': motivos.get(d['id'], []),
                           'integrity': bool(d['integrity']),
                           'values': {campo: valores[d['id']][campo] for campo in importar.CAMPOS_FICHA
                                      if campo in valores.get(d['id'], {})},
                           'versions': [{'export': v['export'], 'line': v['line'], 'content_hash': v['content_hash'],
                                         'fetched_at': v['fetched_at'],
                                         'key': 'player_profile' if v['kind'] == 'player_profile' else v['key']} for v in vs]})
        else:
            salida.append({'event_id': int(d['external_match_id']), 'key': d['key'], 'sub': d['sub'],
                           'reasons': motivos.get(d['id'], []), 'integrity': bool(d['integrity']),
                           'changed_fields': _json(d['changed_fields']),
                           'selected': {'export': d['sel_label'], 'line': d['sel_line'], 'content_hash': d['sel_hash']},
                           'versions': [{k: v[k] for k in ('export', 'line', 'content_hash', 'fetched_at', 'fetched_instant',
                                                           'fetched_at_valid')} for v in vs]})
    return sorted(salida, key=lambda d: (str(d.get('event_id', '')), d['key'], str(d.get('player_id', ''))))


def salida_desde_base(c, con_fichas=True):
    """Los JSON del importador (resumen aparte) tal como los reconstruye la base."""
    salida = {'equipos': equipos(c), 'jugadores': jugadores(c), 'partidos': partidos(c),
              'jugador_partido': jugador_partido(c), 'originales': originales(c), 'discrepancias': discrepancias(c),
              'exportaciones': exportaciones(c), 'resumen': resumen_vigente(c)}
    if con_fichas:
        salida['fichas_originales'] = fichas_originales(c)
    return salida
