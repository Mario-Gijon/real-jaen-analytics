"""Genera tests/fixtures/datos: una muestra pequeña y sintética con la forma de la salida del importador.

Equipos (salvo el Real Jaén), jugadores, partidos y estadísticas son inventados. Los ids de
competición y temporada son los de ambito.json, para que las pruebas usen el ámbito real.
Ejecutar desde la carpeta de la API:  python tests/fixtures/crear.py
"""
import json
from pathlib import Path

DESTINO = Path(__file__).parent / 'datos'
JAEN, RIVAL_A, RIVAL_B, RIVAL_COPA, RIVAL_G1 = 2841, 9001, 9002, 9003, 9004
GRUPO_2 = {'unique_tournament_id': 17073, 'tournament_id': 94191, 'season_id': 97382, 'competition': 'Primera Federacion, Group 2'}
COPA = {'unique_tournament_id': 329, 'tournament_id': 150, 'season_id': 104108, 'competition': 'Copa del Rey'}
GRUPO_1 = {'unique_tournament_id': 17073, 'tournament_id': 94190, 'season_id': 97382, 'competition': 'Primera Federacion, Group 1'}
FINALIZADO, PENDIENTE, EN_COPA, EN_GRUPO_1 = 1001, 1002, 1003, 1004
# Más partidos del Grupo 2 para el balance: empate y derrota en casa, finalizados sin marcador válido
# (ausente, booleano, negativo), un estado desconocido y pendientes (uno con marcador, que no cuenta).
EMPATE_CASA, DERROTA_CASA, SIN_MARCADOR, MARCADOR_BOOLEANO, MARCADOR_NEGATIVO = 1005, 1006, 1007, 1008, 1009
EN_JUEGO, PENDIENTE_CON_MARCADOR, APLAZADO = 1010, 1011, 1012
# Para el orden: MISMO_INSTANTE empieza a la vez que DERROTA_CASA (escrito con otro desfase horario) y tiene una
# jornada mayor; SIN_FECHA está contado pero sin fecha.
MISMO_INSTANTE, SIN_FECHA = 1013, 1014


def item(clave, local, visitante):
    return {'key': clave, 'name': clave, 'home': str(local), 'away': str(visitante), 'homeValue': local, 'awayValue': visitante}


def estadisticas(ident, periodos, fetched_at='2026-10-21T12:00:00+00:00'):
    return original(ident, 'statistics', {'statistics': [{'period': p, 'groups': g} for p, g in periodos]}, fetched_at)
UNO, SOLO_COPA, SUPLENTE = 5001, 5002, 5003
# Acumulados de jugadores: participación contradictoria, registros duplicados, un registro con el lado equivocado,
# un jugador del rival y valores fuera de dominio.
CONTRADICTORIO, DUPLICADO, RIVAL_JUGADOR, LADO_ERRONEO, VALORES_INVALIDOS = 5004, 5005, 5006, 5007, 5008
# Normalización en FINALIZADO (alineación confirmada e incidencias que cuadran con el 0-2: penalti de UNO y autogol del
# rival): sin clave de asistencia pero con una en las incidencias (contradicción), un gol en sus estadísticas que no
# está en las incidencias (contradicción), un suplente con estadísticas pero sin minutos ni entrada, y un suplente rival
# que entra sin minutos. En DERROTA_CASA, sin alineación ni incidencias, un suplente sin cobertura.
SIN_CLAVE_ASISTENCIA, GOL_SIN_EVENTO, TOQUES_SIN_ENTRADA, RIVAL_ENTRA, RIVAL_AUTOGOL, SIN_COBERTURA = 5009, 5010, 5011, 5012, 5013, 5014


def partido(ident, competicion, local, visitante, estado, marcador, kickoff, ronda, secciones):
    return {'source': 'Sofascore', 'match_id': ident, 'kickoff_utc': kickoff, 'status': estado,
            'tournament_id': competicion['tournament_id'], 'competition': competicion['competition'],
            'season_id': competicion['season_id'], 'season': '26/27', 'round': ronda,
            'home_team_id': local, 'away_team_id': visitante,
            'home_score': marcador[0] if marcador else None, 'away_score': marcador[1] if marcador else None,
            'fetched_at': '2026-10-21T12:00:00+00:00', 'available_sections': secciones}


def original(ident, key, payload, fetched_at='2026-10-21T12:00:00+00:00'):
    return {'event_id': ident, 'key': key, 'sub': '', 'fetched_at': fetched_at, 'payload': payload}


def evento(ident, competicion):
    return original(ident, 'event', {'event': {'id': ident, 'tournament': {
        'id': competicion['tournament_id'], 'name': competicion['competition'],
        'uniqueTournament': {'id': competicion['unique_tournament_id']}}, 'season': {'id': competicion['season_id'], 'year': '26/27'}}})


def jugador_sofascore(ident, nombre, posicion, altura):
    return {'id': ident, 'name': nombre, 'position': posicion, 'height': altura, 'country': {'name': 'Spain'},
            'dateOfBirthTimestamp': 946684800}


def alineacion(ident, local, visitante, fetched_at):
    def lado(jugadores):
        return {'players': [{'player': j, 'shirtNumber': n, 'substitute': s, 'position': j['position'],
                             'statistics': st} for j, n, s, st in jugadores]}
    return original(ident, 'lineups', {'confirmed': True, 'home': lado(local), 'away': lado(visitante)}, fetched_at)


SEGUN_MINUTOS = object()


def convocatoria(ident, jugador, lado, suplente, minutos, estadisticas, fetched_at='2026-10-21T12:00:00+00:00',
                 jugado=SEGUN_MINUTOS, equipo=JAEN, dorsal=None):
    return {'source': 'Sofascore', 'match_id': ident, 'team_id': equipo, 'player_id': jugador, 'side': lado,
            'source_team_id': equipo, 'substitute': suplente, 'match_position': 'M',
            'shirt_number': jugador % 100 if dorsal is None else dorsal, 'minutes_played': minutos,
            'played': ((minutos > 0) if minutos is not None else None) if jugado is SEGUN_MINUTOS else jugado,
            'statistics': estadisticas, 'fetched_at': fetched_at}


def ficha(ident, nombre, posicion, altura, pie=None):
    return {'source': 'Sofascore', 'player_id': ident, 'name': nombre, 'usual_position': posicion, 'country': 'Spain',
            'birth_date': '2000-01-01', 'height_cm': altura, 'preferred_foot': pie, 'fetched_at': '2026-10-21T12:00:00+00:00'}


def crear():
    uno = jugador_sofascore(UNO, 'Jugador Sintético Uno', 'M', 175)
    uno_copa = jugador_sofascore(UNO, 'Jugador Sintético Uno (Copa)', 'F', 199)
    solo_copa = jugador_sofascore(SOLO_COPA, 'Jugador Sólo Copa', 'D', 180)
    suplente = jugador_sofascore(SUPLENTE, 'Jugador Sintético Suplente', 'D', 182)
    stats_uno = {'minutesPlayed': 90, 'totalShots': 2, 'goals': 1, 'goalAssist': 0, 'totalPass': 30, 'accuratePass': 25}
    # Estadísticas sustanciales sin la clave goals (como hace la fuente con quien no marca) ni goalAssist.
    stats_sin_asistencia = {'minutesPlayed': 90, 'totalShots': 0, 'totalPass': 40, 'accuratePass': 35, 'touches': 55}
    stats_gol_sin_evento = {'minutesPlayed': 90, 'totalShots': 3, 'goals': 1, 'goalAssist': 0, 'totalPass': 12, 'touches': 30}
    stats_toques = {'totalShots': 0, 'statisticsType': {'statisticsType': 'player'}, 'touches': 3, 'totalPass': 2}
    stats_autogol = {'minutesPlayed': 90, 'totalShots': 0, 'goalAssist': 0, 'totalPass': 20, 'touches': 40, 'ownGoals': 1}
    datos = {
        'equipos': [{'source': 'Sofascore', 'team_id': t, 'name': n} for t, n in
                    ((JAEN, 'Real Jaén'), (RIVAL_A, 'Club Sintético A'), (RIVAL_B, 'Club Sintético B'),
                     (RIVAL_COPA, 'Club Sintético Copa'), (RIVAL_G1, 'Club Sintético Grupo 1'))],
        'partidos': [
            partido(FINALIZADO, GRUPO_2, RIVAL_A, JAEN, 'finished', (0, 2), '2026-08-30T17:15:00+00:00', 1,
                    ['event', 'statistics', 'lineups', 'incidents']),
            partido(PENDIENTE, GRUPO_2, JAEN, RIVAL_B, 'notstarted', None, '2026-11-07T15:30:00+00:00', 11, ['event']),
            partido(EN_COPA, COPA, RIVAL_COPA, JAEN, 'finished', (1, 1), '2026-10-28T19:00:00+00:00', 1, ['event', 'lineups']),
            partido(EN_GRUPO_1, GRUPO_1, RIVAL_G1, JAEN, 'finished', (0, 0), '2026-09-14T17:00:00+00:00', 3, ['event']),
            partido(EMPATE_CASA, GRUPO_2, JAEN, RIVAL_B, 'finished', (1, 1), '2026-09-06T17:00:00+00:00', 2, ['event']),
            partido(DERROTA_CASA, GRUPO_2, JAEN, RIVAL_A, 'finished', (0, 3), '2026-09-20T17:00:00+00:00', 4, ['event']),
            partido(SIN_MARCADOR, GRUPO_2, RIVAL_B, JAEN, 'finished', None, '2026-09-27T17:00:00+00:00', 5, ['event']),
            partido(MARCADOR_BOOLEANO, GRUPO_2, RIVAL_A, JAEN, 'finished', (True, 0), '2026-10-04T17:00:00+00:00', 6, ['event']),
            partido(MARCADOR_NEGATIVO, GRUPO_2, JAEN, RIVAL_B, 'finished', (-1, 2), '2026-10-11T17:00:00+00:00', 7, ['event']),
            partido(EN_JUEGO, GRUPO_2, RIVAL_A, JAEN, 'inprogress', (0, 0), '2026-10-18T17:00:00+00:00', 8, ['event']),
            partido(PENDIENTE_CON_MARCADOR, GRUPO_2, RIVAL_B, JAEN, 'notstarted', (0, 0), '2026-11-14T17:00:00+00:00', 12, ['event']),
            partido(APLAZADO, GRUPO_2, JAEN, RIVAL_A, 'postponed', None, None, 13, ['event']),
            partido(MISMO_INSTANTE, GRUPO_2, RIVAL_B, JAEN, 'finished', (0, 1), '2026-09-20T19:00:00+02:00', 9, ['event']),
            partido(SIN_FECHA, GRUPO_2, JAEN, RIVAL_A, 'finished', (2, 0), None, 10, ['event']),
        ],
        # La ficha guardada de UNO es la más reciente (la de Copa): la API debe rehacerla con el Grupo 2.
        'jugadores': [
            {'source': 'Sofascore', 'player_id': UNO, 'name': 'Jugador Sintético Uno (Copa)', 'usual_position': 'F',
             'country': 'Spain', 'birth_date': '2000-01-01', 'height_cm': 199, 'preferred_foot': None,
             'fetched_at': '2026-10-29T12:00:00+00:00'},
            {'source': 'Sofascore', 'player_id': SOLO_COPA, 'name': 'Jugador Sólo Copa', 'usual_position': 'D',
             'country': 'Spain', 'birth_date': '2000-01-01', 'height_cm': 180, 'preferred_foot': None,
             'fetched_at': '2026-10-29T12:00:00+00:00'},
            {'source': 'Sofascore', 'player_id': SUPLENTE, 'name': 'Jugador Sintético Suplente', 'usual_position': 'D',
             'country': 'Spain', 'birth_date': '2000-01-01', 'height_cm': 182, 'preferred_foot': None,
             'fetched_at': '2026-10-21T12:00:00+00:00'},
            ficha(CONTRADICTORIO, 'Jugador Contradictorio', 'D', 178, 'Right'),
            ficha(DUPLICADO, 'Jugador Duplicado', 'F', None),
            ficha(RIVAL_JUGADOR, 'Jugador del Rival', 'F', 181, 'Left'),
            ficha(LADO_ERRONEO, 'Jugador Lado Erróneo', 'M', 170),
            # Altura y pie fuera de dominio: sin dato.
            ficha(VALORES_INVALIDOS, 'Jugador Valores Inválidos', 'G', True, 5),
            ficha(SIN_CLAVE_ASISTENCIA, 'Jugador Sin Clave de Asistencia', 'M', 177),
            ficha(GOL_SIN_EVENTO, 'Jugador Gol Sin Evento', 'F', 180),
            ficha(TOQUES_SIN_ENTRADA, 'Jugador Toques Sin Entrada', 'M', 172),
            ficha(RIVAL_ENTRA, 'Rival que Entra', 'F', 179),
            ficha(RIVAL_AUTOGOL, 'Rival del Autogol', 'D', 186),
            ficha(SIN_COBERTURA, 'Jugador Sin Cobertura', 'D', 181),
        ],
        'jugador_partido': [
            convocatoria(FINALIZADO, UNO, 'away', False, 90, stats_uno, '2026-10-21T12:00:00+00:00'),
            convocatoria(FINALIZADO, SUPLENTE, 'away', True, None, {'totalShots': 0}, '2026-10-21T12:00:00+00:00'),
            convocatoria(EN_COPA, UNO, 'away', False, 90, {'minutesPlayed': 90}, '2026-10-29T12:00:00+00:00'),
            convocatoria(EN_COPA, SOLO_COPA, 'away', False, 90, {'minutesPlayed': 90}, '2026-10-29T12:00:00+00:00'),
            # Partidos no contados: finalizado sin marcador válido y alineación previa de un pendiente.
            convocatoria(SIN_MARCADOR, UNO, 'away', False, 90, {'minutesPlayed': 90, 'goals': 2, 'goalAssist': 0}),
            convocatoria(PENDIENTE, UNO, 'home', False, None, {}),
            # EMPATE_CASA (local): suplente con minutos y asistencia, sin clave de goles (ausente, no 0); played true con
            # 0 minutos (contradicción); un registro del Real Jaén en el lado visitante (se descarta) y uno del rival.
            convocatoria(EMPATE_CASA, SUPLENTE, 'home', True, 25, {'minutesPlayed': 25, 'goalAssist': 1}),
            convocatoria(EMPATE_CASA, CONTRADICTORIO, 'home', False, 0, {'minutesPlayed': 0, 'goals': 0, 'goalAssist': 0}, jugado=True),
            convocatoria(EMPATE_CASA, LADO_ERRONEO, 'away', False, 90, {'minutesPlayed': 90, 'goals': 0, 'goalAssist': 0}),
            convocatoria(EMPATE_CASA, RIVAL_JUGADOR, 'away', False, 90, {'minutesPlayed': 90, 'goals': 1}, equipo=RIVAL_B),
            # DERROTA_CASA (local): played false con minutos (contradicción) y un registro duplicado idéntico.
            convocatoria(DERROTA_CASA, CONTRADICTORIO, 'home', True, 30, {'minutesPlayed': 30, 'goals': 1}, jugado=False),
            convocatoria(DERROTA_CASA, DUPLICADO, 'home', False, 90, {'minutesPlayed': 90, 'goals': 0, 'goalAssist': 0}, dorsal=9),
            convocatoria(DERROTA_CASA, DUPLICADO, 'home', False, 90, {'minutesPlayed': 90, 'goals': 0, 'goalAssist': 0}, dorsal=9),
            # MISMO_INSTANTE (visitante): ceros explícitos; duplicado con minutos distintos (sin dato de minutos) y otro
            # dorsal; valores fuera de dominio (suplente texto, minutos booleanos, goles negativos, asistencia decimal).
            convocatoria(MISMO_INSTANTE, CONTRADICTORIO, 'away', False, 90, {'minutesPlayed': 90, 'goals': 0, 'goalAssist': 0}),
            convocatoria(MISMO_INSTANTE, DUPLICADO, 'away', False, 80, {'minutesPlayed': 80, 'goalAssist': 0}, jugado=True, dorsal=11),
            convocatoria(MISMO_INSTANTE, DUPLICADO, 'away', False, 85, {'minutesPlayed': 85, 'goalAssist': 0}, jugado=True, dorsal=11),
            convocatoria(MISMO_INSTANTE, VALORES_INVALIDOS, 'away', 'si', True, {'goals': -1, 'goalAssist': 1.0}, jugado=True, dorsal=True),
            # SIN_FECHA (contado) no tiene convocatorias del Real Jaén.
            convocatoria(FINALIZADO, SIN_CLAVE_ASISTENCIA, 'away', False, 90, stats_sin_asistencia),
            convocatoria(FINALIZADO, GOL_SIN_EVENTO, 'away', False, 90, stats_gol_sin_evento),
            convocatoria(FINALIZADO, TOQUES_SIN_ENTRADA, 'away', True, None, stats_toques),
            convocatoria(FINALIZADO, RIVAL_ENTRA, 'home', True, None, {'totalShots': 0}, equipo=RIVAL_A),
            convocatoria(FINALIZADO, RIVAL_AUTOGOL, 'home', False, 90, stats_autogol, equipo=RIVAL_A),
            convocatoria(DERROTA_CASA, SIN_COBERTURA, 'home', True, None, {'totalShots': 0}),
        ],
        'originales': [
            evento(FINALIZADO, GRUPO_2), evento(PENDIENTE, GRUPO_2), evento(EN_COPA, COPA), evento(EN_GRUPO_1, GRUPO_1),
            # Visitante: lo propio es `away`. ALL y 1ST distintos (nunca se suman); una clave repetida con el mismo valor.
            estadisticas(FINALIZADO, [
                ('ALL', [{'groupName': 'Match overview', 'statisticsItems': [
                    item('ballPossession', 40, 60), item('expectedGoals', 0.8, 1.5), item('totalShotsOnGoal', 9, 12),
                    item('cornerKicks', 4, 5)]},
                    {'groupName': 'Shots', 'statisticsItems': [
                        item('expectedGoals', 0.8, 1.5), item('totalShotsOnGoal', 9, 12), item('shotsOnGoal', 3, 2)]}]),
                ('1ST', [{'groupName': 'Match overview', 'statisticsItems': [
                    item('ballPossession', 45, 55), item('expectedGoals', 0.3, 0.7), item('totalShotsOnGoal', 4, 6),
                    item('shotsOnGoal', 1, 1), item('cornerKicks', 2, 2)]}]),
            ]),
            # Local: ceros reales (xG y tiros), posesión booleana (no válida), tiros a puerta contradictorios entre
            # grupos (se descartan) y sin córners.
            estadisticas(EMPATE_CASA, [('ALL', [
                {'groupName': 'Match overview', 'statisticsItems': [
                    item('ballPossession', True, 45), item('expectedGoals', 0.0, 0.9), item('totalShotsOnGoal', 0, 7),
                    item('shotsOnGoal', 1, 2)]},
                {'groupName': 'Shots', 'statisticsItems': [item('shotsOnGoal', 2, 2)]}])]),
            # Sección guardada sin contenido: no son ceros.
            original(DERROTA_CASA, 'statistics', {'statistics': []}),
            original(FINALIZADO, 'incidents', {'incidents': [
                {'incidentType': 'period', 'text': 'FT', 'homeScore': 0, 'awayScore': 2},
                {'incidentType': 'goal', 'incidentClass': 'penalty', 'time': 30, 'isHome': False, 'player': {'id': UNO},
                 'assist1': {'id': SIN_CLAVE_ASISTENCIA}},
                # Autogol del rival: suma al Real Jaén y no es gol de ningún jugador.
                {'incidentType': 'goal', 'incidentClass': 'ownGoal', 'time': 60, 'isHome': True, 'player': {'id': RIVAL_AUTOGOL}},
                {'incidentType': 'substitution', 'incidentClass': 'regular', 'time': 70, 'isHome': True,
                 'playerIn': {'id': RIVAL_ENTRA}, 'playerOut': {'id': 9101}},
                # Una tanda de penaltis no es un gol del jugador.
                {'incidentType': 'penaltyShootout', 'incidentClass': 'scored', 'isHome': False, 'player': {'id': UNO}},
            ]}),
            alineacion(FINALIZADO,
                       [(jugador_sofascore(RIVAL_AUTOGOL, 'Rival del Autogol', 'D', 186), 4, False, stats_autogol),
                        (jugador_sofascore(RIVAL_ENTRA, 'Rival que Entra', 'F', 179), 18, True, {'totalShots': 0})],
                       [(uno, 10, False, stats_uno), (suplente, 20, True, {'totalShots': 0}),
                        (jugador_sofascore(SIN_CLAVE_ASISTENCIA, 'Jugador Sin Clave de Asistencia', 'M', 177), 6, False, stats_sin_asistencia),
                        (jugador_sofascore(GOL_SIN_EVENTO, 'Jugador Gol Sin Evento', 'F', 180), 7, False, stats_gol_sin_evento),
                        (jugador_sofascore(TOQUES_SIN_ENTRADA, 'Jugador Toques Sin Entrada', 'M', 172), 21, True, stats_toques)],
                       '2026-10-21T12:00:00+00:00'),
            alineacion(EN_COPA, [], [(uno_copa, 10, False, {'minutesPlayed': 90}), (solo_copa, 30, False, {'minutesPlayed': 90})],
                       '2026-10-29T12:00:00+00:00'),
        ],
    }
    # Manifiesto como el del importador: la muestra es la exportación propia del Real Jaén.
    datos['exportaciones'] = {'format': 2, 'imported_at': '2026-10-22T09:00:00+00:00', 'followed_team_ids': [JAEN],
                              'exports': [{'id': 'E1', 'file': 'muestra-sintetica.jsonl', 'sha256': None, 'followed_team_id': JAEN,
                                           'records': len(datos['originales']), 'events': len({o['event_id'] for o in datos['originales']}),
                                           'invalid_fetched_at': 0, 'fetched_from': '2026-10-21T12:00:00+00:00',
                                           'fetched_to': '2026-10-29T12:00:00+00:00'}]}
    DESTINO.mkdir(exist_ok=True)
    for nombre, contenido in datos.items():
        (DESTINO / f'{nombre}.json').write_text(json.dumps(contenido, ensure_ascii=False, indent=1) + '\n', encoding='utf-8', newline='\n')


if __name__ == '__main__':
    crear()
