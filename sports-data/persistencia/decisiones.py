"""Carga explícita de sustituciones locales en una base ya cargada (fase 2, docs/persistencia-sqlite.md 2.6 y 4.1).

Las decisiones se validan con las mismas funciones que aplica la API al arrancar (api.leer_sustituciones y
api.aplicar_sustituciones, sobre los partidos del ámbito indicado) y se guardan con motivo, procedencia, evidencia y
el fichero del que salen (sha256). Todo en una transacción: si algo falla no se guarda ninguna decisión y queda
registrada la ejecución fallida. Antes se hace una copia verificada de la base y, antes de confirmar, la
verificación obligatoria de toda escritura (sincronizacion.exigir_coherencia). Un fichero ya incorporado no se
vuelve a aplicar. La gestión de decisiones sobre
otras ya activas (revertir, añadir) queda para más adelante.
"""
import hashlib
from datetime import date
from pathlib import Path

from sqlalchemy import text

import api
from persistencia import reconstruccion, sqlite
from persistencia.bloqueo import exclusivo
from persistencia.carga import ahora, insertar, literal, version_del_codigo


class DecisionRechazada(Exception):
    """No se guarda ninguna decisión."""


@exclusivo('ruta_base')
def cargar_sustituciones(ruta_base, archivo, ambito, comando=None):
    ruta, archivo = Path(ruta_base).resolve(), Path(archivo).resolve()
    if not ruta.is_file():
        raise DecisionRechazada(f'No existe la base {ruta}')
    sha = hashlib.sha256(archivo.read_bytes()).hexdigest()
    try:
        lista = api.leer_sustituciones(archivo)
        competiciones = api.leer_ambito(ambito)
    except (ValueError, OSError) as e:
        raise DecisionRechazada(str(e)) from e
    from persistencia import sincronizacion
    copia = sqlite.crear_copia(ruta, 'antes-de-sustituciones')
    motor = sqlite.crear_motor(ruta)
    try:
        try:
            with motor.begin() as c:
                informe = aplicar(c, lista, competiciones, archivo, sha, comando)
                sincronizacion.exigir_coherencia(c)
                return dict(informe, copia=copia['ruta'])
        except (ValueError, KeyError, sincronizacion.EstadoIncoherente) as e:
            with motor.begin() as c:
                c.execute(text("INSERT INTO import_run (kind, started_at, finished_at, status, tool_version, command, error) "
                               "VALUES ('decisions', :t, :t, 'failed', :v, :cmd, :e)"),
                          {'t': ahora(), 'v': version_del_codigo(), 'cmd': comando, 'e': f'{archivo.name}: {e}'})
            raise DecisionRechazada(str(e)) from e
    finally:
        motor.dispose()


def aplicar(c, lista, competiciones, archivo, sha, comando):
    reconstruccion.informe_de_carga(c)  # exige una carga inicial confirmada
    run = c.execute(text("INSERT INTO import_run (kind, started_at, status, tool_version, command) "
                         "VALUES ('decisions', :t, 'running', :v, :cmd) RETURNING id"),
                    {'t': ahora(), 'v': version_del_codigo(), 'cmd': comando}).scalar()
    conocido = c.execute(text('SELECT id FROM source_file WHERE sha256 = :s'), {'s': sha}).scalar()
    if conocido is not None:
        insertar(c, 'import_run_file', [dict(run_id=run, source_file_id=conocido, position=1, presented_name=archivo.name,
                                             presented_path=str(archivo), outcome='already_known')])
        c.execute(text("UPDATE import_run SET status = 'no_changes', finished_at = :t WHERE id = :r"), {'t': ahora(), 'r': run})
        return {'run': run, 'estado': 'no_changes', 'sustituciones': 0}
    if c.exec_driver_sql("SELECT count(*) FROM local_replacement WHERE status = 'active'").scalar():
        raise ValueError('Ya hay sustituciones activas: combinar decisiones de varios ficheros aún no está implementado')
    # Las reglas de la API, sobre los partidos del ámbito tal como los reconstruye la base.
    reconstruida = reconstruccion.salida_desde_base(c)
    datos = api.aplicar_ambito(dict(reconstruida, originales=api.indexar(reconstruida['originales'])), competiciones)
    api.aplicar_sustituciones(datos, lista)
    previos = c.exec_driver_sql("SELECT count(*) FROM source_file WHERE kind = 'local_decisions'").scalar()
    etiqueta = f'D{previos + 1}'
    archivo_id = c.execute(text("INSERT INTO source_file (sha256, kind, label, records, first_run_id) "
                                "VALUES (:s, 'local_decisions', :l, :n, :r) RETURNING id"),
                           {'s': sha, 'l': etiqueta, 'n': len(lista), 'r': run}).scalar()
    insertar(c, 'import_run_file', [dict(run_id=run, source_file_id=archivo_id, position=1, presented_name=archivo.name,
                                         presented_path=str(archivo), outcome='ingested')])
    partido = lambda externo: c.execute(text("SELECT entity_id FROM match_external_id WHERE provider = 'sofascore' "
                                             "AND namespace = '' AND external_id = :e AND status <> 'rejected'"),
                                        {'e': str(externo)}).scalar()
    insertar(c, 'local_replacement', [dict(
        replaced_match_id=partido(s['sustituido']), current_match_id=partido(s['vigente']),
        external_replaced_id=str(s['sustituido']), external_current_id=str(s['vigente']), reason=s['motivo'],
        decided_by=s['procedencia']['decidido_por'], decided_on=date.fromisoformat(s['procedencia']['fecha']),
        evidence=s['procedencia'].get('evidencia'), source_file_id=archivo_id, created_in_run=run, status='active')
        for s in lista])
    informe = {'archivo': str(archivo), 'sha256': sha, 'etiqueta': etiqueta,
               'ambito': [{k: comp[k] for k in api.CAMPOS_AMBITO} for comp in competiciones],
               'sustituciones': [[s['sustituido'], s['vigente']] for s in lista]}
    c.execute(text("UPDATE import_run SET status = 'committed', finished_at = :t, summary = :s WHERE id = :r"),
              {'t': ahora(), 's': literal(informe), 'r': run})
    return {'run': run, 'estado': 'committed', 'sustituciones': len(lista), 'etiqueta': etiqueta}


def sustituciones_guardadas(c):
    """Las sustituciones activas en el formato de correcciones/sustituciones.json (para comprobar la trazabilidad)."""
    filas = c.exec_driver_sql("""SELECT external_replaced_id, external_current_id, reason, decided_by, decided_on, evidence
                                 FROM local_replacement WHERE status = 'active' ORDER BY id""").fetchall()
    return [{'sustituido': int(a), 'vigente': int(b), 'motivo': m,
             'procedencia': {'decidido_por': d, 'fecha': str(f), **({'evidencia': e} if e is not None else {})}}
            for a, b, m, d, f, e in filas]
