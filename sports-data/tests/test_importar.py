"""Pruebas del importador (importar.py) con exportaciones raw JSONL pequeñas y sintéticas, y de cómo las sirve api.py.

No necesitan datos descargados: cada prueba escribe sus exportaciones en una carpeta temporal.
Ejecutar desde la carpeta de la API:  python -m unittest discover -s tests -v
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
import api  # noqa: E402
import importar  # noqa: E402

JAEN, IBIZA, RIVAL = 2841, 24368, 9001
GRUPO_2 = {'tournament': {'id': 94191, 'name': 'Primera Federacion, Group 2', 'uniqueTournament': {'id': 17073}},
           'season': {'id': 97382, 'year': '26/27'}}
FIJO = '2026-10-22T09:00:00+00:00'


def evento(ident, local, visitante, estado='finished', marcador=(1, 0), ronda=1, **cambios):
    e = dict(GRUPO_2, id=ident, homeTeam={'id': local, 'name': f'Equipo {local}'}, awayTeam={'id': visitante, 'name': f'Equipo {visitante}'},
             status={'type': estado}, homeScore={'current': marcador[0]} if marcador else {}, awayScore={'current': marcador[1]} if marcador else {},
             roundInfo={'round': ronda}, startTimestamp=1790000000 + ronda * 604800)
    e.update(cambios)
    return e


def entrada(jugador, equipo, suplente=False, minutos=90, nombre=None, altura=180, pais='Spain', **estadisticas):
    stats = {'totalShots': 0, 'statisticsType': {'statisticsType': 'player'}}
    if minutos is not None:
        stats.update(minutesPlayed=minutos, totalPass=20, accuratePass=15, touches=40)
    stats.update(estadisticas)
    jugador_sofa = {'id': jugador, 'name': nombre or f'Jugador {jugador}', 'position': 'M', 'country': {'name': pais}}
    if altura is not None:
        jugador_sofa['height'] = altura
    return {'player': jugador_sofa, 'teamId': equipo, 'shirtNumber': jugador % 100, 'substitute': suplente, 'statistics': stats}


def alineacion(local, visitante, confirmada=True):
    return {'confirmed': confirmada, 'home': {'players': local}, 'away': {'players': visitante}}


def linea(ident, key, fetched_at, payload):
    return {'event_id': ident, 'key': key, 'sub': '', 'fetched_at': fetched_at, 'payload': payload}


class Base(unittest.TestCase):
    def setUp(self):
        self.carpeta = Path(tempfile.mkdtemp(prefix='importar-test-'))
        self.addCleanup(shutil.rmtree, self.carpeta, True)

    def exportacion(self, nombre, lineas):
        ruta = self.carpeta / nombre
        ruta.write_text(''.join(json.dumps(l, ensure_ascii=False) + '\n' for l in lineas), encoding='utf-8')
        return ruta

    def importar(self, *exportaciones):
        return importar.transformar(list(exportaciones), importado_en=FIJO)

    def servir(self, datos):
        """Escribe la salida del importador como lo hace main() y la carga con api.py (ámbito del proyecto)."""
        destino = self.carpeta / f'salida-{len(list(self.carpeta.glob("salida-*")))}'
        destino.mkdir()
        for nombre, contenido in datos.items():
            (destino / f'{nombre}.json').write_text(json.dumps(contenido, ensure_ascii=False), encoding='utf-8')
        return api.cargar(destino)


def partido_basico(ident=100, local=JAEN, visitante=RIVAL, fetched_at='2026-10-21T12:00:00+00:00', **kw):
    """event + alineación confirmada posterior al partido + estadísticas."""
    return [linea(ident, 'event', fetched_at, evento(ident, local, visitante, **kw)),
            linea(ident, 'lineups', fetched_at, alineacion([entrada(1, local), entrada(2, local, suplente=True, minutos=None)],
                                                           [entrada(3, visitante)])),
            linea(ident, 'statistics', fetched_at, {'statistics': [{'period': 'ALL', 'groups': []}]})]


class Deduplicacion(Base):
    def test_importacion_repetida_no_duplica_partidos_ni_convocatorias(self):
        ruta = self.exportacion('a.jsonl', partido_basico())
        datos = self.importar((ruta, JAEN), (ruta, JAEN))
        self.assertEqual(len(datos['partidos']), 1)
        self.assertEqual(len(datos['jugador_partido']), 3)
        # Se conservan todas las líneas con su procedencia; una sola operativa por sección; nada que discrepar.
        self.assertEqual(len(datos['originales']), 6)
        self.assertEqual(sum(r['selected'] for r in datos['originales']), 3)
        self.assertEqual({(r['export'], r['line']) for r in datos['originales']}, {(e, l) for e in ('E1', 'E2') for l in (1, 2, 3)})
        self.assertEqual(datos['discrepancias'], [])
        self.assertEqual(datos['resumen']['identical_repeats'], 3)
        # Determinista: la misma entrada da la misma salida.
        self.assertEqual(self.importar((ruta, JAEN), (ruta, JAEN)), datos)

    def test_solapamiento_con_contenido_identico_no_es_discrepancia(self):
        a = self.exportacion('jaen.jsonl', partido_basico(local=JAEN, visitante=IBIZA))
        b = self.exportacion('ibiza.jsonl', [dict(l, fetched_at='2026-10-23T08:00:00+00:00')
                                             for l in partido_basico(local=JAEN, visitante=IBIZA)])
        datos = self.importar((a, JAEN), (b, IBIZA))
        self.assertEqual(datos['discrepancias'], [])
        [partido] = datos['partidos']
        self.assertEqual(partido['exports'], ['E1', 'E2'])
        self.assertFalse(partido['version_conflict'])
        servidos = self.servir(datos)
        self.assertEqual(api.consultar(servidos, '/api/v1/matches/100/statistics')[1]['meta']['versions'], 2)

    def test_contenido_discrepante_se_conserva_y_se_informa(self):
        base = partido_basico()
        a = self.exportacion('a.jsonl', base)
        b = self.exportacion('b.jsonl', [linea(100, 'statistics', '2026-10-22T12:00:00+00:00',
                                               {'statistics': [{'period': 'ALL', 'groups': [{'groupName': 'corregido'}]}]})])
        datos = self.importar((a, JAEN), (b, None))
        [d] = datos['discrepancias']
        self.assertEqual((d['key'], d['reasons'], d['integrity'], len(d['versions'])), ('statistics', ['comparable_versions_differ'], False, 2))
        self.assertEqual(d['selected']['export'], 'E2')
        servidos = self.servir(datos)
        meta = api.consultar(servidos, '/api/v1/matches/100/statistics')[1]['meta']
        self.assertEqual((meta['versions'], meta['discrepancy']), (2, {'reasons': ['comparable_versions_differ'], 'integrity': False}))

    def test_fechas_como_instantes_con_desfase_y_fechas_invalidas(self):
        a = self.exportacion('a.jsonl', partido_basico(fetched_at='2026-10-21T14:00:00+02:00'))  # 12:00Z
        b = self.exportacion('b.jsonl', [linea(100, 'statistics', '2026-10-21T12:30:00+00:00', {'statistics': [{'period': 'B'}]}),
                                         linea(100, 'statistics', 'ayer', {'statistics': [{'period': 'INVALIDA'}]}),
                                         linea(100, 'statistics', '2026-10-21T13:00:00', {'statistics': [{'period': 'SIN DESFASE'}]})])
        datos = self.importar((a, JAEN), (b, None))
        [d] = datos['discrepancias']
        # 12:30Z es posterior a 14:00+02:00 aunque como texto sea menor; ni la inválida ni la que no tiene desfase ganan.
        elegida = next(r for r in datos['originales'] if r['selected'] and r['key'] == 'statistics')
        self.assertEqual(elegida['payload'], {'statistics': [{'period': 'B'}]})
        self.assertEqual(sorted(v['fetched_at_valid'] for v in d['versions']), [False, False, True, True])
        self.assertEqual(datos['exportaciones']['exports'][1]['invalid_fetched_at'], 2)
        self.assertEqual(importar.instante('2026-10-21T14:00:00+02:00'), importar.instante('2026-10-21T12:00:00Z'))


class Estados(Base):
    def importar_versiones(self, *versiones):
        lineas = [linea(100, 'event', fecha, evento(100, JAEN, RIVAL, **kw)) for fecha, kw in versiones]
        return self.importar((self.exportacion('e.jsonl', lineas), JAEN))

    def test_una_version_final_sustituye_a_una_preliminar(self):
        datos = self.importar_versiones(('2026-10-20T12:00:00+00:00', {'estado': 'notstarted', 'marcador': None}),
                                        ('2026-10-22T12:00:00+00:00', {'estado': 'finished', 'marcador': (2, 0)}))
        [d] = datos['discrepancias']
        self.assertEqual((d['reasons'], d['integrity']), (['preliminary_superseded'], False))
        self.assertEqual(datos['partidos'][0]['status'], 'finished')

    def test_un_estado_no_final_posterior_a_uno_final_es_conflicto_y_no_alimenta_agregados(self):
        datos = self.importar_versiones(('2026-10-20T12:00:00+00:00', {'estado': 'finished', 'marcador': (2, 0)}),
                                        ('2026-10-22T12:00:00+00:00', {'estado': 'postponed', 'marcador': None}))
        [d] = datos['discrepancias']
        self.assertEqual((d['reasons'], d['integrity']), (['non_final_after_final'], True))
        self.assertTrue(datos['partidos'][0]['version_conflict'])
        competicion = api.consultar(self.servir(datos), f'/api/v1/teams/{JAEN}/performance')[1]['data']['competitions'][0]
        self.assertEqual((competicion['matches']['excluded_by_version_conflict'], competicion['record']['played']), (1, 0))

    def test_un_aplazado_que_despues_se_juega_no_es_conflicto(self):
        datos = self.importar_versiones(('2026-10-20T12:00:00+00:00', {'estado': 'postponed', 'marcador': None}),
                                        ('2026-10-22T12:00:00+00:00', {'estado': 'finished', 'marcador': (1, 1)}))
        self.assertEqual((datos['discrepancias'][0]['integrity'], datos['partidos'][0]['version_conflict']), (False, False))

    def test_un_marcador_final_corregido_se_usa_e_informa(self):
        datos = self.importar_versiones(('2026-10-20T12:00:00+00:00', {'estado': 'finished', 'marcador': (1, 0)}),
                                        ('2026-10-22T12:00:00+00:00', {'estado': 'finished', 'marcador': (2, 0)}))
        [d] = datos['discrepancias']
        self.assertEqual((d['reasons'], d['integrity'], d['changed_fields']), (['final_score_corrected'], False, ['home_score']))
        record = api.consultar(self.servir(datos), f'/api/v1/teams/{JAEN}/performance')[1]['data']['competitions'][0]['record']
        self.assertEqual((record['goals_for'], record['played']), (2, 1))

    def test_un_cambio_de_identidad_es_conflicto(self):
        datos = self.importar_versiones(('2026-10-20T12:00:00+00:00', {}),
                                        ('2026-10-22T12:00:00+00:00', {'awayTeam': {'id': 9002, 'name': 'Otro'}}))
        self.assertEqual(datos['discrepancias'][0]['reasons'][0], 'identity_changed')
        self.assertTrue(datos['partidos'][0]['version_conflict'])

    def test_sin_version_final_la_mas_reciente_sin_conflicto(self):
        datos = self.importar_versiones(('2026-10-20T12:00:00+00:00', {'estado': 'notstarted', 'marcador': None}),
                                        ('2026-10-22T12:00:00+00:00', {'estado': 'postponed', 'marcador': None}))
        self.assertEqual((datos['discrepancias'][0]['reasons'], datos['partidos'][0]['status']), (['status_changed'], 'postponed'))


class Alineaciones(Base):
    def test_una_alineacion_confirmada_gana_a_una_preliminar_posterior(self):
        lineas = partido_basico() + [linea(100, 'lineups', '2026-10-23T12:00:00+00:00',
                                           alineacion([entrada(1, JAEN, minutos=None)], [], confirmada=False))]
        datos = self.importar((self.exportacion('a.jsonl', lineas), JAEN))
        [d] = datos['discrepancias']
        self.assertEqual(d['reasons'], ['preliminary_superseded'])
        self.assertEqual(len(datos['jugador_partido']), 3)

    def test_versiones_posteriores_que_no_coinciden_no_resuelven_la_participacion(self):
        # Como el caso Gallego: la versión antigua trae estadísticas sin minutos; una nueva le da minutos. La nueva es la
        # operativa, pero el desacuerdo queda como incidencia, con las dos versiones, y la participación sin resolver.
        antigua = alineacion([entrada(1, JAEN), entrada(2, JAEN, suplente=True, minutos=None, touches=3, totalPass=2)], [entrada(3, RIVAL)])
        nueva = alineacion([entrada(1, JAEN), entrada(2, JAEN, suplente=True, minutos=10)], [entrada(3, RIVAL)])
        lineas = [linea(100, 'event', '2026-10-21T12:00:00+00:00', evento(100, JAEN, RIVAL)),
                  linea(100, 'lineups', '2026-10-21T12:00:00+00:00', antigua),
                  linea(100, 'lineups', '2026-10-23T12:00:00+00:00', nueva)]
        datos = self.importar((self.exportacion('a.jsonl', lineas), JAEN))
        servidos = self.servir(datos)
        a = api.actuaciones_del_partido(servidos, 100)[2]
        self.assertEqual((a['participation'], a['basis'], a['issues']), ('unknown', 'lineup_versions_disagree', ['lineup_versions_disagree']))
        [evidencia] = a['evidence']
        self.assertEqual([v['minutesPlayed'] for v in evidencia['fields']['lineup_versions']], [None, 10])
        self.assertEqual(len(evidencia['versions']['lineups']), 2)
        # Quien coincide en ambas versiones se resuelve como siempre.
        self.assertEqual(api.actuaciones_del_partido(servidos, 100)[1]['participation'], 'played')


class ExtraccionDeConvocatorias(Base):
    """importar.extraer_convocatorias: la única extracción de convocatorias (importador JSON y futura carga SQL)."""

    def registro(self, local, visitante):
        return dict(linea(100, 'lineups', FIJO, alineacion(local, visitante)), export='E1', line=2)

    def test_orden_tipos_nulos_y_claves_ausentes_tal_cual(self):
        local = [entrada(7, JAEN, totalShots=True, blockedScoringAttempt=None, fouls='2'), entrada(2, JAEN, suplente=True, minutos=None)]
        sin_stats = entrada(3, RIVAL)
        del sin_stats['statistics'], sin_stats['shirtNumber']
        filas, avisos = importar.extraer_convocatorias(self.registro(local, [sin_stats]),
                                                       {'home_team_id': JAEN, 'away_team_id': RIVAL})
        self.assertEqual(([f['player_id'] for f in filas], [f['side'] for f in filas]), ([7, 2, 3], ['home', 'home', 'away']))
        self.assertEqual(avisos, [])
        stats = filas[0]['statistics']
        self.assertIs(stats['totalShots'], True)
        self.assertEqual((stats['blockedScoringAttempt'], stats['fouls']), (None, '2'))
        self.assertEqual(list(stats), list(local[0]['statistics']))
        self.assertNotIn('onTargetScoringAttempt', stats)
        self.assertEqual((filas[1]['minutes_played'], filas[1]['played']), (None, None))
        self.assertEqual((filas[2]['statistics'], filas[2]['shirt_number'], filas[2]['minutes_played']), (None, None, None))
        self.assertEqual((filas[0]['fetched_at'], filas[0]['source_team_id']), (FIJO, JAEN))

    def test_el_equipo_es_el_del_lado_y_el_teamid_distinto_se_avisa(self):
        filas, avisos = importar.extraer_convocatorias(self.registro([entrada(4, RIVAL)], []),
                                                       {'home_team_id': JAEN, 'away_team_id': RIVAL})
        self.assertEqual((filas[0]['team_id'], filas[0]['source_team_id']), (JAEN, RIVAL))
        self.assertEqual(len(avisos), 1)

    def test_extrae_los_repetidos_y_la_importacion_los_rechaza(self):
        # La extracción no decide: devuelve las dos entradas. Rechazarlas es una regla de la importación.
        filas, _ = importar.extraer_convocatorias(self.registro([entrada(1, JAEN), entrada(1, JAEN, minutos=10)], []),
                                                  {'home_team_id': JAEN, 'away_team_id': RIVAL})
        self.assertEqual([f['player_id'] for f in filas], [1, 1])
        lineas = [linea(100, 'event', FIJO, evento(100, JAEN, RIVAL)),
                  linea(100, 'lineups', FIJO, alineacion([entrada(1, JAEN), entrada(1, JAEN, minutos=10)], []))]
        with self.assertRaisesRegex(ValueError, r'Jugador duplicado en alineación: \(100, 2841, 1\)'):
            self.importar((self.exportacion('a.jsonl', lineas), JAEN))

    def test_transformar_usa_la_misma_extraccion(self):
        lineas = partido_basico()
        datos = self.importar((self.exportacion('a.jsonl', lineas), JAEN))
        [lineup] = [r for r in datos['originales'] if r['key'] == 'lineups']
        filas, _ = importar.extraer_convocatorias(lineup, datos['partidos'][0])
        self.assertEqual(sorted(filas, key=lambda f: (f['match_id'], f['team_id'], f['player_id'])), datos['jugador_partido'])


class EquiposYJugadores(Base):
    def test_jugador_observado_en_varios_equipos(self):
        lineas = [linea(100, 'event', FIJO, evento(100, JAEN, RIVAL, ronda=1)),
                  linea(100, 'lineups', FIJO, alineacion([entrada(7, JAEN)], [entrada(3, RIVAL)])),
                  linea(200, 'event', FIJO, evento(200, IBIZA, RIVAL, ronda=2)),
                  linea(200, 'lineups', FIJO, alineacion([entrada(7, IBIZA)], [entrada(4, RIVAL)]))]
        datos = self.importar((self.exportacion('a.jsonl', lineas), JAEN))
        self.assertEqual(sorted(r['team_id'] for r in datos['jugador_partido'] if r['player_id'] == 7), [JAEN, IBIZA])
        servidos = self.servir(datos)
        for equipo in (JAEN, IBIZA):
            self.assertIn(7, {p['player_id'] for p in api.consultar(servidos, f'/api/v1/teams/{equipo}/players')[1]['data']})
        # Una sola ficha, sin equipo: los dorsales y equipos son relaciones por partido, no atributos del jugador.
        [ficha] = [p for p in datos['jugadores'] if p['player_id'] == 7]
        self.assertNotIn('team_id', ficha)

    def test_los_datos_maestros_no_se_borran_con_una_ausencia(self):
        lineas = partido_basico() + [linea(101, 'event', FIJO, evento(101, RIVAL, JAEN, ronda=2)),
                                     linea(101, 'lineups', '2026-10-25T12:00:00+00:00',
                                           alineacion([entrada(3, RIVAL)], [entrada(1, JAEN, altura=None, pais='Portugal')]))]
        datos = self.importar((self.exportacion('a.jsonl', lineas), JAEN))
        [ficha] = [p for p in datos['jugadores'] if p['player_id'] == 1]
        # La estatura conocida se conserva; el país cambia al más reciente y la discrepancia queda registrada.
        self.assertEqual((ficha['height_cm'], ficha['country']), (180, 'Portugal'))
        [d] = [d for d in datos['discrepancias'] if d.get('player_id') == 1]
        self.assertEqual(d['values'], {'country': ['Spain', 'Portugal']})

    def test_exportacion_propia_frente_a_rival_observado(self):
        a = self.exportacion('jaen.jsonl', partido_basico(ident=100, local=JAEN, visitante=RIVAL))
        b = self.exportacion('ibiza.jsonl', partido_basico(ident=200, local=IBIZA, visitante=RIVAL))
        datos = self.importar((a, JAEN), (b, IBIZA))
        self.assertEqual(datos['exportaciones']['followed_team_ids'], [JAEN, IBIZA])
        alcance = api.consultar(self.servir(datos), '/api/v1/scope')[1]
        self.assertEqual([e['team_id'] for e in alcance['data']['teams_with_own_export']], [JAEN, IBIZA])
        # El rival sólo aparece en partidos ajenos; y una exportación propia no acredita cobertura completa.
        self.assertIn('no acredita', alcance['meta']['note'])
        self.assertEqual(alcance['data']['teams_with_own_export'][1]['exports'][0]['file'], 'ibiza.jsonl')

    def test_una_exportacion_sin_equipo_declarado_no_da_cobertura(self):
        datos = self.importar((self.exportacion('a.jsonl', partido_basico()), None))
        self.assertEqual(api.consultar(self.servir(datos), '/api/v1/scope')[1]['data']['teams_with_own_export'], [])


def fila_listado(ident, local, visitante, ronda=38, estado='notstarted', torneo=17073, grupo=94191, temporada=97382):
    """Una fila del dataset normalizado `events` del scraper (esquema v1), conocida sólo por el calendario."""
    return {'id': ident, 'sport': 'football', 'tournament_id': torneo, 'season_id': temporada,
            'stage': {'id': grupo, 'name': 'Primera Federacion, Group 2'}, 'round': {'number': ronda, 'name': None, 'slug': None},
            'start_utc': '2027-05-23T15:00:00Z', 'status': {'type': estado, 'code': 0, 'description': 'Not started', 'class': 'not_started'},
            'participants': {'home': {'id': local, 'name': f'Equipo {local}'}, 'away': {'id': visitante, 'name': f'Equipo {visitante}'}},
            'score': {'family': 'football', 'home': None, 'away': None}, 'quality': {'source': 'listing', 'observed_at_utc': None}}


class Calendario(Base):
    """Partidos sin payload raw, desde el dataset normalizado `events` del scraper (`--calendario`)."""

    def calendario(self, nombre, filas):
        ruta = self.carpeta / nombre
        ruta.write_text(''.join(json.dumps(f, ensure_ascii=False) + '\n' for f in filas), encoding='utf-8')
        return ruta

    def test_un_partido_solo_del_calendario_entra_sin_secciones_con_su_procedencia(self):
        raw = self.exportacion('jaen.jsonl', partido_basico())
        cal = self.calendario('j38.jsonl', [fila_listado(300, IBIZA, RIVAL)])
        datos = importar.transformar([(raw, JAEN)], importado_en=FIJO, calendarios=[cal])
        partido = next(m for m in datos['partidos'] if m['match_id'] == 300)
        self.assertEqual((partido['calendar_source'], partido['available_sections'], partido['round'], partido['tournament_id'],
                          partido['status'], partido['kickoff_utc'], partido['home_score']),
                         ('listing', [], 38, 94191, 'notstarted', '2027-05-23T15:00:00+00:00', None))
        # La temporada se nombra como en los partidos raw de la misma temporada; la procedencia se conserva.
        self.assertEqual(partido['season'], '26/27')
        self.assertEqual(partido['exports'], ['E2'])
        [registro] = [r for r in datos['originales'] if r['key'] == 'listing']
        self.assertEqual((registro['export'], registro['line'], registro['selected']), ('E2', 1, True))
        # Un calendario no declara cobertura de ningún equipo.
        self.assertEqual(datos['exportaciones']['followed_team_ids'], [JAEN])
        self.assertEqual(datos['exportaciones']['exports'][1]['kind'], 'listing')
        servidos = self.servir(datos)
        self.assertEqual(api.consultar(servidos, '/api/v1/matches/300')[1]['meta']['sections'],
                         {s: 'not_stored' for s in ('statistics', 'lineups', 'incidents')})
        self.assertEqual(api.consultar(servidos, f'/api/v1/teams/{IBIZA}/performance')[1]['data']['competitions'][0]['matches']['pending'], 1)

    def test_el_event_raw_manda_y_la_diferencia_queda_informada(self):
        raw = self.exportacion('jaen.jsonl', partido_basico())
        cal = self.calendario('cal.jsonl', [fila_listado(100, JAEN, RIVAL, ronda=1, estado='finished')])
        datos = importar.transformar([(raw, JAEN)], importado_en=FIJO, calendarios=[cal])
        [partido] = [m for m in datos['partidos'] if m['match_id'] == 100]
        self.assertEqual((partido['calendar_source'], partido['home_score'], partido['available_sections']),
                         ('event', 1, ['event', 'lineups', 'statistics']))
        [d] = datos['discrepancias']
        self.assertEqual((d['reasons'], d['integrity'], d['key']), (['listing_differs_from_event'], False, 'listing'))
        self.assertIn('kickoff_utc', d['changed_fields'])

    def test_un_partido_del_calendario_fuera_del_ambito_no_se_sirve(self):
        cal = self.calendario('cal.jsonl', [fila_listado(400, JAEN, RIVAL, grupo=94190)])
        datos = importar.transformar([(self.exportacion('jaen.jsonl', partido_basico()), JAEN)], importado_en=FIJO, calendarios=[cal])
        servidos = self.servir(datos)
        self.assertIn(400, servidos['excluidos'])
        self.assertEqual(api.consultar(servidos, '/api/v1/matches/400')[0], 404)

    def test_una_fila_que_no_es_del_dataset_events_se_rechaza(self):
        cal = self.calendario('mal.jsonl', [{'event_id': 1, 'key': 'event', 'payload': {}}])
        with self.assertRaises(ValueError):
            importar.transformar([], importado_en=FIJO, calendarios=[cal])


class LineaDeComandos(Base):
    def ejecutar(self, *argumentos):
        entorno = dict(os.environ, PYTHONIOENCODING='utf-8')
        return subprocess.run([sys.executable, str(RAIZ / 'importar.py'), *argumentos], capture_output=True, text=True,
                              encoding='utf-8', cwd=self.carpeta, env=entorno)

    def test_importa_varias_exportaciones_a_una_carpeta_nueva_sin_dejar_restos(self):
        a = self.exportacion('jaen.jsonl', partido_basico(ident=100))
        b = self.exportacion('ibiza.jsonl', partido_basico(ident=200, local=IBIZA))
        resultado = self.ejecutar('--exportacion', f'{JAEN}={a}', '--exportacion', f'{IBIZA}={b}', '--salida', 'nueva')
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        self.assertEqual(sorted(p.name for p in (self.carpeta / 'nueva').iterdir()),
                         sorted(f'{n}.json' for n in ('equipos', 'jugadores', 'partidos', 'jugador_partido', 'originales',
                                                      'discrepancias', 'exportaciones', 'resumen')))
        self.assertFalse((self.carpeta / 'nueva.parcial').exists())
        # La entrada no se modifica y una carpeta existente no se sobrescribe.
        self.assertEqual(a.read_text(encoding='utf-8'), ''.join(json.dumps(l, ensure_ascii=False) + '\n' for l in partido_basico(ident=100)))
        otra = self.ejecutar('--exportacion', f'{JAEN}={a}', '--salida', 'nueva')
        self.assertNotEqual(otra.returncode, 0)
        self.assertIn('ya existe', otra.stderr)

    def test_una_entrada_invalida_no_deja_salida(self):
        mala = self.carpeta / 'mala.jsonl'
        mala.write_text('{"event_id": 1, "key": "event"}\n', encoding='utf-8')
        resultado = self.ejecutar(str(mala), '--salida', 'nueva')
        self.assertNotEqual(resultado.returncode, 0)
        self.assertFalse((self.carpeta / 'nueva').exists())
        self.assertFalse((self.carpeta / 'nueva.parcial').exists())

    def test_calendario_por_linea_de_comandos(self):
        a = self.exportacion('jaen.jsonl', partido_basico(ident=100))
        cal = self.carpeta / 'j38.jsonl'
        cal.write_text(json.dumps(fila_listado(300, JAEN, RIVAL)) + '\n', encoding='utf-8')
        resultado = self.ejecutar('--exportacion', f'{JAEN}={a}', '--calendario', str(cal), '--salida', 'nueva')
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        partidos = json.loads((self.carpeta / 'nueva' / 'partidos.json').read_text(encoding='utf-8'))
        self.assertEqual(sorted(m['match_id'] for m in partidos), [100, 300])

    def test_formato_de_exportacion_equipo_ruta(self):
        resultado = self.ejecutar('--exportacion', 'jaen.jsonl', '--salida', 'nueva')
        self.assertEqual(resultado.returncode, 2)
        self.assertIn('EQUIPO=RUTA', resultado.stderr)


if __name__ == '__main__':
    unittest.main()
