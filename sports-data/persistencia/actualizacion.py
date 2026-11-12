"""Coordinador interno para fixtures locales. No gestiona HTTP ni accede a la red por sí mismo.

Un hilo propietario mantiene el bloqueo durante apertura, trabajo y recuperación.
El journal operativo es independiente de la base deportiva. Ver docs/coordinador-fixtures.md.
"""
from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
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
