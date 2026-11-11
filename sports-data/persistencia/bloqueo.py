"""Coordinación cooperativa por ruta. Solo biblioteca estándar; no bloquea programas SQLite externos.

Un único propietario (API o comando), sin esperas. El SO libera el bloqueo al cerrar el descriptor o morir
el proceso. El archivo permanece: borrarlo permitiría bloquear dos archivos distintos para la misma base.
"""
import errno
import os
import threading
from contextlib import contextmanager
from functools import wraps
from inspect import signature
from pathlib import Path

_local = threading.local()


class BaseEnUso(RuntimeError):
    """Otra API u operación de nuestras herramientas tiene esta base reservada."""


def ruta_normalizada(ruta):
    # resolve: absoluta, .., enlaces simbólicos y junctions; normcase: mayúsculas en Windows.
    return Path(os.path.normcase(str(Path(ruta).resolve())))


def archivo_de_bloqueo(ruta):
    ruta = ruta_normalizada(ruta)
    return ruta.with_name(ruta.name + '.lock')


@contextmanager
def bloquear(ruta):
    archivo = archivo_de_bloqueo(ruta)
    # Reentrada solo en el hilo y proceso propietarios, para comandos que llaman a copia/verificación/Alembic.
    propios = getattr(_local, 'propios', None)
    if propios is None:
        propios = _local.propios = set()
    clave = (os.getpid(), archivo)
    if clave in propios:
        yield
        return
    with archivo.open('a+b') as descriptor:
        try:
            if os.name == 'nt':
                import msvcrt
                descriptor.seek(0)
                msvcrt.locking(descriptor.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            if e.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise
            raise BaseEnUso(f'Base en uso: {ruta_normalizada(ruta)}. Otra API u operación mantiene el bloqueo; '
                            'cierre la API o espere a que termine la operación y vuelva a intentarlo.') from e
        propios.add(clave)
        try:
            yield
        finally:
            propios.remove(clave)
            if os.name == 'nt':
                descriptor.seek(0)
                msvcrt.locking(descriptor.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(descriptor.fileno(), fcntl.LOCK_UN)


def exclusivo(parametro):
    """Reserva la base antes de entrar en una operación pública; admite llamadas anidadas del mismo hilo."""
    def decorar(funcion):
        firma = signature(funcion)
        @wraps(funcion)
        def ejecutar(*args, **kwargs):
            ruta = firma.bind(*args, **kwargs).arguments[parametro]
            with bloquear(ruta):
                return funcion(*args, **kwargs)
        return ejecutar
    return decorar
