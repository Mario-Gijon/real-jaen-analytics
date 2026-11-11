"""Entorno de Alembic. La base se indica siempre (`-x base=RUTA` o, desde código, `config.attributes['base']`); nunca
hay una por defecto. La conexión es la de persistencia.sqlite: claves foráneas comprobadas y BEGIN IMMEDIATE.
Modo batch: SQLite no admite la mayoría de ALTER TABLE y Alembic recrea la tabla (específico de SQLite)."""
from logging.config import fileConfig

from alembic import context

from persistencia.modelos import Base
from persistencia.bloqueo import bloquear
from persistencia.sqlite import comprobar_integridad, crear_motor, preparar_migracion

config = context.config
if config.config_file_name is not None and config.attributes.get('configurar_registro', True):
    fileConfig(config.config_file_name)


def ruta_de_la_base():
    ruta = config.attributes.get('base') or context.get_x_argument(as_dictionary=True).get('base')
    if not ruta:
        raise SystemExit('Indica la base: alembic -x base=RUTA ... (no hay base por defecto)')
    return ruta


def comprobar_convocatorias(conexion):
    """Con vigencia de convocatorias (0004 en adelante), toda activa sale de la alineación seleccionada y su estado es
    el de su última transición: la misma comprobación obligatoria que en cualquier otra escritura."""
    if 'status' not in {fila[1] for fila in conexion.exec_driver_sql('PRAGMA table_info(call_up)')}:
        return
    from persistencia.sincronizacion import convocatorias_fuera_de_seleccion, vigencias_incoherentes
    fuera, incoherentes = convocatorias_fuera_de_seleccion(conexion), vigencias_incoherentes(conexion)
    if fuera or incoherentes:
        raise RuntimeError(f'Convocatorias activas fuera de la alineación seleccionada {fuera[:5]} o con vigencia '
                           f'incoherente {incoherentes[:5]}')


def ejecutar():
    if context.is_offline_mode():
        raise SystemExit('El modo offline (SQL sin conexión) no se usa en este proyecto')
    motor = crear_motor(ruta_de_la_base())
    try:
        with motor.connect() as conexion:
            preparar_migracion(conexion)
            context.configure(connection=conexion, target_metadata=Base.metadata, render_as_batch=True,
                              compare_type=True, compare_server_default=True)
            with context.begin_transaction():
                context.run_migrations()
                # Claves foráneas desactivadas durante el modo batch: se comprueban todas antes de confirmar.
                rotas = comprobar_integridad(conexion)
                if rotas:
                    raise RuntimeError(f'La migración deja referencias rotas: {rotas[:5]}')
                comprobar_convocatorias(conexion)
    finally:
        motor.dispose()


with bloquear(ruta_de_la_base()):
    ejecutar()
