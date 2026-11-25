"""Lectura de la API desde SQLite y, provisionalmente, desde PostgreSQL de desarrollo.

La base se abre solo en lectura (`mode=ro`: nunca se crea ni se modifica), se comprueba que su revisión de esquema es
la de este código, que tiene una carga inicial confirmada y que el estado vigente usa las versiones de reglas del
código (si no, `persistencia reconstruir`; la comparación completa en seco, unos 3 s, queda para `verificar` y cada
importación), y se reconstruyen los mismos datos que lee el modo JSON
(persistencia.reconstruccion). A partir de ahí el camino es el de siempre: api.componer (fichas, ámbito,
sustituciones guardadas en la base) y, al consultar, las reglas actuales de normalización y agregados. Todo se lee al
arrancar; la conexión se cierra después (sin recarga en caliente). La API mantiene además el bloqueo
cooperativo de esa ruta durante toda su ejecución; los lectores directos lo reservan solo durante su lectura.

Con el diario WAL, una conexión de solo lectura puede dejar junto a la base sus archivos `-shm` y `-wal` (vacío): es
el funcionamiento normal de SQLite y no cambia los datos.
"""
from pathlib import Path

from alembic.script import ScriptDirectory
from sqlalchemy.exc import SQLAlchemyError

import api
from persistencia import reconstruccion, sqlite
from persistencia.bloqueo import exclusivo
from persistencia.carga import configuracion_alembic
from persistencia.decisiones import sustituciones_guardadas
from persistencia.sincronizacion import reglas_desfasadas

# Lo que la API necesita de la base: los mismos archivos que lee el modo JSON (resumen aparte).
DATOS = ('equipos', 'jugadores', 'partidos', 'jugador_partido', 'exportaciones', 'discrepancias', 'fichas_originales')


class BaseNoUtilizable(Exception):
    """La API no arranca: la base falta, no es de este esquema o no se puede leer."""


def revision_esperada():
    return ScriptDirectory.from_config(configuracion_alembic(Path('.'))).get_current_head()


@exclusivo('ruta')
def _leer_base(ruta):
    """(datos almacenados con los originales indexados, sustituciones activas) de una base válida."""
    ruta = Path(ruta).resolve()
    if not ruta.is_file():
        raise BaseNoUtilizable(f'No existe la base {ruta} (el modo SQL nunca crea una)')
    esperada = revision_esperada()
    motor = sqlite.crear_motor(ruta, solo_lectura=True)
    try:
        with motor.connect() as c:
            tablas = {t for (t,) in c.exec_driver_sql("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if 'alembic_version' not in tablas:
                raise BaseNoUtilizable(f'{ruta} no es una base de la persistencia (sin revisión de esquema)')
            revision = c.exec_driver_sql('SELECT version_num FROM alembic_version').scalar()
            if revision != esperada:
                raise BaseNoUtilizable(f'{ruta} tiene la revisión de esquema {revision}; este código necesita {esperada}')
            desfasadas = reglas_desfasadas(c)
            if desfasadas:
                raise BaseNoUtilizable(f'{ruta}: el estado guardado usa reglas que no son las de este código '
                                       f'({", ".join(f"{n}@{v}" for n, v in desfasadas)}); ejecute `persistencia reconstruir`')
            try:
                reconstruida = reconstruccion.salida_desde_base(c)
            except ValueError as e:
                raise BaseNoUtilizable(f'{ruta}: {e}') from e
            sustituciones = sustituciones_guardadas(c)
    except SQLAlchemyError as e:
        raise BaseNoUtilizable(f'No se puede leer {ruta}: {getattr(e, "orig", e)}') from e
    finally:
        motor.dispose()
    datos = {n: reconstruida[n] for n in DATOS}
    datos['originales'] = api.indexar(reconstruida['originales'])
    return datos, sustituciones


def leer_base(ruta):
    if not Path(ruta).is_file():
        raise BaseNoUtilizable(f'No existe la base {Path(ruta).resolve()} (el modo SQL nunca crea una)')
    return _leer_base(ruta)


def cargar_desde_base(ruta, ambito):
    """Los datos que sirve la API, desde la base: el equivalente de api.cargar para el modo SQL."""
    datos, sustituciones = leer_base(ruta)
    try:
        return api.componer(datos, api.leer_ambito(ambito), sustituciones)
    except ValueError as e:
        raise BaseNoUtilizable(str(e)) from e


def cargar_desde_postgresql(conexion, ambito):
    """Reconstruye la generación servida desde pg0001 sin consultar alembic_version.

    El llamador mantiene una transacción REPEATABLE READ READ ONLY abierta también al cargar comentarios.
    La firma de esquema se comprueba con metadatos accesibles al rol de lectura, nunca con las credenciales de
    migración ni con la tabla de control Alembic.
    """
    firma_esperada = {
        ('observation', 'fetched_instant'),
        ('narrative_comment_capture', 'source_path'),
        ('narrative_comment_selection', 'selected_spanish_sha256'),
        ('narrative_comment_selection', 'last_successful_source_path'),
    }
    filas = conexion.exec_driver_sql("""
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND ((table_name = 'observation' AND column_name = 'fetched_instant')
            OR (table_name = 'narrative_comment_capture' AND column_name = 'source_path')
            OR (table_name = 'narrative_comment_selection' AND column_name IN
                ('selected_spanish_sha256', 'last_successful_source_path')))
    """).fetchall()
    if {(tabla, columna) for tabla, columna in filas} != firma_esperada:
        raise BaseNoUtilizable('El esquema PostgreSQL no corresponde a la revisión de lectura pg0001')

    try:
        reconstruccion.informe_de_carga(conexion)
        desfasadas = reglas_desfasadas(conexion)
        if desfasadas:
            raise BaseNoUtilizable('El estado PostgreSQL usa reglas que no son las de este código')
        reconstruida = reconstruccion.salida_desde_base(conexion)
        sustituciones = sustituciones_guardadas(conexion)
        datos = {n: reconstruida[n] for n in DATOS}
        datos['originales'] = api.indexar(reconstruida['originales'])
        try:
            return api.componer(datos, api.leer_ambito(ambito), sustituciones)
        except ValueError as e:
            raise BaseNoUtilizable(str(e)) from e
    except SQLAlchemyError:
        # No propagar mensajes del driver: algunos incluyen datos de conexión.
        raise BaseNoUtilizable('No se pudo reconstruir la generación desde PostgreSQL') from None
