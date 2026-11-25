"""Coordinador interno para fixtures locales. No gestiona HTTP ni accede a la red por sí mismo.

Un hilo propietario mantiene el bloqueo durante apertura, trabajo y recuperación.
El journal operativo es independiente de la base deportiva. Ver docs/coordinador-fixtures.md.
"""
from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import queue
import shutil
import sqlite3
import threading
import time
import uuid

import api
from persistencia import carga, lectura, sincronizacion, sqlite
from persistencia.bloqueo import bloquear, ruta_normalizada
from persistencia.comentarios import load_current_comments, match_comments_response
from persistencia.enrichment_snapshot import EnrichmentSnapshot, EnrichmentSnapshotRegistry


class CoordinacionRechazada(RuntimeError):
    pass


def codigo():
    return carga.referencia_del_codigo()['sha256_agregada']


# The 0d12fffc release fingerprint is accepted as the same sporting rules: this cleanup only removes the
# sports-data HTTP/CLI implementations. Historical import metadata stays intact; an old coordinator journal is
# upgraded once to the current fingerprint. Acquisition producer approvals are separate and remain unchanged.
REFERENCIAS_CODIGO_COMPATIBLES = frozenset({
    'bd9446235f4e6a1ec8618fc0f35124a7b31f94a0dad55c4539c4892c875ebb6a',
    # This release only adds mode-matched Acquisition readiness to persistencia.adquisicion; sporting rules are unchanged.
    '376f2cdc92f351ab9fa7056eb584a0e3813f1cf61fe0d9a6b362a996ce7cc7fe',
    # This release keeps waiting for the same durable publication after one coordinator polling window expires.
    '71ede1b31b4c6ca56380d1b702da6b69dc5df8df8a48b9ba264ee04166941404',
    # This release bounds that observation window and reconciles the same durable publication ID; sporting rules stay unchanged.
    '41d545996a790ffbf1116ea906ed48dc37b36570640142833dd8513cb65173b7',
    # The 7aa6f2a release adds the intended group-v3 verifier dispatch; importer, schema and SQLite rules are unchanged.
    'eb595d471b92c7bfb0b464800c8d5f02f0b1d62233a49a84fdea0132ac19332b',
    # The 4d46649 development image is the deployed source fingerprint. This release changes only Acquisition
    # package planning/validation in bundle.py and acquisition orchestration; importer, schema and SQLite rules
    # are unchanged, so the confirmed coordinator metadata can adopt the new fingerprint without a migration.
    '9dd45fbfafcbe374daac683c1f589caaac8ec0548e473ae5d8e8072a30e32f20',
})


def codigo_compatible(referencia):
    return isinstance(referencia, str) and (
        referencia == codigo() or referencia in REFERENCIAS_CODIGO_COMPATIBLES
    )


def comprobar(base):
    resultado = sincronizacion.verificar(base)
    if not resultado['ok']:
        raise CoordinacionRechazada(str(resultado))
    return sqlite.comprobar_copia(base)


@dataclass(frozen=True)
class Generacion:
    id: str
    _datos: dict
    _comentarios: dict
    comments_enabled: bool = True
    enrichment_snapshot: EnrichmentSnapshot | None = None

    def consultar(self, ruta):
        # Devuelve una respuesta propia: los consumidores no pueden mutar la instantánea.
        estado, cuerpo = api.consultar(self._datos, ruta)
        return estado, json.loads(json.dumps(cuerpo, ensure_ascii=False, allow_nan=False))

    def consultar_comentarios(self, public_match_id):
        estado, cuerpo = match_comments_response(self._datos, self._comentarios, public_match_id)
        return estado, json.loads(json.dumps(cuerpo, ensure_ascii=False, allow_nan=False))


def preparar(base, ambito, ident, *, enrichment_snapshots=None):
    from persistencia.comentarios import comments_schema_ready
    snapshot = enrichment_snapshots.create(base, ident) if enrichment_snapshots is not None else None
    source = snapshot.path if snapshot is not None else base
    try:
        generacion = Generacion(ident, lectura.cargar_desde_base(source, ambito), load_current_comments(source),
                                comments_schema_ready(source), snapshot)
        rutas = ['/health', '/api/v1/scope']
        for entidad, plural, clave, sufijos in (
            ('teams', 'equipos', 'team_id', ('', '/players', '/matches', '/performance', '/player-stats')),
            ('players', 'jugadores', 'player_id', ('', '/matches')),
            ('matches', 'partidos', 'match_id', ('', '/statistics', '/lineups', '/incidents')),
        ):
            rutas.extend(f'/api/v1/{entidad}/{fila[clave]}{sufijo}'
                         for fila in generacion._datos[plural] for sufijo in sufijos)
        for ruta in rutas:
            generacion.consultar(ruta)
        for fila in generacion._datos['partidos']:
            public_match_id = f"sofascore-match-{fila['match_id']}"
            status, _ = generacion.consultar_comentarios(public_match_id)
            if status != 200:
                raise CoordinacionRechazada(f'No se pudo preparar la instantánea de comentarios de {public_match_id}')
        return generacion
    except BaseException:
        if snapshot is not None:
            snapshot.release_owner()
        raise


def preparar_postgresql(conexion, ambito, ident):
    """Prepare an immutable API generation from the caller's uncommitted PostgreSQL snapshot."""
    from persistencia.comentarios import comments_schema_ready, load_current_comments_postgresql

    generacion = Generacion(
        ident,
        lectura.cargar_desde_postgresql(conexion, ambito),
        load_current_comments_postgresql(conexion),
        comments_schema_ready(conexion),
        None,
    )
    rutas = ['/health', '/api/v1/scope']
    for plural, entidad, clave, sufijos in (
        ('equipos', 'teams', 'team_id', ('', '/players', '/matches', '/performance', '/player-stats')),
        ('jugadores', 'players', 'player_id', ('', '/matches')),
        ('partidos', 'matches', 'match_id', ('', '/statistics', '/lineups', '/incidents')),
    ):
        rutas.extend(f'/api/v1/{entidad}/{fila[clave]}{sufijo}'
                     for fila in generacion._datos[plural] for sufijo in sufijos)
    for ruta in rutas:
        generacion.consultar(ruta)
    for fila in generacion._datos['partidos']:
        public_match_id = f"sofascore-match-{fila['match_id']}"
        status, _ = generacion.consultar_comentarios(public_match_id)
        if status != 200:
            raise CoordinacionRechazada(f'No se pudo preparar la generación de comentarios de {public_match_id}')
    return generacion


class Coordinador:
    """Uso explícito en pruebas/herramientas internas; no crea ni migra la base.

    exportaciones: secuencia (ruta fixture JSONL, equipo externo); calendarios/fichas: rutas.
    El callback privado _frontera permite pruebas de terminación, no ejecuta comandos.
    """
    def __init__(self, base, ambito, *, reserva=64 * 1024 * 1024, _frontera=None,
                 enrichment_snapshots=False):
        self.base = ruta_normalizada(base)
        self.ambito = Path(ambito).resolve()
        self.directorio = self.base.with_name(self.base.name + '.actualizaciones')
        self.journal = self.directorio / 'jobs.sqlite'
        self._enrichment_snapshots = (EnrichmentSnapshotRegistry(self.directorio / 'enrichment-snapshots')
                                      if enrichment_snapshots else None)
        self.reserva = reserva
        self._frontera = _frontera or (lambda nombre: None)
        self._puerta = threading.Lock()
        self._cola = queue.Queue()
        self._listo = threading.Event()
        self._bloqueado = None
        self._inicio_error = None
        self._cerrado = False
        self._hilo = threading.Thread(target=self._propietario, daemon=False)
        self._hilo.start()
        self._listo.wait()
        if self._inicio_error:
            self._hilo.join()
            actual = getattr(self, '_actual', None)
            if actual is not None:
                self._liberar_generacion(actual)
                self._actual = None
            raise self._inicio_error

    def _db(self):
        c = sqlite3.connect(self.journal, timeout=10)
        try:
            c.execute('PRAGMA synchronous=FULL')
            return c
        except BaseException:
            c.close()
            raise

    def _leer(self, ident):
        with closing(self._db()) as c:
            fila = c.execute('SELECT detalle FROM trabajo WHERE id=?', (ident,)).fetchone()
        if fila is None:
            raise KeyError(ident)
        return json.loads(fila[0])

    def estado(self, ident):
        return self._leer(ident)

    def _guardar(self, trabajo, meta=None):
        with closing(self._db()) as c, c:
            c.execute('UPDATE trabajo SET estado=?, detalle=? WHERE id=?',
                      (trabajo['estado'], json.dumps(trabajo), trabajo['id']))
            if meta is not None:
                c.execute('UPDATE confirmado SET detalle=? WHERE id=1', (json.dumps(meta),))

    def _meta(self):
        with closing(self._db()) as c:
            fila = c.execute('SELECT detalle FROM confirmado WHERE id=1').fetchone()
        return json.loads(fila[0]) if fila else None

    def _activos(self):
        with closing(self._db()) as c:
            return [json.loads(f[0]) for f in c.execute(
                "SELECT detalle FROM trabajo WHERE estado IN ('queued', 'running')")]

    def _reglas(self):
        return {'codigo': codigo(), 'ambito': hashlib.sha256(self.ambito.read_bytes()).hexdigest()}

    def _exigir_reglas(self, esperado):
        if self._reglas() != {k: esperado[k] for k in ('codigo', 'ambito')}:
            raise CoordinacionRechazada('Cambios de codigo o ambito: mantenimiento explicito requerido')

    def _restaurar(self, copia, descripcion):
        # Única vía: también para deshacer una promoción que ya reemplazó el principal.
        sincronizacion.restaurar(copia, self.base)
        if comprobar(self.base) != descripcion:
            raise CoordinacionRechazada('La recuperacion no reproduce la descripcion esperada')

    def _recuperar(self, trabajo):
        trabajo.update(estado='running', fase='recovering')
        self._guardar(trabajo)
        self._restaurar(trabajo['copia'], trabajo['anterior'])
        self._frontera('recovered')

    def _abrir(self):
        if not self.base.is_file():
            raise CoordinacionRechazada('La base debe existir; control interno no inicializa datos')
        self.directorio.mkdir(exist_ok=True)
        with closing(self._db()) as c, c:
            c.execute('CREATE TABLE IF NOT EXISTS trabajo(id TEXT PRIMARY KEY, estado TEXT NOT NULL, detalle TEXT NOT NULL)')
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS unico_activo ON trabajo((1)) WHERE estado IN ('queued','running')")
            c.execute('CREATE TABLE IF NOT EXISTS confirmado(id INTEGER PRIMARY KEY CHECK(id=1), detalle TEXT NOT NULL)')
        for trabajo in self._activos():
            if trabajo['fase'] in ('publishing', 'recovering'):
                self._recuperar(trabajo)
            trabajo.update(estado='interrupted', fase='finished', error='Proceso interrumpido; no se reanuda automaticamente')
            self._guardar(trabajo)
        descripcion = comprobar(self.base)
        actuales = self._reglas()
        meta = self._meta()
        if meta is None:
            # No aceptar silenciosamente reglas distintas a las archivadas en la evidencia.
            with closing(sqlite._lectura(self.base)) as c:
                filas = c.execute("SELECT summary FROM import_run WHERE status IN ('committed','no_changes') ORDER BY id DESC").fetchall()
            referencia = next((json.loads(f[0]).get('codigo') for f in filas
                               if f[0] and json.loads(f[0]).get('codigo')), None)
            if not referencia or not codigo_compatible(referencia.get('sha256_agregada')):
                raise CoordinacionRechazada('No se acredita la huella de reglas de la base inicial')
            meta = dict(actuales, generacion=str(uuid.uuid4()), descripcion=descripcion)
            with closing(self._db()) as c, c:
                c.execute('INSERT INTO confirmado VALUES(1,?)', (json.dumps(meta),))
        elif meta.get('codigo') != actuales['codigo']:
            if not codigo_compatible(meta.get('codigo')):
                raise CoordinacionRechazada('Cambios de codigo o ambito: mantenimiento explicito requerido')
            meta = dict(meta, codigo=actuales['codigo'])
            with closing(self._db()) as c, c:
                c.execute('UPDATE confirmado SET detalle=? WHERE id=1', (json.dumps(meta),))
        self._exigir_reglas(meta)
        if descripcion != meta['descripcion']:
            raise CoordinacionRechazada('El principal no coincide con la generacion confirmada')
        if self._enrichment_snapshots is not None:
            # The previous process no longer owns the base lock. Rebuild a snapshot
            # from the durable confirmed generation and remove only stale snapshot
            # directories; no candidate or journal evidence is touched.
            self._enrichment_snapshots.cleanup_after_restart()
        self._actual = preparar(self.base, self.ambito, meta['generacion'],
                                enrichment_snapshots=self._enrichment_snapshots)

    def _propietario(self):
        try:
            with bloquear(self.base):
                self._abrir()
                self._listo.set()
                while True:
                    item = self._cola.get()
                    if item is None:
                        return
                    if not self._bloqueado:
                        self._ejecutar(item)
        except BaseException as e:
            self._inicio_error = e
        finally:
            self._listo.set()

    def capturar(self, *, enrichment=False):
        with self._puerta:
            generacion = self._capturar_locked()
            if not enrichment:
                return generacion
            if generacion.enrichment_snapshot is None:
                raise CoordinacionRechazada('La instantánea de enrichment no está habilitada')
            try:
                return generacion.enrichment_snapshot.retain()
            except RuntimeError as error:
                raise CoordinacionRechazada('La instantánea de enrichment ya no está disponible') from error

    def _capturar_locked(self):
        if self._cerrado or self._bloqueado or not self._hilo.is_alive():
            raise CoordinacionRechazada(str(self._bloqueado or 'Coordinador cerrado o propietario no disponible'))
        return self._actual

    @staticmethod
    def _liberar_generacion(generacion):
        if generacion is not None and generacion.enrichment_snapshot is not None:
            generacion.enrichment_snapshot.release_owner()

    def _instalar_generacion(self, generacion):
        anterior = self._actual
        if anterior is generacion:
            return None
        self._actual = generacion
        return anterior

    def solicitar(self, *, exportaciones=(), calendarios=(), fichas=(), comentarios=(), identificador=None,
                  generacion_esperada=None):
        entradas = dict(exportaciones=[(str(Path(p).resolve()), e) for p, e in exportaciones],
                        calendarios=[str(Path(p).resolve()) for p in calendarios],
                        fichas=[str(Path(p).resolve()) for p in fichas],
                        comentarios=[str(Path(p).resolve()) for p in comentarios])
        if identificador is not None and str(uuid.UUID(identificador)) != identificador:
            raise CoordinacionRechazada('Identificador de solicitud invalido')
        with self._puerta:
            if self._cerrado or self._bloqueado or not self._hilo.is_alive():
                raise CoordinacionRechazada(str(self._bloqueado or 'Coordinador cerrado'))
            if generacion_esperada is not None and self._actual.id != generacion_esperada:
                raise CoordinacionRechazada('La generacion de la solicitud ya no esta vigente')
            with closing(self._db()) as c, c:
                c.execute('BEGIN IMMEDIATE')
                if identificador is not None:
                    anterior = c.execute('SELECT detalle FROM trabajo WHERE id=?', (identificador,)).fetchone()
                    if anterior:
                        if json.loads(anterior[0])['entradas'] != json.loads(json.dumps(entradas)):
                            raise CoordinacionRechazada('Solicitud reutilizada con otras entradas')
                        return identificador
                activo = c.execute("SELECT id FROM trabajo WHERE estado IN ('queued','running')").fetchone()
                if activo:
                    if identificador is not None and identificador != activo[0]:
                        raise CoordinacionRechazada('Otro trabajo activo')
                    return activo[0]
                ident = identificador or str(uuid.uuid4())
                trabajo = dict(id=ident, estado='queued', fase='queued', entradas=entradas, generacion_base=generacion_esperada)
                c.execute('INSERT INTO trabajo VALUES(?,?,?)', (ident, 'queued', json.dumps(trabajo)))
            self._cola.put(ident)
            return ident

    def esperar(self, ident, timeout=30):
        limite = time.monotonic() + timeout
        while time.monotonic() < limite:
            estado = self.estado(ident)
            if estado['estado'] not in ('queued', 'running'):
                return estado
            if self._bloqueado:
                raise CoordinacionRechazada(str(self._bloqueado))
            time.sleep(0.01)
        raise TimeoutError(ident)

    def _espacio(self, trabajo):
        rutas = [Path(p) for p, _ in trabajo['entradas']['exportaciones']]
        rutas += [Path(p) for grupo in ('calendarios', 'fichas', 'comentarios') for p in trabajo['entradas'][grupo]]
        volumen = self.base.stat().st_dev
        if self.directorio.stat().st_dev != volumen:
            raise CoordinacionRechazada('Candidata y principal necesitan el mismo almacenamiento')
        tamano = self.base.stat().st_size + sum(p.stat().st_size for p in rutas)
        tamano += sum(p.stat().st_size for p in carga.acompanantes(self.base) if p.exists())
        requerido = 12 * tamano + self.reserva
        if shutil.disk_usage(self.base.parent).free < requerido:
            raise OSError('Espacio insuficiente antes de preparar la candidata')

    def _congelar(self, trabajo):
        directorio = self.directorio / trabajo['id']
        directorio.mkdir()
        entradas, manifiesto = {}, []
        for grupo, valores in trabajo['entradas'].items():
            entradas[grupo] = []
            for i, valor in enumerate(valores):
                ruta, equipo = valor if grupo == 'exportaciones' else (valor, None)
                destino = directorio / f'{grupo}-{i}.jsonl'
                shutil.copyfile(ruta, destino)
                # fsync antes de poder publicar una candidata basada en esta evidencia.
                with destino.open('r+b') as f:
                    import os
                    os.fsync(f.fileno())
                manifiesto.append(dict(origen=ruta, fichero=str(destino),
                                       sha256=hashlib.sha256(destino.read_bytes()).hexdigest()))
                entradas[grupo].append((destino, equipo) if grupo == 'exportaciones' else destino)
        trabajo['manifiesto'] = manifiesto
        return directorio, entradas

    def _ejecutar(self, ident):
        trabajo = self._leer(ident)
        nueva = None
        retiradas = []
        try:
            meta = self._meta()
            if trabajo.get('generacion_base') is not None and trabajo['generacion_base'] != meta['generacion']:
                raise CoordinacionRechazada('La generacion cambio antes de preparar la candidata')
            self._exigir_reglas(meta)
            if comprobar(self.base) != meta['descripcion']:
                raise CoordinacionRechazada('El principal cambio fuera del coordinador')
            trabajo.update(estado='running', fase='preparing')
            self._guardar(trabajo)
            self._frontera('before_backup')
            self._espacio(trabajo)
            directorio, entradas = self._congelar(trabajo)
            copia = sincronizacion.copiar(self.base, 'coordinador')
            trabajo.update(copia=copia['ruta'], anterior=meta['descripcion'])
            self._guardar(trabajo)
            self._frontera('backup_saved')
            candidata = directorio / 'candidata.sqlite'
            sqlite.copia_consistente(self.base, candidata)
            trabajo.update(candidata=str(candidata))
            self._guardar(trabajo)
            self._frontera('before_import')
            comentarios = entradas.pop('comentarios', [])
            informe = sincronizacion.importar_evidencia(candidata, **entradas)
            if comentarios:
                from persistencia.comentarios import import_group_bundle
                comentario = import_group_bundle(candidata, comentarios[0])
                if isinstance(informe, dict):
                    informe['comments_persisted'] = comentario
            # Enrichment belongs to the unpublished candidate. It must commit before the semantic description
            # and generation snapshot are calculated, even when this job did not import a comments bundle.
            from enrichment.store import persist_candidate_enrichments
            enriquecimiento = persist_candidate_enrichments(candidata, 'rules-v1')
            if isinstance(informe, dict):
                informe['enrichment_persisted'] = enriquecimiento
            self._frontera('enrichment_committed')
            self._frontera('candidate_committed')
            descripcion = comprobar(candidata)
            self._exigir_reglas(meta)
            generacion = str(uuid.uuid4())
            nueva = preparar(candidata, self.ambito, generacion,
                             enrichment_snapshots=self._enrichment_snapshots)
            self._frontera('snapshot_prepared')
            trabajo.update(fase='publishing', esperada=descripcion, generacion=generacion, resultado=informe)
            self._guardar(trabajo)
            self._frontera('publication_prepared')
            self._restaurar(candidata, descripcion)
            self._frontera('principal_replaced')
            self._exigir_reglas(meta)
            with self._puerta:
                self._frontera('before_confirmation')
                self._exigir_reglas(meta)
                trabajo.update(estado='completed', fase='finished')
                confirmado = dict(codigo=meta['codigo'], ambito=meta['ambito'], generacion=generacion, descripcion=descripcion)
                try:
                    self._guardar(trabajo, confirmado)  # Punto único de confirmación durable.
                    self._frontera('confirmed')
                except Exception:
                    try:
                        confirmado = self._leer(ident)['estado'] == 'completed'
                    except Exception as lectura_error:
                        # Bloquear bajo la puerta: ninguna captura nueva puede adelantarse
                        # a la reconciliacion de un COMMIT cuyo resultado no podemos leer.
                        self._bloqueado = CoordinacionRechazada(f'Confirmacion incierta: {lectura_error}')
                        raise
                    if confirmado:
                        anterior = self._instalar_generacion(nueva)
                        if anterior is not None:
                            retiradas.append(anterior)
                    raise
                anterior = self._instalar_generacion(nueva)
                if anterior is not None:
                    retiradas.append(anterior)
                self._frontera('swapped')
        except Exception as e:
            try:
                # COMMIT puede haber terminado aunque el llamante haya recibido un error.
                durable = self._leer(ident)
                if durable['estado'] == 'completed':
                    with self._puerta:
                        anterior = self._instalar_generacion(nueva)
                        if anterior is not None:
                            retiradas.append(anterior)
                    return
                if durable['fase'] in ('publishing', 'recovering'):
                    with self._puerta:
                        self._recuperar(durable)
                durable.update(estado='failed', fase='finished', error=f'{type(e).__name__}: {e}')
                self._guardar(durable)
            except Exception as recuperacion:
                self._bloqueado = CoordinacionRechazada(f'Recuperacion requerida: {recuperacion}')
        finally:
            for anterior in retiradas:
                self._liberar_generacion(anterior)
            if nueva is not None:
                with self._puerta:
                    instalada = self._actual is nueva
                if not instalada:
                    self._liberar_generacion(nueva)

    def close(self):
        with self._puerta:
            if self._cerrado:
                return
            self._cerrado = True
        self._cola.put(None)
        self._hilo.join()
        with self._puerta:
            actual, self._actual = self._actual, None
        self._liberar_generacion(actual)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class CoordinadorPostgreSQL(Coordinador):
    """PostgreSQL publication coordinator with local operational journals only.

    The shared job queue and API contracts come from ``Coordinador``. Sports state,
    publication identity, and generation changes are authoritative in pg0002.
    """

    es_postgresql = True
    GENERACION_INICIAL = '00000000-0000-4000-8000-000000000001'

    def __init__(self, motor, ambito, directorio, *, _frontera=None):
        self.motor = motor
        self.ambito = Path(ambito).resolve()
        self.directorio = Path(directorio).resolve()
        self.directorio.mkdir(parents=True, exist_ok=True)
        # Compatibility sentinel only. This file is never created or read as a sports database.
        self.base = self.directorio / 'sports-data-postgresql'
        self.acquisition_test_marker = self.directorio / '.acquisition-test-installation'
        self.journal = self.directorio / 'jobs.sqlite'
        self._enrichment_snapshots = None
        self.reserva = 0
        self._frontera = _frontera or (lambda nombre: None)
        self._puerta = threading.Lock()
        self._cola = queue.Queue()
        self._listo = threading.Event()
        self._bloqueado = None
        self._inicio_error = None
        self._cerrado = False
        self._lock_descriptor = None
        self._hilo = threading.Thread(target=self._propietario, daemon=False, name='sports-data-pg-coordinator')
        self._hilo.start()
        self._listo.wait()
        if self._inicio_error:
            self._hilo.join()
            raise CoordinacionRechazada('No se pudo iniciar el coordinador PostgreSQL') from None

    def _adquirir_lock_local(self):
        import errno
        import fcntl

        lock_path = self.directorio / 'coordinator.lock'
        self._lock_descriptor = lock_path.open('a+b')
        try:
            fcntl.flock(self._lock_descriptor.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self._lock_descriptor.close()
            self._lock_descriptor = None
            if error.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise CoordinacionRechazada('Ya hay una instalación activa para este directorio PostgreSQL') from None
            raise

    def _soltar_lock_local(self):
        import fcntl

        if self._lock_descriptor is not None:
            fcntl.flock(self._lock_descriptor.fileno(), fcntl.LOCK_UN)
            self._lock_descriptor.close()
            self._lock_descriptor = None

    def _abrir_postgresql(self):
        from sqlalchemy import text

        self.directorio.mkdir(parents=True, exist_ok=True)
        with closing(self._db()) as journal, journal:
            journal.execute('CREATE TABLE IF NOT EXISTS trabajo(id TEXT PRIMARY KEY, estado TEXT NOT NULL, detalle TEXT NOT NULL)')
            journal.execute("CREATE UNIQUE INDEX IF NOT EXISTS unico_activo ON trabajo((1)) WHERE estado IN ('queued','running')")
            journal.execute('CREATE TABLE IF NOT EXISTS confirmado(id INTEGER PRIMARY KEY CHECK(id=1), detalle TEXT NOT NULL)')

        # A local job may have survived process exit before its operational journal was finalized.
        for trabajo in self._activos():
            publicacion = self._publicacion_durable(trabajo['id'])
            if publicacion is not None:
                trabajo.update(estado='completed', fase='finished', generacion=publicacion['result_generation'],
                               resultado=publicacion['result'])
            else:
                trabajo.update(estado='interrupted', fase='finished',
                               error='Proceso interrumpido antes de confirmar la publicación')
            self._guardar(trabajo)

        reglas = self._reglas()
        with self.motor.connect() as connection, connection.begin():
            # Must precede the state read used to initialize/recover the active generation.
            sincronizacion.bloquear_escritura_postgresql(connection)
            row = connection.execute(text("""
                SELECT generation_id, generation_number, code_sha256, scope_sha256
                FROM sports_data_publication_state WHERE singleton_id = 1 FOR UPDATE
            """)).mappings().first()
            if row is None:
                raise CoordinacionRechazada('Falta la inicialización de pg0002')
            current_scope = hashlib.sha256(self.ambito.read_bytes()).hexdigest()
            if row['scope_sha256'] is not None and row['scope_sha256'] != current_scope:
                raise CoordinacionRechazada('Cambió el ámbito guardado; requiere mantenimiento explícito')
            if row['code_sha256'] is not None and not codigo_compatible(row['code_sha256']):
                raise CoordinacionRechazada('Cambió la huella de reglas guardada; requiere mantenimiento explícito')
            connection.execute(text("""
                UPDATE sports_data_publication_state
                SET code_sha256 = :code,
                    scope_sha256 = COALESCE(scope_sha256, :scope),
                    updated_at = now()
                WHERE singleton_id = 1
            """), {'code': reglas['codigo'], 'scope': current_scope})
            sincronizacion.exigir_coherencia(connection)
            generation_id = str(row['generation_id'])
            self._actual = preparar_postgresql(connection, self.ambito, generation_id)
            confirmed = dict(codigo=reglas['codigo'], ambito=current_scope, generacion=generation_id,
                             numero_generacion=row['generation_number'])
        with closing(self._db()) as journal, journal:
            journal.execute('INSERT OR REPLACE INTO confirmado(id, detalle) VALUES(1, ?)',
                            (json.dumps(confirmed),))

    def _propietario(self):
        try:
            self._adquirir_lock_local()
            self._abrir_postgresql()
            self._listo.set()
            while True:
                item = self._cola.get()
                if item is None:
                    return
                if not self._bloqueado:
                    self._ejecutar(item)
        except BaseException as error:
            self._inicio_error = error
        finally:
            self._soltar_lock_local()
            self._listo.set()

    def capturar(self, *, enrichment=False):
        if enrichment:
            raise CoordinacionRechazada('El enriquecimiento PostgreSQL usa una transacción de lectura coherente')
        return super().capturar(enrichment=False)

    def estado(self, ident):
        trabajo = self._leer(ident)
        if trabajo['estado'] == 'running' and trabajo.get('fase') == 'publishing':
            try:
                durable = self._publicacion_durable(ident)
                if durable is not None:
                    from sqlalchemy import text

                    with self.motor.connect() as connection, connection.begin():
                        sincronizacion.bloquear_escritura_postgresql(connection)
                        state = connection.execute(text("""
                            SELECT generation_id FROM sports_data_publication_state
                            WHERE singleton_id = 1 FOR UPDATE
                        """)).scalar_one()
                        current = preparar_postgresql(connection, self.ambito, str(state))
                    with self._puerta:
                        anterior = self._instalar_generacion(current)
                        self._bloqueado = None
                        if anterior is not None:
                            self._liberar_generacion(anterior)
                        trabajo.update(estado='completed', fase='finished',
                                       generacion=durable['result_generation'], resultado=durable['result'])
                        self._guardar(trabajo)
            except Exception:
                # Keep the job pending until the durable outcome can be read safely.
                pass
        return self._leer(ident)

    def esperar(self, ident, timeout=30):
        if self._bloqueado:
            self.estado(ident)
            trabajo = self._leer(ident)
            if trabajo['estado'] in ('queued', 'running'):
                return dict(estado='pending', identificador=ident)
            return trabajo
        return super().esperar(ident, timeout)

    def _congelar_postgresql(self, trabajo):
        directorio = self.directorio / trabajo['id']
        directorio.mkdir(exist_ok=True)
        entradas, manifiesto = {}, []
        for grupo, valores in trabajo['entradas'].items():
            entradas[grupo] = []
            for i, valor in enumerate(valores):
                ruta, equipo = valor if grupo == 'exportaciones' else (valor, None)
                ruta = Path(ruta)
                if not ruta.is_file() or ruta.is_symlink():
                    raise CoordinacionRechazada('Entrada de publicación ausente o insegura')
                destino = directorio / f'{grupo}-{i}.jsonl'
                shutil.copyfile(ruta, destino)
                with destino.open('r+b') as stream:
                    import os
                    os.fsync(stream.fileno())
                digest = hashlib.sha256(destino.read_bytes()).hexdigest()
                manifiesto.append(dict(grupo=grupo, nombre=ruta.name, equipo=equipo, sha256=digest))
                entradas[grupo].append((destino, equipo) if grupo == 'exportaciones' else destino)
        fingerprint = hashlib.sha256(json.dumps(manifiesto, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        trabajo['manifiesto'] = manifiesto
        trabajo['huella_entradas'] = fingerprint
        return entradas, fingerprint

    def _publicacion_durable(self, ident):
        from sqlalchemy import text

        try:
            with self.motor.connect() as connection:
                row = connection.execute(text("""
                    SELECT publication_id, source_generation, result_generation, input_sha256,
                           published_at, run_status, result_json
                    FROM sports_data_publication WHERE publication_id = :publication_id
                """), {'publication_id': ident}).mappings().first()
            if row is None:
                return None
            result = row['result_json']
            if isinstance(result, str):
                result = json.loads(result)
            return dict(publication_id=str(row['publication_id']), source_generation=str(row['source_generation']),
                        result_generation=str(row['result_generation']), input_sha256=row['input_sha256'],
                        published_at=row['published_at'].isoformat(), run_status=row['run_status'], result=result)
        except Exception:
            # Driver errors can include server or connection details; callers decide whether to block/retry.
            raise CoordinacionRechazada('No se pudo consultar el registro durable de publicaciones') from None

    def _ejecutar(self, ident):
        from sqlalchemy import text

        trabajo = self._leer(ident)
        nueva = None
        instalada = False
        input_fingerprint = None
        try:
            trabajo.update(estado='running', fase='preparing')
            self._guardar(trabajo)
            self._frontera('before_import')
            entradas, input_fingerprint = self._congelar_postgresql(trabajo)
            trabajo['fase'] = 'publishing'
            self._guardar(trabajo)
            self._frontera('before_transaction')

            connection = self.motor.connect()
            transaction = connection.begin()
            try:
                # First database operation in the publication transaction: serialize all sports writers.
                sincronizacion.bloquear_escritura_postgresql(connection)
                existing = connection.execute(text("""
                    SELECT publication_id, source_generation, result_generation, input_sha256,
                           published_at, run_status, result_json
                    FROM sports_data_publication WHERE publication_id = :publication_id
                """), {'publication_id': ident}).mappings().first()
                if existing is not None:
                    old_hash = existing['input_sha256']
                    if old_hash != input_fingerprint:
                        raise CoordinacionRechazada('ID de publicación reutilizado con otras entradas')
                    durable_result = existing['result_json']
                    if isinstance(durable_result, str):
                        durable_result = json.loads(durable_result)
                    result = dict(durable_result, published_at=existing['published_at'].isoformat(),
                                  run_status=existing['run_status'])
                    generation_id = str(existing['result_generation'])
                    transaction.rollback()
                    with self._puerta:
                        trabajo.update(estado='completed', fase='finished', generacion=generation_id,
                                       resultado=result)
                        self._guardar(trabajo)
                    return

                state = connection.execute(text("""
                    SELECT generation_id, generation_number, code_sha256, scope_sha256
                    FROM sports_data_publication_state WHERE singleton_id = 1 FOR UPDATE
                """)).mappings().one()
                source_generation = str(state['generation_id'])
                expected = trabajo.get('generacion_base')
                if expected is not None and expected != source_generation:
                    raise CoordinacionRechazada('La generación esperada ya no está vigente')
                rules = self._reglas()
                if state['scope_sha256'] != rules['ambito'] or not codigo_compatible(state['code_sha256']):
                    raise CoordinacionRechazada('Cambió el código o el ámbito durante la publicación')

                comments = entradas.pop('comentarios', [])
                informe = sincronizacion.importar_evidencia_postgresql(
                    connection,
                    exportaciones=entradas.get('exportaciones', []),
                    calendarios=entradas.get('calendarios', []),
                    fichas=entradas.get('fichas', []),
                )
                if comments:
                    from persistencia.comentarios import import_group_bundle_postgresql
                    comentario = {'captured_ids': [], 'failed_ids': []}
                    for response_file in comments:
                        current = import_group_bundle_postgresql(connection, response_file)
                        comentario['captured_ids'] = sorted(set(comentario['captured_ids']) | set(current['captured_ids']))
                        comentario['failed_ids'] = sorted(set(comentario['failed_ids']) | set(current['failed_ids']))
                    informe['comments_persisted'] = comentario
                from enrichment.store import (
                    ensure_selected_enrichments_complete_postgresql,
                    persist_selected_enrichments_postgresql,
                )
                enrichment_result = persist_selected_enrichments_postgresql(connection, 'rules-v1')
                ensure_selected_enrichments_complete_postgresql(connection, 'rules-v1')
                informe['enrichment_persisted'] = enrichment_result
                sincronizacion.exigir_coherencia(connection)
                self._frontera('before_generation')
                result_generation = str(uuid.uuid4())
                nueva = preparar_postgresql(connection, self.ambito, result_generation)
                published_at = datetime.now(timezone.utc)
                result = dict(informe, generation_id=result_generation, source_generation=source_generation,
                              published_at=published_at.isoformat(), run_status=informe['estado'])
                connection.execute(text("""
                    INSERT INTO sports_data_publication
                        (publication_id, source_generation, result_generation, input_sha256, published_at,
                         run_status, result_json)
                    VALUES (:publication_id, :source_generation, :result_generation, :input_sha256,
                            :published_at, :run_status, CAST(:result_json AS jsonb))
                """), {'publication_id': ident, 'source_generation': source_generation,
                       'result_generation': result_generation, 'input_sha256': input_fingerprint,
                       'published_at': published_at, 'run_status': informe['estado'],
                       'result_json': json.dumps(result, ensure_ascii=False, allow_nan=False)})
                connection.execute(text("""
                    UPDATE sports_data_publication_state
                    SET generation_id = :generation_id, generation_number = generation_number + 1,
                        code_sha256 = :code_sha256, scope_sha256 = :scope_sha256,
                        last_publication_id = :publication_id, updated_at = :published_at
                    WHERE singleton_id = 1
                """), {'generation_id': result_generation, 'code_sha256': rules['codigo'],
                       'scope_sha256': rules['ambito'], 'publication_id': ident, 'published_at': published_at})
                trabajo.update(generacion=result_generation, resultado=result, esperada=source_generation)
                self._guardar(trabajo)
                self._frontera('before_commit')
                with self._puerta:
                    self._frontera('before_confirmation')
                    try:
                        transaction.commit()
                        self._frontera('committed')
                    except Exception:
                        # A failed/uncertain COMMIT must block captures before the gate is released.
                        self._bloqueado = CoordinacionRechazada('Resultado de COMMIT incierto; reconciliación en curso')
                        raise
                    anterior = self._instalar_generacion(nueva)
                    instalada = True
                    if anterior is not None:
                        self._liberar_generacion(anterior)
                    trabajo.update(estado='completed', fase='finished')
                    self._guardar(trabajo, dict(codigo=rules['codigo'], ambito=rules['ambito'],
                                                generacion=result_generation,
                                                numero_generacion=int(state['generation_number']) + 1))
                    self._frontera('swapped')
            finally:
                if transaction.is_active:
                    transaction.rollback()
                connection.close()
        except Exception as error:
            # COMMIT errors can be uncertain. A fresh connection to pg0002 is authoritative.
            try:
                durable = self._publicacion_durable(ident)
                if durable is not None:
                    if durable['input_sha256'] != input_fingerprint:
                        raise CoordinacionRechazada('La publicación durable tiene otra huella de entradas')
                    if nueva is None:
                        raise CoordinacionRechazada('Publicación confirmada; la generación se recuperará al reiniciar')
                    with self._puerta:
                        if not instalada:
                            anterior = self._instalar_generacion(nueva)
                            instalada = True
                            if anterior is not None:
                                self._liberar_generacion(anterior)
                        self._bloqueado = None
                        trabajo.update(estado='completed', fase='finished',
                                       generacion=durable['result_generation'], resultado=durable['result'])
                        self._guardar(trabajo)
                    return
                safe_code = getattr(getattr(error, 'orig', None), 'sqlstate', None)
                detail = f' (SQLSTATE {safe_code})' if safe_code else ''
                trabajo.update(estado='failed', fase='finished',
                               error=f'{type(error).__name__}{detail}')
                self._guardar(trabajo)
            except Exception:
                # Unknown durable outcome: stop serving new captures until a restart reconciles pg0002.
                self._bloqueado = CoordinacionRechazada('Resultado de COMMIT incierto; requiere reconciliación durable')
                trabajo.update(estado='running', fase='publishing',
                               error='Resultado durable temporalmente no disponible')
                try:
                    self._guardar(trabajo)
                except Exception:
                    pass
        finally:
            if nueva is not None and not instalada:
                self._liberar_generacion(nueva)

    def close(self):
        super().close()
