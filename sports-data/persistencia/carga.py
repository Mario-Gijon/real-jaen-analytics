"""Carga inicial (fase 2, docs/persistencia-sqlite.md 4.1): una carpeta de importación verificada -> una base SQLite
nueva, fuera de los repositorios.

1. La carpeta (formato 2) se comprueba: huellas, instantes, orden de ingestión y que las reglas actuales
   (importar.ensamblar: selección, discrepancias, partidos, convocatorias, fichas) la reproducen exactamente.
2. El esquema se crea con Alembic en `<base>.parcial`; la evidencia, la identidad, las declaraciones y el estado
   operativo se escriben en una sola transacción, con las salidas de esas mismas reglas.
3. La base se reconstruye a los JSON del importador (persistencia.reconstruccion) y se compara con la carpeta.
4. Solo si todo coincide, `<base>.parcial` pasa a `<base>`. Ante cualquier diferencia o fallo se borra: nunca queda
   una base a medias con el nombre de una válida. La carpeta de origen no se modifica.
"""
import copy
import hashlib
import json
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

import importar
from persistencia import reconstruccion, sqlite
from persistencia.bloqueo import bloquear

RAIZ = Path(__file__).resolve().parent.parent
PROVEEDOR = 'sofascore'
# Reglas que determinan o persisten las anotaciones. Los módulos de consultas y
# HTTP quedan fuera: solo leen/exponen los resultados ya generados.
ARCHIVOS_ENRIQUECIMIENTO = (
    'enrichment/__init__.py',
    'enrichment/corner_attributes.py',
    'enrichment/foul_attributes.py',
    'enrichment/goal_attributes.py',
    'enrichment/normalizer.py',
    'enrichment/pipeline.py',
    'enrichment/shot_attributes.py',
    'enrichment/store.py',
    'enrichment/taxonomy.py',
)
# Espacio de nombres fijo de los uid (UUIDv5 del primer id externo; se asigna una vez y no se recalcula).
ESPACIO_UID = uuid.uuid5(uuid.NAMESPACE_URL, 'urn:api-real-jaen:identidad')
OBLIGATORIOS = ('equipos', 'jugadores', 'partidos', 'jugador_partido', 'originales', 'discrepancias', 'exportaciones',
                'resumen')
CLAVES_ORIGINAL = {'event_id', 'key', 'sub', 'fetched_at', 'payload', 'export', 'line', 'content_hash', 'fetched_instant',
                   'fetched_at_valid', 'selected'}
CLAVES_FICHA = {'player_id', 'endpoint', 'fetched_at', 'payload', 'export', 'line', 'content_hash', 'fetched_instant',
                'fetched_at_valid'}
TIPO_OBSERVACION = {'raw': 'section', 'listing': 'listing', 'player_profile': 'player_profile'}


class CargaRechazada(Exception):
    """La carga no se hace: la base no existe al terminar."""


def ahora():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def uid(tipo, externo):
    return str(uuid.uuid5(ESPACIO_UID, f'{PROVEEDOR}:{tipo}:{externo}'))


def literal(valor):
    """Literal JSON de un valor de la fuente (NaN o infinito no son JSON: la carga se rechaza)."""
    try:
        return json.dumps(valor, ensure_ascii=False, allow_nan=False)
    except ValueError as e:
        raise CargaRechazada(f'Valor no representable en JSON: {e}') from e


def archivos_de_codigo():
    """Los archivos que implementan reglas, esquema y carga (rutas relativas, en orden)."""
    propios = {'api.py', 'queries.py', 'importar.py', 'alembic.ini', 'requirements-sql.txt'}
    propios |= set(ARCHIVOS_ENRIQUECIMIENTO)
    propios |= {p.relative_to(RAIZ).as_posix() for p in (RAIZ / 'persistencia').rglob('*') if p.is_file()
                and '__pycache__' not in p.parts and p.suffix in {'.py', '.mako'}}
    return sorted(propios)


def referencia_del_codigo():
    """Referencia verificable del código que aplica las reglas: el commit completo y, si esos archivos tienen cambios
    sin confirmar, la huella sha256 de cada uno y una agregada (sha256 del listado `huella  ruta`, el formato de
    sha256sum). Cualquiera puede recalcularlas desde una copia del código. `texto` va en code_ref y tool_version."""
    archivos = archivos_de_codigo()
    huellas = {a: hashlib.sha256((RAIZ / a).read_bytes()).hexdigest() for a in archivos}
    agregada = hashlib.sha256(''.join(f'{huellas[a]}  {a}\n' for a in archivos).encode('utf-8')).hexdigest()
    try:
        commit = subprocess.run(['git', '-C', str(RAIZ), 'rev-parse', 'HEAD'], capture_output=True, text=True,
                                check=True).stdout.strip()
        cambios = bool(subprocess.run(['git', '-C', str(RAIZ), 'status', '--porcelain', '--', *archivos],
                                      capture_output=True, text=True, check=True).stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        commit, cambios = None, True
    texto = f'git:{commit}' if commit and not cambios else f'git:{commit or "desconocido"}+sha256:{agregada}'
    return {'texto': texto, 'commit': commit, 'cambios_sin_confirmar': cambios, 'sha256_agregada': agregada,
            'archivos': huellas}


def version_del_codigo():
    return referencia_del_codigo()['texto']


# ─── Destino ─────────────────────────────────────────────────────────────────────────────────────────────────────

def acompanantes(ruta):
    return [ruta.with_name(ruta.name + sufijo) for sufijo in ('-wal', '-shm', '-journal')]


def parcial_de(ruta):
    return ruta.with_name(ruta.name + '.parcial')


def repositorio_que_contiene(ruta):
    for directorio in (ruta, *ruta.parents):
        if (directorio / '.git').exists():
            return directorio
    return None


def comprobar_destino(ruta):
    """Ruta absoluta de una base nueva: fuera de cualquier repositorio y sin base, parcial ni restos previos."""
    ruta = Path(ruta).resolve()
    repositorio = repositorio_que_contiene(ruta.parent)
    if repositorio is not None:
        raise CargaRechazada(f'La base debe quedar fuera de los repositorios: {ruta} está dentro de {repositorio}')
    for existente in (ruta, *acompanantes(ruta), parcial_de(ruta), *acompanantes(parcial_de(ruta))):
        if existente.exists():
            raise CargaRechazada(f'Ya existe {existente}: la carga inicial solo crea una base nueva')
    return ruta


def borrar(ruta):
    for archivo in (ruta, *acompanantes(ruta)):
        archivo.unlink(missing_ok=True)


# ─── Carpeta de origen ───────────────────────────────────────────────────────────────────────────────────────────

def leer_carpeta(carpeta):
    carpeta = Path(carpeta)
    faltan = [n for n in OBLIGATORIOS if not (carpeta / f'{n}.json').is_file()]
    if faltan:
        raise CargaRechazada(f'{carpeta}: faltan {", ".join(f + ".json" for f in faltan)} (se necesita una importación de formato {importar.FORMATO})')
    datos = {n: json.loads((carpeta / f'{n}.json').read_text(encoding='utf-8')) for n in OBLIGATORIOS}
    if (carpeta / 'fichas_originales.json').is_file():
        datos['fichas_originales'] = json.loads((carpeta / 'fichas_originales.json').read_text(encoding='utf-8'))
    if not isinstance(datos['exportaciones'], dict) or datos['exportaciones'].get('format') != importar.FORMATO:
        raise CargaRechazada(f'{carpeta}: exportaciones.json no es de formato {importar.FORMATO}')
    return datos


def normalizado(valor):
    return json.loads(json.dumps(valor, ensure_ascii=False))


def diferencias(esperado, obtenido, nombres):
    return [n for n in nombres if normalizado(obtenido.get(n)) != esperado.get(n)]


def verificar_carpeta(datos):
    """Comprueba la carpeta y devuelve lo que producen las reglas actuales con sus registros (igual a la carpeta)."""
    manifiesto = datos['exportaciones']['exports']
    etiquetas = [e.get('id') for e in manifiesto]
    if len(set(etiquetas)) != len(etiquetas) or len({e.get('sha256') for e in manifiesto}) != len(manifiesto):
        raise CargaRechazada('El manifiesto repite una etiqueta o un fichero (mismo sha256): esta carga no lo admite; '
                             'un mismo fichero con varias declaraciones se incorporará en la importación incremental')
    tipos = {e['id']: e.get('kind', 'raw') for e in manifiesto}
    if any(t not in TIPO_OBSERVACION for t in tipos.values()):
        raise CargaRechazada(f'Tipos de exportación desconocidos: {sorted(set(tipos.values()) - set(TIPO_OBSERVACION))}')
    posicion = {etiqueta: i for i, etiqueta in enumerate(etiquetas)}
    for nombre, registros, admitidos in (('originales', datos['originales'], ('raw', 'listing')),
                                         ('fichas_originales', datos.get('fichas_originales', []), ('player_profile',))):
        claves = []
        for r in registros:
            if r.get('export') not in posicion or tipos[r['export']] not in admitidos:
                raise CargaRechazada(f'{nombre}: registro de una exportación desconocida o de otro tipo ({r.get("export")})')
            if (tipos[r['export']] == 'listing') != (r.get('key') == 'listing') and nombre == 'originales':
                raise CargaRechazada(f'{nombre}: {r["export"]} línea {r.get("line")} no corresponde a su tipo de exportación')
            literal(r['payload'])  # NaN o infinito no son JSON: se rechaza con su motivo
            if importar.huella(r['payload']) != r.get('content_hash'):
                raise CargaRechazada(f'{nombre}: {r["export"]} línea {r.get("line")}: la huella no coincide con el contenido')
            if r.get('fetched_at') is not None and not isinstance(r['fetched_at'], str):
                raise CargaRechazada(f'{nombre}: {r["export"]} línea {r.get("line")}: fetched_at no es texto ni null')
            momento = importar.instante(r.get('fetched_at'))
            if r.get('fetched_instant') != importar.utc(momento) or r.get('fetched_at_valid') is not (momento is not None):
                raise CargaRechazada(f'{nombre}: {r["export"]} línea {r.get("line")}: instante de obtención incoherente')
            claves.append((posicion[r['export']], r.get('line')))
        if claves != sorted(claves) or len(set(claves)) != len(claves):
            raise CargaRechazada(f'{nombre}: los registros no siguen el orden de ingestión (exportación, línea)')
    registros = [{k: v for k, v in r.items() if k != 'selected'} for r in datos['originales']]
    fichas = [dict(r) for r in datos.get('fichas_originales', [])]
    try:
        salida = importar.ensamblar(registros, copy.deepcopy(manifiesto), fichas, datos['exportaciones'].get('imported_at'),
                                    con_fichas='fichas_originales' in datos)
    except (ValueError, KeyError, TypeError) as e:
        raise CargaRechazada(f'Las reglas actuales de importación rechazan la carpeta: {e}') from e
    distintos = diferencias(datos, salida, [n for n in salida])
    if distintos:
        raise CargaRechazada(f'Las reglas actuales no reproducen la carpeta en: {", ".join(distintos)}')
    return salida


# ─── Escritura ───────────────────────────────────────────────────────────────────────────────────────────────────

class Filas:
    """Las filas que escribiría la carga, por tabla y en orden de dependencias (el de su primera aparición): las
    inserta la carga inicial y las compara la sincronización. Un solo código calcula ambas."""

    def __init__(self):
        self.tablas = {}

    def agregar(self, tabla, filas):
        self.tablas.setdefault(tabla, []).extend(filas)

    def de(self, tabla):
        return self.tablas.get(tabla, [])


# Reglas materializadas: (nombre, versión, descripción). Cambiar una versión exige `persistencia reconstruir`.
REGLAS = (
    ('importar.seleccionar', str(importar.FORMATO), 'versión operativa de cada sección y discrepancias'),
    ('importar.ensamblar', str(importar.FORMATO), 'partidos, equipos, fichas y discrepancias de atributos'),
    ('importar.extraer_convocatorias', '1', 'convocatorias de una alineación'),
)


def insertar(c, tabla, filas):
    if isinstance(c, Filas):
        c.agregar(tabla, filas)
        return
    if not filas:
        return
    columnas = list(filas[0])
    c.execute(text(f'INSERT INTO "{tabla}" ({", ".join(chr(34) + k + chr(34) for k in columnas)}) '
                   f'VALUES ({", ".join(":" + k for k in columnas)})'), filas)


class Identidades:
    """Entidades creadas desde un id externo de SofaScore (correspondencia `origin`), nunca por nombre."""

    def __init__(self, c, run):
        self.c, self.run, self.ids, self.ultimo = c, run, {}, {}

    def crear(self, tipo, externo, **columnas):
        clave = (tipo, str(externo))
        if clave in self.ids:
            return self.ids[clave]
        ident = self.ultimo[tipo] = self.ultimo.get(tipo, 0) + 1
        insertar(self.c, tipo, [dict(id=ident, uid=uid(tipo, externo), created_in_run=self.run, **columnas)])
        insertar(self.c, f'{tipo}_external_id', [dict(provider=PROVEEDOR, external_id=str(externo), entity_id=ident,
                                                      status='origin', created_in_run=self.run)])
        self.ids[clave] = ident
        return ident

    def de(self, tipo, externo):
        return None if externo is None else self.ids.get((tipo, str(externo)))


def entero_externo(valor, que):
    if type(valor) is not int:
        raise CargaRechazada(f'{que}: se esperaba un id entero de SofaScore y llega {valor!r}')
    return valor


def texto_o_nulo(valor, que):
    if valor is not None and not isinstance(valor, str):
        raise CargaRechazada(f'{que}: se esperaba texto o null y llega {valor!r}')
    return valor


def escribir(c, datos, salida, origen):
    """Carga inicial: la ejecución 1 y todas las filas de filas_de_carga, en una transacción."""
    referencia = referencia_del_codigo()
    c.execute(text("INSERT INTO import_run (id, kind, started_at, status, tool_version, command) "
                   "VALUES (1, 'initial_load', :t, 'running', :v, :cmd)"),
              {'t': ahora(), 'v': referencia['texto'], 'cmd': origen['comando']})
    filas, totales = filas_de_carga(datos, salida, 1, referencia['texto'])
    for tabla, lista in filas.tablas.items():
        insertar(c, tabla, lista)
    informe = {'origen': {'carpeta': origen['carpeta'], 'format': datos['exportaciones']['format'],
                          'imported_at': datos['exportaciones'].get('imported_at')},
               'resumen': salida['resumen'], 'codigo': referencia, **totales}
    c.execute(text("UPDATE import_run SET status = 'committed', finished_at = :t, summary = :s WHERE id = 1"),
              {'t': ahora(), 's': literal(informe)})


def filas_de_carga(datos, salida, run, codigo):
    """Todas las filas (identidad, evidencia, declaraciones y estado operativo) que corresponden a la salida de
    importar.ensamblar, sin escribir nada. Los ids de entidad se numeran desde 1 en orden de aparición; la
    sincronización los traduce a los de la base por su id externo."""
    c = Filas()
    reglas = {}
    for i, (nombre, version, descripcion) in enumerate(REGLAS, 1):
        insertar(c, 'rule_set', [dict(id=i, name=nombre, version=version, code_ref=codigo, description=descripcion)])
        reglas[nombre] = i
    ident = Identidades(c, run)
    manifiesto = datos['exportaciones']['exports']
    originales, fichas = salida['originales'], salida.get('fichas_originales', [])
    partidos = {p['match_id']: p for p in salida['partidos']}

    # Identidad: partidos (todo evento observado), equipos, jugadores observados, competiciones, temporadas, etapas.
    for r in originales:
        ident.crear('match', entero_externo(r['event_id'], 'event_id'))
    for e in salida['equipos']:
        ident.crear('team', e['team_id'])
    for j in salida['jugadores']:
        ident.crear('player', entero_externo(j['player_id'], 'player_id'))
    seleccion = {}
    for r in originales:
        if r['selected']:
            seleccion[(r['event_id'], r['key'], r['sub'])] = r
    competicion_de_temporada, competicion_de_etapa, etiqueta_de_temporada = {}, {}, {}
    for p in salida['partidos']:
        origen_partido = seleccion[(p['match_id'], p['calendar_source'], '')]
        general = (importar.rasgos_evento(origen_partido['payload'])['unique_tournament_id'] if p['calendar_source'] == 'event'
                   else origen_partido['payload'].get('tournament_id'))
        for mapa, clave, valor, que in ((competicion_de_temporada, p['season_id'], general, 'temporada'),
                                        (competicion_de_etapa, p['tournament_id'], general, 'etapa'),
                                        (etiqueta_de_temporada, p['season_id'], p['season'], 'nombre de temporada')):
            if clave is None:
                continue
            if mapa.get(clave) is not None and valor is not None and mapa[clave] != valor:
                raise CargaRechazada(f'La {que} {clave} aparece con valores distintos ({mapa[clave]} y {valor}): '
                                     'no se une ni se elige ninguno')
            if valor is not None or clave not in mapa:
                mapa[clave] = valor
    for externo in sorted({v for v in competicion_de_temporada.values() if v is not None} |
                          {v for v in competicion_de_etapa.values() if v is not None}):
        ident.crear('competition', entero_externo(externo, 'competición'))
    for externo, general in competicion_de_temporada.items():
        ident.crear('season', entero_externo(externo, 'temporada'), competition_id=ident.de('competition', general),
                    label=texto_o_nulo(etiqueta_de_temporada.get(externo), 'temporada'))
    for externo, general in competicion_de_etapa.items():
        ident.crear('stage', entero_externo(externo, 'etapa'), competition_id=ident.de('competition', general))

    # Evidencia: ficheros con su presentación y declaraciones, contenidos y observaciones en orden de ingestión.
    archivo = {}
    for i, e in enumerate(manifiesto, 1):
        tipo = e.get('kind', 'raw')
        insertar(c, 'source_file', [dict(id=i, sha256=e['sha256'], kind=tipo, label=e['id'], records=e['records'],
                                         events=e.get('events'), players=e.get('players'),
                                         invalid_fetched_at=e.get('invalid_fetched_at'),
                                         without_profile=e.get('without_profile'), fetched_from=e.get('fetched_from'),
                                         fetched_to=e.get('fetched_to'), first_run_id=run)])
        insertar(c, 'import_run_file', [dict(run_id=run, source_file_id=i, position=i, presented_name=e['file'],
                                             presented_path=None, outcome='ingested')])
        archivo[e['id']] = (i, tipo)
    for e in manifiesto:
        equipo = e.get('followed_team_id')
        if equipo is None:
            continue
        propios = [r for r in originales if r['export'] == e['id'] and r['key'] == 'event']
        if not any(equipo in (importar.rasgos_evento(r['payload'])['home_team_id'],
                              importar.rasgos_evento(r['payload'])['away_team_id']) for r in propios):
            raise CargaRechazada(f'{e["id"]} se declara propia del equipo {equipo}, que no juega ningún partido del fichero')
        if ident.de('team', equipo) is None:
            raise CargaRechazada(f'{e["id"]} se declara propia del equipo {equipo}, que no figura en ningún partido operativo')
        insertar(c, 'file_team_declaration', [dict(source_file_id=archivo[e['id']][0], team_id=ident.de('team', equipo),
                                                   external_team_id=str(equipo), status='active', declared_in_run=run,
                                                   declared_by='manifiesto de la importación (EQUIPO=RUTA)')])
    textos, orden_distinto = {}, 0
    for r in originales + fichas:
        texto = literal(r['payload'])
        if r['content_hash'] not in textos:
            textos[r['content_hash']] = texto
            insertar(c, 'content', [dict(hash=r['content_hash'], payload_text=texto,
                                         text_sha256=hashlib.sha256(texto.encode('utf-8')).hexdigest(),
                                         bytes=len(texto.encode('utf-8')))])
        elif textos[r['content_hash']] != texto:
            orden_distinto += 1
    observacion = {}
    en_orden = sorted(originales + fichas, key=lambda r: (archivo[r['export']][0], r['line']))
    for n, r in enumerate(en_orden, 1):
        tipo = TIPO_OBSERVACION[archivo[r['export']][1]]
        extra = {k: v for k, v in r.items() if k not in (CLAVES_FICHA if tipo == 'player_profile' else CLAVES_ORIGINAL)}
        fila = dict(id=n, source_file_id=archivo[r['export']][0], line=r['line'], kind=tipo, provider=PROVEEDOR,
                    external_event_id=None, external_player_id=None, key=None, sub='', endpoint=None,
                    fetched_at_raw=r['fetched_at'], fetched_instant=r['fetched_instant'],
                    fetched_at_valid=r['fetched_at_valid'], content_hash=r['content_hash'],
                    envelope_extra=literal(extra) if extra else None, match_id=None, player_id=None)
        if tipo == 'player_profile':
            fila.update(external_player_id=str(entero_externo(r['player_id'], 'ficha')), key='player_profile',
                        endpoint=texto_o_nulo(r.get('endpoint'), 'endpoint'), player_id=ident.de('player', r['player_id']))
        else:
            fila.update(external_event_id=str(r['event_id']), key=r['key'], sub=r['sub'],
                        match_id=ident.de('match', r['event_id']))
        insertar(c, 'observation', [fila])
        observacion[(r['export'], r['line'])] = n

    # Estado operativo, con las salidas de las reglas (no se vuelven a decidir aquí).
    versiones = {}
    for r in originales:
        versiones[(r['event_id'], r['key'], r['sub'])] = versiones.get((r['event_id'], r['key'], r['sub']), 0) + 1
    insertar(c, 'section_selection', [dict(match_id=ident.de('match', a), key=k, sub=s,
                                           observation_id=observacion[(r['export'], r['line'])],
                                           versions=versiones[(a, k, s)], rule_set_id=reglas['importar.seleccionar'],
                                           run_id=run) for (a, k, s), r in seleccion.items()])
    for p in salida['partidos']:
        r = seleccion[(p['match_id'], p['calendar_source'], '')]
        general = competicion_de_etapa.get(p['tournament_id']) if p['tournament_id'] is not None else None
        insertar(c, 'match_state', [dict(
            match_id=ident.de('match', p['match_id']), source_key=p['calendar_source'], source_sub='',
            source_observation_id=observacion[(r['export'], r['line'])], stage_id=ident.de('stage', p['tournament_id']),
            season_id=ident.de('season', p['season_id']), competition_id=ident.de('competition', general),
            external_stage_id=None if p['tournament_id'] is None else str(p['tournament_id']),
            external_season_id=None if p['season_id'] is None else str(p['season_id']),
            external_competition_id=None if general is None else str(general),
            external_home_team_id=str(p['home_team_id']), external_away_team_id=str(p['away_team_id']),
            competition_name=texto_o_nulo(p['competition'], 'competition'), season_label=texto_o_nulo(p['season'], 'season'),
            status=texto_o_nulo(p['status'], 'status'), kickoff_utc=texto_o_nulo(p['kickoff_utc'], 'kickoff_utc'),
            round=None if p['round'] is None else literal(p['round']), home_team_id=ident.de('team', p['home_team_id']),
            away_team_id=ident.de('team', p['away_team_id']),
            home_score=None if p['home_score'] is None else literal(p['home_score']),
            away_score=None if p['away_score'] is None else literal(p['away_score']),
            calendar_source=p['calendar_source'], version_conflict=p['version_conflict'],
            rule_set_id=reglas['importar.ensamblar'], run_id=run)])
        insertar(c, 'match_version_issue', [dict(match_id=ident.de('match', p['match_id']), reason=m, ordinal=i)
                                            for i, m in enumerate(p['version_issues'])])
    escribir_nombres(c, salida, seleccion, partidos, ident, observacion, reglas, run)
    totales = escribir_convocatorias(c, seleccion, partidos, ident, observacion, reglas, run)
    escribir_atributos(c, originales, fichas, ident, observacion)
    escribir_discrepancias(c, salida['discrepancias'], ident, observacion, reglas, run)
    return c, dict(totales, contenidos_con_otro_orden_de_claves=orden_distinto)


def escribir_nombres(c, salida, seleccion, partidos, ident, observacion, reglas, run):
    """Nombre publicado y su observación de origen, con la regla del importador: el primero visto entre los `event`
    seleccionados y, después, los `listing` de partidos sin `event`."""
    origen = {}
    for (evento, clave, _), r in seleccion.items():
        if clave == 'event':
            e = importar.evento_de(r['payload'])
            for lado in ('homeTeam', 'awayTeam'):
                if (e.get(lado) or {}).get('id') is not None:
                    origen.setdefault(e[lado]['id'], r)
    for (evento, clave, _), r in seleccion.items():
        if clave == 'listing' and partidos.get(evento, {}).get('calendar_source') == 'listing':
            for lado in ('home', 'away'):
                origen.setdefault(r['payload']['participants'][lado]['id'], r)
    insertar(c, 'team_name', [dict(team_id=ident.de('team', e['team_id']), name=texto_o_nulo(e['name'], 'nombre'),
                                   source_observation_id=observacion[(origen[e['team_id']]['export'], origen[e['team_id']]['line'])],
                                   rule_set_id=reglas['importar.ensamblar'], run_id=run) for e in salida['equipos']])


def entradas_de(payload):
    return [(lado, e) for lado in ('home', 'away') for e in (payload.get(lado) or {}).get('players') or []]


def escribir_convocatorias(c, seleccion, partidos, ident, observacion, reglas, run):
    """Convocatorias de las alineaciones seleccionadas con importar.extraer_convocatorias; de la entrada original
    solo se toma lo que la extracción no distingue (presencia de claves, posición)."""
    convocatorias, estadisticas = [], []
    for (evento, clave, sub), r in seleccion.items():
        if clave != 'lineups':
            continue
        filas, _avisos = importar.extraer_convocatorias(r, partidos[evento])
        entradas = entradas_de(r['payload'])
        if len(filas) != len(entradas):
            raise CargaRechazada(f'Alineación {evento}: la extracción no corresponde a sus entradas')
        for indice, (fila, (lado, entrada)) in enumerate(zip(filas, entradas)):
            jugador = entero_externo(fila['player_id'], f'alineación {evento}')
            equipo_origen = entrada.get('teamId')
            if equipo_origen is not None:
                entero_externo(equipo_origen, f'teamId de la alineación {evento}')
            stats = entrada['statistics'] if 'statistics' in entrada else None
            tipo = ('absent' if 'statistics' not in entrada else 'null' if stats is None
                    else 'object' if isinstance(stats, dict) else 'other')
            ident_convocatoria = len(convocatorias) + 1
            convocatorias.append(dict(
                id=ident_convocatoria, match_id=ident.de('match', evento), lineup_key='lineups', lineup_sub=sub,
                lineup_observation_id=observacion[(r['export'], r['line'])], side=lado, entry_index=indice,
                team_id=ident.de('team', fila['team_id']), external_player_id=str(jugador),
                player_id=ident.de('player', jugador),
                external_team_id=None if equipo_origen is None else str(equipo_origen),
                substitute=literal(entrada['substitute']) if 'substitute' in entrada else None,
                shirt_number=literal(entrada['shirtNumber']) if 'shirtNumber' in entrada else None,
                minutes_played=literal(stats['minutesPlayed']) if isinstance(stats, dict) and 'minutesPlayed' in stats else None,
                match_position=literal(entrada['position']) if 'position' in entrada else None,
                played=fila['played'], statistics_kind=tipo, status='active', retired_in_run=None,
                rule_set_id=reglas['importar.extraer_convocatorias'], run_id=run))
            if tipo == 'object':
                estadisticas.extend(dict(call_up_id=ident_convocatoria, key=k, ordinal=i, value_json=literal(v))
                                    for i, (k, v) in enumerate(stats.items()))
    insertar(c, 'call_up', convocatorias)
    insertar(c, 'call_up_stat', estadisticas)
    return {'convocatorias': len(convocatorias), 'valores_de_estadisticas': len(estadisticas)}


def escribir_atributos(c, originales, fichas, ident, observacion):
    """Los seis atributos personales de cada entrada de alineación (todas las versiones) y de cada ficha, como los lee
    importar.ficha_de_entrada (null incluido). La ficha se combina al consultar."""
    filas = []
    for r in originales:
        if r['key'] != 'lineups':
            continue
        for indice, (_lado, entrada) in enumerate(entradas_de(r['payload'])):
            ficha = importar.ficha_de_entrada(entrada, r.get('fetched_at'))
            if ficha['player_id'] is None:
                continue
            entero_externo(ficha['player_id'], f'alineación {r["event_id"]}')
            filas.extend(dict(observation_id=observacion[(r['export'], r['line'])], entry_index=indice, field=campo,
                              external_player_id=str(ficha['player_id']), player_id=ident.de('player', ficha['player_id']),
                              value_json=literal(ficha[campo]), fetched_instant=r['fetched_instant'])
                         for campo in importar.CAMPOS_FICHA)
    for r in fichas:
        ficha = importar.ficha_de_entrada(r['payload'], r['fetched_at'])
        filas.extend(dict(observation_id=observacion[(r['export'], r['line'])], entry_index=0, field=campo,
                          external_player_id=str(r['player_id']), player_id=ident.de('player', r['player_id']),
                          value_json=literal(ficha[campo]), fetched_instant=r['fetched_instant'])
                     for campo in importar.CAMPOS_FICHA)
    insertar(c, 'player_attribute_observation', filas)


def escribir_discrepancias(c, discrepancias, ident, observacion, reglas, run):
    for n, d in enumerate(discrepancias, 1):
        versiones = [observacion[(v['export'], v['line'])] for v in d['versions']]
        if 'player_id' in d:
            fila = dict(kind='player_attribute', match_id=None, player_id=ident.de('player', d['player_id']), sub='',
                        selected_observation_id=None, changed_fields=None, rule_set_id=reglas['importar.ensamblar'])
        else:
            tipo = 'listing_vs_event' if 'listing_differs_from_event' in d['reasons'] else 'section'
            fila = dict(kind=tipo, match_id=ident.de('match', d['event_id']), player_id=None, sub=d['sub'],
                        selected_observation_id=observacion[(d['selected']['export'], d['selected']['line'])],
                        changed_fields=literal(d['changed_fields']), rule_set_id=reglas['importar.seleccionar'])
        insertar(c, 'discrepancy', [dict(id=n, key=d['key'], integrity=d['integrity'], run_id=run, **fila)])
        insertar(c, 'discrepancy_reason', [dict(discrepancy_id=n, reason=m, ordinal=i) for i, m in enumerate(d['reasons'])])
        insertar(c, 'discrepancy_observation', [dict(discrepancy_id=n, observation_id=o) for o in versiones])
        insertar(c, 'discrepancy_value', [dict(discrepancy_id=n, field=campo, ordinal=i, value_json=literal(v))
                                          for campo, valores in d.get('values', {}).items() for i, v in enumerate(valores)])


# ─── Orquestación ────────────────────────────────────────────────────────────────────────────────────────────────

def configuracion_alembic(ruta):
    cfg = Config(str(RAIZ / 'alembic.ini'))
    cfg.attributes['base'] = str(ruta)
    cfg.attributes['configurar_registro'] = False
    return cfg


def recuentos(c):
    tablas = [t for (t,) in c.exec_driver_sql("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE "
                                              "'sqlite_%' AND name <> 'alembic_version' ORDER BY name")]
    return {t: c.exec_driver_sql(f'SELECT count(*) FROM "{t}"').scalar() for t in tablas}


def cargar(carpeta, ruta_base, comando=None):
    ruta = Path(ruta_base).resolve()
    repositorio = repositorio_que_contiene(ruta.parent)
    if repositorio is not None:
        raise CargaRechazada(f'La base debe quedar fuera de los repositorios: {ruta} está dentro de {repositorio}')
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with bloquear(ruta):
        with bloquear(parcial_de(ruta)):
            return _cargar(carpeta, ruta, comando)


def _cargar(carpeta, ruta_base, comando=None):
    """Carga la carpeta en una base nueva. Devuelve el informe; ante cualquier diferencia o fallo lanza
    CargaRechazada (u otra excepción) y no deja base."""
    carpeta = Path(carpeta).resolve()
    ruta = comprobar_destino(ruta_base)
    datos = leer_carpeta(carpeta)
    salida = verificar_carpeta(datos)
    parcial = parcial_de(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    motor = None
    from persistencia import sincronizacion  # importa este módulo
    try:
        command.upgrade(configuracion_alembic(parcial), 'head')
        motor = sqlite.crear_motor(parcial)
        with motor.begin() as c:
            escribir(c, datos, salida, {'carpeta': str(carpeta), 'comando': comando})
            # Obligatoria antes de confirmar cualquier escritura (sincronizacion.exigir_coherencia).
            sincronizacion.exigir_coherencia(c)
        with motor.connect() as c:
            if sqlite.comprobar_integridad(c):
                raise CargaRechazada('La base tiene filas que violan claves foráneas')
            if c.exec_driver_sql('PRAGMA integrity_check').scalar() != 'ok':
                raise CargaRechazada('PRAGMA integrity_check no es ok')
            revision = c.exec_driver_sql('SELECT version_num FROM alembic_version').scalar()
            esperada = ScriptDirectory.from_config(configuracion_alembic(parcial)).get_current_head()
            if revision != esperada:
                raise CargaRechazada(f'Revisión de esquema {revision}, se esperaba {esperada}')
            reconstruida = reconstruccion.salida_desde_base(c, con_fichas='fichas_originales' in datos)
            distintos = diferencias(datos, reconstruida, list(reconstruida))
            if distintos:
                raise CargaRechazada(f'La base no reproduce la carpeta en: {", ".join(distintos)}')
            cuentas = recuentos(c)
        motor.dispose()
        motor = None
        if any(p.exists() for p in acompanantes(parcial)):
            raise CargaRechazada('Quedan archivos de diario abiertos tras cerrar la base')
        parcial.rename(ruta)
    except BaseException:
        if motor is not None:
            motor.dispose()
        borrar(parcial)
        raise
    return {'base': str(ruta), 'origen': str(carpeta), 'revision': revision, 'comparados': sorted(reconstruida),
            'recuentos': cuentas}
