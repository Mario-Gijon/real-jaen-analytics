"""Sincronización completa (fase 4, docs/fase4-sincronizacion.md): toda la evidencia de la base (más la nueva de una
importación) pasa por importar.ensamblar y carga.filas_de_carga, el mismo código de la carga inicial; el resultado se
compara con la base por claves estables y solo se escriben las diferencias, conservando los ids internos.

- `planificar` solo lee: devuelve las operaciones y su recuento por tabla. Un plan vacío es la prueba de que el estado
  guardado es exactamente el que producen las reglas actuales con la evidencia guardada (sincronización en seco).
- `aplicar` ejecuta un plan dentro de la transacción de quien llama.
- `exigir_coherencia` es obligatoria antes de confirmar cualquier escritura (carga inicial, importación,
  reconstrucción, sustituciones y migraciones a una base con convocatorias): claves foráneas, toda convocatoria
  activa sale de la alineación seleccionada de su partido, transiciones de vigencia coherentes, versiones de reglas
  y sincronización en seco sin cambios.

Convocatorias (2.2): la clave es (partido, lado, jugador). Si la alineación seleccionada deja de incluir una, la fila
se retira (no se borra ni se reutiliza su id) y la transición queda en call_up_change. Una retirada conserva solo
el último estado que tuvo; el historial completo de lo que dijo cada versión está en las observaciones originales
(`observation`/`content`), no en esa fila. Si el jugador reaparece con la misma clave, se reactiva la misma fila; si
aparece en el otro lado, es otra convocatoria (otra clave y otro id) y la del lado anterior queda retirada.
Ninguna lectura de la API usa call_up directamente: solo la vista call_up_vigente (reconstruccion.jugador_partido).
"""
import copy
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import text

import importar
from persistencia import carga, reconstruccion, sqlite
from persistencia.bloqueo import exclusivo
from persistencia.carga import CargaRechazada, ahora, filas_de_carga, insertar, literal

ENTIDADES = ('competition', 'season', 'stage', 'team', 'player', 'match')
# Columna -> lo que referencia: los ids del estado deseado (numerados desde 1) se traducen a los de la base.
REFERENCIAS = {'match_id': 'match', 'team_id': 'team', 'home_team_id': 'team', 'away_team_id': 'team',
               'player_id': 'player', 'stage_id': 'stage', 'season_id': 'season', 'competition_id': 'competition',
               'observation_id': 'observation', 'source_observation_id': 'observation',
               'lineup_observation_id': 'observation', 'selected_observation_id': 'observation',
               'source_file_id': 'source_file', 'rule_set_id': 'rule_set', 'call_up_id': 'call_up',
               'discrepancy_id': 'discrepancy'}
# Columnas que registran la ejecución: en el estado deseado valen la ejecución en curso y no se comparan.
EJECUCION = ('run_id', 'created_in_run', 'first_run_id', 'declared_in_run')
EVIDENCIA = ('raw', 'listing', 'player_profile')


class ReglasDistintas(CargaRechazada):
    """El estado guardado no corresponde a las reglas actuales: hace falta `persistencia reconstruir`."""


class EstadoIncoherente(CargaRechazada):
    """La base no pasa la verificación: no se confirma nada."""


# ─── Estado deseado ──────────────────────────────────────────────────────────────────────────────────────────────

def evidencia(c):
    """(originales, fichas, manifiesto) de toda la evidencia guardada, en orden de ingestión."""
    originales = [{k: v for k, v in r.items() if k != 'selected'} for r in reconstruccion.originales(c)]
    return originales, reconstruccion.fichas_originales(c), reconstruccion.exportaciones(c)


def estado_deseado(c, nuevos=None, run=None, codigo=''):
    """Lo que producen las reglas actuales con la evidencia guardada (y la nueva, si la hay): (filas, salida)."""
    originales, fichas, manifiesto = evidencia(c)
    if nuevos is not None:
        originales += nuevos.originales
        fichas += nuevos.fichas
        manifiesto = dict(manifiesto, exports=nuevos.manifiesto(manifiesto['exports']))
    # `content` guarda la primera representación de cada huella semántica. Se deriva de esa misma
    # representación también antes del COMMIT: un orden de claves equivalente en la entrada no debe
    # cambiar call_up_stat.ordinal y discrepar después de la sincronización en seco, que lee `content`.
    textos = {h: json.loads(texto) for h, texto in c.exec_driver_sql('SELECT hash, payload_text FROM content')}
    def enlazar(registro):
        payload = textos.setdefault(registro['content_hash'], registro['payload'])
        return dict(registro, payload=payload)
    originales, fichas = list(map(enlazar, originales)), list(map(enlazar, fichas))
    try:
        salida = importar.ensamblar(originales, copy.deepcopy(manifiesto['exports']), fichas, manifiesto['imported_at'],
                                    con_fichas=True)
    except (ValueError, KeyError, TypeError) as e:
        raise CargaRechazada(f'Las reglas actuales de importación rechazan la evidencia: {e}') from e
    filas, _totales = filas_de_carga({'exportaciones': manifiesto}, salida, run, codigo)
    return filas, salida


# ─── Plan ────────────────────────────────────────────────────────────────────────────────────────────────────────

class Plan:
    def __init__(self):
        self.operaciones, self.cambios = [], defaultdict(Counter)
        self.resumen = None

    def insertar(self, tabla, filas, que='nuevas'):
        if filas:
            self.operaciones.append(('insertar', tabla, filas, None))
            self.cambios[tabla][que] += len(filas)

    def actualizar(self, tabla, donde, valores, que='actualizadas'):
        self.operaciones.append(('actualizar', tabla, donde, valores))
        self.cambios[tabla][que] += 1

    def borrar(self, tabla, donde, que):
        self.operaciones.append(('borrar', tabla, donde, None))
        self.cambios[tabla][que] += 1

    @property
    def vacio(self):
        return not self.operaciones and not self.recuento()

    def recuento(self):
        return {t: dict(+v) for t, v in sorted(self.cambios.items()) if +v}


def filas_bd(c, tabla, orden='rowid'):
    return [dict(r) for r in c.exec_driver_sql(f'SELECT * FROM "{tabla}" ORDER BY {orden}').mappings()]


def siguiente_id(c, tabla):
    return (c.exec_driver_sql(f'SELECT max(id) FROM "{tabla}"').scalar() or 0) + 1


def agrupar(filas, campo):
    grupos = defaultdict(list)
    for f in filas:
        grupos[f[campo]].append(f)
    return grupos


class Traduccion:
    """Ids del estado deseado -> ids de la base, por tipo; las columnas de ejecución pasan a la ejecución en curso."""

    def __init__(self, run):
        self.run, self.ids = run, defaultdict(dict)

    def __call__(self, fila):
        salida = {}
        for k, v in fila.items():
            if k in EJECUCION:
                v = self.run
            elif k in REFERENCIAS and v is not None:
                v = self.ids[REFERENCIAS[k]][v]
            salida[k] = v
        return salida


def distintos(actual, deseada, ignorar=()):
    return {k: v for k, v in deseada.items() if k not in ignorar and k not in EJECUCION and actual.get(k) != v}


def planificar(c, filas, run, reconstruir=False, resumen=None):
    """Operaciones que llevan la base al estado deseado (`filas`, de estado_deseado). Solo lee la base."""
    p, t = Plan(), Traduccion(run)
    planificar_reglas(c, filas, p, t, reconstruir)
    planificar_entidades(c, filas, p, t)
    planificar_evidencia(c, filas, p, t)
    planificar_derivados(c, filas, p, t)
    planificar_convocatorias(c, filas, p, t)
    planificar_discrepancias(c, filas, p, t)
    if resumen is not None:
        p.resumen = resumen
        if carga.normalizado(resumen) != reconstruccion.resumen_vigente(c):
            p.cambios['resumen']['distinto'] += 1
    return p


def planificar_reglas(c, filas, p, t, reconstruir):
    """Las reglas se identifican por (nombre, versión). Una que la base no tiene es un cambio de reglas: solo una
    reconstrucción explícita la registra."""
    existentes = {(r['name'], r['version']): r['id'] for r in filas_bd(c, 'rule_set', 'id')}
    siguiente = siguiente_id(c, 'rule_set')
    for d in filas.de('rule_set'):
        clave = (d['name'], d['version'])
        if clave not in existentes:
            if not reconstruir:
                raise ReglasDistintas(f'La regla {clave[0]}@{clave[1]} no está en la base: el estado guardado no corresponde '
                                      'a las reglas actuales; ejecute `persistencia reconstruir`')
            p.insertar('rule_set', [dict(d, id=siguiente)])
            existentes[clave] = siguiente
            siguiente += 1
        t.ids['rule_set'][d['id']] = existentes[clave]


def planificar_entidades(c, filas, p, t):
    """Por id externo: nunca se borran; las nuevas reciben el siguiente id y su uid (determinista, una vez)."""
    for tipo in ENTIDADES:
        externo = {r['entity_id']: r['external_id'] for r in filas.de(f'{tipo}_external_id')}
        en_bd = {r['external_id']: r['entity_id'] for r in filas_bd(c, f'{tipo}_external_id')
                 if r['provider'] == carga.PROVEEDOR and r['namespace'] == '' and r['status'] != 'rejected'}
        actuales = {r['id']: r for r in filas_bd(c, tipo)}
        siguiente = siguiente_id(c, tipo)
        for d in filas.de(tipo):
            fila = t({k: v for k, v in d.items() if k != 'id'})
            ext = externo[d['id']]
            if ext in en_bd:
                t.ids[tipo][d['id']] = en_bd[ext]
                cambios = distintos(actuales[en_bd[ext]], fila, ignorar=('uid',))
                if cambios:
                    p.actualizar(tipo, {'id': en_bd[ext]}, cambios)
                continue
            t.ids[tipo][d['id']] = siguiente
            p.insertar(tipo, [dict(fila, id=siguiente)])
            p.insertar(f'{tipo}_external_id', [dict(provider=carga.PROVEEDOR, external_id=ext, entity_id=siguiente,
                                                     status='origin', created_in_run=t.run)], 'nuevos')
            siguiente += 1


def planificar_evidencia(c, filas, p, t):
    """Ficheros (por etiqueta y sha256), contenidos (por huella), observaciones (por fichero y línea) y declaraciones:
    solo se añaden. Una observación de un partido o jugador que acaba de tener entidad se asocia a ella."""
    ficheros = {r['label']: r for r in filas_bd(c, 'source_file', 'id')}
    siguiente = siguiente_id(c, 'source_file')
    for d in filas.de('source_file'):
        actual = ficheros.get(d['label'])
        if actual is None:
            actual = ficheros[d['label']] = dict(t({k: v for k, v in d.items() if k != 'id'}), id=siguiente)
            p.insertar('source_file', [actual])
            siguiente += 1
        elif actual['sha256'] != d['sha256'] or actual['kind'] != d['kind']:
            raise CargaRechazada(f'La etiqueta {d["label"]} ya corresponde a otro fichero')
        t.ids['source_file'][d['id']] = actual['id']
    huellas = {h for (h,) in c.exec_driver_sql('SELECT hash FROM content')}
    p.insertar('content', [d for d in filas.de('content') if d['hash'] not in huellas])
    observaciones = {(r['source_file_id'], r['line']): r for r in filas_bd(c, 'observation', 'id')}
    siguiente = siguiente_id(c, 'observation')
    nuevas = []
    for d in filas.de('observation'):
        fila = t({k: v for k, v in d.items() if k != 'id'})
        actual = observaciones.get((fila['source_file_id'], fila['line']))
        if actual is None:
            nuevas.append(dict(fila, id=siguiente))
            t.ids['observation'][d['id']] = siguiente
            siguiente += 1
            continue
        t.ids['observation'][d['id']] = actual['id']
        cambios = distintos(actual, fila)
        asociar = {k: v for k, v in cambios.items() if k in ('match_id', 'player_id') and actual[k] is None}
        if set(cambios) - set(asociar):
            raise CargaRechazada(f'La observación {actual["id"]} guardada no coincide con su evidencia: {sorted(cambios)}')
        if asociar:
            p.actualizar('observation', {'id': actual['id']}, asociar, 'asociadas')
    p.insertar('observation', nuevas)
    activas = {(r['source_file_id'], r['external_team_id']) for r in filas_bd(c, 'file_team_declaration')
               if r['status'] == 'active'}
    p.insertar('file_team_declaration', [f for f in map(t, filas.de('file_team_declaration'))
                                         if (f['source_file_id'], f['external_team_id']) not in activas])


def conjuntos(c, filas, p, t, tabla, padre, padres=None):
    """Tablas sin referencias entrantes: se sustituye el conjunto de un padre solo si cambia."""
    deseadas = agrupar(map(t, filas.de(tabla)), padre)
    actuales = agrupar(filas_bd(c, tabla), padre)
    comparable = lambda lista: sorted(json.dumps({k: v for k, v in f.items() if k not in EJECUCION}, sort_keys=True,
                                                 default=str) for f in lista)
    for clave in sorted(set(deseadas) | (set(actuales) if padres is None else set()), key=str):
        if padres is not None and clave not in padres:
            continue
        if comparable(deseadas.get(clave, [])) == comparable(actuales.get(clave, [])):
            continue
        if clave in actuales:
            p.borrar(tabla, {padre: clave}, 'padres_sustituidos')
        else:
            p.cambios[tabla]['padres_nuevos'] += 1
        p.operaciones.append(('insertar', tabla, deseadas.get(clave, []), None))


def en_su_sitio(c, filas, p, t, tabla, clave):
    actuales = {tuple(r[k] for k in clave): r for r in filas_bd(c, tabla)}
    for d in filas.de(tabla):
        fila = t(d)
        actual = actuales.get(tuple(fila[k] for k in clave))
        if actual is None:
            p.insertar(tabla, [fila])
            continue
        cambios = distintos(actual, fila)
        if cambios:
            p.actualizar(tabla, {k: fila[k] for k in clave}, dict(cambios, run_id=t.run))


def planificar_derivados(c, filas, p, t):
    conjuntos(c, filas, p, t, 'player_attribute_observation', 'observation_id')
    # Selección: en su sitio; la anterior pasa al historial con su ejecución y su regla.
    actuales = {(r['match_id'], r['key'], r['sub']): r for r in filas_bd(c, 'section_selection')}
    for d in filas.de('section_selection'):
        fila = t(d)
        clave = (fila['match_id'], fila['key'], fila['sub'])
        actual = actuales.get(clave)
        if actual is None:
            p.insertar('section_selection', [fila])
            continue
        cambios = distintos(actual, fila)
        if not cambios:
            continue
        if 'observation_id' in cambios or 'rule_set_id' in cambios:
            p.insertar('section_selection_history', [dict(
                match_id=clave[0], key=clave[1], sub=clave[2], observation_id=actual['observation_id'],
                versions=actual['versions'], rule_set_id=actual['rule_set_id'], valid_from_run=actual['run_id'],
                valid_to_run=t.run)])
        p.actualizar('section_selection', {'match_id': clave[0], 'key': clave[1], 'sub': clave[2]},
                     dict(cambios, run_id=t.run))
    en_su_sitio(c, filas, p, t, 'match_state', ('match_id',))
    conjuntos(c, filas, p, t, 'match_version_issue', 'match_id')
    en_su_sitio(c, filas, p, t, 'team_name', ('team_id',))


def planificar_convocatorias(c, filas, p, t):
    """Vigencia (2.2): clave (partido, lado, jugador). Nuevas, actualizadas en su sitio, retiradas o reactivadas."""
    actuales = {(r['match_id'], r['side'], r['external_player_id']): r for r in filas_bd(c, 'call_up', 'id')}
    seleccion = {r['match_id']: r['observation_id'] for r in filas_bd(c, 'section_selection') if r['key'] == 'lineups'}
    for d in filas.de('section_selection'):
        if d['key'] == 'lineups':
            seleccion[t.ids['match'][d['match_id']]] = t.ids['observation'][d['observation_id']]
    siguiente = siguiente_id(c, 'call_up')
    vistas = set()
    for d in filas.de('call_up'):
        fila = t({k: v for k, v in d.items() if k != 'id'})
        clave = (fila['match_id'], fila['side'], fila['external_player_id'])
        if clave in vistas:
            raise CargaRechazada(f'Convocatoria repetida en el estado deseado (partido, lado, jugador): {clave}')
        vistas.add(clave)
        actual = actuales.get(clave)
        if actual is None:
            t.ids['call_up'][d['id']] = siguiente
            p.insertar('call_up', [dict(fila, id=siguiente)])
            siguiente += 1
            continue
        t.ids['call_up'][d['id']] = actual['id']
        cambios = distintos(actual, fila)
        if actual['status'] == 'retired':
            p.insertar('call_up_change', [dict(call_up_id=actual['id'], run_id=t.run, change='reactivated',
                                               lineup_observation_id=fila['lineup_observation_id'])])
            p.cambios['call_up']['reactivadas'] += 1
            p.cambios['call_up']['actualizadas'] -= 1  # la reactivación se cuenta aparte
        if cambios:
            p.actualizar('call_up', {'id': actual['id']}, dict(cambios, run_id=t.run))
    for clave, actual in actuales.items():
        if clave in vistas or actual['status'] == 'retired':
            continue
        p.actualizar('call_up', {'id': actual['id']}, {'status': 'retired', 'retired_in_run': t.run}, 'retiradas')
        p.insertar('call_up_change', [dict(call_up_id=actual['id'], run_id=t.run, change='retired',
                                           lineup_observation_id=seleccion.get(actual['match_id']))])
    # Estadísticas: solo de las convocatorias vigentes; las de una retirada se conservan con ella.
    conjuntos(c, filas, p, t, 'call_up_stat', 'call_up_id', padres=set(t.ids['call_up'].values()))


def planificar_discrepancias(c, filas, p, t):
    """Clave (tipo, sujeto, clave, sub). Si cambia, la anterior se marca superada y se crea otra; nada se borra."""
    def hijas(campo, filas_tabla):
        return {k: sorted((x[campo], x.get('ordinal', 0)) for x in v) for k, v in agrupar(filas_tabla, 'discrepancy_id').items()}

    def valores(filas_tabla):
        return {k: sorted((x['field'], x['ordinal'], x['value_json']) for x in v)
                for k, v in agrupar(filas_tabla, 'discrepancy_id').items()}

    motivos_bd = hijas('reason', filas_bd(c, 'discrepancy_reason'))
    obs_bd = hijas('observation_id', filas_bd(c, 'discrepancy_observation'))
    valores_bd = valores(filas_bd(c, 'discrepancy_value'))
    vigentes = {(r['kind'], r['match_id'], r['player_id'], r['key'], r['sub']): r
                for r in filas_bd(c, 'discrepancy', 'id') if r['superseded_in_run'] is None}
    obs_d = agrupar(map(t_sin_discrepancia(t), filas.de('discrepancy_observation')), 'discrepancy_id')
    motivos_d = agrupar(filas.de('discrepancy_reason'), 'discrepancy_id')
    valores_d = agrupar(filas.de('discrepancy_value'), 'discrepancy_id')
    siguiente = siguiente_id(c, 'discrepancy')
    firma = lambda f, m, o, v: (bool(f['integrity']), f['selected_observation_id'], f['changed_fields'], f['rule_set_id'],
                                m, o, v)
    vistas = set()
    for d in filas.de('discrepancy'):
        fila = t({k: v for k, v in d.items() if k != 'id'})
        clave = (fila['kind'], fila['match_id'], fila['player_id'], fila['key'], fila['sub'])
        vistas.add(clave)
        m = sorted((x['reason'], x['ordinal']) for x in motivos_d.get(d['id'], []))
        o = sorted((x['observation_id'], 0) for x in obs_d.get(d['id'], []))
        v = sorted((x['field'], x['ordinal'], x['value_json']) for x in valores_d.get(d['id'], []))
        actual = vigentes.get(clave)
        if actual is not None and firma(actual, motivos_bd.get(actual['id'], []), obs_bd.get(actual['id'], []),
                                        valores_bd.get(actual['id'], [])) == firma(fila, m, o, v):
            continue
        if actual is not None:
            p.actualizar('discrepancy', {'id': actual['id']}, {'superseded_in_run': t.run}, 'superadas')
        p.insertar('discrepancy', [dict(fila, id=siguiente)])
        p.insertar('discrepancy_reason', [dict(discrepancy_id=siguiente, reason=r, ordinal=i) for r, i in m])
        p.insertar('discrepancy_observation', [dict(discrepancy_id=siguiente, observation_id=x) for x, _ in o])
        p.insertar('discrepancy_value', [dict(discrepancy_id=siguiente, field=a, ordinal=b, value_json=x) for a, b, x in v])
        siguiente += 1
    for clave, actual in vigentes.items():
        if clave not in vistas:
            p.actualizar('discrepancy', {'id': actual['id']}, {'superseded_in_run': t.run}, 'superadas')


def t_sin_discrepancia(t):
    """Traduce las observaciones de discrepancy_observation; su discrepancy_id se resuelve aparte."""
    return lambda f: dict(f, observation_id=t.ids['observation'][f['observation_id']])


def aplicar(c, plan):
    for operacion, tabla, a, b in plan.operaciones:
        if operacion == 'insertar':
            for columnas, grupo in agrupar_por_columnas(a):
                insertar(c, tabla, grupo)
        elif operacion == 'actualizar':
            asignar = ', '.join(f'"{k}" = :v_{k}' for k in b)
            donde = ' AND '.join(f'"{k}" = :w_{k}' for k in a)
            r = c.execute(text(f'UPDATE "{tabla}" SET {asignar} WHERE {donde}'),
                          {**{f'v_{k}': v for k, v in b.items()}, **{f'w_{k}': v for k, v in a.items()}})
            if r.rowcount != 1:
                raise CargaRechazada(f'{tabla} {a}: se esperaba actualizar una fila y son {r.rowcount}')
        else:
            donde = ' AND '.join(f'"{k}" = :w_{k}' for k in a)
            c.execute(text(f'DELETE FROM "{tabla}" WHERE {donde}'), {f'w_{k}': v for k, v in a.items()})


def agrupar_por_columnas(filas):
    grupos = {}
    for f in filas:
        grupos.setdefault(tuple(f), []).append(f)
    return grupos.items()


# ─── Verificación ────────────────────────────────────────────────────────────────────────────────────────────────

def convocatorias_fuera_de_seleccion(c):
    """Convocatorias activas que no salen de la alineación seleccionada de su partido (deben ser ninguna). SQLite no
    admite claves foráneas parciales: esta es la regla que sustituye a la antigua FK a la selección."""
    return [r[0] for r in c.exec_driver_sql("""
        SELECT u.id FROM call_up u
        LEFT JOIN section_selection s ON s.match_id = u.match_id AND s.key = u.lineup_key AND s.sub = u.lineup_sub
        WHERE u.status = 'active' AND (s.observation_id IS NULL OR s.observation_id <> u.lineup_observation_id)
        ORDER BY u.id""")]


def vigencias_incoherentes(c):
    """Convocatorias cuyo estado no es el de su última transición (retirada <-> última transición `retired`, en la
    ejecución que la retiró; activa <-> sin transiciones o última `reactivated`)."""
    return [r[0] for r in c.exec_driver_sql("""
        SELECT u.id FROM call_up u
        LEFT JOIN call_up_change x ON x.id = (SELECT max(id) FROM call_up_change y WHERE y.call_up_id = u.id)
        WHERE (u.status = 'retired') <> (coalesce(x.change, 'reactivated') = 'retired')
           OR (u.status = 'retired' AND u.retired_in_run <> x.run_id)
        ORDER BY u.id""")]


def reglas_desfasadas(c):
    """(nombre, versión) de reglas que usa el estado vigente y que no son las del código (barato: al arrancar la API)."""
    codigo = {(n, v) for n, v, _ in carga.REGLAS}
    usadas = c.exec_driver_sql("""
        SELECT DISTINCT name, version FROM rule_set WHERE id IN (
            SELECT rule_set_id FROM section_selection UNION SELECT rule_set_id FROM match_state
            UNION SELECT rule_set_id FROM team_name UNION SELECT rule_set_id FROM call_up WHERE status = 'active'
            UNION SELECT rule_set_id FROM discrepancy WHERE superseded_in_run IS NULL)""").fetchall()
    return sorted({tuple(r) for r in usadas} - codigo)


def problemas_de_estado(c):
    """(problemas, plan en seco). Ningún problema y plan vacío = el estado guardado es el de las reglas actuales."""
    problemas = []
    rotas = sqlite.comprobar_integridad(c)
    if rotas:
        problemas.append(f'claves foráneas rotas: {[tuple(r) for r in rotas[:5]]}')
    fuera = convocatorias_fuera_de_seleccion(c)
    if fuera:
        problemas.append(f'{len(fuera)} convocatorias activas no salen de la alineación seleccionada: {fuera[:10]}')
    incoherentes = vigencias_incoherentes(c)
    if incoherentes:
        problemas.append(f'{len(incoherentes)} convocatorias con vigencia distinta de su última transición: {incoherentes[:10]}')
    desfasadas = reglas_desfasadas(c)
    if desfasadas:
        problemas.append(f'el estado usa reglas que no son las del código: {desfasadas}')
    plan = None
    try:
        filas, salida = estado_deseado(c)
        plan = planificar(c, filas, None, resumen=salida['resumen'])
    except ReglasDistintas as e:
        problemas.append(str(e))
    if plan is not None and not plan.vacio:
        problemas.append(f'la sincronización en seco da cambios: {plan.recuento()}')
    return problemas, plan


def exigir_coherencia(c):
    """Obligatoria antes de confirmar cada escritura: si falla, quien llama deshace la transacción."""
    problemas, _plan = problemas_de_estado(c)
    if problemas:
        raise EstadoIncoherente('; '.join(problemas))


# ─── Importación incremental ─────────────────────────────────────────────────────────────────────────────────────

class Presentacion:
    """Lo que trae un comando `importar`: registros de ficheros nuevos y declaraciones (también sobre ficheros ya
    conocidos), con el orden y el nombre en que se presentó cada fichero."""

    def __init__(self, c):
        self.conocidos = {r['sha256']: r for r in filas_bd(c, 'source_file', 'id')}
        numeros = [int(r['label'][1:]) for r in self.conocidos.values()
                   if r['kind'] in EVIDENCIA and r['label'][:1] == 'E' and r['label'][1:].isdigit()]
        self.siguiente = max(numeros, default=0) + 1
        self.originales, self.fichas, self.entradas, self.presentados = [], [], [], []
        self.declaraciones = defaultdict(list)  # etiqueta -> equipos nuevos
        self.por_sha = {}

    def presentar(self, ruta, tipo, equipo=None):
        ruta = Path(ruta).resolve()
        sha = hashlib.sha256(ruta.read_bytes()).hexdigest()
        conocido = self.conocidos.get(sha) or self.por_sha.get(sha)
        if conocido is not None:
            if conocido['kind'] != tipo:
                raise CargaRechazada(f'{ruta.name} ya está en la base como {conocido["kind"]}, no como {tipo}')
            if equipo is not None and equipo not in self.declaraciones[conocido['label']]:
                self.declaraciones[conocido['label']].append(equipo)
            if all(p['sha256'] != sha for p in self.presentados):
                self.presentados.append(dict(sha256=sha, nombre=ruta.name, ruta=str(ruta), etiqueta=conocido['label'],
                                             outcome='already_known' if sha in self.conocidos else 'ingested'))
            return
        etiqueta = f'E{self.siguiente}'
        self.siguiente += 1
        try:
            if tipo == 'raw':
                registros, info = importar.leer_exportacion(ruta, etiqueta, None)
                info = dict(info, kind='raw')
                self.originales.extend(registros)
            elif tipo == 'listing':
                registros, info = importar.leer_calendario(ruta, etiqueta)
                self.originales.extend(registros)
            else:
                registros, info = importar.leer_fichas(ruta, etiqueta)
                self.fichas.extend(registros)
        except (ValueError, OSError) as e:
            raise CargaRechazada(str(e)) from e
        self.entradas.append(info)
        if equipo is not None:
            self.declaraciones[etiqueta].append(equipo)
        self.por_sha[sha] = {'kind': tipo, 'label': etiqueta}
        self.presentados.append(dict(sha256=sha, nombre=ruta.name, ruta=str(ruta), etiqueta=etiqueta, outcome='ingested'))

    def manifiesto(self, existentes):
        """El manifiesto completo: los ficheros guardados y después los nuevos; una entrada por declaración activa,
        las nuevas detrás de las de su fichero (el orden en que las reconstruye reconstruccion.exportaciones)."""
        salida = [*existentes, *self.entradas]
        for etiqueta, equipos in self.declaraciones.items():
            posiciones = [i for i, e in enumerate(salida) if e['id'] == etiqueta]
            nuevos = [x for x in equipos if x not in {salida[i]['followed_team_id'] for i in posiciones}]
            if not nuevos:
                continue
            ultima = posiciones[-1]
            entradas = [dict(salida[ultima], followed_team_id=x) for x in nuevos]
            if salida[ultima]['followed_team_id'] is None:
                salida[ultima:ultima + 1] = entradas  # el fichero no tenía declaración: la sin equipo desaparece
            else:
                salida[ultima + 1:ultima + 1] = entradas
        return salida

    @property
    def cambia_evidencia(self):
        return bool(self.entradas)


@exclusivo('ruta_base')
def importar_evidencia(ruta_base, exportaciones=(), calendarios=(), fichas=(), entradas=(), comando=None):
    """`persistencia importar`: comprobaciones previas, copia verificada y una transacción (evidencia nueva,
    declaraciones, sincronización completa, verificación, ejecución confirmada). Si algo falla no se escribe nada y
    queda una ejecución `failed` con su error; la copia se conserva."""
    ruta = exigir_base(ruta_base)
    motor = sqlite.crear_motor(ruta)
    referencia = carga.referencia_del_codigo()
    try:
        with motor.begin() as c:
            reconstruccion.informe_de_carga(c)
            exigir_coherencia(c)
        copia = sqlite.crear_copia(ruta, 'antes-de-importar')
        try:
            with motor.begin() as c:
                run = nueva_ejecucion(c, 'import', referencia, comando)
                nuevos = Presentacion(c)
                for ruta_fichero in entradas:
                    nuevos.presentar(ruta_fichero, 'raw')
                for ruta_fichero, equipo in exportaciones:
                    nuevos.presentar(ruta_fichero, 'raw', equipo)
                for ruta_fichero in calendarios:
                    nuevos.presentar(ruta_fichero, 'listing')
                for ruta_fichero in fichas:
                    nuevos.presentar(ruta_fichero, 'player_profile')
                filas, salida = estado_deseado(c, nuevos, run, referencia['texto'])
                plan = planificar(c, filas, run, resumen=salida['resumen'])
                aplicar(c, plan)
                ids = {r['label']: r['id'] for r in filas_bd(c, 'source_file')}
                insertar(c, 'import_run_file', [dict(run_id=run, source_file_id=ids[x['etiqueta']], position=i,
                                                     presented_name=x['nombre'], presented_path=x['ruta'],
                                                     outcome=x['outcome']) for i, x in enumerate(nuevos.presentados, 1)])
                informe = {'copia': copia, 'cambios': plan.recuento(), 'resumen': salida['resumen'], 'codigo': referencia,
                           'ficheros': [{k: x[k] for k in ('etiqueta', 'nombre', 'outcome')} for x in nuevos.presentados]}
                if nuevos.cambia_evidencia:
                    informe['imported_at'] = ahora()
                estado = 'committed' if plan.operaciones else 'no_changes'
                # Primero se cierra la ejecución (su resumen pasa a ser el vigente) y después se verifica, dentro de
                # la misma transacción: si la verificación falla no queda nada.
                cerrar_ejecucion(c, run, estado, informe)
                exigir_coherencia(c)
        except BaseException as e:
            registrar_fallo(motor, 'import', referencia, comando, e, copia)
            raise
    finally:
        motor.dispose()
    return {'base': str(ruta), 'run': run, 'estado': estado, 'copia': copia['ruta'], 'cambios': plan.recuento(),
            'ficheros': informe['ficheros']}


@exclusivo('ruta_base')
def reconstruir(ruta_base, confirmar=False, comando=None):
    """`persistencia reconstruir`: sin confirmar, la diferencia entre lo guardado y las reglas actuales (en seco); con
    confirmar, copia verificada y una transacción que registra las reglas nuevas y aplica la sincronización completa
    como ejecución `rebuild` (selecciones que cambian -> historial; discrepancias que cambian -> superadas)."""
    ruta = exigir_base(ruta_base)
    referencia = carga.referencia_del_codigo()
    if not confirmar:
        motor = sqlite.crear_motor(ruta, solo_lectura=True)
        try:
            with motor.connect() as c:
                filas, salida = estado_deseado(c, None, None, referencia['texto'])
                plan = planificar(c, filas, None, reconstruir=True, resumen=salida['resumen'])
        finally:
            motor.dispose()
        return {'base': str(ruta), 'en_seco': True, 'cambios': plan.recuento()}
    motor = sqlite.crear_motor(ruta)
    try:
        copia = sqlite.crear_copia(ruta, 'antes-de-reconstruir')
        try:
            with motor.begin() as c:
                reconstruccion.informe_de_carga(c)
                run = nueva_ejecucion(c, 'rebuild', referencia, comando)
                filas, salida = estado_deseado(c, None, run, referencia['texto'])
                plan = planificar(c, filas, run, reconstruir=True, resumen=salida['resumen'])
                aplicar(c, plan)
                cerrar_ejecucion(c, run, 'committed' if not plan.vacio else 'no_changes',
                                 {'copia': copia, 'cambios': plan.recuento(), 'resumen': salida['resumen'],
                                  'codigo': referencia})
                exigir_coherencia(c)
        except BaseException as e:
            registrar_fallo(motor, 'rebuild', referencia, comando, e, copia)
            raise
    finally:
        motor.dispose()
    return {'base': str(ruta), 'run': run, 'copia': copia['ruta'], 'cambios': plan.recuento()}


@exclusivo('ruta_base')
def verificar(ruta_base):
    """`persistencia verificar`: solo lectura. Integridad, revisión, claves foráneas, convocatorias, versiones de
    reglas y sincronización en seco."""
    ruta = exigir_base(ruta_base)
    motor = sqlite.crear_motor(ruta, solo_lectura=True)
    try:
        with motor.connect() as c:
            integridad = c.exec_driver_sql('PRAGMA integrity_check').scalar()
            problemas, plan = problemas_de_estado(c)
            retiradas = c.exec_driver_sql("SELECT count(*) FROM call_up WHERE status = 'retired'").scalar()
    finally:
        motor.dispose()
    if integridad != 'ok':
        problemas.insert(0, f'integrity_check: {integridad}')
    return {'base': str(ruta), 'ok': not problemas, 'problemas': problemas,
            'cambios_en_seco': plan.recuento() if plan is not None else None, 'convocatorias_retiradas': retiradas}


# ─── Mantenimiento ───────────────────────────────────────────────────────────────────────────────────────────────

def revision_del_codigo():
    return ScriptDirectory.from_config(carga.configuracion_alembic(Path('.'))).get_current_head()


def revision_de(ruta):
    return sqlite.revision(ruta)


def exigir_base(ruta_base, revision=True):
    ruta = Path(ruta_base).resolve()
    if not ruta.is_file():
        raise CargaRechazada(f'No existe la base {ruta}')
    if revision and revision_de(ruta) != revision_del_codigo():
        raise CargaRechazada(f'{ruta} tiene la revisión {revision_de(ruta)}; este código necesita {revision_del_codigo()}: '
                             'ejecute `persistencia migrar` (hace una copia antes)')
    return ruta


@exclusivo('ruta_base')
def migrar(ruta_base):
    """`persistencia migrar`: copia verificada y `alembic upgrade head`. La migración comprueba claves foráneas y
    vigencia de convocatorias antes de confirmar (migraciones/env.py); después se verifica la base entera."""
    ruta = exigir_base(ruta_base, revision=False)
    antes, objetivo = revision_de(ruta), revision_del_codigo()
    if antes == objetivo:
        return {'base': str(ruta), 'revision': antes, 'migrada': False}
    en_uso = sqlite.archivos_en_uso(ruta)
    if en_uso:
        raise CargaRechazada(f'La base está en uso ({", ".join(en_uso)}): cierre la API y cualquier otro proceso')
    copia = sqlite.crear_copia(ruta, f'antes-de-migrar-{antes}')
    command.upgrade(carga.configuracion_alembic(ruta), 'head')
    return {'base': str(ruta), 'desde': antes, 'revision': revision_de(ruta), 'copia': copia['ruta'], 'migrada': True,
            'verificacion': verificar(ruta)}


@exclusivo('ruta_base')
def restaurar(ruta_copia, ruta_base, revision=None):
    """`persistencia restaurar`: solo si ningún proceso usa la base (sqlite.restaurar). La copia debe tener la
    revisión del código, salvo que se indique otra a propósito (volver a una copia anterior a una migración junto con
    el código de esa revisión)."""
    return sqlite.restaurar(ruta_copia, ruta_base, revision or revision_del_codigo())


@exclusivo('ruta_base')
def copiar(ruta_base, motivo='manual'):
    return sqlite.crear_copia(exigir_base(ruta_base, revision=False), motivo)


# ─── Ejecuciones ─────────────────────────────────────────────────────────────────────────────────────────────────

def nueva_ejecucion(c, tipo, referencia, comando):
    return c.execute(text("INSERT INTO import_run (kind, started_at, status, tool_version, command) "
                          "VALUES (:k, :t, 'running', :v, :cmd) RETURNING id"),
                     {'k': tipo, 't': ahora(), 'v': referencia['texto'], 'cmd': comando}).scalar()


def cerrar_ejecucion(c, run, estado, informe):
    c.execute(text('UPDATE import_run SET status = :e, finished_at = :t, summary = :s WHERE id = :r'),
              {'e': estado, 't': ahora(), 's': literal(informe), 'r': run})


def registrar_fallo(motor, tipo, referencia, comando, error, copia):
    """La ejecución fallida, en una transacción aparte (la de la operación ya se deshizo)."""
    with motor.begin() as c:
        c.execute(text("INSERT INTO import_run (kind, started_at, finished_at, status, tool_version, command, summary, error) "
                       "VALUES (:k, :t, :t, 'failed', :v, :cmd, :s, :e)"),
                  {'k': tipo, 't': ahora(), 'v': referencia['texto'], 'cmd': comando, 's': literal({'copia': copia}),
                   'e': f'{type(error).__name__}: {error}'})
