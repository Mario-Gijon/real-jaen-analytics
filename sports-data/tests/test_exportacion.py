"""Pruebas contra la exportación real de datos/ (solo biblioteca estándar).

datos/ no se versiona: sin la exportación importada estas pruebas se omiten y las de
test_api.py (muestra sintética de tests/fixtures) cubren el comportamiento.
Ejecutar desde la carpeta de la API:  python -m unittest discover -s tests -v
Usan los JSON de datos/ sin modificarlos; los casos que no existen en la muestra
(varias versiones, contenido vacío, sin originales) se construyen en una carpeta temporal.
Los recuentos son los de la exportación del 2026-10-21 (real-jaen-raw_2026-10-21_gfmykcat.jsonl).
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
import api  # noqa: E402

DATOS = RAIZ / 'datos'
if not all((DATOS / f'{n}.json').exists() for n in ('equipos', 'jugadores', 'partidos', 'jugador_partido', 'originales')):
    raise unittest.SkipTest('Sin la exportación real en datos/: ver docs/importador-y-api.txt')
FINALIZADO = 16599363   # CE Europa 0-3 Real Jaén, con estadísticas, alineaciones e incidencias
PENDIENTE = 16599386    # notstarted: solo el registro `event` en originales
INEXISTENTE = 1
COPA = 17259821         # Copa del Rey, fuera del ámbito (ambito.json): sigue en datos/ pero no se sirve
GRUPO_2 = {'unique_tournament_id': 17073, 'tournament_id': 94191, 'season_id': 97382}
SECCIONES = ('statistics', 'lineups', 'incidents')

DATOS_REALES = api.cargar(DATOS)
ORIGINALES = json.loads((DATOS / 'originales.json').read_text(encoding='utf-8'))


def consultar(ruta, datos=DATOS_REALES):
    return api.consultar(datos, ruta)


TEMPORALES = []


def tearDownModule():
    for carpeta in TEMPORALES:
        shutil.rmtree(carpeta, ignore_errors=True)


def carpeta_con_originales(registros):
    """Copia los JSON normalizados a una carpeta temporal con otros originales (o sin ellos si es None)."""
    destino = Path(tempfile.mkdtemp(prefix='api-real-jaen-test-'))
    TEMPORALES.append(destino)
    for archivo in DATOS.glob('*.json'):
        if archivo.name != 'originales.json':
            shutil.copy(archivo, destino / archivo.name)
    if registros is not None:
        (destino / 'originales.json').write_text(json.dumps(registros), encoding='utf-8')
    return destino


class RutasExistentes(unittest.TestCase):
    def test_health(self):
        self.assertEqual(consultar('/health'), (200, {'data': {'status': 'ok'}}))

    def test_equipo_y_sus_partidos_y_jugadores(self):
        estado, cuerpo = consultar('/api/v1/teams/2841')
        self.assertEqual((estado, cuerpo['data']['name']), (200, 'Real Jaén'))
        estado, cuerpo = consultar('/api/v1/teams/2841/matches')
        self.assertEqual((estado, cuerpo['meta']['count'], len(cuerpo['data'])), (200, 37, 37))
        estado, cuerpo = consultar('/api/v1/teams/2841/players')
        self.assertEqual((estado, cuerpo['meta']['count']), (200, 25))
        self.assertIn('no es una plantilla completa', cuerpo['meta']['scope'])

    def test_jugador_y_sus_partidos(self):
        estado, cuerpo = consultar('/api/v1/players/966303')
        self.assertEqual((estado, cuerpo['data']['name']), (200, 'Moha Keita'))
        estado, cuerpo = consultar('/api/v1/players/966303/matches')
        self.assertEqual((estado, cuerpo['meta']['count']), (200, 6))
        self.assertEqual(cuerpo['data'][0]['match']['match_id'], cuerpo['data'][0]['match_id'])

    def test_errores(self):
        self.assertEqual(consultar('/api/v1/teams/1')[0], 404)
        self.assertEqual(consultar('/api/v1/players/abc')[0], 400)
        self.assertEqual(consultar('/api/v1/teams/2841/otra')[0], 404)
        self.assertEqual(consultar('/api/v1/otra/1')[0], 404)
        self.assertEqual(consultar('/')[0], 404)


class DetallePartido(unittest.TestCase):
    def test_partido_normalizado_con_nombres_de_ambos_equipos(self):
        estado, cuerpo = consultar(f'/api/v1/matches/{FINALIZADO}')
        self.assertEqual(estado, 200)
        normalizado = next(m for m in DATOS_REALES['partidos'] if m['match_id'] == FINALIZADO)
        # Todos los campos normalizados sin cambios, más los nombres.
        self.assertEqual({k: cuerpo['data'][k] for k in normalizado}, normalizado)
        self.assertEqual((cuerpo['data']['home_team_name'], cuerpo['data']['away_team_name']), ('CE Europa', 'Real Jaén'))
        self.assertEqual((cuerpo['data']['home_score'], cuerpo['data']['away_score']), (0, 3))
        self.assertEqual(cuerpo['meta']['sections'], {s: 'stored' for s in SECCIONES})

    def test_partido_pendiente_existe_sin_secciones_y_sin_marcador_inventado(self):
        estado, cuerpo = consultar(f'/api/v1/matches/{PENDIENTE}')
        self.assertEqual(estado, 200)
        self.assertEqual(cuerpo['data']['status'], 'notstarted')
        self.assertIsNone(cuerpo['data']['home_score'])
        self.assertIsNone(cuerpo['data']['away_score'])
        self.assertEqual(cuerpo['meta']['sections'], {s: 'not_stored' for s in SECCIONES})
        for seccion in SECCIONES:
            estado, cuerpo = consultar(f'/api/v1/matches/{PENDIENTE}/{seccion}')
            self.assertEqual(estado, 200, seccion)
            self.assertIsNone(cuerpo['data'])
            self.assertEqual(cuerpo['meta']['availability'], 'not_stored')
            self.assertIsNone(cuerpo['meta']['fetched_at'])
            self.assertEqual(cuerpo['meta']['versions'], 0)

    def test_secciones_devuelven_el_payload_original_con_procedencia(self):
        for seccion in SECCIONES:
            original = next(r for r in ORIGINALES if r['event_id'] == FINALIZADO and r['key'] == seccion)
            estado, cuerpo = consultar(f'/api/v1/matches/{FINALIZADO}/{seccion}')
            self.assertEqual(estado, 200, seccion)
            self.assertEqual(cuerpo['data'], original['payload'], seccion)
            self.assertEqual(cuerpo['meta'], {
                'match_id': FINALIZADO, 'section': seccion, 'source': 'Sofascore', 'source_key': seccion,
                'availability': 'stored', 'fetched_at': original['fetched_at'], 'versions': 1, 'discrepancy': None,
                'predates_score_correction': False, 'predates_team_change': False, 'note': api.NOTA_FETCHED_AT,
            })
        # Identificadores de SofaScore intactos dentro del payload.
        alineaciones = consultar(f'/api/v1/matches/{FINALIZADO}/lineups')[1]['data']
        self.assertTrue(all(isinstance(p['player']['id'], int) for p in alineaciones['home']['players']))

    def test_partido_inexistente_y_rutas_invalidas(self):
        for sufijo in ('', '/statistics', '/lineups', '/incidents'):
            self.assertEqual(consultar(f'/api/v1/matches/{INEXISTENTE}{sufijo}'),
                             (404, {'error': 'Partido no encontrado'}), sufijo)
        self.assertEqual(consultar('/api/v1/matches/abc')[0], 400)
        self.assertEqual(consultar(f'/api/v1/matches/{FINALIZADO}/h2h')[0], 404)
        self.assertEqual(consultar(f'/api/v1/matches/{FINALIZADO}/statistics/extra')[0], 404)

    def test_todas_las_respuestas_son_json_valido(self):
        for partido in DATOS_REALES['partidos']:
            for sufijo in ('', '/statistics', '/lineups', '/incidents'):
                estado, cuerpo = consultar(f"/api/v1/matches/{partido['match_id']}{sufijo}")
                self.assertEqual(estado, 200)
                json.dumps(cuerpo, ensure_ascii=False, allow_nan=False)


class CasosConstruidos(unittest.TestCase):
    def registro(self, key, fetched_at, payload, event_id=FINALIZADO):
        return {'event_id': event_id, 'key': key, 'sub': '', 'fetched_at': fetched_at, 'payload': payload}

    def test_varias_versiones_sirve_la_que_selecciono_el_importador(self):
        # La API no aplica otra política: sirve la marcada, aunque no sea la más reciente.
        datos = api.cargar(carpeta_con_originales([
            dict(self.registro('statistics', '2026-10-21T12:00:00+00:00', {'statistics': [{'period': 'NUEVA'}]}), selected=False),
            dict(self.registro('statistics', '2026-10-20T12:00:00+00:00', {'statistics': [{'period': 'ANTIGUA'}]}), selected=True),
            dict(self.registro('statistics', 'no-es-fecha', {'statistics': [{'period': 'ILEGIBLE'}]}), selected=False),
        ]))
        estado, cuerpo = consultar(f'/api/v1/matches/{FINALIZADO}/statistics', datos)
        self.assertEqual(estado, 200)
        self.assertEqual(cuerpo['data'], {'statistics': [{'period': 'ANTIGUA'}]})
        self.assertEqual((cuerpo['meta']['fetched_at'], cuerpo['meta']['versions']), ('2026-10-20T12:00:00+00:00', 3))

    def test_varias_versiones_sin_seleccion_no_se_cargan(self):
        for registros in ([self.registro('statistics', '2026-10-21T12:00:00+00:00', {'statistics': [{'period': 'A'}]}),
                           self.registro('statistics', '2026-10-20T12:00:00+00:00', {'statistics': [{'period': 'B'}]})],
                          [dict(self.registro('statistics', '2026-10-21T12:00:00+00:00', {'statistics': [{'period': 'A'}]}), selected=True),
                           dict(self.registro('statistics', '2026-10-20T12:00:00+00:00', {'statistics': [{'period': 'B'}]}), selected=True)]):
            with self.assertRaises(ValueError):
                api.cargar(carpeta_con_originales(registros))

    def test_contenido_vacio_se_distingue_de_seccion_no_almacenada(self):
        datos = api.cargar(carpeta_con_originales([
            self.registro('statistics', '2026-10-21T12:00:00+00:00', {'statistics': []}),
            self.registro('lineups', '2026-10-21T12:00:00+00:00', {'confirmed': False, 'home': {'players': []}, 'away': {'players': []}}),
            self.registro('incidents', '2026-10-21T12:00:00+00:00', {}),
        ]))
        for seccion, payload in (('statistics', {'statistics': []}),
                                 ('lineups', {'confirmed': False, 'home': {'players': []}, 'away': {'players': []}}),
                                 ('incidents', {})):
            estado, cuerpo = consultar(f'/api/v1/matches/{FINALIZADO}/{seccion}', datos)
            self.assertEqual(estado, 200, seccion)
            self.assertEqual(cuerpo['meta']['availability'], 'empty', seccion)
            self.assertEqual(cuerpo['data'], payload, seccion)
            self.assertIsNotNone(cuerpo['meta']['fetched_at'])
        estado, cuerpo = consultar(f'/api/v1/matches/{PENDIENTE}/statistics', datos)
        self.assertEqual(cuerpo['meta']['availability'], 'not_stored')
        self.assertEqual(consultar(f'/api/v1/matches/{FINALIZADO}', datos)[1]['meta']['sections'],
                         {s: 'empty' for s in SECCIONES})

    def test_sin_originales_arranca_y_ninguna_seccion_figura_almacenada(self):
        datos = api.cargar(carpeta_con_originales(None))
        self.assertEqual(consultar('/api/v1/teams/2841/matches', datos)[0], 200)
        estado, cuerpo = consultar(f'/api/v1/matches/{FINALIZADO}/lineups', datos)
        self.assertEqual((estado, cuerpo['data'], cuerpo['meta']['availability']), (200, None, 'not_stored'))

    def test_equipo_sin_nombre_conocido_queda_en_null(self):
        carpeta = carpeta_con_originales([])
        equipos = [e for e in json.loads((carpeta / 'equipos.json').read_text(encoding='utf-8')) if e['team_id'] != 135672]
        (carpeta / 'equipos.json').write_text(json.dumps(equipos), encoding='utf-8')
        cuerpo = consultar(f'/api/v1/matches/{FINALIZADO}', api.cargar(carpeta))[1]
        self.assertIsNone(cuerpo['data']['home_team_name'])
        self.assertEqual(cuerpo['data']['away_team_name'], 'Real Jaén')


def carpeta_modificada(cambiar, ambito=None):
    """Copia datos/ a una carpeta temporal, deja que `cambiar(datos)` altere sus listas y escribe el ámbito dado."""
    destino = Path(tempfile.mkdtemp(prefix='api-real-jaen-test-'))
    TEMPORALES.append(destino)
    datos = {a.stem: json.loads(a.read_text(encoding='utf-8')) for a in DATOS.glob('*.json')}
    cambiar(datos)
    for nombre, contenido in datos.items():
        (destino / f'{nombre}.json').write_text(json.dumps(contenido), encoding='utf-8')
    (destino / 'ambito.json').write_text(json.dumps(ambito if ambito is not None else {'competiciones': [dict(GRUPO_2, nombre='Grupo 2')]}), encoding='utf-8')
    return destino


def cargar_modificada(cambiar, ambito=None):
    carpeta = carpeta_modificada(cambiar, ambito)
    return api.cargar(carpeta, carpeta / 'ambito.json')


def partido_extra(datos, ident, tournament_id, season_id, unique_tournament_id=17073, status='finished'):
    """Un partido del Real Jaén más, con su registro `event` original."""
    datos['partidos'].append(dict(datos['partidos'][0], match_id=ident, tournament_id=tournament_id, season_id=season_id,
                                  status=status, competition='Otra'))
    datos['originales'].append({'event_id': ident, 'key': 'event', 'sub': '', 'fetched_at': '2026-10-21T12:00:00+00:00',
                                'payload': {'event': {'tournament': {'id': tournament_id, 'uniqueTournament': {'id': unique_tournament_id}},
                                                      'season': {'id': season_id}}}})


def alineacion_extra(datos, ident, jugador, fetched_at='2026-10-22T12:00:00+00:00'):
    """Una alineación (y su convocatoria) del partido `ident` con el jugador dado en el Real Jaén."""
    datos['originales'].append({'event_id': ident, 'key': 'lineups', 'sub': '', 'fetched_at': fetched_at,
                                'payload': {'home': {'players': []}, 'away': {'players': [{'player': jugador, 'substitute': False}]}}})
    datos['jugador_partido'].append({'source': 'Sofascore', 'match_id': ident, 'team_id': 2841, 'player_id': jugador['id'],
                                     'side': 'away', 'substitute': False, 'minutes_played': 90, 'played': True,
                                     'statistics': {'minutesPlayed': 90}, 'fetched_at': fetched_at})


class Ambito(unittest.TestCase):
    """Sólo Primera Federación, Grupo 2 (26/27): lo excluido no se borra de datos/, pero ninguna ruta lo sirve."""

    def test_el_ambito_se_publica(self):
        estado, cuerpo = consultar('/api/v1/scope')
        self.assertEqual(estado, 200)
        self.assertEqual(cuerpo['data']['competitions'],
                         [dict(GRUPO_2, name='Primera Federación, Grupo 2', season='26/27')])

    def test_recuentos_de_la_exportacion(self):
        partidos = consultar('/api/v1/teams/2841/matches')[1]['data']
        estados = [m['status'] for m in partidos]
        self.assertEqual((len(partidos), estados.count('finished'), estados.count('notstarted')), (37, 8, 29))
        self.assertEqual({(m['tournament_id'], m['season_id']) for m in partidos}, {(94191, 97382)})
        # El original sigue en datos/: excluir no es borrar.
        self.assertIn(COPA, {m['match_id'] for m in json.loads((DATOS / 'partidos.json').read_text(encoding='utf-8'))})

    def test_copa_del_rey_no_se_sirve_ni_por_su_identificador(self):
        for ruta in (f'/api/v1/matches/{COPA}', *(f'/api/v1/matches/{COPA}/{s}' for s in SECCIONES)):
            self.assertEqual(consultar(ruta), (404, {'error': 'Partido fuera del ámbito de esta versión'}), ruta)
        self.assertEqual(consultar(f'/api/v1/matches/{INEXISTENTE}')[1], {'error': 'Partido no encontrado'})
        for ruta in ('/api/v1/teams/2841/matches', '/api/v1/players/966303/matches'):
            self.assertNotIn(COPA, {m['match_id'] for m in consultar(ruta)[1]['data']}, ruta)

    def test_otro_grupo_de_la_misma_competicion_y_temporada_queda_fuera(self):
        # Mismo uniqueTournament (Primera Federación) y misma temporada, otro grupo: no basta la competición general.
        datos = cargar_modificada(lambda d: partido_extra(d, 900001, tournament_id=94190, season_id=97382))
        self.assertNotIn(900001, {m['match_id'] for m in datos['partidos']})
        self.assertEqual(consultar('/api/v1/matches/900001', datos)[0], 404)

    def test_otra_temporada_valida_se_incluye_si_el_ambito_la_declara(self):
        def cambiar(d):
            partido_extra(d, 900002, tournament_id=99999, season_id=12345)
        ambito = {'competiciones': [dict(GRUPO_2, nombre='Grupo 2'),
                                    {'nombre': 'Grupo 2', 'temporada': '27/28', 'unique_tournament_id': 17073,
                                     'tournament_id': 99999, 'season_id': 12345}]}
        datos = cargar_modificada(cambiar, ambito)
        self.assertIn(900002, {m['match_id'] for m in datos['partidos']})
        self.assertEqual(len(datos['partidos']), 38)
        # Sin declararla, esa temporada (con su propio id de grupo) no se sirve.
        self.assertNotIn(900002, {m['match_id'] for m in cargar_modificada(cambiar)['partidos']})

    def test_el_original_que_contradice_al_partido_lo_excluye(self):
        datos = cargar_modificada(lambda d: partido_extra(d, 900003, tournament_id=94191, season_id=97382, unique_tournament_id=329))
        self.assertNotIn(900003, {m['match_id'] for m in datos['partidos']})

    def test_jugadores_solo_de_convocatorias_incluidas(self):
        solo_fuera = {'id': 900100, 'name': 'Sólo en Copa', 'position': 'M'}
        def cambiar(d):
            alineacion_extra(d, COPA, solo_fuera)
            d['jugadores'].append({'source': 'Sofascore', 'player_id': 900100, 'name': 'Sólo en Copa', 'usual_position': 'M',
                                   'country': None, 'birth_date': None, 'height_cm': None, 'preferred_foot': None,
                                   'fetched_at': '2026-10-22T12:00:00+00:00'})
        datos = cargar_modificada(cambiar)
        self.assertEqual(consultar('/api/v1/players/900100', datos), (404, {'error': 'Jugador fuera del ámbito de esta versión'}))
        self.assertNotIn(900100, {p['player_id'] for p in consultar('/api/v1/teams/2841/players', datos)[1]['data']})
        self.assertEqual(consultar('/api/v1/teams/2841/players', datos)[1]['meta']['count'], 25)

    def test_la_ficha_no_sale_de_un_partido_excluido(self):
        # La alineación más reciente de Moha Keita sería la de Copa, con otros datos: la ficha usa las incluidas.
        def cambiar(d):
            alineacion_extra(d, COPA, {'id': 966303, 'name': 'Moha Keita (Copa)', 'position': 'F', 'height': 199})
            ficha = next(p for p in d['jugadores'] if p['player_id'] == 966303)
            ficha.update(name='Moha Keita (Copa)', usual_position='F', height_cm=199, fetched_at='2026-10-22T12:00:00+00:00')
        datos = cargar_modificada(cambiar)
        estado, cuerpo = consultar('/api/v1/players/966303', datos)
        self.assertEqual((estado, cuerpo['data']['name'], cuerpo['data']['height_cm']), (200, 'Moha Keita', 165))
        self.assertEqual(cuerpo['data']['usual_position'], 'M')
        self.assertNotIn(COPA, {r['match_id'] for r in consultar('/api/v1/players/966303/matches', datos)[1]['data']})

    def test_un_ambito_incompleto_impide_arrancar(self):
        for ambito in ({}, {'competiciones': []}, {'competiciones': [{'nombre': 'Grupo 2', 'tournament_id': 94191}]}):
            with self.assertRaises(ValueError, msg=ambito):
                cargar_modificada(lambda d: None, ambito)


class RendimientoExportacion(unittest.TestCase):
    def test_balance_de_la_exportacion(self):
        estado, cuerpo = consultar('/api/v1/teams/2841/performance')
        [c] = cuerpo['data']['competitions']
        self.assertEqual((estado, c['tournament_id'], c['season_id']), (200, 94191, 97382))
        self.assertEqual(c['matches'], {'in_scope': 37, 'finished': 8, 'pending': 29, 'counted': 8,
                                        'finished_without_valid_score': 0, 'excluded_by_version_conflict': 0, 'unknown_status': 0, 'replaced_locally': 0})
        self.assertEqual(c['record'], {'played': 8, 'wins': 4, 'draws': 3, 'losses': 1,
                                       'goals_for': 12, 'goals_against': 8, 'goal_difference': 4})
        self.assertEqual(c['venues'], {'home': {'wins': 2, 'draws': 2, 'losses': 0}, 'away': {'wins': 2, 'draws': 1, 'losses': 1}})
        self.assertEqual([(r['match']['round'], r['result'][0], r['goals_for'], r['goals_against']) for r in c['results']],
                         [(1, 'w', 3, 0), (2, 'd', 2, 2), (3, 'w', 1, 0), (4, 'w', 2, 1), (5, 'l', 0, 2), (6, 'd', 0, 0),
                          (7, 'd', 3, 3), (8, 'w', 1, 0)])
        self.assertNotIn(COPA, {r['match']['match_id'] for r in c['results']})

    def test_produccion_y_concesion_de_la_exportacion(self):
        # Periodo ALL de las 8 estadísticas; Real Jaén local en J2, J4, J7 y J8 y visitante en el resto.
        salida = consultar('/api/v1/teams/2841/performance')[1]['data']['competitions'][0]['output']
        esperadas = {'goals': 12 / 8, 'goalsAgainst': 8 / 8, 'xg': 9.25 / 8, 'xgAgainst': 15.21 / 8, 'shots': 85 / 8,
                     'shotsAgainst': 134 / 8, 'shotsOnTarget': 32 / 8, 'shotsOnTargetAgainst': 36 / 8,
                     'possession': 399 / 8, 'corners': 30 / 8}
        for indicador, valor in esperadas.items():
            media = salida['season'][indicador]
            self.assertEqual((media['matches'], media['of']), (8, 8), indicador)
            self.assertAlmostEqual(media['value'], valor, places=9, msg=indicador)
        self.assertEqual(salida['last3']['match_ids'], [16599748, 16599895, 16599928])
        self.assertAlmostEqual(salida['last3']['values']['xg']['value'], (1.34 + 2.01 + 0.34) / 3, places=9)
        self.assertAlmostEqual(salida['last3']['values']['possession']['value'], (49 + 58 + 45) / 3, places=9)
        self.assertEqual(salida['undated'], 0)


class AcumuladosJugadoresExportacion(unittest.TestCase):
    def test_acumulados_de_la_exportacion(self):
        estado, cuerpo = consultar('/api/v1/teams/2841/player-stats')
        [c] = cuerpo['data']['competitions']
        self.assertEqual((estado, c['tournament_id'], c['season_id']), (200, 94191, 97382))
        self.assertEqual(c['matches'], {'counted': 8, 'with_call_ups': 8})
        # 170 convocatorias: 125 jugadas, 44 suplentes no utilizados (alineación confirmada y sustituciones que cuadran)
        # y 1 sin resolver (Francisco Gallego en J4: estadísticas de toques sin minutos ni entrada).
        self.assertEqual(c['call_ups'], {'total': 170, 'played': 125, 'unused': 44, 'unknown': 1, 'contradictions': 0,
                                         'mismatches': 0, 'team_warnings': 0, 'duplicate_records': 0, 'discarded_records': 0})
        jugadores = {p['player_id']: p for p in c['players']}
        self.assertEqual(len(jugadores), 25)
        total = lambda clave: sum(p[clave]['value'] or 0 for p in jugadores.values())
        self.assertEqual((total('starts'), total('appearances'), total('minutes'), total('goals'), total('assists')), (88, 125, 7898, 12, 7))
        parciales = {(p['player_id'], k) for p in jugadores.values() for k in ('starts', 'appearances', 'minutes', 'goals', 'assists')
                     if p[k]['matches'] < p[k]['of']}
        self.assertEqual(parciales, {(1982588, k) for k in ('appearances', 'minutes', 'goals', 'assists')})
        cifras = lambda p: {k: (p[k]['value'], p[k]['matches'], p[k]['of']) for k in ('starts', 'appearances', 'minutes', 'goals', 'assists')}
        self.assertEqual(cifras(jugadores[942663]), {'starts': (6, 7, 7), 'appearances': (7, 7, 7), 'minutes': (504, 7, 7),
                                                     'goals': (6, 7, 7), 'assists': (0, 7, 7)})
        self.assertEqual(cifras(jugadores[1982588]), {'starts': (0, 8, 8), 'appearances': (5, 7, 8), 'minutes': (70, 7, 8),
                                                      'goals': (0, 7, 8), 'assists': (0, 7, 8)})
        # Salvi: no utilizado en los 8 (0 minutos por no haber jugado, nunca «sin datos» ni minutos inventados).
        self.assertEqual(cifras(jugadores[1086428]), {'starts': (0, 8, 8), 'appearances': (0, 8, 8), 'minutes': (0, 8, 8),
                                                      'goals': (0, 8, 8), 'assists': (0, 8, 8)})
        self.assertEqual((jugadores[942663]['name'], jugadores[942663]['height_cm'], jugadores[942663]['last_shirt_number']),
                         ('Marco Siverio Toro', 184, 9))

    def test_goles_y_asistencias_cuadran_con_las_incidencias(self):
        datos = DATOS_REALES
        for m in (m for m in datos['partidos'] if m['status'] == 'finished'):
            contexto = api.contexto_partido(datos, m['match_id'])
            self.assertIsNotNone(contexto['goles'], m['match_id'])
            self.assertEqual(contexto['cobertura'], {'home': True, 'away': True}, m['match_id'])
            actuaciones = api.actuaciones_del_partido(datos, m['match_id']).values()
            for lado in ('home', 'away'):
                goles = sum(a['goals']['value'] or 0 for a in actuaciones if a['side'] == lado)
                self.assertEqual(goles, m[f'{lado}_score'], (m['match_id'], lado))
            self.assertFalse([a for a in actuaciones if 'goals_mismatch' in a['issues'] or 'assists_mismatch' in a['issues']])
        # El único caso sin resolver de ambos equipos.
        desconocidas = [(m['match_id'], a['player_id'], a['basis']) for m in datos['partidos'] if m['status'] == 'finished'
                        for a in api.actuaciones_del_partido(datos, m['match_id']).values() if a['participation'] == 'unknown']
        self.assertEqual(desconocidas, [(16599916, 1982588, 'stats_without_entry')])
        # Discrepancia pendiente, con su evidencia: suplente sin minutos ni entrada (el lado cuadra sus 5 cambios) pero
        # con estadísticas de toques y pases. Se conserva tal cual para contrastarla con otras fuentes.
        [evidencia] = api.actuaciones_del_partido(datos, 16599916)[1982588]['evidence']
        self.assertEqual(evidencia['fields'], {
            'substitute': [True], 'played': [None], 'minutes_played': [None],
            'statistics_keys': ['accurateOppositionHalfPasses', 'accuratePass', 'expectedAssists', 'totalOppositionHalfPasses',
                                'totalPass', 'touches'],
            'substitution_in': False, 'lineup_confirmed': True, 'substitutions_cover_side': True})
        self.assertEqual(evidencia['records'], ['2026-10-21T12:07:34+00:00'])



if __name__ == '__main__':
    unittest.main()
