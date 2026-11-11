"""Importación incremental con sincronización completa (fase 4, docs/fase4-sincronizacion.md 7).

Bases y ficheros temporales, fuera del repositorio. Cada paso se compara en todas las rutas de la API con el camino
independiente: los mismos ficheros, con las mismas etiquetas y en el mismo orden, por importar.ensamblar a una
carpeta del modo JSON. Requieren requirements-sql.txt; sin SQLAlchemy se omiten.
"""
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(Path(__file__).resolve().parent))
CON_SQL = importlib.util.find_spec('sqlalchemy') is not None and importlib.util.find_spec('alembic') is not None

import api  # noqa: E402
import importar  # noqa: E402
from test_carga import AMBITO, T2, calendario, exportaciones_sinteticas, respuestas, sin_estadisticas  # noqa: E402
from test_importar import JAEN, IBIZA, RIVAL, alineacion, entrada, evento, linea  # noqa: E402

if CON_SQL:
    from alembic import command

    from persistencia import carga, sincronizacion, sqlite
    from persistencia.carga import CargaRechazada, cargar, configuracion_alembic
    from persistencia.decisiones import cargar_sustituciones
    from persistencia.lectura import BaseNoUtilizable, cargar_desde_base
    from persistencia.sincronizacion import EstadoIncoherente, ReglasDistintas
    from persistencia.sqlite import CopiaNoValida

T3, T4, T5 = '2026-10-24T12:00:00+00:00', '2026-10-25T12:00:00+00:00', '2026-10-26T12:00:00+00:00'
NUEVO = 7777  # equipo que aparece en una importación posterior


def alineacion_200(local, visitante, fetched_at):
    """Una versión posterior de la alineación del partido 200 (la de la carga inicial, en test_carga, es de T2)."""
    return linea(200, 'lineups', fetched_at, alineacion(local, visitante))


def jugador(n, **k):
    """Entradas del partido 200 iguales a las de la versión T2 (salvo lo indicado)."""
    return {1: lambda: entrada(1, JAEN, totalShots=2, blockedScoringAttempt=1, **k),
            2: lambda: sin_estadisticas(entrada(2, JAEN, suplente=True, minutos=None)),
            3: lambda: entrada(3, RIVAL, nombre='Otro nombre', **dict(totalShots=True, blockedScoringAttempt=-1,
                                                                       shotOffTarget=1.5, onTargetScoringAttempt='4',
                                                                       totalContest=None)),
            4: lambda: entrada(4, RIVAL, suplente=True, minutos=12),
            5: lambda: sin_estadisticas(entrada(5, RIVAL, minutos=0), nulas=True)}[n]()


@unittest.skipUnless(CON_SQL, 'requiere requirements-sql.txt (.venv)')
class Base(unittest.TestCase):
    """Una base con la carga inicial sintética de test_carga (E1 propia, E2 ibiza, E3 calendario), copiada de una
    plantilla para cada prueba."""

    @classmethod
    def setUpClass(cls):
        cls.plantilla_dir = Path(tempfile.mkdtemp(prefix='rj-sinc-plantilla-'))
        cls.iniciales = []
        entradas = []
        for archivo, (equipo, lineas) in exportaciones_sinteticas().items():
            ruta = escribir(cls.plantilla_dir / archivo, lineas)
            entradas.append((ruta, equipo))
            cls.iniciales.append((f'E{len(cls.iniciales) + 1}', 'raw', ruta, [equipo]))
        cal = escribir(cls.plantilla_dir / 'calendario.jsonl', calendario())
        cls.iniciales.append(('E3', 'listing', cal, []))
        datos = importar.transformar(entradas, importado_en=T2, calendarios=[cal])
        carpeta = cls.plantilla_dir / 'importacion'
        carpeta.mkdir()
        for n, c in datos.items():
            (carpeta / f'{n}.json').write_text(json.dumps(c, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        cls.plantilla = cls.plantilla_dir / 'plantilla.sqlite'
        cargar(carpeta, cls.plantilla)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.plantilla_dir, True)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='rj-sinc-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.base = self.tmp / 'bases' / 'real-jaen.sqlite'
        self.base.parent.mkdir()
        shutil.copyfile(self.plantilla, self.base)
        self.ficheros = [(e, t, r, list(q)) for e, t, r, q in self.iniciales]

    # ─── Utilidades ──────────────────────────────────────────────────────────────────────────────────────────────

    def fichero(self, nombre, lineas):
        return escribir(self.tmp / nombre, lineas)

    def importa(self, exportaciones=(), calendarios=(), fichas=()):
        """Importa en la base y registra, para el camino independiente, los ficheros nuevos y las declaraciones."""
        informe = sincronizacion.importar_evidencia(self.base, exportaciones, calendarios, fichas)
        etiquetas = {f['nombre']: f['etiqueta'] for f in informe['ficheros']}
        for tipo, lista in (('raw', exportaciones), ('listing', [(r, None) for r in calendarios]),
                            ('player_profile', [(r, None) for r in fichas])):
            for ruta, equipo in lista:
                etiqueta = etiquetas[Path(ruta).name]
                existente = next((i for i, f in enumerate(self.ficheros) if f[0] == etiqueta), None)
                if existente is None:
                    self.ficheros.append((etiqueta, tipo, Path(ruta), [equipo] if equipo is not None else []))
                elif equipo is not None and equipo not in self.ficheros[existente][3]:
                    self.ficheros[existente][3].append(equipo)
        return informe

    def independiente(self, sustituciones=None):
        """Los mismos ficheros por importar.ensamblar a una carpeta del modo JSON, cargada con api.cargar."""
        originales, manifiesto, fichas = [], [], []
        for etiqueta, tipo, ruta, equipos in self.ficheros:
            if tipo == 'raw':
                registros, info = importar.leer_exportacion(ruta, etiqueta, None)
                info = dict(info, kind='raw')
                originales += registros
            elif tipo == 'listing':
                registros, info = importar.leer_calendario(ruta, etiqueta)
                originales += registros
            else:
                registros, info = importar.leer_fichas(ruta, etiqueta)
                fichas += registros
            manifiesto += [dict(info, followed_team_id=e) for e in equipos] or [info]
        salida = importar.ensamblar(originales, manifiesto, fichas, T2, con_fichas=bool(fichas))
        carpeta = Path(tempfile.mkdtemp(prefix='json-', dir=self.tmp))
        for n, c in salida.items():
            (carpeta / f'{n}.json').write_text(json.dumps(c, ensure_ascii=False), encoding='utf-8')
        return api.cargar(carpeta, AMBITO, sustituciones)

    def sql(self):
        return cargar_desde_base(self.base, AMBITO)

    def igual_al_camino_independiente(self, sustituciones=None):
        self.assertEqual(respuestas(self.sql()), respuestas(self.independiente(sustituciones)))

    def consulta(self, sql, *parametros):
        c = sqlite3.connect(f'{self.base.resolve().as_uri()}?mode=ro', uri=True)
        try:
            return c.execute(sql, parametros).fetchall()
        finally:
            c.close()

    def convocatoria(self, partido, jugador_externo, lado=None):
        """(id, status, retired_in_run, lineup_observation_id) de la convocatoria del jugador en el partido."""
        sql = ('SELECT u.id, u.status, u.retired_in_run, u.lineup_observation_id, u.side FROM call_up u '
               'JOIN match_external_id m ON m.entity_id = u.match_id WHERE m.external_id = ? AND u.external_player_id = ?')
        filas = self.consulta(sql + (' AND u.side = ?' if lado else '') + ' ORDER BY u.id',
                              str(partido), str(jugador_externo), *([lado] if lado else []))
        return filas

    def huella_sin_ejecuciones(self):
        return huella_sin(self.base, {'import_run', 'import_run_file'})

    def verificacion(self):
        return sincronizacion.verificar(self.base)


def escribir(ruta, lineas):
    ruta.write_text(''.join(json.dumps(l, ensure_ascii=False) + '\n' for l in lineas), encoding='utf-8')
    return ruta


def huella_sin(ruta, excluidas):
    import hashlib
    c = sqlite3.connect(f'{Path(ruta).resolve().as_uri()}?mode=ro', uri=True)
    try:
        h = hashlib.sha256()
        for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
            if t not in excluidas:
                cols = ', '.join(f'"{x[1]}"' for x in c.execute(f'PRAGMA table_info("{t}")'))
                h.update(t.encode())
                for fila in c.execute(f'SELECT {cols} FROM "{t}" ORDER BY {cols}'):
                    h.update(repr(fila).encode())
        return h.hexdigest()
    finally:
        c.close()


class SinEvidenciaNueva(Base):
    def test_sincronizacion_en_seco_sin_cambios(self):
        v = self.verificacion()
        self.assertTrue(v['ok'], v['problemas'])
        self.assertEqual(v['cambios_en_seco'], {})
        self.igual_al_camino_independiente()

    def test_repetir_la_importacion_no_cambia_datos_ni_crea_historial(self):
        antes = self.huella_sin_ejecuciones()
        observaciones = self.consulta('SELECT count(*) FROM observation')
        repetidos = [(r, e[0]) for _, tipo, r, e in self.iniciales if tipo == 'raw']
        cal = [r for _, tipo, r, _ in self.iniciales if tipo == 'listing']
        for _ in range(2):
            informe = self.importa(repetidos, cal)
            self.assertEqual(informe['estado'], 'no_changes')
            self.assertEqual(informe['cambios'], {})
            self.assertEqual({f['outcome'] for f in informe['ficheros']}, {'already_known'})
        self.assertEqual(self.huella_sin_ejecuciones(), antes)
        self.assertEqual(self.consulta('SELECT count(*) FROM observation'), observaciones)
        self.assertEqual(self.consulta('SELECT count(*) FROM section_selection_history'), [(0,)])
        self.assertEqual(self.consulta('SELECT count(*) FROM call_up_change'), [(0,)])
        self.assertEqual(self.consulta('SELECT count(*) FROM discrepancy WHERE superseded_in_run IS NOT NULL'), [(0,)])
        self.assertEqual(self.consulta("SELECT kind, status FROM import_run WHERE id > 1"),
                         [('import', 'no_changes'), ('import', 'no_changes')])
        self.igual_al_camino_independiente()

    def test_mismo_fichero_dos_veces_en_un_comando(self):
        nuevo = self.fichero('nuevo.jsonl', [linea(500, 'event', T3, evento(500, JAEN, RIVAL, ronda=7))])
        informe = self.importa([(nuevo, JAEN), (nuevo, JAEN)])
        self.assertEqual([(f['etiqueta'], f['outcome']) for f in informe['ficheros']], [('E4', 'ingested')])
        self.assertEqual(self.consulta("SELECT count(*) FROM observation o JOIN source_file f ON f.id = o.source_file_id "
                                       "WHERE f.label = 'E4'"), [(1,)])
        self.igual_al_camino_independiente()


class EvidenciaNueva(Base):
    def test_versiones_nuevas_partido_equipo_y_jugador_nuevos(self):
        ids_antes = dict(self.consulta("SELECT x.external_id, x.entity_id FROM team_external_id x"))
        jugadores_antes = dict(self.consulta("SELECT external_id, entity_id FROM player_external_id"))
        convocatorias_antes = self.consulta('SELECT id, match_id, side, external_player_id FROM call_up ORDER BY id')
        maximo = self.consulta('SELECT max(id) FROM team')[0][0]
        nuevo = self.fichero('jaen-nuevo.jsonl', [
            # Otra versión de la alineación de 200 (cambian las estadísticas del jugador 1).
            alineacion_200([entrada(1, JAEN, totalShots=3, blockedScoringAttempt=1), jugador(2), jugador(4)],
                           [jugador(3), jugador(5)], T3),
            # 400 solo estaba en el calendario (pendiente): ahora con su event, finalizado.
            linea(400, 'event', T3, evento(400, RIVAL, IBIZA, marcador=(2, 1), ronda=9)),
            # Partido nuevo con un equipo y un jugador nuevos.
            linea(600, 'event', T3, evento(600, JAEN, NUEVO, marcador=(1, 1), ronda=10)),
            linea(600, 'lineups', T3, alineacion([entrada(1, JAEN)], [entrada(77, NUEVO, totalShots=1)])),
        ])
        informe = self.importa([(nuevo, JAEN)])
        self.assertEqual(informe['estado'], 'committed')
        cambios = informe['cambios']
        self.assertEqual(cambios['team'], {'nuevas': 1})
        self.assertEqual(cambios['match'], {'nuevas': 1})
        self.assertEqual(cambios['player'], {'nuevas': 1})
        # Solo la alineación de 200 cambia de versión (400 no tenía event: su selección es nueva).
        self.assertEqual(cambios['section_selection_history'], {'nuevas': 1})
        # Ids conservados: entidades y convocatorias existentes; las nuevas, a continuación.
        self.assertEqual({k: v for k, v in self.consulta('SELECT external_id, entity_id FROM team_external_id')
                          if k in ids_antes}, ids_antes)
        self.assertEqual(dict(self.consulta(f"SELECT external_id, entity_id FROM team_external_id WHERE external_id = '{NUEVO}'")),
                         {str(NUEVO): maximo + 1})
        self.assertEqual({k: v for k, v in self.consulta('SELECT external_id, entity_id FROM player_external_id')
                          if k in jugadores_antes}, jugadores_antes)
        self.assertEqual([f for f in self.consulta('SELECT id, match_id, side, external_player_id FROM call_up ORDER BY id')
                          if f[0] <= len(convocatorias_antes)], convocatorias_antes)
        # Historial con la ejecución y la regla; la selección vigente, en la ejecución nueva.
        run = informe['run']
        self.assertEqual(self.consulta("SELECT h.valid_from_run, h.valid_to_run, r.name FROM section_selection_history h "
                                       "JOIN rule_set r ON r.id = h.rule_set_id JOIN match_external_id m ON "
                                       "m.entity_id = h.match_id WHERE m.external_id = '200' AND h.key = 'lineups'"),
                         [(1, run, 'importar.seleccionar')])
        self.assertEqual(self.consulta("SELECT s.calendar_source, s.status FROM match_state s JOIN match_external_id m ON "
                                       "m.entity_id = s.match_id WHERE m.external_id = '400'"), [('event', 'finished')])
        self.assertEqual(self.consulta("SELECT label FROM source_file ORDER BY id"), [('E1',), ('E2',), ('E3',), ('E4',)])
        v = self.verificacion()
        self.assertTrue(v['ok'], v['problemas'])
        self.igual_al_camino_independiente()

    def test_ficheros_y_declaraciones_posteriores_en_el_manifiesto(self):
        a = self.fichero('a.jsonl', [linea(500, 'event', T3, evento(500, JAEN, RIVAL, ronda=7))])
        b = self.fichero('b.jsonl', [linea(501, 'event', T3, evento(501, IBIZA, RIVAL, ronda=7))])
        self.importa([(a, JAEN)])
        # Declaración nueva sobre un fichero ya conocido (E2, ibiza.jsonl, donde JAEN juega el 100) y un fichero nuevo.
        ibiza = next(r for e, _, r, _ in self.iniciales if e == 'E2')
        informe = self.importa([(ibiza, JAEN), (b, IBIZA)])
        self.assertEqual([(f['etiqueta'], f['outcome']) for f in informe['ficheros']],
                         [('E2', 'already_known'), ('E5', 'ingested')])
        self.assertEqual(informe['cambios']['file_team_declaration'], {'nuevas': 2})
        scope = api.consultar(self.sql(), '/api/v1/scope')[1]['data']['teams_with_own_export']
        self.assertEqual({e['team_id']: [x['file'] for x in e['exports']] for e in scope},
                         {JAEN: ['propia.jsonl', 'ibiza.jsonl', 'a.jsonl'], IBIZA: ['ibiza.jsonl', 'b.jsonl']})
        self.igual_al_camino_independiente()

    def test_declaracion_invalida_se_rechaza_sin_escribir(self):
        ibiza = next(r for e, _, r, _ in self.iniciales if e == 'E2')
        antes = self.huella_sin_ejecuciones()
        with self.assertRaisesRegex(CargaRechazada, 'no juega ningún partido del fichero'):
            self.importa([(ibiza, RIVAL)])
        self.assertEqual(self.huella_sin_ejecuciones(), antes)
        self.assertEqual(self.consulta("SELECT kind, status FROM import_run WHERE id > 1"), [('import', 'failed')])

    def test_jugador_repetido_en_una_alineacion_se_rechaza_sin_escribir(self):
        repetido = self.fichero('repetido.jsonl', [alineacion_200([jugador(1), jugador(1), jugador(2), jugador(4)],
                                                                  [jugador(3), jugador(5)], T3)])
        antes = self.huella_sin_ejecuciones()
        with self.assertRaisesRegex(CargaRechazada, 'Jugador duplicado'):
            self.importa([(repetido, None)])
        self.assertEqual(self.huella_sin_ejecuciones(), antes)

    def test_clave_de_convocatoria_repetida_en_el_estado_deseado(self):
        motor = sqlite.crear_motor(self.base, solo_lectura=True)
        try:
            with motor.connect() as c:
                filas, _ = sincronizacion.estado_deseado(c)
                filas.tablas['call_up'].append(dict(filas.de('call_up')[0], id=999))
                with self.assertRaisesRegex(CargaRechazada, 'Convocatoria repetida'):
                    sincronizacion.planificar(c, filas, None)
        finally:
            motor.dispose()


class Vigencia(Base):
    """Un jugador que desaparece de la versión operativa, reaparece y cambia de lado."""

    def comprueba_todas_las_rutas(self):
        v = self.verificacion()
        self.assertTrue(v['ok'], v['problemas'])
        self.igual_al_camino_independiente()

    def rutas_del_jugador(self, datos, ident):
        return {r: api.consultar(datos, r) for r in (f'/api/v1/players/{ident}', f'/api/v1/players/{ident}/matches',
                                                     '/api/v1/matches/200/lineups', f'/api/v1/teams/{JAEN}/players',
                                                     f'/api/v1/teams/{JAEN}/player-stats', f'/api/v1/teams/{JAEN}/performance',
                                                     f'/api/v1/teams/{RIVAL}/player-stats')}

    def test_retirada_reactivacion_y_cambio_de_lado(self):
        (ident, estado, _, linea_inicial, _), = self.convocatoria(200, 2)
        stats_antes = self.consulta('SELECT key, ordinal, value_json FROM call_up_stat WHERE call_up_id = ? ORDER BY ordinal', ident)
        datos_antes = self.sql()
        self.assertIn(200, [m['match']['match_id'] for m in api.consultar(datos_antes, '/api/v1/players/2/matches')[1]['data']])

        # 1. Retirada: la versión T3 ya no incluye al jugador 2.
        sin_2 = self.fichero('sin-2.jsonl', [alineacion_200([jugador(1), jugador(4)], [jugador(3), jugador(5)], T3)])
        r1 = self.importa([(sin_2, None)])
        self.assertEqual(r1['cambios']['call_up'].get('retiradas'), 1)
        self.assertEqual(self.convocatoria(200, 2), [(ident, 'retired', r1['run'], linea_inicial, 'home')])
        # Conserva su última alineación y sus estadísticas (lo que decía esa versión), pero no se lee.
        self.assertEqual(self.consulta('SELECT key, ordinal, value_json FROM call_up_stat WHERE call_up_id = ? ORDER BY ordinal',
                                       ident), stats_antes)
        seleccionada = self.consulta("SELECT s.observation_id FROM section_selection s JOIN match_external_id m ON "
                                     "m.entity_id = s.match_id WHERE m.external_id = '200' AND s.key = 'lineups'")[0][0]
        self.assertEqual(self.consulta('SELECT call_up_id, run_id, change, lineup_observation_id FROM call_up_change'),
                         [(ident, r1['run'], 'retired', seleccionada)])
        datos = self.sql()
        # El jugador 2 solo estaba convocado en el 200: sin convocatoria vigente, su ficha y sus partidos dejan de
        # servirse (como en el modo JSON con esa alineación).
        for ruta in ('/api/v1/players/2', '/api/v1/players/2/matches'):
            self.assertEqual(api.consultar(datos, ruta), (404, {'error': 'Jugador fuera del ámbito de esta versión'}))
        alineacion =api.consultar(datos, '/api/v1/matches/200/lineups')[1]['data']
        # La sección servida es la versión seleccionada original (T3), sin el jugador 2.
        self.assertNotIn(2, [p['player']['id'] for lado in ('home', 'away') for p in alineacion[lado]['players']])
        self.assertNotEqual(self.rutas_del_jugador(datos, 2), self.rutas_del_jugador(datos_antes, 2))
        self.comprueba_todas_las_rutas()

        # Repetir la misma importación no crea otra transición.
        self.assertEqual(self.importa([(sin_2, None)])['estado'], 'no_changes')
        self.assertEqual(self.consulta('SELECT count(*) FROM call_up_change'), [(1,)])

        # 2. Reaparece (T4): la misma fila y el mismo id, activa; dos transiciones.
        con_2 = self.fichero('con-2.jsonl', [alineacion_200([jugador(1), jugador(2), jugador(4)], [jugador(3), jugador(5)], T4)])
        r2 = self.importa([(con_2, None)])
        self.assertEqual(r2['cambios']['call_up'].get('reactivadas'), 1)
        (mismo, estado, retirada, linea_nueva, lado), = self.convocatoria(200, 2)
        self.assertEqual((mismo, estado, retirada, lado), (ident, 'active', None, 'home'))
        self.assertNotEqual(linea_nueva, linea_inicial)
        self.assertEqual([x[2] for x in self.consulta('SELECT call_up_id, run_id, change FROM call_up_change ORDER BY id')],
                         ['retired', 'reactivated'])
        self.assertIn(200, [m['match']['match_id'] for m in api.consultar(self.sql(), '/api/v1/players/2/matches')[1]['data']])
        self.comprueba_todas_las_rutas()

        # 3. El jugador 4 pasa del lado local al visitante (T5): la del lado anterior se retira y la nueva tiene su
        # propia identidad (otro id, el siguiente).
        (local, *_), = self.convocatoria(200, 4, 'home')
        maximo = self.consulta('SELECT max(id) FROM call_up')[0][0]
        lado = self.fichero('lado.jsonl', [alineacion_200([jugador(1), jugador(2)], [jugador(3), jugador(5), jugador(4)], T5)])
        r3 = self.importa([(lado, None)])
        # Las otras cuatro siguen vigentes, en su sitio y con su id, apuntando a la versión nueva.
        self.assertEqual(r3['cambios']['call_up'], {'actualizadas': 4, 'nuevas': 1, 'retiradas': 1})
        self.assertEqual([(i, s, u) for i, s, _, _, u in self.convocatoria(200, 4)],
                         [(local, 'retired', 'home'), (maximo + 1, 'active', 'away')])
        self.comprueba_todas_las_rutas()


class Reglas(Base):
    def test_version_de_regla_nueva(self):
        nuevas = tuple((n, '3' if n == 'importar.seleccionar' else v, d) for n, v, d in carga.REGLAS)
        nuevo = self.fichero('nuevo.jsonl', [linea(500, 'event', T3, evento(500, JAEN, RIVAL, ronda=7))])
        with mock.patch.object(carga, 'REGLAS', nuevas):
            antes = self.huella_sin_ejecuciones()
            with self.assertRaisesRegex(EstadoIncoherente, 'persistencia reconstruir'):
                self.importa([(nuevo, JAEN)])
            self.assertEqual(self.huella_sin_ejecuciones(), antes)
            with self.assertRaisesRegex(BaseNoUtilizable, 'importar.seleccionar@2'):
                self.sql()
            en_seco = sincronizacion.reconstruir(self.base)
            self.assertTrue(en_seco['en_seco'])
            self.assertEqual(en_seco['cambios']['rule_set'], {'nuevas': 1})
            self.assertEqual(self.huella_sin_ejecuciones(), antes)
            hecha = sincronizacion.reconstruir(self.base, confirmar=True)
            selecciones = self.consulta('SELECT count(*) FROM section_selection')[0][0]
            self.assertEqual(hecha['cambios']['section_selection_history'], {'nuevas': selecciones})
            self.assertEqual(self.consulta("SELECT DISTINCT r.version FROM section_selection s JOIN rule_set r "
                                           "ON r.id = s.rule_set_id"), [('3',)])
            self.assertEqual(self.consulta(f"SELECT kind, status FROM import_run WHERE id = {hecha['run']}"),
                             [('rebuild', 'committed')])
            self.assertTrue(self.verificacion()['ok'])
            self.importa([(nuevo, JAEN)])
            self.igual_al_camino_independiente()
        # Con las reglas del código otra vez, la base guardada con la versión 3 no se sirve.
        with self.assertRaisesRegex(BaseNoUtilizable, 'importar.seleccionar@3'):
            self.sql()

    def test_codigo_de_reglas_cambiado_sin_version(self):
        original = importar.extraer_convocatorias

        def distinta(registro, partido):
            filas, avisos = original(registro, partido)
            return [dict(f, played=None) for f in filas], avisos

        nuevo = self.fichero('nuevo.jsonl', [linea(500, 'event', T3, evento(500, JAEN, RIVAL, ronda=7))])
        with mock.patch.object(importar, 'extraer_convocatorias', distinta):
            v = self.verificacion()
            self.assertFalse(v['ok'])
            self.assertIn('call_up', v['cambios_en_seco'])
            with self.assertRaisesRegex(EstadoIncoherente, 'sincronización en seco da cambios'):
                self.importa([(nuevo, JAEN)])


class Fallos(Base):
    def test_fallo_a_mitad_de_la_transaccion(self):
        nuevo = self.fichero('nuevo.jsonl', [linea(500, 'event', T3, evento(500, JAEN, RIVAL, ronda=7))])
        antes = self.huella_sin_ejecuciones()
        aplicar = sincronizacion.aplicar

        def a_medias(c, plan):
            aplicar(c, plan)
            raise RuntimeError('fallo simulado')

        with mock.patch.object(sincronizacion, 'aplicar', a_medias):
            with self.assertRaisesRegex(RuntimeError, 'fallo simulado'):
                self.importa([(nuevo, JAEN)])
        self.assertEqual(self.huella_sin_ejecuciones(), antes)
        (estado, error, informe), = self.consulta("SELECT status, error, summary FROM import_run WHERE id > 1")
        self.assertEqual(estado, 'failed')
        self.assertIn('fallo simulado', error)
        copia = json.loads(informe)['copia']
        self.assertTrue(Path(copia['ruta']).is_file())
        self.assertEqual(sqlite.describir(copia['ruta'])['huella'], copia['huella'])

    def test_la_verificacion_es_obligatoria_antes_de_confirmar(self):
        nuevo = self.fichero('nuevo.jsonl', [linea(500, 'event', T3, evento(500, JAEN, RIVAL, ronda=7))])
        antes = self.huella_sin_ejecuciones()
        fuera = sincronizacion.convocatorias_fuera_de_seleccion
        llamadas = []

        def falla_tras_escribir(c):
            llamadas.append(1)
            return fuera(c) if len(llamadas) == 1 else [123]

        with mock.patch.object(sincronizacion, 'convocatorias_fuera_de_seleccion', falla_tras_escribir):
            with self.assertRaisesRegex(EstadoIncoherente, 'no salen de la alineación seleccionada'):
                self.importa([(nuevo, JAEN)])
        self.assertEqual(self.huella_sin_ejecuciones(), antes)


class CopiasYRestauracion(Base):
    def test_copia_con_un_lector_abierto_y_diario_wal(self):
        escritor = sqlite3.connect(self.base)
        lector = sqlite3.connect(self.base)
        try:
            escritor.execute('PRAGMA journal_mode = WAL')
            escritor.execute("INSERT INTO import_run (kind, started_at, finished_at, status, error) "
                             "VALUES ('import', 'x', 'y', 'failed', 'prueba')")
            escritor.commit()
            self.assertGreater(Path(str(self.base) + '-wal').stat().st_size, 0)
            lector.execute('BEGIN')
            lector.execute('SELECT count(*) FROM import_run').fetchone()
            copia = sqlite.crear_copia(self.base, 'prueba')
            self.assertEqual(sqlite.describir(copia['ruta'])['recuentos']['import_run'], 2)
            self.assertEqual(copia['huella'], sqlite.describir(self.base)['huella'])
        finally:
            lector.close()
            escritor.close()

    def test_restauracion(self):
        copia = sqlite.crear_copia(self.base, 'antes-de-cambiar')
        respuestas_antes = respuestas(self.sql())
        nuevo = self.fichero('nuevo.jsonl', [linea(500, 'event', T3, evento(500, JAEN, RIVAL, ronda=7))])
        self.importa([(nuevo, JAEN)])
        self.assertNotEqual(respuestas(self.sql()), respuestas_antes)
        revision = sincronizacion.revision_del_codigo()

        # Una copia corrupta o de otra revisión se rechaza; la base no se toca.
        corrupta = self.tmp / 'corrupta.sqlite'
        corrupta.write_bytes(b'esto no es una base')
        otra = self.tmp / 'otra.sqlite'
        shutil.copyfile(copia['ruta'], otra)
        with sqlite3.connect(otra) as c:
            c.execute("UPDATE alembic_version SET version_num = '0003'")
        c.close()
        actual = sqlite.describir(self.base)['huella']
        with self.assertRaises(CopiaNoValida):
            sincronizacion.restaurar(corrupta, self.base)
        with self.assertRaisesRegex(CopiaNoValida, 'revisión 0003'):
            sincronizacion.restaurar(otra, self.base)
        # Con un proceso usando la base, no se sustituye.
        abierta = sqlite3.connect(self.base)
        abierta.execute('SELECT 1').fetchone()
        try:
            if os.name == 'nt':
                with self.assertRaisesRegex(CopiaNoValida, 'en uso'):
                    sincronizacion.restaurar(copia['ruta'], self.base)
        finally:
            abierta.close()
        self.assertEqual(sqlite.describir(self.base)['huella'], actual)

        # Un -wal y un -shm que no son de la copia (sin proceso que los use) se eliminan: aplicados sobre el archivo
        # restaurado lo corromperían.
        for sufijo in ('-wal', '-shm'):
            Path(str(self.base) + sufijo).write_bytes(b'restos')
        hecho = sincronizacion.restaurar(copia['ruta'], self.base)
        self.assertEqual(sqlite.describir(self.base)['huella'], copia['huella'])
        for sufijo in ('-wal', '-shm'):
            resto = Path(str(self.base) + sufijo)
            self.assertFalse(resto.exists() and resto.read_bytes() == b'restos')
        self.assertTrue(Path(hecho['anterior']['ruta']).is_file())
        self.assertIn('antes-de-restaurar', hecho['anterior']['ruta'])
        self.assertEqual(respuestas(self.sql()), respuestas_antes)
        self.assertEqual(sqlite.revision(self.base), revision)


class Migraciones(Base):
    def test_bajar_a_0002_y_subir_conserva_los_datos(self):
        respuestas_antes = respuestas(self.sql())
        command.downgrade(configuracion_alembic(self.base), '0002')
        self.assertEqual(sqlite.revision(self.base), '0002')
        informe = sincronizacion.migrar(self.base)
        self.assertEqual((informe['desde'], informe['revision']), ('0002', '0007'))
        self.assertTrue(Path(informe['copia']).is_file())
        self.assertTrue(informe['verificacion']['ok'], informe['verificacion']['problemas'])
        self.assertEqual(respuestas(self.sql()), respuestas_antes)

    def test_0004_no_baja_con_convocatorias_retiradas(self):
        sin_2 = self.fichero('sin-2.jsonl', [alineacion_200([jugador(1), jugador(4)], [jugador(3), jugador(5)], T3)])
        self.importa([(sin_2, None)])
        with self.assertRaisesRegex(RuntimeError, 'convocatorias retiradas'):
            command.downgrade(configuracion_alembic(self.base), '0003')
        self.assertEqual(sqlite.revision(self.base), '0004')

    def test_no_migra_una_base_en_uso_y_la_marcha_atras_restaura_la_copia_previa(self):
        command.downgrade(configuracion_alembic(self.base), '0002')
        antes = sqlite.describir(self.base)['huella']
        abierta = sqlite3.connect(self.base)
        abierta.execute('SELECT 1').fetchone()
        try:
            if os.name == 'nt':
                with self.assertRaisesRegex(CargaRechazada, 'en uso'):
                    sincronizacion.migrar(self.base)
        finally:
            abierta.close()
        self.assertEqual(sqlite.revision(self.base), '0002')
        informe = sincronizacion.migrar(self.base)
        # Marcha atrás: la copia previa (0002) solo se restaura indicando su revisión a propósito.
        with self.assertRaisesRegex(CopiaNoValida, 'revisión 0002'):
            sincronizacion.restaurar(informe['copia'], self.base)
        sincronizacion.restaurar(informe['copia'], self.base, '0002')
        self.assertEqual((sqlite.revision(self.base), sqlite.describir(self.base)['huella']), ('0002', antes))

    def test_una_base_sin_migrar_no_se_importa(self):
        command.downgrade(configuracion_alembic(self.base), '0002')
        with self.assertRaisesRegex(CargaRechazada, 'persistencia migrar'):
            sincronizacion.verificar(self.base)


class Sustituciones(Base):
    def test_sustituciones_tras_una_importacion(self):
        archivo = self.tmp / 'sustituciones.json'
        archivo.write_text(json.dumps({'sustituciones': [
            {'sustituido': 300, 'vigente': 301, 'motivo': 'aplazado y jugado con otro id',
             'procedencia': {'decidido_por': 'analista', 'fecha': '2026-10-22'}}]}, ensure_ascii=False), encoding='utf-8')
        informe = cargar_sustituciones(self.base, archivo, AMBITO)
        self.assertTrue(Path(informe['copia']).is_file())
        nuevo = self.fichero('nuevo.jsonl', [linea(500, 'event', T3, evento(500, JAEN, RIVAL, ronda=7))])
        self.importa([(nuevo, JAEN)])
        self.assertEqual(self.consulta("SELECT external_replaced_id, external_current_id, status FROM local_replacement"),
                         [('300', '301', 'active')])
        self.igual_al_camino_independiente(archivo)


class SinLecturasRetiradas(unittest.TestCase):
    def test_ninguna_lectura_de_tablas_retiradas_ni_de_convocatorias_sin_la_vista(self):
        lectores = [RAIZ / 'api.py', RAIZ / 'queries.py', RAIZ / 'persistencia' / 'reconstruccion.py', RAIZ / 'persistencia' / 'lectura.py',
                    RAIZ / 'persistencia' / 'decisiones.py']
        for ruta in [*lectores, RAIZ / 'persistencia' / 'carga.py', RAIZ / 'persistencia' / 'sincronizacion.py']:
            texto = ruta.read_text(encoding='utf-8')
            self.assertIsNone(re.search(r'team_match_stat|\bincident\b', texto), ruta.name)
        for ruta in lectores:
            self.assertIsNone(re.search(r'(FROM|JOIN)\s+call_up\b(?!_)', ruta.read_text(encoding='utf-8')), ruta.name)


if __name__ == '__main__':
    unittest.main()
