"""Carga inicial y sustituciones locales en una base nueva (fase 2, docs/persistencia-sqlite.md 4.1).

Casos sintéticos (conflictos de versiones, valores inválidos, avisos, fechas inválidas, duplicados legados), en
carpetas y bases temporales fuera del repositorio. Requieren requirements-sql.txt; sin SQLAlchemy se omiten.
"""
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(Path(__file__).resolve().parent))
CON_SQL = importlib.util.find_spec('sqlalchemy') is not None and importlib.util.find_spec('alembic') is not None

import api  # noqa: E402
import importar  # noqa: E402
from test_importar import JAEN, IBIZA, RIVAL, alineacion, entrada, evento, linea  # noqa: E402

if CON_SQL:
    from persistencia import carga, reconstruccion, sqlite
    from persistencia.carga import CargaRechazada, cargar
    from persistencia.decisiones import DecisionRechazada, cargar_sustituciones, sustituciones_guardadas

AMBITO = RAIZ / 'ambito.json'
T0, T1, T2 = '2026-10-21T12:00:00+00:00', '2026-10-22T12:00:00+00:00', '2026-10-23T12:00:00+00:00'
INVALIDAS = dict(totalShots=True, blockedScoringAttempt=-1, shotOffTarget=1.5, onTargetScoringAttempt='4', totalContest=None)


def sin_estadisticas(e, nulas=False):
    e = dict(e)
    if nulas:
        e['statistics'] = None
    else:
        del e['statistics']
    return e


def exportaciones_sinteticas():
    """Versiones contradictorias de event, lineups y statistics; valores inválidos; aviso de teamId; atributos
    distintos; estadísticas ausentes y null; un partido aplazado y su vigente para una sustitución."""
    return {
        'propia.jsonl': (JAEN, [
            linea(100, 'event', T0, evento(100, JAEN, RIVAL, marcador=(1, 0))),
            linea(100, 'event', T1, evento(100, JAEN, IBIZA, marcador=(2, 0))),
            linea(100, 'event', T2, evento(100, JAEN, IBIZA, estado='postponed', marcador=None)),
            linea(101, 'event', T0, evento(101, RIVAL, JAEN, estado='notstarted', marcador=None, ronda=2)),
            linea(101, 'event', T2, evento(101, RIVAL, JAEN, marcador=(0, 2), ronda=2)),
            linea(102, 'event', 'sin-fecha', evento(102, JAEN, IBIZA, marcador=(True, 0), ronda=3)),
            linea(200, 'event', T0, evento(200, JAEN, RIVAL, marcador=(0, 0), ronda=4)),
            linea(200, 'lineups', T0, alineacion([entrada(1, JAEN), entrada(2, JAEN, suplente=True, minutos=None)],
                                                 [entrada(3, RIVAL)], confirmada=False)),
            linea(200, 'lineups', T1, alineacion([entrada(1, JAEN, totalShots=2, blockedScoringAttempt=2),
                                                  sin_estadisticas(entrada(2, JAEN, suplente=True, minutos=None)),
                                                  entrada(4, RIVAL, suplente=True, minutos=12)],
                                                 [entrada(3, RIVAL, **INVALIDAS),
                                                  sin_estadisticas(entrada(5, RIVAL, minutos=0), nulas=True)])),
            linea(200, 'lineups', T2, alineacion([entrada(1, JAEN, totalShots=2, blockedScoringAttempt=1),
                                                  sin_estadisticas(entrada(2, JAEN, suplente=True, minutos=None)),
                                                  entrada(4, RIVAL, suplente=True, minutos=12)],
                                                 [entrada(3, RIVAL, nombre='Otro nombre', **INVALIDAS),
                                                  sin_estadisticas(entrada(5, RIVAL, minutos=0), nulas=True)])),
            linea(200, 'incidents', T1, {'incidents': [{'incidentType': 'substitution', 'isHome': True, 'time': 60,
                                                        'playerIn': {'id': 4}, 'playerOut': {'id': 1}}, 'no es un objeto']}),
            linea(200, 'statistics', T1, {'statistics': [{'period': 'ALL', 'groups': [{'groupName': 'Shots', 'statisticsItems': [
                {'key': 'totalShotsOnGoal', 'homeValue': 2, 'awayValue': True}, {'homeValue': 1}]}]}]}),
            linea(300, 'event', T0, evento(300, JAEN, RIVAL, estado='postponed', marcador=None, ronda=5)),
            linea(301, 'event', T1, evento(301, JAEN, RIVAL, marcador=(1, 1), ronda=5)),
        ]),
        'ibiza.jsonl': (IBIZA, [linea(100, 'event', T0, evento(100, JAEN, IBIZA, marcador=(1, 0)))]),
    }


def calendario():
    base = lambda ident, local, visitante, estado, marcador, observado: {
        'id': ident, 'tournament_id': 17073, 'stage': {'id': 94191, 'name': 'Primera Federacion, Group 2'},
        'season_id': 97382, 'participants': {'home': {'id': local, 'name': f'Equipo {local}'}, 'away': {'id': visitante, 'name': f'Equipo {visitante}'}},
        'status': {'type': estado}, 'round': {'number': 9}, 'start_utc': '2026-11-01T17:00:00Z',
        'score': {'home': marcador[0], 'away': marcador[1]} if marcador else None, 'quality': {'observed_at_utc': observado}}
    # 200 contradice a su event; 400 solo está en el calendario y su fecha de obtención es inválida.
    return [base(200, JAEN, RIVAL, 'finished', (3, 0), T1), base(400, RIVAL, IBIZA, 'notstarted', None, None)]


def rutas(datos):
    r = ['/health', '/api/v1/scope', '/api/v1/teams/abc', '/api/v1/teams/1', '/api/v1/players/1', '/api/v1/matches/1']
    for e in datos['equipos']:
        r += [f'/api/v1/teams/{e["team_id"]}' + s for s in ('', '/players', '/matches', '/performance', '/player-stats')]
    for p in sorted({p['player_id'] for p in datos['jugadores']} | set(datos['jugadores_excluidos'])):
        r += [f'/api/v1/players/{p}', f'/api/v1/players/{p}/matches']
    for m in sorted({m['match_id'] for m in datos['partidos']} | set(datos['excluidos'])):
        r += [f'/api/v1/matches/{m}' + s for s in ('', '/statistics', '/lineups', '/incidents')]
    return r


def respuestas(datos):
    """Estado y texto exacto de cada respuesta, como los serializa el servidor."""
    salida = []
    for ruta in rutas(datos):
        estado, cuerpo = api.consultar(datos, ruta)
        salida.append((ruta, estado, json.dumps(cuerpo, ensure_ascii=False, allow_nan=False)))
    return salida


@unittest.skipUnless(CON_SQL, 'requiere requirements-sql.txt (.venv)')
class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='rj-carga-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.base = self.tmp / 'bases' / 'real-jaen.sqlite'

    def importacion(self, nombre='importacion'):
        entradas = []
        for archivo, (equipo, lineas) in exportaciones_sinteticas().items():
            ruta = self.tmp / archivo
            ruta.write_text(''.join(json.dumps(l, ensure_ascii=False) + '\n' for l in lineas), encoding='utf-8')
            entradas.append((ruta, equipo))
        cal = self.tmp / 'calendario.jsonl'
        cal.write_text(''.join(json.dumps(f, ensure_ascii=False) + '\n' for f in calendario()), encoding='utf-8')
        datos = importar.transformar(entradas, importado_en=T2, calendarios=[cal])
        carpeta = self.tmp / nombre
        carpeta.mkdir()
        for n, c in datos.items():
            (carpeta / f'{n}.json').write_text(json.dumps(c, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        return carpeta

    def sustituciones(self, *pares, nombre='sustituciones.json'):
        ruta = self.tmp / nombre
        ruta.write_text(json.dumps({'sustituciones': [
            {'sustituido': a, 'vigente': b, 'motivo': 'aplazado y jugado con otro id',
             'procedencia': {'decidido_por': 'analista', 'fecha': '2026-10-22', 'evidencia': 'misma jornada y equipos'}}
            for a, b in pares]}, ensure_ascii=False), encoding='utf-8')
        return ruta

    def datos_desde_base(self):
        motor = sqlite.crear_motor(self.base, solo_lectura=True)
        try:
            with motor.connect() as c:
                rec = reconstruccion.salida_desde_base(c)
                guardadas = sustituciones_guardadas(c)
        finally:
            motor.dispose()
        datos = api.aplicar_ambito(dict(rec, originales=api.indexar(rec['originales'])), api.leer_ambito(AMBITO))
        return api.aplicar_sustituciones(datos, guardadas)

    def consulta(self, sql):
        motor = sqlite.crear_motor(self.base, solo_lectura=True)
        try:
            with motor.connect() as c:
                return c.exec_driver_sql(sql).fetchall()
        finally:
            motor.dispose()

    def sin_restos(self):
        parcial = carga.parcial_de(self.base)
        return not any(p.exists() for p in (self.base, parcial, *carga.acompanantes(self.base), *carga.acompanantes(parcial)))


class CargaValida(Base):
    def test_carga_conflictos_y_valores_invalidos_y_reproduce_la_carpeta(self):
        informe = cargar(self.importacion(), self.base, 'prueba')
        self.assertTrue(self.base.is_file())
        self.assertFalse(carga.parcial_de(self.base).exists())
        self.assertEqual(informe['revision'], '0007')
        self.assertEqual(informe['comparados'], sorted(['discrepancias', 'equipos', 'exportaciones', 'jugador_partido', 'jugadores',
                                                        'originales', 'partidos', 'resumen']))
        # Referencia verificable del código: commit completo y, con cambios sin confirmar, sus huellas recalculables.
        [(version, resumen)] = self.consulta("SELECT tool_version, summary FROM import_run WHERE kind = 'initial_load'")
        codigo = json.loads(resumen)['codigo']
        self.assertEqual(codigo, carga.referencia_del_codigo())
        self.assertEqual(version, codigo['texto'])
        self.assertRegex(version, r'^git:[0-9a-f]{40}(\+sha256:[0-9a-f]{64})?$')
        self.assertEqual(self.consulta('SELECT DISTINCT code_ref FROM rule_set'), [(version,)])
        # La selección inicial conserva su ejecución y su regla de origen; no se inventa historial anterior.
        self.assertEqual(self.consulta('SELECT DISTINCT s.run_id, r.name FROM section_selection s '
                                       'JOIN rule_set r ON r.id = s.rule_set_id'), [(1, 'importar.seleccionar')])
        self.assertEqual(self.consulta('SELECT count(*) FROM section_selection_history'), [(0,)])
        # Discrepancias con sus motivos en orden; versiones contradictorias conservadas como evidencia.
        filas_motivos = self.consulta("SELECT x.external_id, d.key, r.reason FROM discrepancy d "
                                      "JOIN match_external_id x ON x.entity_id = d.match_id "
                                      "JOIN discrepancy_reason r ON r.discrepancy_id = d.id "
                                      "WHERE d.kind <> 'player_attribute' ORDER BY x.external_id, d.key, r.ordinal")
        agrupados = {}
        for ident, clave, motivo in filas_motivos:
            agrupados.setdefault((ident, clave), []).append(motivo)
        motivos = [(ident, clave, ','.join(lista)) for (ident, clave), lista in agrupados.items()]
        self.assertIn(('100', 'event', 'identity_changed,non_final_after_final,final_score_corrected'), motivos)
        self.assertIn(('101', 'event', 'preliminary_superseded'), motivos)
        self.assertIn(('200', 'listing', 'listing_differs_from_event'), motivos)
        self.assertEqual(self.consulta("SELECT count(*) FROM observation WHERE external_event_id = '100' AND key = 'event'"), [(4,)])
        # Listing con fecha inválida: sin fecha inventada.
        self.assertEqual(self.consulta("SELECT fetched_at_raw, fetched_instant, fetched_at_valid FROM observation "
                                       "WHERE kind = 'listing' AND external_event_id = '400'"), [(None, None, 0)])
        self.assertEqual(self.consulta("SELECT fetched_at_raw, fetched_at_valid FROM observation WHERE external_event_id = '102'"),
                         [('sin-fecha', 0)])
        # Tipos originales: literal JSON tal cual; ausente sin fila; statistics ausente y null distinguidos.
        literales = dict(self.consulta("SELECT s.key, s.value_json FROM call_up_stat s JOIN call_up u ON u.id = s.call_up_id "
                                       "WHERE u.external_player_id = '3'"))
        self.assertEqual({k: literales[k] for k in INVALIDAS},
                         {'totalShots': 'true', 'blockedScoringAttempt': '-1', 'shotOffTarget': '1.5',
                          'onTargetScoringAttempt': '"4"', 'totalContest': 'null'})
        self.assertEqual(dict(self.consulta("SELECT external_player_id, statistics_kind FROM call_up")),
                         {'1': 'object', '2': 'absent', '4': 'object', '3': 'object', '5': 'null'})
        # teamId de origen distinto del lado: se conserva el original y el equipo es el del lado.
        self.assertEqual(self.consulta("SELECT u.external_team_id, x.external_id FROM call_up u JOIN team_external_id x "
                                       "ON x.entity_id = u.team_id WHERE u.external_player_id = '4'"), [(str(RIVAL), str(JAEN))])
        # Procedencia: etiquetas, declaraciones validadas, uid deterministas asignados una vez.
        self.assertEqual(self.consulta('SELECT label, kind FROM source_file ORDER BY id'),
                         [('E1', 'raw'), ('E2', 'raw'), ('E3', 'listing')])
        self.assertEqual(self.consulta('SELECT f.label, d.external_team_id FROM file_team_declaration d '
                                       'JOIN source_file f ON f.id = d.source_file_id ORDER BY f.id'),
                         [('E1', str(JAEN)), ('E2', str(IBIZA))])
        self.assertEqual(self.consulta("SELECT t.uid FROM team t JOIN team_external_id x ON x.entity_id = t.id "
                                       f"WHERE x.external_id = '{JAEN}'"),
                         [(str(uuid.uuid5(carga.ESPACIO_UID, f'sofascore:team:{JAEN}')),)])
        # Convocatorias vigentes desde la carga; ninguna transición. La regla persistencia.carga ya no se registra.
        self.assertEqual(self.consulta("SELECT DISTINCT status FROM call_up"), [('active',)])
        self.assertEqual(self.consulta('SELECT count(*) FROM call_up_change'), [(0,)])
        self.assertNotIn(('persistencia.carga',), self.consulta('SELECT name FROM rule_set'))

    def test_la_api_sobre_la_base_responde_igual_que_sobre_la_carpeta(self):
        carpeta = self.importacion()
        cargar(carpeta, self.base)
        self.assertEqual(respuestas(self.datos_desde_base()), respuestas(api.cargar(carpeta, AMBITO)))

    def test_sustituciones_con_motivo_procedencia_y_trazabilidad(self):
        carpeta = self.importacion()
        cargar(carpeta, self.base)
        archivo = self.sustituciones((300, 301))
        informe = cargar_sustituciones(self.base, archivo, AMBITO, 'prueba')
        self.assertEqual((informe['estado'], informe['sustituciones'], informe['etiqueta']), ('committed', 1, 'D1'))
        motor = sqlite.crear_motor(self.base, solo_lectura=True)
        with motor.connect() as c:
            self.assertEqual(sustituciones_guardadas(c), json.loads(archivo.read_text(encoding='utf-8'))['sustituciones'])
            self.assertEqual(c.exec_driver_sql("SELECT f.kind, f.label FROM local_replacement l JOIN source_file f "
                                               "ON f.id = l.source_file_id").fetchall(), [('local_decisions', 'D1')])
        motor.dispose()
        self.assertEqual(respuestas(self.datos_desde_base()), respuestas(api.cargar(carpeta, AMBITO, archivo)))
        # Repetir el mismo fichero no cambia nada.
        self.assertEqual(cargar_sustituciones(self.base, archivo, AMBITO)['estado'], 'no_changes')
        self.assertEqual(self.consulta('SELECT count(*) FROM local_replacement'), [(1,)])


class CargaRechazadaSinRestos(Base):
    def test_destino_existente_o_dentro_de_un_repositorio(self):
        self.base.parent.mkdir(parents=True)
        self.base.write_bytes(b'no es una base')
        with self.assertRaisesRegex(CargaRechazada, 'Ya existe'):
            cargar(self.importacion(), self.base)
        self.assertEqual(self.base.read_bytes(), b'no es una base')
        with self.assertRaisesRegex(CargaRechazada, 'fuera de los repositorios'):
            cargar(self.importacion('otra'), RAIZ / 'no-debe-crearse.sqlite')
        self.assertFalse((RAIZ / 'no-debe-crearse.sqlite').exists())

    def test_huella_manipulada(self):
        carpeta = self.importacion()
        originales = json.loads((carpeta / 'originales.json').read_text(encoding='utf-8'))
        originales[0]['payload']['homeScore'] = {'current': 9}
        (carpeta / 'originales.json').write_text(json.dumps(originales), encoding='utf-8')
        with self.assertRaisesRegex(CargaRechazada, 'huella'):
            cargar(carpeta, self.base)
        self.assertTrue(self.sin_restos())

    def test_duplicados_legados_los_lee_la_api_pero_no_los_carga_una_importacion(self):
        carpeta = self.importacion()
        filas = json.loads((carpeta / 'jugador_partido.json').read_text(encoding='utf-8'))
        filas.append(dict(filas[0], shirt_number=99))
        (carpeta / 'jugador_partido.json').write_text(json.dumps(filas), encoding='utf-8')
        # El lector histórico (api.cargar) los admite y los une con sus reglas...
        datos = api.cargar(carpeta, AMBITO)
        actuacion = api.actuaciones_del_partido(datos, filas[0]['match_id'])[filas[0]['player_id']]
        self.assertEqual(actuacion['duplicate_records'], 1)
        # ...pero las reglas actuales de importación no reproducen esa carpeta: no se carga.
        with self.assertRaisesRegex(CargaRechazada, 'jugador_partido'):
            cargar(carpeta, self.base)
        self.assertTrue(self.sin_restos())

    def test_jugador_repetido_en_una_alineacion(self):
        carpeta = self.importacion()
        originales = json.loads((carpeta / 'originales.json').read_text(encoding='utf-8'))
        r = next(r for r in originales if r['key'] == 'lineups' and r['selected'])
        r['payload']['home']['players'].append(r['payload']['home']['players'][0])
        r['content_hash'] = importar.huella(r['payload'])
        (carpeta / 'originales.json').write_text(json.dumps(originales), encoding='utf-8')
        with self.assertRaisesRegex(CargaRechazada, 'Jugador duplicado en alineación'):
            cargar(carpeta, self.base)
        self.assertTrue(self.sin_restos())

    def test_valor_no_representable_en_json(self):
        carpeta = self.importacion()
        originales = json.loads((carpeta / 'originales.json').read_text(encoding='utf-8'))
        r = next(r for r in originales if r['key'] == 'statistics')
        r['payload']['statistics'][0]['groups'][0]['statisticsItems'][0]['homeValue'] = float('nan')
        r['content_hash'] = importar.huella(r['payload'])
        (carpeta / 'originales.json').write_text(json.dumps(originales), encoding='utf-8')
        with self.assertRaisesRegex(CargaRechazada, 'no representable en JSON'):
            cargar(carpeta, self.base)
        self.assertTrue(self.sin_restos())

    def test_fallo_a_mitad_de_la_escritura(self):
        with mock.patch.object(carga, 'escribir_discrepancias', side_effect=RuntimeError('fallo simulado')):
            with self.assertRaisesRegex(RuntimeError, 'fallo simulado'):
                cargar(self.importacion(), self.base)
        self.assertTrue(self.sin_restos())

    def test_la_base_no_reproduce_la_carpeta(self):
        with mock.patch.object(reconstruccion, 'jugadores', return_value=[]):
            with self.assertRaisesRegex(CargaRechazada, 'no reproduce la carpeta en: jugadores'):
                cargar(self.importacion(), self.base)
        self.assertTrue(self.sin_restos())

    def test_carpeta_sin_formato_2(self):
        carpeta = self.importacion()
        (carpeta / 'exportaciones.json').unlink()
        with self.assertRaisesRegex(CargaRechazada, 'formato'):
            cargar(carpeta, self.base)
        self.assertTrue(self.sin_restos())


class SustitucionesRechazadas(Base):
    def setUp(self):
        super().setUp()
        cargar(self.importacion(), self.base)

    def comprobar_rechazo(self, archivo, ambito=AMBITO, mensaje=''):
        with self.assertRaisesRegex(DecisionRechazada, mensaje):
            cargar_sustituciones(self.base, archivo, ambito)
        self.assertEqual(self.consulta('SELECT count(*) FROM local_replacement'), [(0,)])

    def test_sustituido_finalizado_o_inexistente(self):
        self.comprobar_rechazo(self.sustituciones((301, 300)), mensaje='finalizado')
        self.assertEqual(self.consulta("SELECT kind, status FROM import_run WHERE status = 'failed'"), [('decisions', 'failed')])
        self.comprobar_rechazo(self.sustituciones((300, 999), nombre='otra.json'), mensaje='no existe')

    def test_formato_y_ambito(self):
        self.comprobar_rechazo(self.sustituciones((300, 300)), mensaje='sí mismo')
        ambito = self.tmp / 'ambito.json'
        ambito.write_text(json.dumps({'competiciones': [{'nombre': 'Otro', 'unique_tournament_id': 1, 'tournament_id': 2,
                                                         'season_id': 3}]}), encoding='utf-8')
        self.comprobar_rechazo(self.sustituciones((300, 301), nombre='otra.json'), ambito, 'fuera del ámbito')


if __name__ == '__main__':
    unittest.main()
