"""Actualizacion interna: solo fixtures y bases temporales, nunca red ni servicios reales."""
import base64
import concurrent.futures
from contextlib import closing
import errno
import hashlib
import json
from pathlib import Path
import subprocess
import sqlite3
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_carga import Base, AMBITO, CON_SQL, respuestas
from test_importar import JAEN, RIVAL, evento, linea

if CON_SQL:
    from persistencia import actualizacion, carga, lectura, sincronizacion, sqlite
    from persistencia.actualizacion import Coordinador, CoordinacionRechazada
    from persistencia.decisiones import cargar_sustituciones
    from enrichment.store import DEFAULT_ENRICHMENT_VERSION


@unittest.skipUnless(CON_SQL, 'requiere dependencias SQL')
class Actualizacion(Base):
    def setUp(self):
        super().setUp()
        # The isolated test runner may place a marker at /tmp/.git; temporary test databases are still valid here.
        repo_guard = carga.repositorio_que_contiene
        tmp_git_marker = Path('/tmp/.git')
        def ignore_tmp_marker(path):
            resolved = Path(path).resolve()
            if ((resolved == Path('/tmp') or Path('/tmp') in resolved.parents)
                    and tmp_git_marker.is_dir() and not (tmp_git_marker / 'HEAD').is_file()):
                return None
            return repo_guard(path)
        self._tmp_repository_marker = mock.patch.object(carga, 'repositorio_que_contiene', side_effect=ignore_tmp_marker)
        self._tmp_repository_marker.start()
        self.addCleanup(self._tmp_repository_marker.stop)
        carga.cargar(self.importacion(), self.base)
        cargar_sustituciones(self.base, self.sustituciones((300, 301)), AMBITO)
        self.fixture = self.tmp / 'nuevo.jsonl'
        self.fixture.write_text(json.dumps(linea(999, 'event', '2026-10-28T12:00:00+00:00',
            evento(999, JAEN, RIVAL, marcador=(4, 1)))) + '\n', encoding='utf-8')
        self.antes = sqlite.describir(self.base)

    def _seed_selected_comments(self):
        document = {'comments': [{
            'id': 7001,
            'text': '¡Gol! Autogol de Jugador de prueba.',
            'type': 'scoreChange',
            'time': 42,
            'periodName': '1ST',
            'player': {'id': 1393275, 'name': 'Jugador de prueba'},
        }]}
        raw = json.dumps(document, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        digest = hashlib.sha256(raw).hexdigest()
        with closing(sqlite3.connect(self.base)) as connection, connection:
            connection.execute('PRAGMA foreign_keys = ON')
            match_id, event_id = connection.execute(
                """SELECT match.id, external_id.external_id
                   FROM match
                   JOIN match_external_id AS external_id ON external_id.entity_id = match.id
                   WHERE external_id.provider = 'sofascore' AND external_id.status = 'origin'
                   ORDER BY match.id LIMIT 1"""
            ).fetchone()
            cursor = connection.execute(
                """INSERT INTO narrative_comment_capture
                   (match_id, provider, source_event_id, source_path, captured_at, http_status, availability,
                    comment_count, response_sha256, response_json, transport_report_json)
                   VALUES (?, 'sofascore', ?, ?, '2026-11-05T12:00:00+00:00', 200, 'available', 1, ?, ?, '{}')""",
                (match_id, str(event_id), f'/event/{event_id}/comments/es-la', digest, raw.decode('utf-8')),
            )
            capture_id = cursor.lastrowid
            comment = document['comments'][0]
            connection.execute(
                """INSERT INTO narrative_comment
                   (capture_id, source_comment_id, source_position, text, type, minute, period_name,
                    participants_json, context_json)
                   VALUES (?, '7001', 0, ?, ?, 42, '1ST', ?, '{}')""",
                (capture_id, comment['text'], comment['type'], json.dumps({'player': comment['player']})),
            )
            connection.execute(
                """INSERT INTO narrative_comment_selection
                   (match_id, capture_id, last_successful_source_path, selected_spanish_sha256,
                    last_attempt_state, last_attempt_at, last_http_status, last_error_code)
                   VALUES (?, ?, ?, ?, 'available', '2026-11-05T12:00:00+00:00', 200, NULL)""",
                (match_id, capture_id, f'/event/{event_id}/comments/es-la', digest),
            )
        return match_id, int(event_id)

    def _seed_selected_empty_capture(self):
        document = {'comments': []}
        raw = json.dumps(document, separators=(',', ':')).encode('utf-8')
        digest = hashlib.sha256(raw).hexdigest()
        with closing(sqlite3.connect(self.base)) as connection, connection:
            connection.execute('PRAGMA foreign_keys = ON')
            match_id, event_id = connection.execute(
                """SELECT match.id, external_id.external_id
                   FROM match
                   JOIN match_external_id AS external_id ON external_id.entity_id = match.id
                   WHERE external_id.provider = 'sofascore' AND external_id.status = 'origin'
                     AND match.id <> ?
                   ORDER BY match.id LIMIT 1""",
                (self.comment_match_id,),
            ).fetchone()
            cursor = connection.execute(
                """INSERT INTO narrative_comment_capture
                   (match_id, provider, source_event_id, source_path, captured_at, http_status, availability,
                    comment_count, response_sha256, response_json, transport_report_json)
                   VALUES (?, 'sofascore', ?, ?, '2026-11-05T12:00:00+00:00', 200, 'empty', 0, ?, ?, '{}')""",
                (match_id, str(event_id), f'/event/{event_id}/comments/es-la', digest, raw.decode('utf-8')),
            )
            capture_id = cursor.lastrowid
            connection.execute(
                """INSERT INTO narrative_comment_selection
                   (match_id, capture_id, last_successful_source_path, selected_spanish_sha256,
                    last_attempt_state, last_attempt_at, last_http_status, last_error_code)
                   VALUES (?, ?, ?, ?, 'empty', '2026-11-05T12:00:00+00:00', 200, NULL)""",
                (match_id, capture_id, f'/event/{event_id}/comments/es-la', digest),
            )
        return match_id, int(event_id)

    def _seed_enrichment_scenario(self):
        self.assertEqual(sqlite.revision(self.base), '0008')
        self.comment_match_id, self.comment_event_id = self._seed_selected_comments()
        self.empty_match_id, self.empty_event_id = self._seed_selected_empty_capture()
        self.antes = sqlite.describir(self.base)

    def _comments_bundle(self, event_id, comments, suffix):
        raw = json.dumps({'comments': comments}, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        record = dict(event_id=event_id, status=200, raw_b64=base64.b64encode(raw).decode('ascii'),
                      raw_sha256=hashlib.sha256(raw).hexdigest(), reason=None,
                      captured_at=f'2026-11-06T12:00:{suffix:02d}+00:00',
                      transport_report={'provider': 'fixture-site', 'mode': 'offline-fixture'})
        path = self.tmp / f'comments-{suffix}.jsonl'
        path.write_text(json.dumps(record, ensure_ascii=False) + '\n', encoding='utf-8')
        return path

    def abrir(self, **kwargs):
        coordinador = Coordinador(self.base, AMBITO, reserva=0, **kwargs)
        self.addCleanup(coordinador.close)
        return coordinador

    def pedir(self, c):
        return c.solicitar(exportaciones=[(self.fixture, JAEN)])

    def test_enrichment_commits_on_candidate_before_its_description_and_empty_capture_is_complete(self):
        self._seed_enrichment_scenario()
        comprobar = actualizacion.comprobar
        described_candidates = []

        def comprobar_candidata(path):
            description = comprobar(path)
            if Path(path).name == 'candidata.sqlite':
                with closing(sqlite3.connect(path)) as connection:
                    self.assertEqual(connection.execute(
                        """SELECT count(*) FROM narrative_comment_enrichment
                           WHERE capture_id = (SELECT capture_id FROM narrative_comment_selection WHERE match_id = ?)
                             AND enrichment_version = ?""",
                        (self.comment_match_id, DEFAULT_ENRICHMENT_VERSION),
                    ).fetchone()[0], 1)
                    empty_capture = connection.execute(
                        "SELECT capture_id FROM narrative_comment_selection WHERE match_id = ?",
                        (self.empty_match_id,),
                    ).fetchone()[0]
                    self.assertEqual(connection.execute(
                        "SELECT count(*) FROM narrative_comment_enrichment WHERE capture_id = ?",
                        (empty_capture,),
                    ).fetchone()[0], 0)
                described_candidates.append(description)
            return description

        with mock.patch.object(actualizacion, 'comprobar', side_effect=comprobar_candidata):
            with self.abrir() as coordinator:
                state = coordinator.esperar(self.pedir(coordinator))
                self.assertEqual(state['estado'], 'completed', state)
                self.assertEqual(state['resultado']['enrichment_persisted'], {
                    'enrichment_version': DEFAULT_ENRICHMENT_VERSION,
                    'eligible_captures': 2,
                    'comments_seen': 1,
                    'inserted': 1,
                    'unchanged': 0,
                    'participants_inserted': 1,
                })
        self.assertEqual(len(described_candidates), 1)
        self.assertEqual(sqlite.comprobar_copia(state['candidata']), described_candidates[0])

    def test_update_without_new_comments_is_idempotent_and_enriches_newly_selected_capture(self):
        self._seed_enrichment_scenario()
        with self.abrir() as coordinator:
            first = coordinator.esperar(coordinator.solicitar())
            self.assertEqual(first['estado'], 'completed', first)
            self.assertEqual(first['resultado']['enrichment_persisted']['inserted'], 1)
            second = coordinator.esperar(coordinator.solicitar())
            self.assertEqual(second['estado'], 'completed', second)
            self.assertEqual(second['resultado']['enrichment_persisted']['inserted'], 0)
            self.assertEqual(second['resultado']['enrichment_persisted']['unchanged'], 1)

            bundle = self._comments_bundle(self.comment_event_id, [{
                'id': 7002,
                'text': 'Remate de prueba con la derecha',
                'type': 'shotSaved',
                'time': 51,
                'periodName': '2ND',
                'player': {'id': 1393276, 'name': 'Otro jugador'},
            }], 1)
            added = coordinator.esperar(coordinator.solicitar(comentarios=[bundle]))
            self.assertEqual(added['estado'], 'completed', added)
            self.assertEqual(added['resultado']['enrichment_persisted']['inserted'], 1)
            with closing(sqlite3.connect(self.base)) as connection:
                selected = connection.execute(
                    """SELECT selection.capture_id FROM narrative_comment_selection AS selection
                       JOIN narrative_comment_capture AS capture ON capture.id = selection.capture_id
                       WHERE capture.source_event_id = ?""",
                    (str(self.comment_event_id),),
                ).fetchone()[0]
                self.assertEqual(connection.execute(
                    """SELECT count(*) FROM narrative_comment_enrichment
                       WHERE capture_id = ? AND source_comment_id = '7002' AND enrichment_version = ?""",
                    (selected, DEFAULT_ENRICHMENT_VERSION),
                ).fetchone()[0], 1)

    def test_rules_v1_conflict_fails_without_publishing_candidate(self):
        self._seed_enrichment_scenario()
        with closing(sqlite3.connect(self.base)) as connection, connection:
            connection.execute('PRAGMA foreign_keys = ON')
            capture_id = connection.execute(
                "SELECT capture_id FROM narrative_comment_selection WHERE match_id = ?",
                (self.comment_match_id,),
            ).fetchone()[0]
            connection.execute(
                """INSERT INTO narrative_comment_enrichment
                   (capture_id, source_comment_id, enrichment_version, category, attributes_json)
                   VALUES (?, '7001', ?, 'conflicting-category', '{}')""",
                (capture_id, DEFAULT_ENRICHMENT_VERSION),
            )
        self.antes = sqlite.describir(self.base)
        with self.abrir() as coordinator:
            previous = coordinator.capturar()
            state = coordinator.esperar(coordinator.solicitar())
            self.assertEqual(state['estado'], 'failed', state)
            self.assertIn('EnrichmentConflict', state['error'])
            self.assertIs(coordinator.capturar(), previous)
        self.assertEqual(sqlite.describir(self.base), self.antes)

    def test_incomplete_selected_coverage_fails_without_publishing_candidate(self):
        self._seed_enrichment_scenario()
        with self.abrir() as coordinator, mock.patch(
            'enrichment.store.persist_selected_enrichments',
            return_value={'enrichment_version': DEFAULT_ENRICHMENT_VERSION, 'eligible_captures': 2,
                          'comments_seen': 1, 'inserted': 0, 'unchanged': 0, 'participants_inserted': 0},
        ):
            previous = coordinator.capturar()
            state = coordinator.esperar(coordinator.solicitar())
            self.assertEqual(state['estado'], 'failed', state)
            self.assertIn('missing rules-v1 annotations', state['error'])
            self.assertIs(coordinator.capturar(), previous)
        self.assertEqual(sqlite.describir(self.base), self.antes)

    def test_failure_after_candidate_enrichment_does_not_change_confirmed_generation(self):
        self._seed_enrichment_scenario()
        def fail_after_enrichment(point):
            if point == 'enrichment_committed':
                raise RuntimeError('injected failure after enrichment')

        with self.abrir(_frontera=fail_after_enrichment) as coordinator:
            previous = coordinator.capturar()
            state = coordinator.esperar(self.pedir(coordinator))
            self.assertEqual(state['estado'], 'failed', state)
            self.assertIn('injected failure after enrichment', state['error'])
            self.assertIs(coordinator.capturar(), previous)
            with closing(sqlite3.connect(state['candidata'])) as candidate:
                self.assertEqual(candidate.execute(
                    """SELECT count(*) FROM narrative_comment_enrichment
                       WHERE enrichment_version = ?""",
                    (DEFAULT_ENRICHMENT_VERSION,),
                ).fetchone()[0], 1)
        self.assertEqual(sqlite.describir(self.base), self.antes)

    def test_exito_lectores_ids_originales_decisiones_y_respuestas(self):
        ids = {t: self.consulta(f'SELECT * FROM {t} ORDER BY 1') for t in
               ('team', 'player', 'match', 'observation', 'content', 'local_replacement')}
        pausado, seguir = threading.Event(), threading.Event()
        def frontera(punto):
            if punto == 'principal_replaced':
                pausado.set()
                assert seguir.wait(10)
        c = self.abrir(_frontera=frontera)
        vieja = c.capturar()
        previas = respuestas(vieja._datos)
        ident = self.pedir(c)
        self.assertTrue(pausado.wait(10))
        self.assertIs(c.capturar(), vieja)
        self.assertEqual(respuestas(vieja._datos), previas)
        seguir.set()
        self.assertEqual(c.esperar(ident)['estado'], 'completed')
        nueva = c.capturar()
        self.assertNotEqual(nueva.id, vieja.id)
        self.assertEqual(vieja.consultar('/api/v1/matches/999')[0], 404)
        self.assertEqual(nueva.consultar('/api/v1/matches/999')[0], 200)
        self.assertEqual(respuestas(vieja._datos), previas)
        c.close()
        self.assertEqual(respuestas(nueva._datos), respuestas(lectura.cargar_desde_base(self.base, AMBITO)))
        for tabla, filas in ids.items():
            actuales = self.consulta(f'SELECT * FROM {tabla} ORDER BY 1')
            self.assertTrue(set(filas) <= set(actuales), tabla)
        # Respuesta ajena no puede mutar la instantanea publicada.
        _, cuerpo = nueva.consultar('/api/v1/matches/999')
        cuerpo.clear()
        self.assertTrue(nueva.consultar('/api/v1/matches/999')[1])
        c.close()
        with Coordinador(self.base, AMBITO, reserva=0) as reabierto:
            self.assertEqual(reabierto.capturar().id, nueva.id)
            self.assertEqual(reabierto.estado(ident)['estado'], 'completed')

    def test_sin_cambios_y_rechazo_de_otra_instancia_o_cli(self):
        with self.abrir() as c:
            anteriores = respuestas(c.capturar()._datos)
            ident = c.solicitar()
            estado = c.esperar(ident)
            self.assertEqual(estado['estado'], 'completed')
            self.assertEqual(estado['resultado']['estado'], 'no_changes')
            self.assertEqual(respuestas(c.capturar()._datos), anteriores)
            with self.assertRaises(Exception):
                Coordinador(self.base, AMBITO)
            p = subprocess.run([sys.executable, '-B', '-m', 'persistencia', 'migrar', '--base', str(self.base)],
                               capture_output=True, text=True, timeout=15)
            self.assertEqual(p.returncode, 1)
            self.assertIn('Base en uso', p.stderr)

    def test_concurrentes_deduplicadas(self):
        espera, continuar = threading.Event(), threading.Event()
        def pausa(p):
            if p == 'before_backup':
                espera.set()
                assert continuar.wait(10)
        with self.abrir(_frontera=pausa) as c:
            primero = self.pedir(c)
            self.assertTrue(espera.wait(5))
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                ids = list(pool.map(lambda _: self.pedir(c), range(24)))
            self.assertEqual(set(ids), {primero})
            continuar.set()
            self.assertEqual(c.esperar(primero)['estado'], 'completed')
            with closing(sqlite.sqlite3.connect(c.journal)) as j:
                self.assertEqual(j.execute('SELECT count(*) FROM trabajo').fetchone()[0], 1)

    def test_fallos_antes_y_despues_commit_y_recuperacion(self):
        for punto in ('before_backup', 'backup_saved', 'before_import', 'candidate_committed',
                      'snapshot_prepared', 'publication_prepared', 'principal_replaced', 'before_confirmation'):
            with self.subTest(punto=punto):
                visitadas = []
                def fallo(p):
                    visitadas.append(p)
                    if p == punto:
                        raise RuntimeError('fallo inyectado ' + p)
                with self.abrir(_frontera=fallo) as c:
                    vieja = c.capturar()
                    estado = c.esperar(self.pedir(c))
                    self.assertEqual(estado['estado'], 'failed', estado)
                    self.assertIn(punto, visitadas)
                    self.assertIs(c.capturar(), vieja)
                    self.assertEqual(sqlite.describir(self.base), self.antes)

    def test_error_post_confirmacion_no_revierte_y_commit_ambiguo(self):
        for punto in ('confirmed', 'swapped'):
            with self.subTest(punto=punto):
                def fallo(p):
                    if p == punto:
                        raise OSError('fallo despues del COMMIT')
                with self.abrir(_frontera=fallo) as c:
                    ident = self.pedir(c)
                    self.assertEqual(c.esperar(ident)['estado'], 'completed')
                    self.assertEqual(c.capturar().consultar('/api/v1/matches/999')[0], 200)
        with self.abrir() as c:
            guardar = c._guardar
            def ambiguo(trabajo, meta=None):
                guardar(trabajo, meta)
                if meta:
                    raise OSError('COMMIT confirmado pero excepcion al devolver')
            with mock.patch.object(c, '_guardar', side_effect=ambiguo):
                ident = c.solicitar()
                self.assertEqual(c.esperar(ident)['estado'], 'completed')
                self.assertEqual(c.capturar().id, c.estado(ident)['generacion'])

    def test_fallo_transaccional_en_importador_existente(self):
        original = sincronizacion.exigir_coherencia
        def fallar(c):
            if c.exec_driver_sql("SELECT count(*) FROM match_external_id WHERE external_id='999'").scalar():
                raise RuntimeError('fallo antes del COMMIT real')
            return original(c)
        with self.abrir() as coordinador, mock.patch.object(sincronizacion, 'exigir_coherencia', side_effect=fallar):
            estado = coordinador.esperar(self.pedir(coordinador))
            self.assertEqual(estado['estado'], 'failed')
            candidata = Path(estado['candidata'])
            with closing(sqlite._lectura(candidata)) as c:
                self.assertEqual(c.execute('SELECT count(*) FROM match_external_id WHERE external_id=999').fetchone()[0], 0)
            self.assertEqual(sqlite.describir(self.base), self.antes)

    def test_recuperacion_fallida_bloquea_y_se_reintenta_al_abrir(self):
        def fallo(p):
            if p == 'principal_replaced':
                raise RuntimeError('no publicar')
        c = self.abrir(_frontera=fallo)
        restaurar = c._restaurar
        def no_recuperar(copia, descripcion):
            if str(copia).endswith('candidata.sqlite'):
                return restaurar(copia, descripcion)
            raise OSError(errno.ENOSPC, 'disco lleno al recuperar')
        with mock.patch.object(c, '_restaurar', side_effect=no_recuperar):
            ident = self.pedir(c)
            with self.assertRaises(CoordinacionRechazada):
                c.esperar(ident)
            with self.assertRaises(CoordinacionRechazada):
                c.solicitar()
        c.close()
        with Coordinador(self.base, AMBITO, reserva=0) as reabierto:
            self.assertEqual(sqlite.describir(self.base), self.antes)
            self.assertEqual(reabierto.estado(ident)['estado'], 'interrupted')
            self.assertEqual(reabierto.capturar().consultar('/api/v1/matches/999')[0], 404)

    def test_codigo_ambito_y_disco(self):
        c = self.abrir()
        with mock.patch.object(actualizacion, 'codigo', return_value='otra-huella'):
            estado = c.esperar(self.pedir(c))
            self.assertEqual(estado['estado'], 'failed')
            self.assertIn('codigo', estado['error'])
        with mock.patch.object(actualizacion.shutil, 'disk_usage') as uso:
            uso.return_value.free = 0
            self.assertIn('Espacio', c.esperar(self.pedir(c))['error'])
        c.close()
        with mock.patch.object(actualizacion, 'codigo', return_value='otra-huella'):
            with self.assertRaises(CoordinacionRechazada):
                Coordinador(self.base, AMBITO)
        self.assertEqual(sqlite.describir(self.base), self.antes)

    def test_acepta_la_huella_anterior_a_la_eliminacion_del_servidor_http(self):
        anterior = 'bd9446235f4e6a1ec8618fc0f35124a7b31f94a0dad55c4539c4892c875ebb6a'
        with sqlite3.connect(self.base) as connection:
            summary = json.loads(connection.execute('SELECT summary FROM import_run WHERE id=1').fetchone()[0])
            summary['codigo']['sha256_agregada'] = anterior
            connection.execute('UPDATE import_run SET summary=? WHERE id=1', (json.dumps(summary),))

        with Coordinador(self.base, AMBITO, reserva=0) as coordinator:
            self.assertEqual(coordinator._meta()['codigo'], actualizacion.codigo())

        journal_path = self.base.with_name(self.base.name + '.actualizaciones') / 'jobs.sqlite'
        with sqlite3.connect(self.base) as connection:
            stored = json.loads(connection.execute('SELECT summary FROM import_run WHERE id=1').fetchone()[0])
        with sqlite3.connect(journal_path) as connection:
            journal = json.loads(connection.execute('SELECT detalle FROM confirmado WHERE id=1').fetchone()[0])
            journal['codigo'] = anterior
            connection.execute('UPDATE confirmado SET detalle=? WHERE id=1', (json.dumps(journal),))
        with Coordinador(self.base, AMBITO, reserva=0) as coordinator:
            self.assertEqual(coordinator._meta()['codigo'], actualizacion.codigo())
        with sqlite3.connect(journal_path) as connection:
            journal = json.loads(connection.execute('SELECT detalle FROM confirmado WHERE id=1').fetchone()[0])
        self.assertEqual(stored['codigo']['sha256_agregada'], anterior)
        self.assertEqual(journal['codigo'], actualizacion.codigo())
        self.assertTrue(actualizacion.codigo_compatible(anterior))
        self.assertFalse(actualizacion.codigo_compatible('0' * 64))

    def test_adopta_huella_de_espera_de_publicacion_sin_cambiar_datos_deportivos(self):
        anterior = '71ede1b31b4c6ca56380d1b702da6b69dc5df8df8a48b9ba264ee04166941404'
        antes = sqlite.describir(self.base)
        with self.abrir() as coordinator:
            metadata = coordinator._meta()
            metadata['codigo'] = anterior
            with sqlite3.connect(coordinator.journal) as journal:
                journal.execute('UPDATE confirmado SET detalle=? WHERE id=1', (json.dumps(metadata),))

        with self.abrir() as coordinator:
            self.assertEqual(coordinator._meta()['codigo'], actualizacion.codigo())
        self.assertEqual(sqlite.describir(self.base), antes)

    def test_adopta_huella_del_release_desplegado_sin_cambiar_datos_deportivos(self):
        anterior = '9dd45fbfafcbe374daac683c1f589caaac8ec0548e473ae5d8e8072a30e32f20'
        antes = sqlite.describir(self.base)
        with self.abrir() as coordinator:
            metadata = coordinator._meta()
            metadata['codigo'] = anterior
            with sqlite3.connect(coordinator.journal) as journal:
                journal.execute('UPDATE confirmado SET detalle=? WHERE id=1', (json.dumps(metadata),))

        with self.abrir() as coordinator:
            self.assertEqual(coordinator._meta()['codigo'], actualizacion.codigo())
        self.assertEqual(sqlite.describir(self.base), antes)

    def test_adopta_huella_group_v3_sin_cambiar_datos_deportivos(self):
        anterior = 'eb595d471b92c7bfb0b464800c8d5f02f0b1d62233a49a84fdea0132ac19332b'
        antes = sqlite.describir(self.base)
        with self.abrir() as coordinator:
            metadata = coordinator._meta()
            metadata['codigo'] = anterior
            with sqlite3.connect(coordinator.journal) as journal:
                journal.execute('UPDATE confirmado SET detalle=? WHERE id=1', (json.dumps(metadata),))

        with self.abrir() as coordinator:
            self.assertEqual(coordinator._meta()['codigo'], actualizacion.codigo())
        self.assertEqual(sqlite.describir(self.base), antes)

    def test_enospc_copia_y_journal_antes_de_confirmar(self):
        original = sqlite.copia_consistente
        def sin_espacio(origen, destino):
            if Path(destino).name == 'candidata.sqlite':
                raise OSError(errno.ENOSPC, 'sin espacio para candidata')
            return original(origen, destino)
        with self.abrir() as c, mock.patch.object(sqlite, 'copia_consistente', side_effect=sin_espacio):
            estado = c.esperar(self.pedir(c))
            self.assertEqual(estado['estado'], 'failed')
            self.assertEqual(sqlite.describir(self.base), self.antes)
        with self.abrir() as c:
            guardar = c._guardar
            def journal_lleno(trabajo, meta=None):
                if meta:
                    raise OSError(errno.ENOSPC, 'sin espacio para confirmar journal')
                guardar(trabajo, meta)
            with mock.patch.object(c, '_guardar', side_effect=journal_lleno):
                estado = c.esperar(self.pedir(c))
                self.assertEqual(estado['estado'], 'failed')
                self.assertEqual(sqlite.describir(self.base), self.antes)
                self.assertEqual(c.capturar().consultar('/api/v1/matches/999')[0], 404)

    def test_fallo_de_replace_usa_la_misma_restauracion(self):
        original = sqlite.os.replace
        llamadas = []
        def replace(origen, destino):
            llamadas.append(str(destino))
            if len(llamadas) == 1:
                raise OSError(errno.ENOSPC, 'fallo de replace')
            return original(origen, destino)
        with self.abrir() as c, mock.patch.object(sqlite.os, 'replace', side_effect=replace):
            estado = c.esperar(self.pedir(c))
            self.assertEqual(estado['estado'], 'failed')
            self.assertEqual(len(llamadas), 2)
            self.assertEqual(sqlite.describir(self.base), self.antes)

    def test_confirmacion_ilegible_bloquea_capturas_y_reconcilia(self):
        def fallo(p):
            if p == 'confirmed':
                raise OSError('confirmacion ambigua')
        c = self.abrir(_frontera=fallo)
        vieja = c.capturar()
        leer = c._leer
        def ilegible(ident):
            trabajo = leer(ident)
            if trabajo['estado'] == 'completed':
                raise OSError('journal temporalmente ilegible')
            return trabajo
        with mock.patch.object(c, '_leer', side_effect=ilegible):
            ident = self.pedir(c)
            limite = time.monotonic() + 10
            while c._bloqueado is None and time.monotonic() < limite:
                time.sleep(0.01)
            self.assertIsNotNone(c._bloqueado)
            with self.assertRaises(CoordinacionRechazada):
                c.capturar()
            with self.assertRaises(CoordinacionRechazada):
                c.solicitar()
            self.assertEqual(vieja.consultar('/api/v1/matches/999')[0], 404)
        c.close()
        with Coordinador(self.base, AMBITO, reserva=0) as nuevo:
            self.assertEqual(nuevo.estado(ident)['estado'], 'completed')
            self.assertEqual(nuevo.capturar().id, nuevo.estado(ident)['generacion'])
            self.assertEqual(nuevo.capturar().consultar('/api/v1/matches/999')[0], 200)

    def test_cambio_ambito_rechazado_sin_tocar_principal(self):
        otro = self.tmp / 'otro-ambito.json'
        otro.write_bytes(AMBITO.read_bytes() + b'\n')
        with self.abrir() as c:
            with mock.patch.object(c, 'ambito', otro):
                estado = c.esperar(self.pedir(c))
                self.assertEqual(estado['estado'], 'failed')
                self.assertIn('ambito', estado['error'])
            self.assertEqual(sqlite.describir(self.base), self.antes)
        with self.assertRaises(CoordinacionRechazada):
            Coordinador(self.base, otro)

    def test_terminacion_y_reinicio_en_fronteras(self):
        puntos = ('before_backup', 'backup_saved', 'before_import', 'candidate_committed',
                  'snapshot_prepared', 'publication_prepared', 'principal_replaced',
                  'before_confirmation', 'confirmed', 'swapped', 'recovered')
        for numero, punto in enumerate(puntos):
            with self.subTest(punto=punto):
                base = self.tmp / f'crash-{numero}.sqlite'
                sqlite.copia_consistente(self.base, base)
                code = '''import os, sys
from persistencia.actualizacion import Coordinador
punto = sys.argv[4]
def frontera(p):
    if punto == 'recovered' and p == 'principal_replaced':
        raise RuntimeError('recuperar')
    if p == punto:
        print(p, flush=True)
        os._exit(73)
c = Coordinador(sys.argv[1], sys.argv[2], reserva=0, _frontera=frontera)
i = c.solicitar(exportaciones=[(sys.argv[3], int(sys.argv[5]))])
try:
    resultado = c.esperar(i)
    raise RuntimeError('La frontera no se alcanzo: ' + str(resultado))
finally:
    c.close()
'''
                p = subprocess.run([sys.executable, '-B', '-u', '-c', code, str(base), str(AMBITO),
                                    str(self.fixture), punto, str(JAEN)], capture_output=True, text=True, timeout=30)
                self.assertEqual(p.returncode, 73, p.stderr)
                self.assertIn(punto, p.stdout)
                with Coordinador(base, AMBITO, reserva=0) as nuevo:
                    publicado = punto in ('confirmed', 'swapped')
                    self.assertEqual(nuevo.capturar().consultar('/api/v1/matches/999')[0], 200 if publicado else 404)
                    estados = nuevo._activos()
                    self.assertEqual(estados, [])
                    if not publicado:
                        self.assertEqual(sqlite.describir(base), self.antes)
                    # Reiniciar no importa el fixture ni crea otro trabajo.
                    with closing(sqlite.sqlite3.connect(nuevo.journal)) as j:
                        self.assertEqual(j.execute('SELECT count(*) FROM trabajo').fetchone()[0], 1)
