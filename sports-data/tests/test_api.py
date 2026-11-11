"""Pruebas de las reglas de consulta con la muestra sintética de tests/fixtures/datos.

La interfaz HTTP y su ciclo de vida se verifican en tests/test_fastapi_api.py.
Ejecutar desde la carpeta de la API:  python -m unittest discover -s tests -v
Para regenerar la muestra:  python tests/fixtures/crear.py
"""
import copy
import json
import sys
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(Path(__file__).resolve().parent / 'fixtures'))
import api  # noqa: E402
import crear  # noqa: E402

MUESTRA = Path(__file__).resolve().parent / 'fixtures' / 'datos'
DATOS = api.cargar(MUESTRA)
SECCIONES = ('statistics', 'lineups', 'incidents')


def consultar(ruta):
    return api.consultar(DATOS, ruta)


class Rutas(unittest.TestCase):
    def test_health_y_equipo(self):
        self.assertEqual(consultar('/health'), (200, {'data': {'status': 'ok'}}))
        self.assertEqual(consultar(f'/api/v1/teams/{crear.JAEN}')[1]['data']['name'], 'Real Jaén')

    def test_errores(self):
        self.assertEqual(consultar('/api/v1/teams/1')[0], 404)
        self.assertEqual(consultar('/api/v1/players/abc')[0], 400)
        self.assertEqual(consultar(f'/api/v1/teams/{crear.JAEN}/otra')[0], 404)
        self.assertEqual(consultar('/api/v1/otra/1')[0], 404)
        self.assertEqual(consultar('/api/v1/matches/1/otra'), (404, {'error': 'Partido no encontrado'}))

    def test_partido_y_secciones(self):
        estado, cuerpo = consultar(f'/api/v1/matches/{crear.FINALIZADO}')
        self.assertEqual((estado, cuerpo['data']['home_team_name']), (200, 'Club Sintético A'))
        self.assertEqual(cuerpo['meta']['sections'], {s: 'stored' for s in SECCIONES})
        estado, cuerpo = consultar(f'/api/v1/matches/{crear.FINALIZADO}/lineups')
        self.assertEqual((estado, cuerpo['meta']['availability'], cuerpo['meta']['versions']), (200, 'stored', 1))
        # Un partido pendiente existe sin secciones y sin marcador inventado.
        estado, cuerpo = consultar(f'/api/v1/matches/{crear.PENDIENTE}')
        self.assertEqual((cuerpo['data']['home_score'], cuerpo['meta']['sections']), (None, {s: 'not_stored' for s in SECCIONES}))
        self.assertEqual(consultar(f'/api/v1/matches/{crear.PENDIENTE}/statistics')[1]['data'], None)


class Ambito(unittest.TestCase):
    """Sólo Primera Federación, Grupo 2 (26/27), según el ambito.json del proyecto."""

    def test_el_ambito_del_proyecto_se_publica(self):
        self.assertEqual(consultar('/api/v1/scope')[1]['data']['competitions'], [
            {'name': 'Primera Federación, Grupo 2', 'season': '26/27',
             'unique_tournament_id': 17073, 'tournament_id': 94191, 'season_id': 97382}])

    def test_equipos_con_exportacion_propia_segun_el_manifiesto(self):
        # Sólo el Real Jaén; los rivales observados en sus partidos no tienen exportación propia.
        equipos = consultar('/api/v1/scope')[1]['data']['teams_with_own_export']
        self.assertEqual([(e['team_id'], e['name']) for e in equipos], [(crear.JAEN, 'Real Jaén')])
        self.assertEqual(equipos[0]['exports'][0]['file'], 'muestra-sintetica.jsonl')

    def test_solo_partidos_del_grupo_2(self):
        partidos = consultar(f'/api/v1/teams/{crear.JAEN}/matches')[1]['data']
        self.assertEqual({m['tournament_id'] for m in partidos}, {94191})
        self.assertNotIn(crear.EN_COPA, {m['match_id'] for m in partidos})
        self.assertNotIn(crear.EN_GRUPO_1, {m['match_id'] for m in partidos})
        self.assertEqual(len(partidos), 12)

    def test_copa_y_otro_grupo_no_se_sirven_ni_por_su_identificador(self):
        # El Grupo 1 comparte competición general (17073) y temporada: no basta con ellas.
        for ident in (crear.EN_COPA, crear.EN_GRUPO_1):
            for ruta in (f'/api/v1/matches/{ident}', *(f'/api/v1/matches/{ident}/{s}' for s in SECCIONES)):
                self.assertEqual(consultar(ruta), (404, {'error': 'Partido fuera del ámbito de esta versión'}), ruta)

    def test_jugadores_y_convocatorias_solo_del_ambito(self):
        cuerpo = consultar(f'/api/v1/teams/{crear.JAEN}/players')[1]
        self.assertEqual(sorted(p['player_id'] for p in cuerpo['data']),
                         [crear.UNO, crear.SUPLENTE, crear.CONTRADICTORIO, crear.DUPLICADO, crear.LADO_ERRONEO, crear.VALORES_INVALIDOS,
                          crear.SIN_CLAVE_ASISTENCIA, crear.GOL_SIN_EVENTO, crear.TOQUES_SIN_ENTRADA, crear.SIN_COBERTURA])
        self.assertEqual(consultar(f'/api/v1/players/{crear.SOLO_COPA}'), (404, {'error': 'Jugador fuera del ámbito de esta versión'}))
        partidos = consultar(f'/api/v1/players/{crear.UNO}/matches')[1]['data']
        self.assertEqual([r['match_id'] for r in partidos], [crear.FINALIZADO, crear.SIN_MARCADOR, crear.PENDIENTE])

    def test_la_ficha_no_sale_de_un_partido_excluido(self):
        # La ficha guardada es la de Copa (más reciente): se rehace con la alineación del Grupo 2.
        ficha = consultar(f'/api/v1/players/{crear.UNO}')[1]['data']
        self.assertEqual((ficha['name'], ficha['usual_position'], ficha['height_cm'], ficha['birth_date']),
                         ('Jugador Sintético Uno', 'M', 175, '2000-01-01'))
        self.assertEqual(ficha['fetched_at'], '2026-10-21T12:00:00+00:00')
        # Sin convocatorias excluidas, la ficha guardada se sirve tal cual.
        self.assertEqual(consultar(f'/api/v1/players/{crear.SUPLENTE}')[1]['data']['name'], 'Jugador Sintético Suplente')

    def test_los_datos_cargados_conservan_los_excluidos(self):
        # Excluir no es borrar: lo excluido sigue en los JSON de origen.
        partidos = json.loads((MUESTRA / 'partidos.json').read_text(encoding='utf-8'))
        self.assertEqual(len(partidos), 14)
        self.assertEqual(DATOS['excluidos'], {crear.EN_COPA, crear.EN_GRUPO_1})


class Rendimiento(unittest.TestCase):
    """Balance del equipo con los finalizados del ámbito con marcador válido; sin clasificación ni puntos."""

    def competicion(self, equipo=crear.JAEN):
        estado, cuerpo = consultar(f'/api/v1/teams/{equipo}/performance')
        self.assertEqual(estado, 200)
        self.assertIn('no es necesariamente la temporada completa', cuerpo['meta']['note'])
        [competicion] = cuerpo['data']['competitions']
        return competicion

    def test_una_entrada_por_competicion_del_ambito_con_sus_ids(self):
        c = self.competicion()
        self.assertEqual({k: c[k] for k in ('name', 'season', 'unique_tournament_id', 'tournament_id', 'season_id')},
                         {'name': 'Primera Federación, Grupo 2', 'season': '26/27',
                          'unique_tournament_id': 17073, 'tournament_id': 94191, 'season_id': 97382})
        self.assertNotIn('points', c['record'])
        self.assertNotIn('rank', c)

    def test_recuentos_por_estado(self):
        # Copa y Grupo 1, aunque finalizados y con marcador, no están en el ámbito.
        self.assertEqual(self.competicion()['matches'], {
            'in_scope': 12, 'finished': 8, 'pending': 3, 'counted': 5,
            'finished_without_valid_score': 3, 'excluded_by_version_conflict': 0, 'unknown_status': 1, 'replaced_locally': 0})

    def test_balance_casa_fuera_y_diferencia(self):
        c = self.competicion()
        self.assertEqual(c['record'], {'played': 5, 'wins': 3, 'draws': 1, 'losses': 1,
                                       'goals_for': 6, 'goals_against': 4, 'goal_difference': 2})
        self.assertEqual(c['venues'], {'home': {'wins': 1, 'draws': 1, 'losses': 1}, 'away': {'wins': 2, 'draws': 0, 'losses': 0}})

    def test_resultados_por_fecha_desde_el_lado_del_equipo(self):
        resultados = self.competicion()['results']
        self.assertEqual([(r['match']['match_id'], r['side'], r['opponent_team_id'], r['goals_for'], r['goals_against'], r['result'])
                          for r in resultados],
                         [(crear.FINALIZADO, 'away', crear.RIVAL_A, 2, 0, 'win'),
                          (crear.EMPATE_CASA, 'home', crear.RIVAL_B, 1, 1, 'draw'),
                          # Mismo instante (17:00Z y 19:00+02:00): decide el id, no la jornada ni el texto de la fecha.
                          (crear.DERROTA_CASA, 'home', crear.RIVAL_A, 0, 3, 'loss'),
                          (crear.MISMO_INSTANTE, 'away', crear.RIVAL_B, 1, 0, 'win'),
                          # Sin fecha, al final.
                          (crear.SIN_FECHA, 'home', crear.RIVAL_A, 2, 0, 'win')])
        # Ningún marcador inválido, pendiente o en juego entra como resultado (ni como 0-0).
        excluidos = {crear.SIN_MARCADOR, crear.MARCADOR_BOOLEANO, crear.MARCADOR_NEGATIVO, crear.EN_JUEGO,
                     crear.PENDIENTE_CON_MARCADOR, crear.APLAZADO, crear.PENDIENTE}
        self.assertFalse(excluidos & {r['match']['match_id'] for r in resultados})

    def test_xg_por_partido_reutiliza_la_orientacion_y_la_cobertura_del_calculo(self):
        resultados = self.competicion()['results']
        por_id = {resultado['match']['match_id']: resultado for resultado in resultados}
        # FINALIZADO es visitante: los valores de xG locales/visitantes se expresan desde el lado del Real Jaén.
        self.assertEqual((por_id[crear.FINALIZADO]['xg_for'], por_id[crear.FINALIZADO]['xg_against']), (1.5, 0.8))
        # EMPATE_CASA es local y conserva el cero real, distinto de una ausencia.
        self.assertEqual((por_id[crear.EMPATE_CASA]['xg_for'], por_id[crear.EMPATE_CASA]['xg_against']), (0.0, 0.9))
        # Un partido sin estadísticas queda ausente en ambos sentidos, nunca como cero.
        self.assertEqual((por_id[crear.DERROTA_CASA]['xg_for'], por_id[crear.DERROTA_CASA]['xg_against']), (None, None))

    def test_xg_contradictorio_entre_grupos_se_sigue_excluyendo(self):
        originales = copy.deepcopy(DATOS['originales'])
        payload = originales[(crear.FINALIZADO, 'statistics')]['registro']['payload']
        duplicate = payload['statistics'][0]['groups'][1]['statisticsItems'][0]
        self.assertEqual(duplicate['key'], 'expectedGoals')
        duplicate['awayValue'] = 1.6
        valores = api.valores_por_partido(originales, {
            'match': {'match_id': crear.FINALIZADO}, 'side': 'away', 'goals_for': 2, 'goals_against': 0,
        })
        self.assertEqual((valores['xg'], valores['xgAgainst']), (None, None))

    def media(self, valor, partidos, de):
        return {'value': valor, 'matches': partidos, 'of': de}

    def assertMedias(self, recibidas, esperadas):
        self.assertEqual(set(recibidas), set(esperadas))
        for indicador, esperada in esperadas.items():
            recibida = recibidas[indicador]
            self.assertEqual((recibida['matches'], recibida['of']), (esperada['matches'], esperada['of']), indicador)
            if esperada['value'] is None:
                self.assertIsNone(recibida['value'], indicador)
            else:
                self.assertAlmostEqual(recibida['value'], esperada['value'], places=12, msg=indicador)

    def test_produccion_y_concesion_de_la_temporada(self):
        # 5 contados. Con estadísticas: FINALIZADO (visitante, completas) y EMPATE_CASA (local, parciales);
        # DERROTA_CASA tiene la sección vacía y MISMO_INSTANTE y SIN_FECHA no la tienen: ausentes, nunca ceros.
        m = self.media
        self.assertMedias(self.competicion()['output']['season'], {
            'goals': m(6 / 5, 5, 5), 'goalsAgainst': m(4 / 5, 5, 5),
            'xg': m((1.5 + 0.0) / 2, 2, 5), 'xgAgainst': m((0.8 + 0.9) / 2, 2, 5),
            'shots': m((12 + 0) / 2, 2, 5), 'shotsAgainst': m((9 + 7) / 2, 2, 5),
            # Tiros a puerta de EMPATE_CASA contradictorios entre grupos: descartados en ambos lados.
            'shotsOnTarget': m(2, 1, 5), 'shotsOnTargetAgainst': m(3, 1, 5),
            # Posesión booleana no válida: sólo cuenta la de FINALIZADO (media de porcentajes, no suma).
            'possession': m(60, 1, 5), 'corners': m(5, 1, 5),
        })

    def test_ultimos_3_primero_los_partidos_despues_la_cobertura(self):
        salida = self.competicion()['output']
        # Los tres contados con fecha más recientes; el sin fecha no entra y no se buscan anteriores para completar.
        self.assertEqual(salida['last3']['match_ids'], [crear.EMPATE_CASA, crear.DERROTA_CASA, crear.MISMO_INSTANTE])
        self.assertEqual(salida['undated'], 1)
        m = self.media
        self.assertMedias(salida['last3']['values'], {
            'goals': m(2 / 3, 3, 3), 'goalsAgainst': m(4 / 3, 3, 3),
            # Cero real de EMPATE_CASA: media 0 con dato, distinta de ausencia.
            'xg': m(0.0, 1, 3), 'xgAgainst': m(0.9, 1, 3), 'shots': m(0, 1, 3), 'shotsAgainst': m(7, 1, 3),
            'shotsOnTarget': m(None, 0, 3), 'shotsOnTargetAgainst': m(None, 0, 3),
            'possession': m(None, 0, 3), 'corners': m(None, 0, 3),
        })

    def test_estadisticas_all_y_dominios(self):
        originales = DATOS['originales']
        self.assertEqual(api.estadisticas_all(originales, crear.FINALIZADO)['expectedGoals'], (0.8, 1.5))
        self.assertIsNone(api.estadisticas_all(originales, crear.DERROTA_CASA))
        self.assertIsNone(api.estadisticas_all(originales, crear.MISMO_INSTANTE))
        self.assertNotIn('shotsOnGoal', api.estadisticas_all(originales, crear.EMPATE_CASA))
        for clave, valor, valido in (('expectedGoals', 0.0, True), ('expectedGoals', -0.1, False), ('expectedGoals', float('nan'), False),
                                     ('expectedGoals', float('inf'), False), ('expectedGoals', True, False), ('expectedGoals', '1.2', False),
                                     ('totalShotsOnGoal', 0, True), ('totalShotsOnGoal', 1.5, False), ('totalShotsOnGoal', False, False),
                                     ('ballPossession', 0, True), ('ballPossession', 100, True), ('ballPossession', 50.5, True),
                                     ('ballPossession', 101, False), ('ballPossession', -1, False), ('cornerKicks', -1, False)):
            self.assertIs(api.DOMINIOS[clave](valor), valido, (clave, valor))

    def test_marcador_valido(self):
        for valor, valido in ((0, True), (3, True), (True, False), (False, False), (-1, False), (1.0, False), ('1', False), (None, False)):
            self.assertIs(api.marcador_valido(valor), valido, repr(valor))

    def test_equipo_sin_partidos_en_el_ambito_y_equipo_desconocido(self):
        # Sólo jugó en Copa: cero partidos, que no es un error.
        c = self.competicion(crear.RIVAL_COPA)
        self.assertEqual((c['matches']['in_scope'], c['record']['played'], c['results']), (0, 0, []))
        # Sin partidos: medias ausentes, nunca 0.
        self.assertEqual(c['output']['season']['xg'], {'value': None, 'matches': 0, 'of': 0})
        self.assertEqual(c['output']['last3']['match_ids'], [])
        self.assertEqual(consultar('/api/v1/teams/1/performance'), (404, {'error': 'Entidad no encontrada'}))


class Normalizacion(unittest.TestCase):
    """Una convocatoria normalizada con las reglas de esta fuente: participación, minutos, goles y asistencias."""

    def actuacion(self, partido, jugador):
        return api.actuaciones_del_partido(DATOS, partido)[jugador]

    def resumen(self, partido, jugador):
        a = self.actuacion(partido, jugador)
        return (a['participation'], a['basis'], a['minutes']['value'], a['goals'], a['assists'], a['issues'])

    def test_goles_y_asistencias_omitidos_son_cero_en_una_actuacion_sustancial(self):
        # Sin clave goals en estadísticas sustanciales: 0 por regla de esta fuente, no un 0 explícito del payload.
        a = self.actuacion(crear.FINALIZADO, crear.RIVAL_AUTOGOL)
        self.assertEqual((a['goals'], a['assists']), ({'value': 0, 'source': 'omitted_zero'}, {'value': 0, 'source': 'reported'}))
        # El explícito se conserva como informado.
        self.assertEqual(self.actuacion(crear.FINALIZADO, crear.UNO)['goals'], {'value': 1, 'source': 'reported'})

    def test_sin_estadisticas_sustanciales_la_omision_no_es_cero(self):
        # 25 minutos con sólo goalAssist: no hay base para leer la ausencia de goals como 0.
        self.assertEqual(self.resumen(crear.EMPATE_CASA, crear.SUPLENTE),
                         ('played', 'minutes', 25, {'value': None, 'source': None}, {'value': 1, 'source': 'reported'}, []))

    def test_el_penalti_cuenta_y_el_autogol_y_la_tanda_no(self):
        contexto = api.contexto_partido(DATOS, crear.FINALIZADO)
        # Penalti de UNO; el autogol no es gol de su autor y la tanda no es un incidentType goal.
        self.assertEqual(contexto['goles'], {crear.UNO: 1})
        self.assertEqual(contexto['asistencias'], {crear.SIN_CLAVE_ASISTENCIA: 1})
        self.assertEqual(self.actuacion(crear.FINALIZADO, crear.UNO)['issues'], [])

    def test_lo_que_contradice_a_las_incidencias_queda_sin_dato_y_como_incidencia(self):
        # Estadísticas sin asistencia frente a una asistencia en las incidencias; un gol que las incidencias no tienen.
        self.assertEqual(self.resumen(crear.FINALIZADO, crear.SIN_CLAVE_ASISTENCIA)[4:],
                         ({'value': None, 'source': None}, ['assists_mismatch']))
        self.assertEqual(self.resumen(crear.FINALIZADO, crear.GOL_SIN_EVENTO)[3:],
                         ({'value': None, 'source': None}, {'value': 0, 'source': 'reported'}, ['goals_mismatch']))

    def test_sin_cuadrar_con_el_marcador_no_se_contrasta(self):
        # EMPATE_CASA no tiene incidencias: no hay contraste (ni contradicción).
        self.assertIsNone(api.contexto_partido(DATOS, crear.EMPATE_CASA)['goles'])

    def test_suplente_no_utilizado_con_cobertura_de_sustituciones(self):
        # Alineación confirmada, las entradas del lado coinciden con los suplentes con minutos y sólo el resto de banquillo.
        self.assertEqual(self.resumen(crear.FINALIZADO, crear.SUPLENTE),
                         ('unused', 'unused_substitute', 0, {'value': 0, 'source': 'unused'}, {'value': 0, 'source': 'unused'}, []))

    def test_totalshots_cero_no_prueba_participacion_y_sin_cobertura_queda_desconocido(self):
        # DERROTA_CASA no tiene alineación ni incidencias: {'totalShots': 0} no resuelve nada.
        self.assertEqual(self.resumen(crear.DERROTA_CASA, crear.SIN_COBERTURA)[:3], ('unknown', 'insufficient_coverage', None))

    def test_estadisticas_sin_entrada_quedan_desconocidas(self):
        self.assertEqual(self.resumen(crear.FINALIZADO, crear.TOQUES_SIN_ENTRADA)[:3], ('unknown', 'stats_without_entry', None))
        self.assertEqual(self.actuacion(crear.FINALIZADO, crear.TOQUES_SIN_ENTRADA)['issues'], ['stats_without_entry'])

    def test_cada_incidencia_conserva_su_evidencia(self):
        # Jugador, partido, campos en conflicto con los valores originales y fuentes; nada se resuelve a mano.
        [evidencia] = self.actuacion(crear.FINALIZADO, crear.TOQUES_SIN_ENTRADA)['evidence']
        self.assertEqual((evidencia['issue'], evidencia['match_id'], evidencia['player_id']),
                         ('stats_without_entry', crear.FINALIZADO, crear.TOQUES_SIN_ENTRADA))
        self.assertEqual(evidencia['fields']['statistics_keys'], ['totalPass', 'touches'])
        self.assertEqual((evidencia['fields']['minutes_played'], evidencia['fields']['substitution_in']), ([None], False))
        self.assertEqual(evidencia['sources'], ['jugador_partido', 'originales:lineups', 'originales:incidents'])
        [evidencia] = self.actuacion(crear.FINALIZADO, crear.GOL_SIN_EVENTO)['evidence']
        self.assertEqual((evidencia['issue'], evidencia['fields']['statistics.goals'], evidencia['fields']['incidents']),
                         ('goals_mismatch', [1], 0))
        [evidencia] = self.actuacion(crear.EMPATE_CASA, crear.CONTRADICTORIO)['evidence']
        self.assertEqual((evidencia['fields']['played'], evidencia['fields']['minutes_played']), ([True], [0]))
        self.assertEqual(self.actuacion(crear.FINALIZADO, crear.UNO)['evidence'], [])

    def test_una_entrada_sin_minutos_es_participacion_sin_minutos_inventados(self):
        # La entrada del rival basta para saber que jugó; sus minutos no se derivan.
        self.assertEqual(self.resumen(crear.FINALIZADO, crear.RIVAL_ENTRA)[:3], ('played', 'substitution_in', None))
        # Su lado ya no tiene cobertura completa (entra alguien sin minutos).
        self.assertEqual(api.contexto_partido(DATOS, crear.FINALIZADO)['cobertura'], {'home': False, 'away': True})

    def test_contradicciones_de_participacion(self):
        for partido in (crear.EMPATE_CASA, crear.DERROTA_CASA):
            self.assertEqual(self.resumen(partido, crear.CONTRADICTORIO)[:2], ('unknown', 'contradiction'))
            self.assertEqual(self.actuacion(partido, crear.CONTRADICTORIO)['issues'], ['participation_contradiction'])

    def test_el_equipo_es_el_del_lado_y_el_team_id_discrepante_es_advertencia(self):
        a = self.actuacion(crear.EMPATE_CASA, crear.LADO_ERRONEO)
        self.assertEqual((a['side'], a['team_id'], a['participation']), ('away', crear.RIVAL_B, 'played'))
        self.assertEqual(a['issues'], ['team_id_mismatch', 'source_team_mismatch'])

    def test_participacion(self):
        for jugado, minutos, esperado in ((True, 90, (True, False)), (True, None, (True, False)), (None, 10, (True, False)),
                                          (None, None, (False, False)), (False, None, (False, False)), (False, 0, (False, False)),
                                          (None, 0, (False, False)), (True, 0, (False, True)), (False, 30, (False, True))):
            self.assertEqual(api.participacion(jugado, minutos), esperado, (jugado, minutos))

    def test_la_ficha_y_las_alineaciones_llevan_la_misma_normalizacion(self):
        filas = consultar(f'/api/v1/players/{crear.SUPLENTE}/matches')[1]['data']
        normalizadas = {f['match_id']: f['normalized'] for f in filas}
        self.assertEqual(normalizadas[crear.FINALIZADO], self.actuacion(crear.FINALIZADO, crear.SUPLENTE))
        # El registro original sigue intacto junto a la normalización.
        self.assertIsNone(next(f for f in filas if f['match_id'] == crear.FINALIZADO)['minutes_played'])
        estado, cuerpo = consultar(f'/api/v1/matches/{crear.FINALIZADO}/lineups')
        self.assertEqual(estado, 200)
        alineacion = {a['player_id']: a for a in cuerpo['normalized']['players']}
        self.assertEqual(alineacion[crear.SUPLENTE], normalizadas[crear.FINALIZADO])
        self.assertEqual(cuerpo['data']['confirmed'], True)


class CorreccionDelEvento(unittest.TestCase):
    """Un evento corregido (marcador final 0-2 -> 0-3, o cambio de equipos) cuyas secciones no se pudieron releer: se
    sirven como evidencia, señaladas como anteriores a la corrección, y lo que la corrección hace incompatible deja de
    usarse en fichas, tablas y agregados, sin convertirse en cero."""
    CORREGIDO = '2026-10-25T12:00:00+00:00'
    OTRO_EQUIPO = 9999

    def corregido(self, secciones_leidas=(), equipos=False):
        datos = copy.deepcopy(DATOS)
        evento = datos['originales'][(crear.FINALIZADO, 'event')]
        # La muestra guarda un evento mínimo: se le da el resultado y los equipos que publica (0-2) y se corrige.
        evento['registro']['payload']['event'].update(
            status={'type': 'finished'}, homeScore={'current': 0}, awayScore={'current': 2},
            homeTeam={'id': 9001}, awayTeam={'id': crear.JAEN})
        nuevo = copy.deepcopy(evento['registro'])
        partido = next(m for m in datos['partidos'] if m['match_id'] == crear.FINALIZADO)
        if equipos:
            # El rival local pasa a ser otro: integridad, el partido queda marcado y fuera de los agregados.
            nuevo['payload']['event']['homeTeam'] = {'id': self.OTRO_EQUIPO}
            partido.update(home_team_id=self.OTRO_EQUIPO, version_conflict=True)
            motivos, integridad = ['identity_changed'], True
        else:
            nuevo['payload']['event']['awayScore'] = {'current': 3}
            partido['away_score'] = 3
            motivos, integridad = ['final_score_corrected'], False
        nuevo['fetched_at'] = self.CORREGIDO
        for r in evento['todas']:
            r['selected'] = False
        evento.update(registro=nuevo, versiones=2, todas=evento['todas'] + [dict(nuevo, selected=True)])
        datos['discrepancias'].append({'event_id': crear.FINALIZADO, 'key': 'event', 'sub': '', 'reasons': motivos,
                                       'integrity': integridad})
        for k in secciones_leidas:
            datos['originales'][(crear.FINALIZADO, k)]['registro']['fetched_at'] = '2026-10-26T12:00:00+00:00'
        return datos

    @staticmethod
    def meta(datos, k):
        return api.consultar(datos, f'/api/v1/matches/{crear.FINALIZADO}/{k}')[1]

    def test_sin_correccion_nada_es_anterior(self):
        for k in SECCIONES:
            self.assertEqual(api.anterior_a(DATOS, crear.FINALIZADO, k), set())
            self.assertEqual((self.meta(DATOS, k)['meta']['predates_score_correction'],
                              self.meta(DATOS, k)['meta']['predates_team_change']), (False, False))

    def test_las_secciones_se_conservan_y_se_senalan(self):
        datos = self.corregido()
        for k in SECCIONES:
            cuerpo = self.meta(datos, k)
            # La evidencia es la misma que antes de la corrección, con su fecha; solo cambia la señal.
            self.assertEqual(cuerpo['data'], consultar(f'/api/v1/matches/{crear.FINALIZADO}/{k}')[1]['data'])
            self.assertEqual((cuerpo['meta']['availability'], cuerpo['meta']['predates_score_correction'],
                              cuerpo['meta']['predates_team_change']), ('stored', True, False), k)
        self.assertEqual(api.consultar(datos, f'/api/v1/matches/{crear.FINALIZADO}')[1]['data']['away_score'], 3)

    def test_las_incidencias_anteriores_no_se_contrastan(self):
        # Antes de la corrección cuadraban con el marcador (UNO marcó); con el corregido ya no se usan para los goles.
        self.assertEqual(api.contexto_partido(DATOS, crear.FINALIZADO)['goles'], {crear.UNO: 1})
        contexto = api.contexto_partido(self.corregido(), crear.FINALIZADO)
        self.assertIsNone(contexto['goles'])
        self.assertEqual(contexto['anteriores'], {'lineups': {'score'}, 'incidents': {'score'}})

    def test_goles_y_asistencias_de_la_alineacion_anterior_quedan_sin_dato(self):
        datos = self.corregido()
        a = api.actuaciones_del_partido(datos, crear.FINALIZADO)[crear.UNO]
        self.assertEqual((a['participation'], a['goals'], a['assists']),
                         ('played', {'value': None, 'source': None}, {'value': None, 'source': None}))
        self.assertIn('lineups_before_score_correction', a['issues'])
        [evidencia] = [e for e in a['evidence'] if e['issue'] == 'lineups_before_score_correction']
        self.assertEqual((evidencia['fields']['score_observed_at'], evidencia['fields']['score']), (self.CORREGIDO, [0, 3]))
        # Las estadísticas independientes del marcador siguen: minutos y las que ya tenía (nada nuevo sin dato).
        antes = api.actuaciones_del_partido(DATOS, crear.FINALIZADO)[crear.UNO]
        self.assertEqual(a['minutes'], antes['minutes'])
        self.assertEqual({m: s for m, s in a['stats'].items() if s['value'] is not None},
                         {m: s for m, s in antes['stats'].items() if s['value'] is not None and s['source'] != 'derived'})
        # La participación no depende del marcador: el suplente no utilizado sigue siéndolo, con sus ceros.
        suplente = api.actuaciones_del_partido(datos, crear.FINALIZADO)[crear.SUPLENTE]
        self.assertEqual((suplente['participation'], suplente['goals'], suplente['issues']),
                         ('unused', {'value': 0, 'source': 'unused'}, []))

    def test_en_la_ficha_del_jugador_y_en_los_acumulados_queda_sin_dato_no_en_cero(self):
        datos = self.corregido()
        [fila] = [f for f in api.consultar(datos, f'/api/v1/players/{crear.UNO}/matches')[1]['data']
                  if f['match_id'] == crear.FINALIZADO]
        self.assertEqual((fila['normalized']['goals'], fila['normalized']['assists']),
                         ({'value': None, 'source': None}, {'value': None, 'source': None}))
        jugador = lambda d: next(p for p in api.acumulados_jugadores(d, crear.JAEN)['competitions'][0]['players']  # noqa: E731
                                 if p['player_id'] == crear.UNO)
        antes, despues = jugador(DATOS), jugador(datos)
        for k in ('goals', 'assists'):
            # Una convocatoria menos con dato, de las mismas; el total no la cuenta como 0.
            self.assertEqual((despues[k]['matches'], despues[k]['of']), (antes[k]['matches'] - 1, antes[k]['of']), k)
        self.assertEqual(despues['goals']['value'], antes['goals']['value'] - 1 if antes['goals']['matches'] > 1 else None)
        self.assertEqual(despues['minutes'], antes['minutes'])

    def test_el_partido_cuenta_con_el_marcador_corregido(self):
        competicion = DATOS['competiciones'][0]
        _, resultados = api.partidos_contados(self.corregido(), crear.JAEN, competicion)
        [r] = [r for r in resultados if r['match']['match_id'] == crear.FINALIZADO]
        self.assertEqual((r['goals_for'], r['goals_against']), (3, 0))

    def test_releidas_despues_de_la_correccion_vuelven_a_usarse(self):
        datos = self.corregido(secciones_leidas=SECCIONES)
        for k in SECCIONES:
            self.assertEqual(api.anterior_a(datos, crear.FINALIZADO, k), set())
            self.assertFalse(self.meta(datos, k)['meta']['predates_score_correction'])
        self.assertNotIn('lineups_before_score_correction', api.actuaciones_del_partido(datos, crear.FINALIZADO)[crear.UNO]['issues'])

    def test_una_fecha_ilegible_no_se_afirma(self):
        datos = self.corregido()
        datos['originales'][(crear.FINALIZADO, 'lineups')]['registro']['fetched_at'] = 'sin fecha'
        self.assertEqual(api.anterior_a(datos, crear.FINALIZADO, 'lineups'), set())

    def test_con_otros_equipos_nada_antiguo_se_atribuye_a_los_nuevos(self):
        datos = api.apartar_alineaciones_de_otros_equipos(self.corregido(equipos=True))
        # La evidencia original sigue en sus secciones, señalada.
        for k in SECCIONES:
            self.assertEqual((self.meta(datos, k)['meta']['predates_team_change'], self.meta(datos, k)['data']),
                             (True, consultar(f'/api/v1/matches/{crear.FINALIZADO}/{k}')[1]['data']), k)
        # Las convocatorias de la alineación anterior no son de ningún equipo vigente: apartadas, no reasignadas.
        self.assertEqual(api.actuaciones_del_partido(datos, crear.FINALIZADO), {})
        self.assertTrue(datos['convocatorias_apartadas'])
        self.assertTrue(all(r['match_id'] == crear.FINALIZADO for r in datos['convocatorias_apartadas']))
        self.assertEqual(api.consultar(datos, f'/api/v1/teams/{self.OTRO_EQUIPO}/players')[0], 404)  # sin entidad
        datos['equipos'].append({'source': 'Sofascore', 'team_id': self.OTRO_EQUIPO, 'name': 'Equipo nuevo'})
        self.assertEqual(api.consultar(datos, f'/api/v1/teams/{self.OTRO_EQUIPO}/players')[1]['data'], [])
        self.assertEqual(api.acumulados_jugadores(datos, self.OTRO_EQUIPO)['competitions'][0]['players'], [])
        self.assertEqual(api.rendimiento(datos, self.OTRO_EQUIPO)['competitions'][0]['record']['played'], 0)
        for jugador in {r['player_id'] for r in datos['convocatorias_apartadas']}:
            filas = api.consultar(datos, f'/api/v1/players/{jugador}/matches')[1].get('data', [])
            self.assertNotIn(crear.FINALIZADO, [f['match_id'] for f in filas], jugador)
        # Las incidencias anteriores tampoco: ni entradas ni goles para los equipos vigentes.
        contexto = api.contexto_partido(datos, crear.FINALIZADO)
        self.assertEqual((contexto['goles'], contexto['entradas']), (None, {'home': set(), 'away': set()}))
        # Ni estadísticas: el partido está fuera del balance de los dos equipos vigentes.
        for equipo in (self.OTRO_EQUIPO, crear.JAEN):
            cuenta, resultados = api.partidos_contados(datos, equipo, DATOS['competiciones'][0])
            self.assertNotIn(crear.FINALIZADO, [r['match']['match_id'] for r in resultados], equipo)
        self.assertEqual(api.partidos_contados(datos, crear.JAEN, DATOS['competiciones'][0])[0]['excluded_by_version_conflict'], 1)

    def test_releida_con_los_equipos_vigentes_vuelve_a_usarse(self):
        datos = api.apartar_alineaciones_de_otros_equipos(self.corregido(equipos=True, secciones_leidas=SECCIONES))
        self.assertEqual({k: api.anterior_a(datos, crear.FINALIZADO, k) for k in SECCIONES}, {k: set() for k in SECCIONES})
        self.assertNotIn('convocatorias_apartadas', datos)
        self.assertIn(crear.UNO, api.actuaciones_del_partido(datos, crear.FINALIZADO))

    @staticmethod
    def con_valoracion(datos, valor=7.4):
        """La muestra no trae valoraciones: se da una a UNO en este partido (sus registros de convocatoria)."""
        for r in datos['jugador_partido']:
            if r['match_id'] == crear.FINALIZADO and r['player_id'] == crear.UNO:
                r['statistics'] = dict(r['statistics'], rating=valor)
        return datos

    def test_la_valoracion_de_la_alineacion_anterior_no_es_vigente(self):
        valoracion = lambda d: api.actuaciones_del_partido(d, crear.FINALIZADO)[crear.UNO]['stats']['rating']  # noqa: E731
        self.assertEqual(valoracion(self.con_valoracion(copy.deepcopy(DATOS)))['value'], 7.4)
        datos = self.con_valoracion(self.corregido())
        self.assertEqual(valoracion(datos), {'value': None, 'source': None, 'rule': None, 'reason': 'before_score_correction'})
        # El original sigue en su registro y en la sección; no cuenta en ninguna media.
        [fila] = [f for f in api.consultar(datos, f'/api/v1/players/{crear.UNO}/matches')[1]['data'] if f['match_id'] == crear.FINALIZADO]
        self.assertEqual((fila['statistics']['rating'], fila['normalized']['stats']['rating']['value']), (7.4, None))
        # Tras una relectura válida vuelve a usarse.
        self.assertEqual(valoracion(self.con_valoracion(self.corregido(secciones_leidas=SECCIONES)))['value'], 7.4)

    def test_las_metricas_que_dependen_del_gol_no_se_derivan(self):
        # Sin un gol fiable no se fija ningún tiro a puerta por la partición de tiros: queda lo que da la fuente.
        datos = self.corregido()
        for jugador, a in api.actuaciones_del_partido(datos, crear.FINALIZADO).items():
            if 'lineups_before_score_correction' in a['issues']:
                self.assertTrue(all(s['source'] != 'derived' for s in a['stats'].values()), jugador)


class AcumuladosJugadores(unittest.TestCase):
    """Acumulados por jugador sobre los partidos contados del balance, con las convocatorias normalizadas."""

    def competicion(self, equipo=crear.JAEN):
        estado, cuerpo = consultar(f'/api/v1/teams/{equipo}/player-stats')
        self.assertEqual(estado, 200)
        self.assertIn('No es una plantilla completa', cuerpo['meta']['note'])
        [competicion] = cuerpo['data']['competitions']
        return competicion

    def jugadores(self, equipo=crear.JAEN):
        return {p['player_id']: p for p in self.competicion(equipo)['players']}

    @staticmethod
    def cifras(jugador):
        return {k: (jugador[k]['value'], jugador[k]['matches'], jugador[k]['of'])
                for k in ('starts', 'appearances', 'minutes', 'goals', 'assists')}

    def test_competicion_del_ambito_y_cobertura(self):
        c = self.competicion()
        self.assertEqual({k: c[k] for k in ('name', 'season', 'unique_tournament_id', 'tournament_id', 'season_id')},
                         {'name': 'Primera Federación, Grupo 2', 'season': '26/27',
                          'unique_tournament_id': 17073, 'tournament_id': 94191, 'season_id': 97382})
        # Los 5 contados del balance; SIN_FECHA no tiene convocatorias del Real Jaén.
        self.assertEqual(c['matches'], {'counted': 5, 'with_call_ups': 4})
        self.assertEqual(c['call_ups'], {'total': 13, 'played': 8, 'unused': 1, 'unknown': 4, 'contradictions': 2, 'mismatches': 2,
                                         'team_warnings': 0, 'duplicate_records': 2, 'discarded_records': 0})

    def test_solo_partidos_contados_y_del_ambito(self):
        # UNO: no cuentan el finalizado sin marcador válido, el pendiente ni la Copa.
        uno = self.jugadores()[crear.UNO]
        self.assertEqual(uno['call_ups'], 1)
        self.assertEqual(self.cifras(uno), {'starts': (1, 1, 1), 'appearances': (1, 1, 1), 'minutes': (90, 1, 1),
                                            'goals': (1, 1, 1), 'assists': (0, 1, 1)})
        self.assertNotIn(crear.SOLO_COPA, self.jugadores())

    def test_el_no_utilizado_cuenta_como_cero_y_completa_la_cifra(self):
        # No utilizado en FINALIZADO (0) y 25 minutos en EMPATE_CASA, donde sus goles no se pueden leer.
        self.assertEqual(self.cifras(self.jugadores()[crear.SUPLENTE]), {
            'starts': (0, 2, 2), 'appearances': (1, 2, 2), 'minutes': (25, 2, 2), 'goals': (0, 1, 2), 'assists': (1, 2, 2)})

    def test_lo_desconocido_sigue_parcial(self):
        jugadores = self.jugadores()
        self.assertEqual(self.cifras(jugadores[crear.CONTRADICTORIO]), {
            'starts': (2, 3, 3), 'appearances': (1, 1, 3), 'minutes': (90, 1, 3), 'goals': (0, 1, 3), 'assists': (0, 1, 3)})
        for jugador in (crear.TOQUES_SIN_ENTRADA, crear.SIN_COBERTURA):
            self.assertEqual(self.cifras(jugadores[jugador])['appearances'], (0, 0, 1))
            self.assertEqual(self.cifras(jugadores[jugador])['minutes'], (None, 0, 1))

    def test_contradicciones_con_las_incidencias(self):
        jugadores = self.jugadores()
        self.assertEqual(self.cifras(jugadores[crear.SIN_CLAVE_ASISTENCIA])['assists'], (None, 0, 1))
        self.assertEqual(self.cifras(jugadores[crear.SIN_CLAVE_ASISTENCIA])['goals'], (0, 1, 1))
        self.assertEqual(self.cifras(jugadores[crear.GOL_SIN_EVENTO])['goals'], (None, 0, 1))

    def test_duplicados_cuentan_una_vez_y_lo_que_discrepa_queda_sin_dato(self):
        jugador = self.jugadores()[crear.DUPLICADO]
        self.assertEqual(jugador['call_ups'], 2)
        self.assertEqual(self.cifras(jugador), {'starts': (2, 2, 2), 'appearances': (2, 2, 2), 'minutes': (90, 1, 2),
                                                'goals': (0, 1, 2), 'assists': (0, 2, 2)})
        # El dorsal de su última convocatoria con fecha (MISMO_INSTANTE), no el de otra.
        self.assertEqual(jugador['last_shirt_number'], 11)

    def test_valores_fuera_de_dominio_quedan_sin_dato(self):
        jugador = self.jugadores()[crear.VALORES_INVALIDOS]
        self.assertEqual(self.cifras(jugador), {'starts': (None, 0, 1), 'appearances': (1, 1, 1), 'minutes': (None, 0, 1),
                                                'goals': (None, 0, 1), 'assists': (None, 0, 1)})
        self.assertEqual((jugador['height_cm'], jugador['preferred_foot'], jugador['last_shirt_number']), (None, None, None))

    def test_datos_personales_en_la_respuesta_agregada(self):
        jugadores = self.jugadores()
        self.assertEqual({k: jugadores[crear.CONTRADICTORIO][k] for k in ('name', 'usual_position', 'height_cm', 'preferred_foot')},
                         {'name': 'Jugador Contradictorio', 'usual_position': 'D', 'height_cm': 178, 'preferred_foot': 'Right'})
        # La ficha de UNO se rehace sin la Copa.
        self.assertEqual((jugadores[crear.UNO]['name'], jugadores[crear.UNO]['height_cm']), ('Jugador Sintético Uno', 175))

    def test_separacion_por_equipo_segun_el_lado(self):
        # El registro con team_id del Real Jaén en el lado del rival cuenta para el rival, con advertencia.
        self.assertNotIn(crear.LADO_ERRONEO, self.jugadores())
        self.assertNotIn(crear.RIVAL_JUGADOR, self.jugadores())
        rival = self.competicion(crear.RIVAL_B)
        self.assertEqual(rival['matches'], {'counted': 2, 'with_call_ups': 1})
        self.assertEqual(rival['call_ups']['team_warnings'], 1)
        jugadores = {p['player_id']: p for p in rival['players']}
        # Sin estadísticas sustanciales (sólo goals), su asistencia omitida no se lee como 0.
        self.assertEqual(self.cifras(jugadores[crear.RIVAL_JUGADOR]), {
            'starts': (1, 1, 1), 'appearances': (1, 1, 1), 'minutes': (90, 1, 1), 'goals': (1, 1, 1), 'assists': (None, 0, 1)})
        self.assertEqual(self.cifras(jugadores[crear.LADO_ERRONEO])['minutes'], (90, 1, 1))

    def test_equipo_sin_partidos_en_el_ambito_y_equipo_desconocido(self):
        c = self.competicion(crear.RIVAL_COPA)
        self.assertEqual((c['matches'], c['players']), ({'counted': 0, 'with_call_ups': 0}, []))
        self.assertEqual(consultar('/api/v1/teams/1/player-stats'), (404, {'error': 'Entidad no encontrada'}))



if __name__ == '__main__':
    unittest.main()
