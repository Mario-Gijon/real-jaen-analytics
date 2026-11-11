"""Importador de exportaciones raw JSONL de sofascore_scraper. Python 3.10+, sin dependencias.

Combina una o varias exportaciones en una carpeta nueva con los JSON que sirve api.py. Conserva todas las líneas de
entrada con su procedencia y elige, con una política explícita y única (`seleccionar`), la versión operativa de cada
sección; las discrepancias se guardan, nunca se resuelven en silencio. No accede a Internet ni modifica las entradas.

  python importar.py --exportacion 2841=real-jaen-raw.jsonl --exportacion 24368=ud-ibiza-raw.jsonl --salida datos_nuevos
  python importar.py real-jaen-raw.jsonl                    (una exportación sin equipo seguido declarado)
  python importar.py ... --calendario grupo2-eventos-normalizado.jsonl
                     (calendario: dataset normalizado `events` del scraper, para partidos sin payload raw)
"""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

FORMATO = 2
CAMPOS_FICHA = ('name', 'usual_position', 'country', 'birth_date', 'height_cm', 'preferred_foot')
# Estados de un `event`: final, preliminares (antes o durante el partido) y el resto (aplazado, cancelado,
# interrumpido, abandonado, desconocido...), que nunca se toman por un avance hacia el final.
FINALES = {'finished'}
PRELIMINARES = {'notstarted', 'inprogress'}


def instante(texto):
    """Instante (segundos UTC) de un fetched_at ISO 8601 con desfase; None si falta, no se lee o no lleva desfase."""
    if not isinstance(texto, str):
        return None
    try:
        momento = datetime.fromisoformat(texto.replace('Z', '+00:00'))
    except ValueError:
        return None
    return momento.timestamp() if momento.tzinfo is not None else None


def huella(payload):
    """SHA256 del payload canónico: contenido idéntico, misma huella, sea cual sea la fecha de obtención."""
    canonico = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(canonico.encode('utf-8')).hexdigest()


def fecha(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat() if timestamp is not None else None


def utc(momento):
    return datetime.fromtimestamp(momento, timezone.utc).isoformat() if momento is not None else None


def leer_exportacion(ruta, ident, equipo):
    """Las líneas de una exportación con su procedencia (exportación, línea, huella, instante de obtención)."""
    registros = []
    for numero, linea in enumerate(ruta.read_text(encoding='utf-8-sig').splitlines(), 1):
        if not linea.strip():
            continue
        try:
            r = json.loads(linea)
            if not isinstance(r, dict) or not isinstance(r.get('payload'), dict):
                raise ValueError('payload debe ser un objeto')
            if type(r.get('event_id')) is not int or not isinstance(r.get('key'), str):
                raise ValueError('event_id y key son obligatorios')
        except (ValueError, TypeError) as e:
            raise ValueError(f'{ruta.name}, línea {numero}: {e}') from e
        momento = instante(r.get('fetched_at'))
        registros.append(dict(r, sub=r.get('sub', '') or '', export=ident, line=numero, content_hash=huella(r['payload']),
                              fetched_instant=utc(momento), fetched_at_valid=momento is not None))
    instantes = [instante(r['fetched_at']) for r in registros if r['fetched_at_valid']]
    info = {'id': ident, 'file': ruta.name, 'sha256': hashlib.sha256(ruta.read_bytes()).hexdigest(),
            'followed_team_id': equipo, 'records': len(registros), 'events': len({r['event_id'] for r in registros}),
            'invalid_fetched_at': sum(not r['fetched_at_valid'] for r in registros),
            'fetched_from': utc(min(instantes)) if instantes else None, 'fetched_to': utc(max(instantes)) if instantes else None}
    return registros, info


def leer_calendario(ruta, ident):
    """Un dataset normalizado `events` del scraper (`ssc export --dataset events`, esquema v1): partidos que el
    scraper conoce aunque no tenga su payload (p. ej. sólo por el calendario de la jornada, `quality.source`
    listing). Cada fila se conserva como registro `listing` con su procedencia; no declara equipo seguido."""
    registros = []
    for numero, linea in enumerate(ruta.read_text(encoding='utf-8-sig').splitlines(), 1):
        if not linea.strip():
            continue
        try:
            fila = json.loads(linea)
            participantes = fila.get('participants') if isinstance(fila, dict) else None
            if (not isinstance(fila, dict) or type(fila.get('id')) is not int or not isinstance(fila.get('stage'), dict)
                    or type(fila['stage'].get('id')) is not int or type(fila.get('tournament_id')) is not int
                    or type(fila.get('season_id')) is not int or not isinstance(participantes, dict)
                    or any(type((participantes.get(l) or {}).get('id')) is not int for l in ('home', 'away'))
                    or not isinstance((fila.get('status') or {}).get('type'), str)):
                raise ValueError('se esperaba una fila del dataset events (id, tournament_id, stage, season_id, participants, status)')
        except (ValueError, TypeError) as e:
            raise ValueError(f'{ruta.name}, línea {numero}: {e}') from e
        obtenido = (fila.get('quality') or {}).get('observed_at_utc')
        momento = instante(obtenido)
        registros.append({'event_id': fila['id'], 'key': 'listing', 'sub': '', 'fetched_at': obtenido, 'payload': fila,
                          'export': ident, 'line': numero, 'content_hash': huella(fila), 'fetched_instant': utc(momento),
                          'fetched_at_valid': momento is not None})
    info = {'id': ident, 'kind': 'listing', 'file': ruta.name, 'sha256': hashlib.sha256(ruta.read_bytes()).hexdigest(),
            'followed_team_id': None, 'records': len(registros), 'events': len({r['event_id'] for r in registros}),
            'invalid_fetched_at': sum(not r['fetched_at_valid'] for r in registros), 'fetched_from': None, 'fetched_to': None}
    return registros, info


def partido_de_listado(fila):
    """Un partido del calendario a partir de una fila `listing`: mismo formato que desde un `event` raw, con el grupo
    (stage) como tournament_id, como hace el `event`."""
    inicio = fila.get('start_utc')
    return {
        'kickoff_utc': inicio.replace('Z', '+00:00') if isinstance(inicio, str) else None,
        'status': fila['status']['type'], 'tournament_id': fila['stage']['id'], 'competition': fila['stage'].get('name'),
        'season_id': fila['season_id'], 'round': (fila.get('round') or {}).get('number'),
        'home_team_id': fila['participants']['home']['id'], 'away_team_id': fila['participants']['away']['id'],
        'home_score': (fila.get('score') or {}).get('home'), 'away_score': (fila.get('score') or {}).get('away'),
    }


CAMPOS_LISTADO = ('status', 'tournament_id', 'season_id', 'round', 'home_team_id', 'away_team_id', 'home_score', 'away_score', 'kickoff_utc')


def orden(r):
    """Para elegir la más reciente: una fecha inválida nunca gana a una válida; a igualdad, la última leída."""
    momento = instante(r['fetched_at'])
    return (momento is not None, momento or 0.0)


def mas_reciente(versiones):
    return max(enumerate(versiones), key=lambda iv: (orden(iv[1]), iv[0]))[1]


def evento_de(payload):
    return payload.get('event', payload) if isinstance(payload, dict) else {}


def rasgos_evento(payload):
    """Los campos del `event` que importan: identidad del partido, estado, marcador y hora."""
    e = evento_de(payload)
    torneo = e.get('tournament') or {}
    return {'home_team_id': (e.get('homeTeam') or {}).get('id'), 'away_team_id': (e.get('awayTeam') or {}).get('id'),
            'tournament_id': torneo.get('id'), 'unique_tournament_id': (torneo.get('uniqueTournament') or {}).get('id'),
            'season_id': (e.get('season') or {}).get('id'), 'status': (e.get('status') or {}).get('type'),
            'home_score': (e.get('homeScore') or {}).get('current'), 'away_score': (e.get('awayScore') or {}).get('current'),
            'start': e.get('startTimestamp')}


IDENTIDAD = ('home_team_id', 'away_team_id', 'tournament_id', 'unique_tournament_id', 'season_id')


def seleccionar(versiones):
    """La versión operativa de una sección (event_id, key, sub) y su discrepancia, si la hay.

    - Contenido idéntico (misma huella) con distintas fechas no es una discrepancia.
    - Las fechas se comparan como instantes; una inválida queda señalada y no gana por defecto.
    - event: se prefiere una versión final (finished) a las preliminares; un cambio de identidad (equipos, torneo,
      temporada) o un estado no final obtenido después de una versión final (aplazado, cancelado, no empezado...) es
      una contradicción de integridad: el partido queda marcado y no alimenta agregados. Marcadores finales distintos
      son una corrección: se usa la final más reciente y se informa.
    - lineups: se prefiere una alineación confirmada a una preliminar.
    - Entre versiones comparables, la más reciente es la operativa, sin afirmar que sea la correcta.
    Devuelve (elegida, discrepancia | None)."""
    huellas = list(dict.fromkeys(v['content_hash'] for v in versiones))
    if len(huellas) == 1:
        return mas_reciente(versiones), None
    key = versiones[0]['key']
    integridad, motivos, cambios = False, [], []
    if key == 'event':
        rasgos = {v['content_hash']: rasgos_evento(v['payload']) for v in versiones}
        distintos = lambda campo: len({json.dumps(r[campo]) for r in rasgos.values()}) > 1
        cambios = [campo for campo in rasgos_evento({}) if distintos(campo)]
        finales = [v for v in versiones if rasgos[v['content_hash']]['status'] in FINALES]
        elegida = mas_reciente(finales or versiones)
        if any(distintos(campo) for campo in IDENTIDAD):
            integridad = True
            motivos.append('identity_changed')
        if finales:
            limite = orden(elegida)
            posteriores = [v for v in versiones if rasgos[v['content_hash']]['status'] not in FINALES and orden(v) > limite]
            if posteriores or not limite[0]:
                if posteriores:
                    motivos.append('non_final_after_final')
                else:
                    motivos.append('final_without_valid_date')
                integridad = True
            elif len(finales) < len(versiones):
                motivos.append('preliminary_superseded')
            if len({(rasgos[v['content_hash']]['home_score'], rasgos[v['content_hash']]['away_score']) for v in finales}) > 1:
                motivos.append('final_score_corrected')
        elif 'status' in cambios:
            motivos.append('status_changed')
        if not cambios:
            motivos.append('other_fields_only')
    elif key == 'lineups':
        confirmadas = [v for v in versiones if v['payload'].get('confirmed') is True]
        elegida = mas_reciente(confirmadas or versiones)
        motivos.append('preliminary_superseded' if confirmadas and len(confirmadas) < len(versiones)
                       and len({v['content_hash'] for v in confirmadas}) == 1 else 'comparable_versions_differ')
    else:
        elegida = mas_reciente(versiones)
        motivos.append('comparable_versions_differ')
    if not elegida['fetched_at_valid']:
        motivos.append('selected_without_valid_date')
    discrepancia = {
        'event_id': elegida['event_id'], 'key': key, 'sub': elegida['sub'], 'reasons': motivos, 'integrity': integridad,
        'changed_fields': cambios, 'selected': {'export': elegida['export'], 'line': elegida['line'], 'content_hash': elegida['content_hash']},
        'versions': [{'export': v['export'], 'line': v['line'], 'content_hash': v['content_hash'], 'fetched_at': v.get('fetched_at'),
                      'fetched_instant': v['fetched_instant'], 'fetched_at_valid': v['fetched_at_valid']} for v in versiones],
    }
    return elegida, discrepancia


def ficha_de_entrada(entrada, fetched_at):
    """Datos maestros de un jugador en una entrada de alineación, tal como los da la fuente (ausente = None)."""
    p = entrada.get('player') or {}
    nacimiento = p.get('dateOfBirthTimestamp')
    return {'source': 'Sofascore', 'player_id': p.get('id'), 'name': p.get('name'), 'usual_position': p.get('position'),
            'country': (p.get('country') or {}).get('name'),
            'birth_date': fecha(nacimiento)[:10] if isinstance(nacimiento, (int, float)) and type(nacimiento) is not bool else None,
            'height_cm': p.get('height'), 'preferred_foot': p.get('preferredFoot'), 'fetched_at': fetched_at}


def ficha_combinada(observaciones):
    """Ficha de un jugador a partir de sus observaciones [(fetched_at, ficha)], campo a campo: el valor conocido más
    reciente (por instante; una fecha inválida nunca gana a una válida). Una ausencia de la fuente nunca borra un valor
    conocido. Devuelve (ficha, {campo: valores distintos observados}) para registrar las discrepancias."""
    ordenadas = sorted(enumerate(observaciones), key=lambda io: ((instante(io[1][0]) is not None, instante(io[1][0]) or 0.0), io[0]))
    ficha = dict(ordenadas[-1][1][1])
    distintos = {}
    for campo in CAMPOS_FICHA:
        conocidos = [f[campo] for _, (_, f) in ordenadas if f.get(campo) is not None]
        ficha[campo] = conocidos[-1] if conocidos else None
        unicos = list(dict.fromkeys(json.dumps(v, ensure_ascii=False) for v in conocidos))
        if len(unicos) > 1:
            distintos[campo] = [json.loads(v) for v in unicos]
    return ficha, distintos


def validar_ficha(r):
    """Sobre complementario de una ficha original; identidad y fecha explícitas, sin inferencias."""
    if not isinstance(r, dict) or type(r.get('player_id')) is not int or r['player_id'] <= 0:
        raise ValueError('player_id debe ser un entero positivo')
    if r.get('endpoint') != f'/player/{r["player_id"]}':
        raise ValueError('endpoint no coincide con player_id')
    if instante(r.get('fetched_at')) is None:
        raise ValueError('fetched_at debe llevar zona horaria')
    payload = r.get('payload')
    p = payload.get('player') if isinstance(payload, dict) else None
    if not isinstance(p, dict) or type(p.get('id')) is not int or p['id'] != r['player_id']:
        raise ValueError('payload.player.id no coincide con player_id')
    for campo in ('name', 'position', 'preferredFoot'):
        if p.get(campo) is not None and (not isinstance(p[campo], str) or not p[campo].strip()):
            raise ValueError(f'player.{campo} debe ser texto no vacío o null')
    if p.get('country') is not None:
        c = p['country']
        if not isinstance(c, dict) or (c.get('name') is not None and (not isinstance(c['name'], str) or not c['name'].strip())):
            raise ValueError('player.country debe ser un objeto con name textual o null')
    if p.get('height') is not None and (type(p['height']) is not int or p['height'] <= 0):
        raise ValueError('player.height debe ser un entero positivo o null')
    nacimiento = p.get('dateOfBirthTimestamp')
    if nacimiento is not None:
        if type(nacimiento) not in (int, float):
            raise ValueError('player.dateOfBirthTimestamp debe ser numérico o null')
        try:
            fecha(nacimiento)
        except (ValueError, OverflowError, OSError) as e:
            raise ValueError('player.dateOfBirthTimestamp inválido') from e
    return r


def sobre_de_slice(r):
    """Registro Slice de `ssc export --dataset player_profiles` → sobre de ficha (None si no trae ficha).
    Exige propietario jugador y clave player_profile; un 404 o un error sin ficha previa no aporta datos."""
    if r.get('owner_kind') != 'player':
        raise ValueError('owner_kind debe ser player')
    if r.get('key') != 'player_profile' or r.get('sub') is not None:
        raise ValueError('key debe ser player_profile, sin sub')
    if type(r.get('owner_id')) is not int or r['owner_id'] <= 0:
        raise ValueError('owner_id debe ser un entero positivo')
    if r.get('has_payload') is not True or r.get('payload') is None:
        if r.get('payload') is not None:
            raise ValueError('payload sin has_payload')
        return None
    return {'player_id': r['owner_id'], 'endpoint': f'/player/{r["owner_id"]}',
            'fetched_at': r.get('fetched_at_utc'), 'payload': r['payload']}


def leer_fichas(ruta, ident):
    """JSONL de fichas: sobres complementarios (`player_id`, `endpoint`, `fetched_at`, `payload`) o registros
    Slice del scraper (`owner_kind`, `owner_id`, `key`, ...); pueden mezclarse."""
    registros, sin_ficha = [], 0
    for numero, linea in enumerate(ruta.read_text(encoding='utf-8-sig').splitlines(), 1):
        if not linea.strip():
            continue
        try:
            r = json.loads(linea)
            if isinstance(r, dict) and 'owner_kind' in r:
                r = sobre_de_slice(r)
                if r is None:
                    sin_ficha += 1
                    continue
            r = validar_ficha(r)
        except (ValueError, TypeError) as e:
            raise ValueError(f'{ruta.name}, línea {numero}: {e}') from e
        registros.append(dict(r, export=ident, line=numero, content_hash=huella(r['payload']),
                              fetched_instant=utc(instante(r['fetched_at'])), fetched_at_valid=True))
    instantes = [instante(r['fetched_at']) for r in registros]
    return registros, {'id': ident, 'kind': 'player_profile', 'file': ruta.name,
                       'sha256': hashlib.sha256(ruta.read_bytes()).hexdigest(), 'followed_team_id': None,
                       'records': len(registros), 'events': 0, 'players': len({r['player_id'] for r in registros}),
                       'invalid_fetched_at': 0, 'without_profile': sin_ficha,
                       'fetched_from': utc(min(instantes)) if instantes else None,
                       'fetched_to': utc(max(instantes)) if instantes else None}


def extraer_convocatorias(registro, partido):
    """Las convocatorias de una alineación (registro `lineups` con su procedencia) del partido `partido`: una fila por
    entrada de jugador, local y después visitante, en el orden del payload. El equipo es el del lado en el partido;
    el teamId original se conserva aparte (source_team_id) y, si difiere, se avisa. statistics, dorsal, posición y
    suplente se copian tal cual, con sus tipos, nulos y claves ausentes. Devuelve (filas, avisos).
    No rechaza jugadores repetidos: eso es una regla de la importación (transformar); la API, al leer, une los
    registros duplicados de formatos anteriores con sus propias reglas."""
    mid = registro['event_id']
    filas, avisos = [], []
    for lado, campo in (('home', 'home_team_id'), ('away', 'away_team_id')):
        tid = partido[campo]
        for entrada in (registro['payload'].get(lado) or {}).get('players') or []:
            pid = entrada['player']['id']
            if entrada.get('teamId', tid) != tid:
                avisos.append(f'Partido {mid}, jugador {pid}: teamId de origen {entrada.get("teamId")} difiere del lado de la alineación {tid}; se conserva el equipo del partido y el original.')
            stats = entrada.get('statistics')
            minutos = stats.get('minutesPlayed') if isinstance(stats, dict) else None
            # La ausencia de minutos NO demuestra que un suplente no jugase.
            jugo = minutos > 0 if isinstance(minutos, (int, float)) else None
            filas.append({
                'source': 'Sofascore', 'match_id': mid, 'team_id': tid, 'player_id': pid,
                'side': lado, 'source_team_id': entrada.get('teamId'), 'substitute': entrada.get('substitute'),
                'match_position': entrada.get('position'),
                'shirt_number': entrada.get('shirtNumber'),
                'minutes_played': minutos, 'played': jugo,
                'statistics': stats, 'fetched_at': registro.get('fetched_at'),
            })
    return filas, avisos


def transformar(exportaciones, importado_en=None, calendarios=(), fichas=()):
    """exportaciones: [(ruta, equipo_seguido | None)]; calendarios: [ruta] del dataset normalizado `events`.
    Devuelve los JSON de la carpeta de salida."""
    originales, manifiesto = [], []
    for indice, (ruta, equipo) in enumerate(exportaciones, 1):
        registros, info = leer_exportacion(ruta, f'E{indice}', equipo)
        originales.extend(registros)
        manifiesto.append(dict(info, kind='raw'))
    for indice, ruta in enumerate(calendarios, len(exportaciones) + 1):
        registros, info = leer_calendario(ruta, f'E{indice}')
        originales.extend(registros)
        manifiesto.append(info)
    fichas_originales = []
    for indice, ruta in enumerate(fichas, len(manifiesto) + 1):
        registros, info = leer_fichas(ruta, f'E{indice}')
        fichas_originales.extend(registros)
        manifiesto.append(info)
    return ensamblar(originales, manifiesto, fichas_originales, importado_en, con_fichas=bool(fichas))


def ensamblar(originales, manifiesto, fichas_originales, importado_en=None, con_fichas=False):
    """Las reglas de la importación sobre registros ya leídos (con su procedencia, en orden de lectura): versión
    operativa de cada sección (`selected`, que se escribe en cada registro), discrepancias, partidos, equipos,
    convocatorias y fichas. Es la única implementación: la usan transformar y la carga en la base."""
    grupos = {}
    for r in originales:
        grupos.setdefault((r['event_id'], r['key'], r['sub']), []).append(r)
    seleccion, discrepancias = {}, []
    for clave, versiones in grupos.items():
        elegida, discrepancia = seleccionar(versiones)
        seleccion[clave] = elegida
        if discrepancia:
            discrepancias.append(discrepancia)
    elegidas = {id(r) for r in seleccion.values()}
    for r in originales:
        r['selected'] = id(r) in elegidas
    integridad = {d['event_id']: d['reasons'] for d in discrepancias if d['integrity']}

    equipos, partidos, participaciones, observaciones = {}, {}, {}, {}
    avisos = []
    for r in seleccion.values():
        if r['key'] != 'event':
            continue
        e = evento_de(r['payload'])
        if e.get('id') != r['event_id']:
            raise ValueError(f'Identificador de partido inconsistente: {r["event_id"]}')
        for lado in ('homeTeam', 'awayTeam'):
            if (e.get(lado) or {}).get('id') is not None:
                equipos.setdefault(e[lado]['id'], {'source': 'Sofascore', 'team_id': e[lado]['id'], 'name': e[lado].get('name')})
        partidos[e['id']] = {
            'source': 'Sofascore', 'match_id': e['id'],
            'kickoff_utc': fecha(e.get('startTimestamp')),
            'status': (e.get('status') or {}).get('type'),
            'tournament_id': (e.get('tournament') or {}).get('id'),
            'competition': (e.get('tournament') or {}).get('name'),
            'season_id': (e.get('season') or {}).get('id'),
            'season': (e.get('season') or {}).get('year'),
            'round': (e.get('roundInfo') or {}).get('round'),
            'home_team_id': e['homeTeam']['id'], 'away_team_id': e['awayTeam']['id'],
            'home_score': (e.get('homeScore') or {}).get('current'),
            'away_score': (e.get('awayScore') or {}).get('current'),
            'fetched_at': r.get('fetched_at'),
            'available_sections': sorted({k for (ev, k, _) in seleccion if ev == e['id'] and k != 'listing'}),
            'exports': sorted({v['export'] for v in originales if v['event_id'] == e['id']}),
            'version_conflict': e['id'] in integridad,
            'version_issues': integridad.get(e['id'], []),
            'calendar_source': 'event',
        }
    # Partidos que sólo están en un calendario normalizado: entran sin secciones, marcados como `listing`. Si el
    # partido tiene `event` raw, el raw manda y la diferencia queda como discrepancia informativa.
    temporadas = {m['season_id']: m['season'] for m in partidos.values() if m.get('season')}
    for r in seleccion.values():
        if r['key'] != 'listing':
            continue
        fila = r['payload']
        desde_listado = partido_de_listado(fila)
        if fila['id'] in partidos:
            distintos = [c for c in CAMPOS_LISTADO if partidos[fila['id']][c] != desde_listado[c]]
            if distintos:
                discrepancias.append({'event_id': fila['id'], 'key': 'listing', 'sub': '', 'reasons': ['listing_differs_from_event'],
                                      'integrity': False, 'changed_fields': distintos,
                                      'selected': {'export': r['export'], 'line': r['line'], 'content_hash': r['content_hash']},
                                      'versions': [{'export': r['export'], 'line': r['line'], 'content_hash': r['content_hash'],
                                                    'fetched_at': r.get('fetched_at'), 'fetched_instant': r['fetched_instant'],
                                                    'fetched_at_valid': r['fetched_at_valid']}]})
            continue
        for lado in ('home', 'away'):
            p = fila['participants'][lado]
            equipos.setdefault(p['id'], {'source': 'Sofascore', 'team_id': p['id'], 'name': p.get('name')})
        partidos[fila['id']] = dict(desde_listado, source='Sofascore', match_id=fila['id'], season=temporadas.get(fila['season_id']),
                                    fetched_at=r.get('fetched_at'), available_sections=[],
                                    exports=sorted({v['export'] for v in originales if v['event_id'] == fila['id']}),
                                    version_conflict=False, version_issues=[], calendar_source='listing')
    for r in originales:
        # Datos maestros de todas las versiones de alineación: ninguna observación se pierde.
        if r['key'] == 'lineups':
            for lado in ('home', 'away'):
                for entrada in (r['payload'].get(lado) or {}).get('players') or []:
                    ficha = ficha_de_entrada(entrada, r.get('fetched_at'))
                    if ficha['player_id'] is not None:
                        observaciones.setdefault(ficha['player_id'], []).append((r.get('fetched_at'), ficha))
    for r in seleccion.values():
        if r['key'] != 'lineups':
            continue
        mid = r['event_id']
        if mid not in partidos:
            raise ValueError(f'Alineación sin partido: {mid}')
        convocatorias, avisos_alineacion = extraer_convocatorias(r, partidos[mid])
        avisos.extend(avisos_alineacion)
        for fila in convocatorias:
            # Regla de la importación (no de la extracción): un jugador sólo una vez por lado en una alineación.
            clave = (mid, fila['team_id'], fila['player_id'])
            if clave in participaciones:
                raise ValueError(f'Jugador duplicado en alineación: {clave}')
            participaciones[clave] = fila
    # Sólo enriquece jugadores ya observados. Una ficha nunca crea convocatorias ni equipos.
    for r in fichas_originales:
        if r['player_id'] in observaciones:
            observaciones[r['player_id']].append((r['fetched_at'], ficha_de_entrada(r['payload'], r['fetched_at'])))
    jugadores = []
    for pid, obs in observaciones.items():
        ficha, distintos = ficha_combinada(obs)
        jugadores.append(ficha)
        if distintos:
            discrepancias.append({'player_id': pid, 'key': 'player', 'reasons': ['player_attribute_values_differ'],
                                  'integrity': False, 'values': distintos,
                                  'versions': [dict(export=r['export'], line=r['line'], content_hash=r['content_hash'],
                                                    fetched_at=r.get('fetched_at'), key=r.get('key', 'player_profile'))
                                               for r in originales + fichas_originales
                                               if (r.get('player_id') == pid if 'player_id' in r else r['key'] == 'lineups' and
                                                   any((e.get('player') or {}).get('id') == pid
                                                       for lado in ('home', 'away')
                                                       for e in (r['payload'].get(lado) or {}).get('players', [])))]})
    jp = list(participaciones.values())
    seguidos = sorted({info['followed_team_id'] for info in manifiesto if info['followed_team_id'] is not None})
    resumen = {
        'format': FORMATO, 'exports': manifiesto,
        'raw_records': len(originales), 'sections': len(seleccion),
        'identical_repeats': sum(len(v) - 1 for v in grupos.values()) - sum(len({x['content_hash'] for x in v}) - 1 for v in grupos.values()),
        'discrepancies': len(discrepancias), 'integrity_conflicts': sorted(integridad),
        'teams': len(equipos), 'players': len(jugadores), 'matches': len(partidos),
        'matches_by_status': {s: sum(m['status'] == s for m in partidos.values()) for s in sorted({m['status'] for m in partidos.values()}, key=str)},
        'lineup_records': len(jp),
        'followed_teams': {str(t): {'matches': sum(t in (m['home_team_id'], m['away_team_id']) for m in partidos.values()),
                                    'players_in_lineups': len({p['player_id'] for p in jp if p['team_id'] == t}),
                                    'lineup_records': sum(p['team_id'] == t for p in jp),
                                    'positive_minutes': sum(p['team_id'] == t and p['played'] is True for p in jp),
                                    'unknown_participation': sum(p['team_id'] == t and p['played'] is None for p in jp)}
                           for t in seguidos},
        'warnings': avisos,
        'notes': ['Identificadores exclusivos de SofaScore.',
                  'Una exportación propia de un equipo no acredita un calendario ni unas estadísticas completas.',
                  'originales.json conserva todas las líneas de entrada con su procedencia; selected marca la versión operativa.',
                  'Las discrepancias (discrepancias.json) se conservan; ninguna se resuelve a mano.',
                  'Campos ausentes no se convierten en cero; statistics conserva las claves originales.'],
    }
    importacion = {'format': FORMATO, 'imported_at': importado_en or datetime.now(timezone.utc).isoformat(timespec='seconds'),
                   'exports': manifiesto, 'followed_team_ids': seguidos}
    return {**({'fichas_originales': fichas_originales} if con_fichas else {}),
            'equipos': sorted(equipos.values(), key=lambda x: x['team_id']),
            'jugadores': sorted(jugadores, key=lambda x: x['player_id']),
            'partidos': sorted(partidos.values(), key=lambda x: x['match_id']),
            'jugador_partido': sorted(jp, key=lambda x: (x['match_id'], x['team_id'], x['player_id'])),
            'originales': originales,
            'discrepancias': sorted(discrepancias, key=lambda d: (str(d.get('event_id', '')), d['key'], str(d.get('player_id', '')))),
            'exportaciones': importacion,
            'resumen': resumen}


def exportacion(valor):
    """EQUIPO=RUTA: la exportación propia del equipo seguido (id de SofaScore)."""
    equipo, separador, ruta = valor.partition('=')
    if not separador or not equipo.isdigit() or int(equipo) <= 0 or not ruta:
        raise argparse.ArgumentTypeError(f'se esperaba EQUIPO=RUTA, por ejemplo 2841=real-jaen-raw.jsonl: {valor!r}')
    return Path(ruta), int(equipo)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('entrada', type=Path, nargs='*', help='exportación sin equipo seguido declarado')
    ap.add_argument('--exportacion', type=exportacion, action='append', default=[], metavar='EQUIPO=RUTA',
                    help='exportación propia del equipo EQUIPO (repetible)')
    ap.add_argument('--calendario', type=Path, action='append', default=[], metavar='RUTA',
                    help='dataset normalizado `events` del scraper (repetible): partidos sin payload raw, sin equipo seguido')
    ap.add_argument('--salida', type=Path, default=Path('datos_normalizados'))
    ap.add_argument('--fichas-jugadores', type=Path, action='append', default=[], metavar='RUTA',
                    help='fichas individuales originales JSONL (repetible), sólo atributos personales')
    args = ap.parse_args()
    entradas = [(ruta, None) for ruta in args.entrada] + args.exportacion
    if not entradas:
        ap.error('indica al menos una exportación')
    try:
        if args.salida.exists():
            raise ValueError('La carpeta de salida ya existe. Usa --salida con una carpeta nueva para no sobrescribir datos.')
        datos = transformar(entradas, calendarios=args.calendario, fichas=args.fichas_jugadores)
        # Se escribe en una carpeta temporal y se renombra al final: un fallo no deja una salida a medias.
        temporal = args.salida.with_name(args.salida.name + '.parcial')
        if temporal.exists():
            raise ValueError(f'Existe {temporal}: bórrala si es de una importación fallida.')
        temporal.mkdir(parents=True)
        for nombre, contenido in datos.items():
            (temporal / (nombre + '.json')).write_text(json.dumps(contenido, ensure_ascii=False, indent=2) + '\n',
                                                        encoding='utf-8', newline='\n')
        temporal.rename(args.salida)
        print(json.dumps(datos['resumen'], ensure_ascii=False, indent=2))
    except (ValueError, KeyError, TypeError, OSError) as e:
        ap.exit(1, f'Error: {e}\n')


if __name__ == '__main__':
    main()
