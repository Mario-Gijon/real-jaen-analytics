"""Todo lo específico de SQLite en la persistencia (docs/persistencia-sqlite.md, 5.2). Los modelos y los comandos usan
estas funciones en lugar de escribir PRAGMA, transacciones o SQL propio del motor.

SQLAlchemy no hace portable lo que hay aquí: cambiar de motor exigiría reescribir este módulo, revisar las migraciones
(modo batch) y validar la equivalencia de los datos.
"""
import hashlib
import os
import sqlite3
import tempfile

from persistencia.bloqueo import exclusivo
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, event, text

# Espera ante un bloqueo de otro proceso antes de fallar (milisegundos).
ESPERA_BLOQUEO_MS = 5000


class ClavesForaneasDesactivadas(RuntimeError):
    """La conexión no comprueba claves foráneas: no se usa."""


def url(ruta, solo_lectura=False):
    """URL de SQLAlchemy para un archivo. En solo lectura se abre con URI `mode=ro`: el archivo debe existir."""
    ruta = Path(ruta).resolve()
    if solo_lectura:
        return f'sqlite:///{ruta.as_uri()}?mode=ro&uri=true'
    return f'sqlite:///{ruta.as_posix()}'


def preparar_conexion(conexion_dbapi, solo_lectura):
    """Cada conexión nueva: el driver no abre transacciones por su cuenta (las abre `al_empezar`), claves foráneas
    activadas y comprobadas, espera ante bloqueos y, para escribir, diario WAL (un lector no bloquea al escritor)."""
    conexion_dbapi.isolation_level = None
    cursor = conexion_dbapi.cursor()
    try:
        cursor.execute('PRAGMA foreign_keys = ON')
        cursor.execute(f'PRAGMA busy_timeout = {ESPERA_BLOQUEO_MS}')
        if not solo_lectura:
            cursor.execute('PRAGMA journal_mode = WAL')
        if cursor.execute('PRAGMA foreign_keys').fetchone()[0] != 1:
            raise ClavesForaneasDesactivadas('SQLite no ha activado las claves foráneas en esta conexión')
    finally:
        cursor.close()


def crear_motor(ruta, solo_lectura=False):
    """Motor de SQLAlchemy para una base SQLite. Escritura: cada transacción es BEGIN IMMEDIATE (un único escritor,
    que toma el bloqueo al empezar y no a mitad). Lectura: BEGIN normal sobre una conexión `mode=ro`."""
    motor = create_engine(url(ruta, solo_lectura))

    @event.listens_for(motor, 'connect')
    def al_conectar(conexion_dbapi, _registro):
        preparar_conexion(conexion_dbapi, solo_lectura)

    @event.listens_for(motor, 'begin')
    def al_empezar(conexion):
        conexion.exec_driver_sql('BEGIN' if solo_lectura else 'BEGIN IMMEDIATE')

    return motor


def claves_foraneas_activas(conexion):
    """1 si la conexión comprueba claves foráneas (para pruebas y verificaciones de arranque)."""
    return conexion.exec_driver_sql('PRAGMA foreign_keys').scalar()


def comprobar_integridad(conexion):
    """Filas que violan claves foráneas (PRAGMA foreign_key_check); lista vacía si ninguna."""
    return conexion.exec_driver_sql('PRAGMA foreign_key_check').fetchall()


@contextmanager
def transaccion_de_escritura(motor):
    """Una transacción de escritura completa: confirma al terminar sin error y deshace todo si falla."""
    with motor.begin() as conexion:
        yield conexion


def preparar_migracion(conexion):
    """El modo batch de Alembic recrea tablas (copia, borra la antigua y renombra). Con las claves foráneas activas,
    borrar una tabla referenciada fallaría aunque la copia conserve las filas: se desactivan durante la migración
    (fuera de transacción, como exige SQLite). Antes de confirmar se comprueban todas con comprobar_integridad."""
    conexion.connection.driver_connection.execute('PRAGMA foreign_keys = OFF')


# ─── Copias, verificación y restauración ─────────────────────────────────────────────────────────────────────

ACOMPANANTES = ('-wal', '-shm', '-journal')


class CopiaNoValida(RuntimeError):
    """La copia o la base no pasan la verificación: no se sigue."""


def _lectura(ruta):
    return sqlite3.connect(f'{Path(ruta).resolve().as_uri()}?mode=ro', uri=True)


def describir(ruta):
    """Lo que identifica el contenido de una base: integridad, claves foráneas, revisión de esquema, filas por tabla
    y una huella sha256 de todas las filas de todas las tablas (en orden de todas sus columnas)."""
    c = _lectura(ruta)
    try:
        tablas = [t for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
                                          "ORDER BY name")]
        huella, recuentos = hashlib.sha256(), {}
        for tabla in tablas:
            columnas = [fila[1] for fila in c.execute(f'PRAGMA table_info("{tabla}")')]
            orden = ', '.join(f'"{x}"' for x in columnas)
            filas = c.execute(f'SELECT {orden} FROM "{tabla}" ORDER BY {orden}').fetchall()
            recuentos[tabla] = len(filas)
            huella.update(f'{tabla}|{columnas}|'.encode())
            for fila in filas:
                huella.update(repr(fila).encode())
        revision = c.execute('SELECT version_num FROM alembic_version').fetchone() if 'alembic_version' in tablas else None
        return {'integridad': c.execute('PRAGMA integrity_check').fetchone()[0],
                'claves_foraneas': c.execute('PRAGMA foreign_key_check').fetchall(),
                'revision': revision[0] if revision else None, 'recuentos': recuentos, 'huella': huella.hexdigest()}
    finally:
        c.close()


def revision(ruta):
    """La revisión de Alembic de una base (None si no tiene), sin leer nada más."""
    c = _lectura(ruta)
    try:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'alembic_version'").fetchone():
            return None
        fila = c.execute('SELECT version_num FROM alembic_version').fetchone()
        return fila[0] if fila else None
    finally:
        c.close()


def comprobar_copia(copia, esperado=None):
    """Una copia íntegra, sin violaciones de claves foráneas y, si se da, con el mismo contenido que `esperado`."""
    try:
        obtenido = describir(copia)
    except sqlite3.DatabaseError as e:
        raise CopiaNoValida(f'{copia}: no es una base SQLite legible ({e})') from e
    if obtenido['integridad'] != 'ok' or obtenido['claves_foraneas']:
        raise CopiaNoValida(f'{copia}: integridad {obtenido["integridad"]}, claves foráneas {obtenido["claves_foraneas"][:3]}')
    if esperado is not None and (obtenido['huella'], obtenido['revision']) != (esperado['huella'], esperado['revision']):
        raise CopiaNoValida(f'{copia}: su contenido no coincide con el de origen')
    return obtenido


@exclusivo('origen')
def copia_consistente(origen, destino):
    """Copia con la API de copia de SQLite: una imagen coherente aunque haya lectores y diario WAL (copiar el archivo a
    mano no lo garantiza). El destino no debe existir. La copia queda con diario `DELETE`: un solo archivo, sin -wal ni
    -shm al abrirla en solo lectura (la conexión de escritura vuelve a WAL al abrirla, sqlite.preparar_conexion)."""
    destino = Path(destino)
    if any(p.exists() for p in (destino, *(destino.with_name(destino.name + s) for s in ACOMPANANTES))):
        raise FileExistsError(f'Ya existe {destino}: una copia nunca sobrescribe nada')
    destino.parent.mkdir(parents=True, exist_ok=True)
    with closing(_lectura(origen)) as fuente, closing(sqlite3.connect(destino)) as copia:
        fuente.backup(copia)
        copia.execute('PRAGMA journal_mode = DELETE')


@exclusivo('base')
def crear_copia(base, motivo):
    """Copia verificada de una base antes de modificarla, en `<carpeta de la base>/copias`. Devuelve su descripción."""
    base = Path(base).resolve()
    momento = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    destino = base.parent / 'copias' / f'{base.stem}.{momento}.{motivo}.sqlite'
    copia_consistente(base, destino)
    descripcion = comprobar_copia(destino, describir(base))
    return {'ruta': str(destino), 'sha256_archivo': hashlib.sha256(destino.read_bytes()).hexdigest(),
            'huella': descripcion['huella'], 'revision': descripcion['revision']}


def archivos_en_uso(ruta):
    """Archivos de la base (principal, -wal, -shm, -journal) que algún proceso tiene abiertos. En Windows se comprueba
    abriéndolos sin compartir: una violación de uso compartido indica que otro proceso (o esta misma conexión) los usa.
    Comprobación adicional en Windows; en POSIX no detecta programas externos. No sustituye al bloqueo cooperativo."""
    ruta = Path(ruta)
    archivos = [p for p in (ruta, *(ruta.with_name(ruta.name + s) for s in ACOMPANANTES)) if p.exists()]
    if os.name != 'nt':
        return []  # La coordinación portable la mantiene bloqueo.bloquear, no los archivos abiertos.
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                     wintypes.DWORD, wintypes.HANDLE)
    invalido, en_uso = wintypes.HANDLE(-1).value, []
    for archivo in archivos:
        manejador = kernel32.CreateFileW(str(archivo), 0x80000000, 0, None, 3, 0x80, None)  # GENERIC_READ, sin compartir
        if manejador == invalido:
            error = ctypes.get_last_error()
            if error in (32, 33):  # ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION
                en_uso.append(str(archivo))
                continue
            raise OSError(error, f'No se puede comprobar el uso de {archivo}')
        kernel32.CloseHandle(manejador)
    return en_uso


@exclusivo('destino')
@exclusivo('copia')
def restaurar(copia, destino, revision_esperada):
    """Con exclusividad sobre destino y copia: prepara y verifica la nueva imagen, conserva una copia anterior,
    integra el WAL del destino y cierra SQLite antes de retirar acompañantes. Reemplazo atómico del principal;
    si falla antes del reemplazo, el estado lógico anterior permanece. Requiere cerrar programas externos."""
    copia, destino = Path(copia).resolve(), Path(destino).resolve()
    origen = comprobar_copia(copia)
    if origen['revision'] != revision_esperada:
        raise CopiaNoValida(f'{copia} tiene la revisión {origen["revision"]}; este código necesita {revision_esperada}')
    en_uso = archivos_en_uso(destino)
    if en_uso:
        raise CopiaNoValida(f'La base de destino está en uso ({", ".join(en_uso)}): cierre la API y cualquier otro proceso')
    if copia == destino:
        raise CopiaNoValida('La copia y el destino deben ser distintos')
    # Todo lo que puede fallar al leer/copiar/verificar se hace antes de sustituir el destino.
    fd, nombre = tempfile.mkstemp(prefix=destino.name + '.restauracion-', suffix='.sqlite', dir=destino.parent)
    os.close(fd)
    preparada = Path(nombre)
    preparada.unlink()  # copia_consistente exige un destino nuevo; el nombre es privado y aleatorio.
    try:
        copia_consistente(copia, preparada)
        comprobar_copia(preparada, origen)
        anterior = crear_copia(destino, 'antes-de-restaurar') if destino.exists() else None
        if destino.exists():
            # Integra el WAL antes de quitar acompañantes. Si falla el reemplazo, la base anterior conserva
            # todos sus datos en el archivo principal. Nunca se elimina un WAL con cambios pendientes.
            c = sqlite3.connect(destino.as_uri() + '?mode=rw', uri=True)
            try:
                if c.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] != 0:
                    raise CopiaNoValida('La base está en uso: no se pudo integrar su WAL')
            finally:
                c.close()
        if archivos_en_uso(destino):
            raise CopiaNoValida('La base está en uso por un programa externo')
        for sufijo in ACOMPANANTES:
            destino.with_name(destino.name + sufijo).unlink(missing_ok=True)
        os.replace(preparada, destino)
        comprobar_copia(destino, origen)
    finally:
        preparada.unlink(missing_ok=True)
    return {'restaurada': str(destino), 'desde': str(copia), 'huella': origen['huella'], 'anterior': anterior}


# ─── Expresiones SQL propias del dialecto, para las restricciones de los modelos ───────────────────────────────

def json_valido(columna, nulo=True):
    """CHECK de un literal JSON guardado como texto (extensión JSON1). `nulo`: NULL de SQL (ausente) admitido."""
    return f'{columna} IS NULL OR json_valid({columna})' if nulo else f'json_valid({columna})'


def indice_parcial(condicion):
    """Argumentos de un índice parcial (único sólo para las filas que cumplen la condición)."""
    return {'sqlite_where': text(condicion)}


def hexadecimal(columna, longitud):
    """CHECK de una huella hexadecimal en minúsculas de longitud fija."""
    return f"length({columna}) = {longitud} AND {columna} NOT GLOB '*[^0-9a-f]*'"


def texto_no_vacio(columna):
    return f"length(trim({columna})) > 0"
