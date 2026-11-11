"""Carga y composición de los datos servidos por sports-data. Python 3.10+."""
import json
from datetime import date, datetime
from pathlib import Path

# Las reglas de versiones y de datos maestros son las del importador: una sola implementación.
from importar import ficha_combinada, ficha_de_entrada, rasgos_evento, validar_ficha
from queries import (
    CAMPOS_AMBITO,
    CLASES_DE_GOL,
    CORRECCIONES,
    DOMINIOS,
    ESTADISTICAS,
    ESTADISTICAS_DE_BANQUILLO,
    GRUPOS_ESTADISTICA,
    GRUPO_DE_CLAVE,
    INCIDENCIA_DE_MOTIVO,
    INDICADORES,
    MIN_CLAVES_SUSTANCIALES,
    NOTA_EXPORTACIONES,
    NOTA_FETCHED_AT,
    NOTA_JUGADORES,
    NOTA_RENDIMIENTO,
    NOTA_SUSTITUCION,
    NO_RECUENTOS,
    ORDEN_INDICADORES,
    PENDIENTES,
    SECCIONES,
    _numero,
    actuacion,
    actuaciones_del_partido,
    acumulados_jugadores,
    alineaciones_comparables,
    anterior_a,
    apartar_alineaciones_de_otros_equipos,
    comun,
    conflictos_de_estadisticas,
    consultar,
    contexto_partido,
    correcciones_del_evento,
    dato,
    desacuerdos_de_alineacion,
    detalle_partido,
    discrepancia_de,
    disponibilidad,
    entero_no_negativo,
    equipos_con_exportacion,
    estadisticas_all,
    estadisticas_de_actuacion,
    estado_de_gol,
    incoherencia_combinada,
    indexar,
    inicio,
    instante,
    jugadores_de_alineacion,
    lectura,
    marcador_valido,
    medias,
    orden_cronologico,
    participacion,
    partidos_contados,
    rendimiento,
    se_completan,
    suma,
    vacia,
    valida,
    valor,
    valores_por_partido,
    version,
    vigente,
)

# Competiciones que sirve esta versión (ver ambito.json).
AMBITO = Path(__file__).parent / 'ambito.json'

def cargar(carpeta, ambito=AMBITO, sustituciones=None):
    datos = {n: json.loads((carpeta / (n+'.json')).read_text(encoding='utf-8')) for n in ('equipos','jugadores','partidos','jugador_partido')}
    # Los originales son opcionales: sin ellos, ninguna sección de detalle figura como almacenada.
    ruta = carpeta / 'originales.json'
    datos['originales'] = indexar(json.loads(ruta.read_text(encoding='utf-8'))) if ruta.exists() else {}
    # Manifiesto y discrepancias del importador (formato 2); una importación anterior no los tiene.
    opcional = lambda nombre, defecto: json.loads((carpeta / nombre).read_text(encoding='utf-8')) if (carpeta / nombre).exists() else defecto
    datos['exportaciones'] = opcional('exportaciones.json', None)
    datos['discrepancias'] = opcional('discrepancias.json', [])
    datos['fichas_originales'] = opcional('fichas_originales.json', [])
    # Sin archivo de sustituciones, ninguna: los datos se sirven como los dejó el importador.
    return componer(datos, leer_ambito(ambito), leer_sustituciones(sustituciones) if sustituciones else [])

def componer(datos, competiciones, sustituciones):
    """Los datos que sirve la API a partir de lo almacenado (carpeta JSON o base SQL, con los mismos campos y los
    originales ya indexados): fichas validadas, ámbito y sustituciones locales. Un solo camino para ambos modos."""
    for r in datos['fichas_originales']:
        validar_ficha(r)
    datos = apartar_alineaciones_de_otros_equipos(aplicar_ambito(datos, competiciones))
    return aplicar_sustituciones(datos, sustituciones)


def leer_ambito(ruta):
    """Competiciones permitidas. Un ámbito ausente o incompleto impide arrancar: nunca se sirve todo por defecto."""
    competiciones = json.loads(Path(ruta).read_text(encoding='utf-8')).get('competiciones')
    if not isinstance(competiciones, list) or not competiciones:
        raise ValueError(f'{ruta}: se esperaba una lista "competiciones" no vacía')
    for c in competiciones:
        if not isinstance(c, dict) or not isinstance(c.get('nombre'), str) or any(type(c.get(k)) is not int for k in CAMPOS_AMBITO):
            raise ValueError(f'{ruta}: cada competición necesita "nombre" y los enteros {", ".join(CAMPOS_AMBITO)}')
    return competiciones

def competicion_original(originales, ident):
    """(unique_tournament_id, tournament_id, season_id) del registro `event` guardado o, si no lo hay, de la fila
    `listing` de un calendario normalizado (tournament_id ahí es la competición general y stage el grupo); None si
    no hay ninguno."""
    entrada = originales.get((ident, 'event'))
    if entrada is None:
        listado = (originales.get((ident, 'listing')) or {}).get('registro', {}).get('payload')
        if isinstance(listado, dict):
            return (listado.get('tournament_id'), (listado.get('stage') or {}).get('id'), listado.get('season_id'))
        return None
    payload = entrada['registro'].get('payload')
    evento = payload.get('event', payload) if isinstance(payload, dict) else {}
    torneo = evento.get('tournament') or {}
    return ((torneo.get('uniqueTournament') or {}).get('id'), torneo.get('id'), (evento.get('season') or {}).get('id'))

def competicion_de(originales, partido, competiciones):
    """La competición permitida del partido por su grupo (tournament_id) y temporada, confirmada con el `event`
    original cuando existe (si el original dice otra cosa, se excluye). Nunca por nombre ni por la competición general."""
    c = next((c for c in competiciones if (c['tournament_id'], c['season_id']) == (partido.get('tournament_id'), partido.get('season_id'))), None)
    original = competicion_original(originales, partido['match_id'])
    if c is None or (original is not None and original != tuple(c[k] for k in CAMPOS_AMBITO)):
        return None
    return c

def ficha_de_alineaciones(originales, ident, fichas=()):
    """Ficha del jugador como la construye el importador (`ficha_combinada`: campo a campo, el valor conocido más
    reciente), pero sólo desde las versiones de estas alineaciones."""
    observaciones = []
    for (_, key), entrada in originales.items():
        if key != 'lineups':
            continue
        for r in entrada['todas']:
            for lado in ('home', 'away'):
                for e in ((r.get('payload') or {}).get(lado) or {}).get('players', []):
                    if (e.get('player') or {}).get('id') == ident:
                        observaciones.append((r.get('fetched_at'), ficha_de_entrada(e, r.get('fetched_at'))))
    # Las fichas personales no dependen del equipo/competición, pero no acreditan pertenencia.
    observaciones.extend((r['fetched_at'], ficha_de_entrada(r['payload'], r['fetched_at']))
                         for r in fichas if r['player_id'] == ident)
    return ficha_combinada(observaciones)[0] if observaciones else None

def aplicar_ambito(datos, competiciones):
    """Los datos que sirve la API, limitados a las competiciones permitidas; todas las rutas parten de aquí:
    partidos, convocatorias, secciones de detalle y jugadores (sólo los observados en partidos incluidos)."""
    partidos = [m for m in datos['partidos'] if competicion_de(datos['originales'], m, competiciones)]
    incluidos = {m['match_id'] for m in partidos}
    convocatorias = [r for r in datos['jugador_partido'] if r['match_id'] in incluidos]
    originales = {clave: v for clave, v in datos['originales'].items() if clave[0] in incluidos}
    dentro = {r['player_id'] for r in convocatorias}
    fuera = {r['player_id'] for r in datos['jugador_partido'] if r['match_id'] not in incluidos}
    jugadores = []
    for p in datos['jugadores']:
        if p['player_id'] not in dentro:
            continue
        # La ficha guardada es la de la última alineación observada, quizá excluida: se rehace con las incluidas.
        if p['player_id'] in fuera:
            p = ficha_de_alineaciones(originales, p['player_id'], datos.get('fichas_originales', []))
            if p is None:
                continue
        jugadores.append(p)
    return dict(datos, partidos=partidos, jugador_partido=convocatorias, originales=originales, jugadores=jugadores,
                competiciones=competiciones, excluidos={m['match_id'] for m in datos['partidos']} - incluidos,
                jugadores_excluidos={p['player_id'] for p in datos['jugadores']} - {p['player_id'] for p in jugadores})

def leer_sustituciones(ruta):
    """Decisiones locales «este evento queda sustituido por este otro» (ver docs/importador-y-api.txt, SUSTITUCIONES LOCALES).
    Comprueba el archivo por sí mismo: campos, autorreferencias, eventos sustituidos dos veces y cadenas o ciclos
    (un evento vigente no puede estar sustituido). Cualquier error impide arrancar: nunca se aplica a medias."""
    contenido = json.loads(Path(ruta).read_text(encoding='utf-8'))
    lista = contenido.get('sustituciones') if isinstance(contenido, dict) else None
    if not isinstance(lista, list):
        raise ValueError(f'{ruta}: se esperaba una lista "sustituciones"')
    texto = lambda v: isinstance(v, str) and v.strip()
    for i, s in enumerate(lista):
        donde = f'{ruta}, sustituciones[{i}]'
        if not isinstance(s, dict) or any(type(s.get(k)) is not int or s[k] <= 0 for k in ('sustituido', 'vigente')):
            raise ValueError(f'{donde}: "sustituido" y "vigente" deben ser identificadores enteros de evento')
        procedencia = s.get('procedencia')
        if not texto(s.get('motivo')) or not isinstance(procedencia, dict) or not texto(procedencia.get('decidido_por')):
            raise ValueError(f'{donde}: faltan "motivo" o "procedencia.decidido_por"')
        try:
            date.fromisoformat(procedencia.get('fecha'))
        except (TypeError, ValueError):
            raise ValueError(f'{donde}: "procedencia.fecha" debe ser una fecha AAAA-MM-DD') from None
        if s['sustituido'] == s['vigente']:
            raise ValueError(f'{donde}: un evento no puede sustituirse a sí mismo ({s["sustituido"]})')
    sustituidos = [s['sustituido'] for s in lista]
    repetidos = sorted({e for e in sustituidos if sustituidos.count(e) > 1})
    if repetidos:
        raise ValueError(f'{ruta}: eventos sustituidos más de una vez (asignaciones contradictorias): {repetidos}')
    encadenados = sorted(set(sustituidos) & {s['vigente'] for s in lista})
    if encadenados:
        raise ValueError(f'{ruta}: eventos que son a la vez sustituidos y vigentes (cadena o ciclo): {encadenados}')
    return lista

def aplicar_sustituciones(datos, sustituciones):
    """Marca cada evento sustituido (`replaced_by`) y su vigente (`replaces`). Ambos deben ser partidos servidos de la
    misma competición, con el mismo local y el mismo visitante, y el sustituido no puede estar finalizado (sus
    resultados nunca se retiran así). No se borra nada: cambia sólo qué evento es el encuentro vigente."""
    if not sustituciones:
        return dict(datos, sustituciones=[])
    partidos = {m['match_id']: m for m in datos['partidos']}
    for s in sustituciones:
        for papel in ('sustituido', 'vigente'):
            if s[papel] not in partidos:
                fuera = 'está fuera del ámbito servido' if s[papel] in datos['excluidos'] else 'no existe en la importación'
                raise ValueError(f'sustitución {s["sustituido"]} -> {s["vigente"]}: el evento {papel} {s[papel]} {fuera}')
        antiguo, vigente = partidos[s['sustituido']], partidos[s['vigente']]
        for campo, nombre in (('tournament_id', 'competición'), ('season_id', 'temporada'),
                              ('home_team_id', 'equipo local'), ('away_team_id', 'equipo visitante')):
            if antiguo[campo] != vigente[campo]:
                raise ValueError(f'sustitución {s["sustituido"]} -> {s["vigente"]}: no coincide {nombre} '
                                 f'({antiguo[campo]} frente a {vigente[campo]})')
        if antiguo['status'] == 'finished':
            raise ValueError(f'sustitución {s["sustituido"]} -> {s["vigente"]}: el evento sustituido está finalizado')
    decision = lambda s, otro: {'match_id': otro, 'decision': 'local', 'reason': s['motivo'],
                                'decided_by': s['procedencia']['decidido_por'], 'decided_on': s['procedencia']['fecha'],
                                'evidence': s['procedencia'].get('evidencia'), 'note': NOTA_SUSTITUCION}
    marcados = {}
    for s in sustituciones:
        marcados[s['sustituido']] = dict(partidos[s['sustituido']], replaced_by=decision(s, s['vigente']))
    for s in sustituciones:
        vigente = marcados.get(s['vigente']) or dict(partidos[s['vigente']], replaces=[])
        vigente['replaces'] = vigente['replaces'] + [decision(s, s['sustituido'])]
        marcados[s['vigente']] = vigente
    return dict(datos, partidos=[marcados.get(m['match_id'], m) for m in datos['partidos']], sustituciones=sustituciones)
