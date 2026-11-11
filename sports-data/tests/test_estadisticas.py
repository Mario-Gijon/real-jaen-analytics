"""Estadísticas individuales normalizadas (docs/normalizacion-estadisticas-individuales.md, casos T1-T17).

Unitarias sobre api.estadisticas_de_actuacion y, de punta a punta, con exportaciones sintéticas del importador.
Ejecutar desde la carpeta de la API:  python -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import api  # noqa: E402
from test_importar import JAEN, RIVAL, Base, alineacion, entrada, evento, linea  # noqa: E402

JUGADO = {'minutesPlayed': 90, 'statisticsType': {'statisticsType': 'player'}}


def normalizar(*registros, estado='played', conflictos=None, incidencias=None):
    registros = [dict(JUGADO, **r) for r in registros]
    gol = api.estado_de_gol(registros, incidencias, conflictos or {})
    return api.estadisticas_de_actuacion(registros, estado, conflictos or {}, gol)


def metrica(resultado, nombre):
    m = resultado[0][nombre]
    return (m['value'], m['source'], m['rule'], m['reason'])


class TirosAPuerta(unittest.TestCase):
    def test_t1_keita_dos_tiros_bloqueados(self):
        # Keita ante Rayo Majadahonda: totalShots 2, blockedScoringAttempt 2, sin shotOffTarget ni a puerta.
        r = normalizar({'totalShots': 2, 'blockedScoringAttempt': 2})
        self.assertEqual(metrica(r, 'shots_on_target'), (0, 'derived', 'shots_partition', None))
        self.assertEqual(metrica(r, 'shots'), (2, 'reported', None, None))
        self.assertEqual(r[1], [])

    def test_t2_sin_tiros(self):
        self.assertEqual(metrica(normalizar({'totalShots': 0}), 'shots_on_target'), (0, 'derived', 'shots_partition', None))

    def test_t3_cero_explicito(self):
        r = normalizar({'totalShots': 2, 'onTargetScoringAttempt': 0, 'shotOffTarget': 2})
        self.assertEqual(metrica(r, 'shots_on_target'), (0, 'reported', None, None))

    def test_t4_los_complementos_no_agotan_el_total(self):
        # El fuera ausente no se suma como 0: sin prueba, sin dato.
        r = normalizar({'totalShots': 2, 'blockedScoringAttempt': 1})
        self.assertEqual(metrica(r, 'shots_on_target'), (None, None, None, 'not_reported'))

    def test_t5_categorias_que_exceden_el_total(self):
        r = normalizar({'totalShots': 1, 'onTargetScoringAttempt': 1, 'shotOffTarget': 1, 'totalPass': 20, 'accuratePass': 15})
        for nombre in ('shots', 'shots_on_target'):
            self.assertEqual(metrica(r, nombre), (None, None, None, 'inconsistent'))
        # Sólo el grupo de tiros: los pases conservan su valor.
        self.assertEqual(metrica(r, 'passes'), (20, 'reported', None, None))
        self.assertEqual([(c, g) for c, g, _ in r[1]], [('stat_inconsistent', 'tiros')])

    def test_t6_un_gol_fiable_impide_la_deduccion_incompatible(self):
        r = normalizar({'totalShots': 1, 'shotOffTarget': 1, 'goals': 1})
        self.assertEqual(metrica(r, 'shots_on_target'), (None, None, None, 'inconsistent'))
        # El gol de las incidencias también es fiable aunque statistics.goals falte.
        r = normalizar({'totalShots': 1, 'shotOffTarget': 1}, incidencias=1)
        self.assertEqual(metrica(r, 'shots_on_target'), (None, None, None, 'inconsistent'))
        # Más goles que tiros a puerta explícitos.
        r = normalizar({'totalShots': 3, 'onTargetScoringAttempt': 1, 'goals': 2})
        self.assertEqual(metrica(r, 'shots_on_target'), (None, None, None, 'inconsistent'))

    def test_un_gol_dudoso_no_permite_deducir(self):
        # statistics.goals contradice a las incidencias: no es fiable, ni para bloquear como incoherencia ni para deducir.
        r = normalizar({'totalShots': 1, 'shotOffTarget': 1, 'goals': 1}, incidencias=0)
        self.assertEqual(metrica(r, 'shots_on_target'), (None, None, None, 'not_reported'))

    def test_la_ausencia_de_goles_no_bloquea_ni_se_toma_como_cero(self):
        r = normalizar({'totalShots': 2, 'blockedScoringAttempt': 2}, incidencias=None)
        self.assertEqual(metrica(r, 'shots_on_target'), (0, 'derived', 'shots_partition', None))

    def test_t7_el_palo_no_es_una_categoria(self):
        r = normalizar({'totalShots': 1, 'shotOffTarget': 1, 'hitWoodwork': 1})
        self.assertEqual(metrica(r, 'shots_on_target'), (0, 'derived', 'shots_partition', None))

    def test_t11_un_complemento_invalido_impide_la_deduccion(self):
        for malo in (-1, True, 1.5, '2'):
            with self.subTest(malo=malo):
                r = normalizar({'totalShots': 2, 'blockedScoringAttempt': malo, 'shotOffTarget': 2, 'totalPass': 20})
                self.assertEqual(metrica(r, 'shots_on_target'), (None, None, None, 'not_reported'))
                self.assertEqual(metrica(r, 'shots'), (2, 'reported', None, None))
                self.assertEqual(metrica(r, 'passes'), (20, 'reported', None, None))
                self.assertIn(('stat_invalid', 'tiros', ['blockedScoringAttempt']), r[1])

    def test_una_clave_mostrada_invalida_queda_sin_dato_y_como_incidencia(self):
        r = normalizar({'totalShots': -1, 'totalPass': 20})
        self.assertEqual(metrica(r, 'shots'), (None, None, None, 'invalid'))
        self.assertEqual(metrica(r, 'shots_on_target'), (None, None, None, 'not_reported'))
        self.assertEqual(metrica(r, 'passes'), (20, 'reported', None, None))

    def test_t12_null_explicito(self):
        r = normalizar({'totalShots': 0, 'onTargetScoringAttempt': None})
        self.assertEqual(metrica(r, 'shots_on_target'), (None, None, None, 'reported_null'))
        # Un complemento null tampoco se trata como ausente.
        r = normalizar({'totalShots': 2, 'blockedScoringAttempt': 2, 'shotOffTarget': None})
        self.assertEqual(metrica(r, 'shots_on_target'), (None, None, None, 'not_reported'))


class Participacion(unittest.TestCase):
    def test_t8_no_utilizado(self):
        r = normalizar({'totalShots': 0}, estado='unused')
        self.assertEqual(metrica(r, 'shots_on_target'), (0, 'unused', None, None))
        self.assertEqual(metrica(r, 'fouls'), (0, 'unused', None, None))
        for nombre in ('xg', 'rating', 'pass_accuracy'):
            self.assertEqual(metrica(r, nombre), (None, None, None, 'not_applicable'))

    def test_t9_participacion_sin_resolver(self):
        r = normalizar({'totalShots': 0}, estado='unknown')
        self.assertEqual({m['reason'] for m in r[0].values()}, {'participation_unknown'})
        self.assertEqual({m['value'] for m in r[0].values()}, {None})


class GruposYDuplicados(unittest.TestCase):
    def test_t10_duplicados_que_discrepan_solo_afectan_a_su_grupo(self):
        r = normalizar({'totalShots': 2, 'blockedScoringAttempt': 2, 'totalContest': 3},
                       {'totalShots': 2, 'blockedScoringAttempt': 1, 'totalContest': 3})
        self.assertEqual(metrica(r, 'shots_on_target'), (None, None, None, 'duplicate_disagree'))
        self.assertEqual(metrica(r, 'dribbles'), (3, 'reported', None, None))
        self.assertEqual([(c, g) for c, g, _ in r[1]], [('stat_duplicate_disagree', 'tiros')])

    def test_t13_conflicto_de_versiones_solo_afecta_a_su_grupo(self):
        r = normalizar({'totalShots': 2, 'blockedScoringAttempt': 2, 'totalPass': 20},
                       conflictos={'tiros': [{'statistics': {}}, {'statistics': {}}]})
        self.assertEqual(metrica(r, 'shots_on_target'), (None, None, None, 'version_conflict'))
        self.assertEqual(metrica(r, 'shots'), (None, None, None, 'version_conflict'))
        self.assertEqual(metrica(r, 'passes'), (20, 'reported', None, None))

    def test_t14_regates_y_pases(self):
        self.assertEqual(metrica(normalizar({'totalContest': 4}), 'dribbles_won'), (None, None, None, 'not_reported'))
        r = normalizar({'totalContest': 2, 'wonContest': 3})
        self.assertEqual((metrica(r, 'dribbles')[3], metrica(r, 'dribbles_won')[3]), ('inconsistent', 'inconsistent'))
        r = normalizar({'totalPass': 21, 'accuratePass': 22, 'totalContest': 2})
        for nombre in ('passes', 'passes_accurate', 'pass_accuracy'):
            self.assertEqual(metrica(r, nombre), (None, None, None, 'inconsistent'))
        self.assertEqual(metrica(r, 'dribbles'), (2, 'reported', None, None))

    def test_t15_precision_de_pase(self):
        self.assertEqual(metrica(normalizar({'totalPass': 0, 'accuratePass': 0}), 'pass_accuracy'),
                         (None, None, None, 'not_applicable'))
        valor, source, rule, reason = metrica(normalizar({'totalPass': 21, 'accuratePass': 13}), 'pass_accuracy')
        self.assertEqual((round(valor, 1), source, rule, reason), (61.9, 'calculated', 'ratio', None))
        self.assertEqual(metrica(normalizar({'totalPass': 21}), 'pass_accuracy'), (None, None, None, 'not_reported'))

    def test_las_metricas_sin_relacion_solo_se_informan(self):
        # Faltas, regates ganados, duelos, paradas, xG: una ausencia nunca se convierte en 0.
        r = normalizar({'totalShots': 0, 'totalContest': 4})
        for nombre in ('fouls', 'dribbles_won', 'duels_won', 'duels_lost', 'saves', 'xg', 'ball_recoveries', 'possession_lost'):
            self.assertEqual(metrica(r, nombre), (None, None, None, 'not_reported'))


class DePuntaAPunta(Base):
    def servidos(self, *alineaciones_por_fecha):
        lineas = [linea(100, 'event', '2026-10-21T12:00:00+00:00', evento(100, JAEN, RIVAL, marcador=(0, 0)))]
        lineas += [linea(100, 'lineups', fecha, a) for fecha, a in alineaciones_por_fecha]
        lineas.append(linea(100, 'incidents', '2026-10-21T12:00:00+00:00', {'incidents': [{'incidentType': 'period'}]}))
        return self.servir(self.importar((self.exportacion('a.jsonl', lineas), JAEN)))

    def test_t13_versiones_que_discrepan_en_un_grupo(self):
        antigua = alineacion([entrada(1, JAEN, totalShots=2, blockedScoringAttempt=2)], [entrada(3, RIVAL)])
        nueva = alineacion([entrada(1, JAEN, totalShots=2, blockedScoringAttempt=1)], [entrada(3, RIVAL)])
        datos = self.servidos(('2026-10-21T12:00:00+00:00', antigua), ('2026-10-23T12:00:00+00:00', nueva))
        a = api.actuaciones_del_partido(datos, 100)[1]
        self.assertEqual(a['participation'], 'played')
        self.assertEqual(a['stats']['shots_on_target']['reason'], 'version_conflict')
        self.assertEqual(a['stats']['passes'], {'value': 20, 'source': 'reported', 'rule': None, 'reason': None})
        self.assertIn('stat_version_conflict', a['issues'])
        [evidencia] = [e for e in a['evidence'] if e['issue'] == 'stat_version_conflict']
        self.assertEqual([v['statistics'] for v in evidencia['fields']['lineup_versions']],
                         [{'totalShots': 2, 'blockedScoringAttempt': 2}, {'totalShots': 2, 'blockedScoringAttempt': 1}])
        # Quien coincide en ambas versiones conserva todo.
        self.assertEqual(api.actuaciones_del_partido(datos, 100)[3]['issues'], [])

    def test_t16_y_endpoints_con_la_misma_normalizacion(self):
        # La alineación está guardada, pero ninguna fila trae faltas: eso no prueba que sean 0.
        a = alineacion([entrada(1, JAEN, totalShots=2, blockedScoringAttempt=2)], [entrada(3, RIVAL)])
        datos = self.servidos(('2026-10-21T12:00:00+00:00', a))
        [fila] = api.consultar(datos, '/api/v1/players/1/matches')[1]['data']
        stats = fila['normalized']['stats']
        self.assertEqual(stats['fouls']['reason'], 'not_reported')
        self.assertEqual(stats['shots_on_target'], {'value': 0, 'source': 'derived', 'rule': 'shots_partition', 'reason': None})
        # El original no se toca.
        self.assertNotIn('onTargetScoringAttempt', fila['statistics'])
        jugadores = api.consultar(datos, '/api/v1/matches/100/lineups')[1]['normalized']['players']
        self.assertEqual(next(p for p in jugadores if p['player_id'] == 1)['stats'], stats)

    def dos_versiones(self, antiguas, nuevas):
        """Actuación del jugador 1 con dos versiones confirmadas posteriores al partido (la nueva es la seleccionada)."""
        antigua = alineacion([entrada(1, JAEN, **antiguas)], [entrada(3, RIVAL)])
        nueva = alineacion([entrada(1, JAEN, **nuevas)], [entrada(3, RIVAL)])
        datos = self.servidos(('2026-10-21T12:00:00+00:00', antigua), ('2026-10-27T13:00:00+00:00', nueva))
        return api.actuaciones_del_partido(datos, 100)[1], datos

    def test_ceros_explicitos_nuevos_no_son_conflicto(self):
        # Caso real del 27-10: la versión nueva añade ceros que la antigua omitía; ningún valor común cambia.
        a, _ = self.dos_versiones(dict(totalShots=2, blockedScoringAttempt=2),
                                  dict(totalShots=2, blockedScoringAttempt=2, onTargetScoringAttempt=0, shotOffTarget=0,
                                       fouls=0, saves=0, goals=0))
        self.assertEqual(a['issues'], [])
        self.assertEqual(a['stats']['shots'], {'value': 2, 'source': 'reported', 'rule': None, 'reason': None})
        # Lo que da la versión seleccionada: ahora un 0 informado, no deducido.
        self.assertEqual(a['stats']['shots_on_target'], {'value': 0, 'source': 'reported', 'rule': None, 'reason': None})
        self.assertEqual(a['stats']['fouls']['value'], 0)
        self.assertEqual(a['goals'], {'value': 0, 'source': 'reported'})

    def test_un_valor_nuevo_que_completa_se_usa(self):
        a, _ = self.dos_versiones(dict(totalContest=3), dict(totalContest=3, wonContest=2))
        self.assertEqual((a['issues'], a['stats']['dribbles_won']['value']), ([], 2))

    def test_no_se_toman_valores_de_otra_version(self):
        # La seleccionada omite una clave que la antigua daba: sin conflicto y sin dato (no se completa con la otra).
        a, _ = self.dos_versiones(dict(fouls=2), dict())
        self.assertEqual(a['issues'], [])
        self.assertEqual(a['stats']['fouls'], {'value': None, 'source': None, 'rule': None, 'reason': 'not_reported'})

    def test_un_goal_nuevo_a_cero_no_bloquea_la_deduccion(self):
        a, _ = self.dos_versiones(dict(totalShots=2, blockedScoringAttempt=2), dict(totalShots=2, blockedScoringAttempt=2, goals=0))
        self.assertEqual(a['issues'], [])
        self.assertEqual(a['stats']['shots_on_target'], {'value': 0, 'source': 'derived', 'rule': 'shots_partition', 'reason': None})

    def test_null_e_invalidos_no_equivalen_a_una_clave_ausente(self):
        casos = [('null frente a valor', dict(fouls=None), dict(fouls=1)),
                 ('null frente a ausencia', dict(fouls=None), dict()),
                 ('ausencia frente a null', dict(), dict(fouls=None)),
                 ('inválido frente a valor', dict(fouls=-1), dict(fouls=1)),
                 ('inválido frente a ausencia', dict(fouls='1'), dict()),
                 ('null frente a inválido', dict(fouls=None), dict(fouls=-1))]
        for nombre, antiguas, nuevas in casos:
            with self.subTest(nombre):
                a, _ = self.dos_versiones(antiguas, nuevas)
                self.assertEqual(a['stats']['fouls']['reason'], 'version_conflict')
                self.assertIn('stat_version_conflict', a['issues'])
                self.assertEqual(a['stats']['passes']['value'], 20)
        # El mismo null en ambas versiones no es un desacuerdo: es lo que dice la fuente.
        a, _ = self.dos_versiones(dict(fouls=None, totalContest=3), dict(fouls=None, totalContest=3, wonContest=1))
        self.assertEqual((a['issues'], a['stats']['fouls']['reason']), ([], 'reported_null'))

    def test_valores_explicitos_distintos_siguen_en_conflicto(self):
        a, _ = self.dos_versiones(dict(fouls=1), dict(fouls=2))
        self.assertEqual(a['stats']['fouls']['reason'], 'version_conflict')
        # Ceros explícitos añadidos no ocultan que otra clave del grupo cambió de valor.
        a, _ = self.dos_versiones(dict(totalShots=2, blockedScoringAttempt=2), dict(totalShots=3, blockedScoringAttempt=2, shotOffTarget=0))
        self.assertEqual(a['stats']['shots']['reason'], 'version_conflict')

    def test_versiones_que_juntas_incumplen_las_relaciones_del_grupo(self):
        # Una da 1 a puerta y 1 fuera; la otra, 1 fuera y 1 bloqueado: juntas suman 3 de 2 tiros.
        a, _ = self.dos_versiones(dict(totalShots=2, onTargetScoringAttempt=1, shotOffTarget=1),
                                  dict(totalShots=2, shotOffTarget=1, blockedScoringAttempt=1))
        self.assertEqual({a['stats'][m]['reason'] for m in ('shots', 'shots_on_target')}, {'version_conflict'})
        self.assertEqual(a['stats']['passes']['value'], 20)
        [evidencia] = [e for e in a['evidence'] if e['issue'] == 'stat_version_conflict']
        self.assertEqual(len(evidencia['fields']['lineup_versions']), 2)
        # Regates ganados de una versión por encima de los intentados de la otra.
        a, _ = self.dos_versiones(dict(totalContest=2), dict(wonContest=3))
        self.assertEqual(a['stats']['dribbles_won']['reason'], 'version_conflict')

    def test_la_seleccion_y_los_originales_se_conservan(self):
        _, datos = self.dos_versiones(dict(totalShots=2), dict(totalShots=2, fouls=0))
        todas = datos['originales'][(100, 'lineups')]['todas']
        self.assertEqual([r['fetched_at'] for r in todas], ['2026-10-21T12:00:00+00:00', '2026-10-27T13:00:00+00:00'])
        self.assertEqual(datos['originales'][(100, 'lineups')]['registro']['fetched_at'], '2026-10-27T13:00:00+00:00')
        [d] = [d for d in datos['discrepancias'] if d['key'] == 'lineups']
        self.assertEqual(d['reasons'], ['comparable_versions_differ'])

    def test_t17_goles_y_asistencias_no_cambian(self):
        a = alineacion([entrada(1, JAEN, totalShots=2, blockedScoringAttempt=2, goalAssist=0)], [entrada(3, RIVAL)])
        actuacion = api.actuaciones_del_partido(self.servidos(('2026-10-21T12:00:00+00:00', a)), 100)[1]
        self.assertEqual((actuacion['goals'], actuacion['assists']),
                         ({'value': 0, 'source': 'omitted_zero'}, {'value': 0, 'source': 'reported'}))


if __name__ == '__main__':
    unittest.main()
