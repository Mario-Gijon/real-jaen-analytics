"""Comandos de la persistencia (requieren requirements-sql.txt):

  python -m persistencia cargar --importacion CARPETA --base RUTA.sqlite
      Carga inicial de una importación verificada en una base nueva, fuera de los repositorios.
  python -m persistencia importar --base RUTA.sqlite [--exportacion EQUIPO=RUTA ...] [--calendario RUTA ...]
                                  [--fichas-jugadores RUTA ...] [RUTA ...]
      Añade evidencia (ficheros nuevos o declaraciones sobre ficheros conocidos) y sincroniza el estado operativo.
      Copia verificada antes; todo en una transacción.
  python -m persistencia verificar --base RUTA.sqlite
      Solo lectura: integridad, convocatorias, versiones de reglas y sincronización en seco (0 cambios).
  python -m persistencia reconstruir --base RUTA.sqlite [--confirmar]
      Tras un cambio de reglas. Sin --confirmar solo muestra la diferencia; con él la aplica (copia antes).
  python -m persistencia sustituciones --base RUTA.sqlite --archivo sustituciones.json --ambito ambito.json
      Incorpora las sustituciones locales, validadas con las reglas de la API sobre el ámbito indicado.
  python -m persistencia migrar --base RUTA.sqlite
      Copia verificada y migración del esquema a la revisión del código.
  python -m persistencia copiar --base RUTA.sqlite [--motivo TEXTO]
      Copia consistente y verificada en <carpeta de la base>/copias.
  python -m persistencia restaurar --copia COPIA.sqlite --base RUTA.sqlite --confirmar [--revision REV]
      Sustituye la base por la copia, solo si ningún proceso la usa (cierre antes la API). Copia antes la actual.
      La copia debe tener la revisión del código, salvo --revision explícita (marcha atrás de una migración).
  python -m persistencia adquirir --base RUTA.sqlite --ambito ambito.json --solicitud UUID [--receta RECETA]
                                  [--url http://acquisition:8002] [--secreto RUTA]
      Pide al servicio de adquisición la receta fija, verifica el bundle y lo publica por el coordinador (API detenida).
      Repetir la misma --solicitud reanuda esa solicitud: nunca vuelve a consultar al proveedor si ya hay bundle.
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import importar  # noqa: E402
from persistencia import sincronizacion  # noqa: E402
from persistencia.carga import CargaRechazada, cargar  # noqa: E402
from persistencia.decisiones import DecisionRechazada, cargar_sustituciones  # noqa: E402
from persistencia.sqlite import CopiaNoValida
from persistencia.bloqueo import BaseEnUso  # noqa: E402

# Órdenes tras las que la API (que lee la base al arrancar) debe reiniciarse.
CAMBIAN_LA_BASE = ('importar', 'reconstruir', 'sustituciones', 'restaurar', 'migrar', 'adquirir')


def main():
    ap = argparse.ArgumentParser(prog='python -m persistencia', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='orden', required=True)
    a = sub.add_parser('cargar', help='carga inicial en una base nueva')
    a.add_argument('--importacion', type=Path, required=True)
    a.add_argument('--base', type=Path, required=True)
    i = sub.add_parser('importar', help='añade evidencia y sincroniza el estado')
    i.add_argument('--base', type=Path, required=True)
    i.add_argument('entrada', type=Path, nargs='*', help='exportación sin equipo declarado')
    i.add_argument('--exportacion', type=importar.exportacion, action='append', default=[], metavar='EQUIPO=RUTA')
    i.add_argument('--calendario', type=Path, action='append', default=[], metavar='RUTA')
    i.add_argument('--fichas-jugadores', type=Path, action='append', default=[], metavar='RUTA')
    v = sub.add_parser('verificar', help='verificación de solo lectura')
    v.add_argument('--base', type=Path, required=True)
    r = sub.add_parser('reconstruir', help='aplica las reglas actuales a toda la evidencia')
    r.add_argument('--base', type=Path, required=True)
    r.add_argument('--confirmar', action='store_true')
    s = sub.add_parser('sustituciones', help='incorpora las sustituciones locales')
    s.add_argument('--base', type=Path, required=True)
    s.add_argument('--archivo', type=Path, required=True)
    s.add_argument('--ambito', type=Path, required=True)
    m = sub.add_parser('migrar', help='migra el esquema (copia antes)')
    m.add_argument('--base', type=Path, required=True)
    k = sub.add_parser('copiar', help='copia consistente y verificada')
    k.add_argument('--base', type=Path, required=True)
    k.add_argument('--motivo', default='manual')
    t = sub.add_parser('restaurar', help='restaura una copia (la base no debe estar en uso)')
    t.add_argument('--copia', type=Path, required=True)
    t.add_argument('--base', type=Path, required=True)
    t.add_argument('--confirmar', action='store_true')
    t.add_argument('--revision', help='revisión de la copia, si no es la del código (marcha atrás de una migración)')
    q = sub.add_parser('adquirir', help='adquisición fija, verificación y publicación (API detenida)')
    q.add_argument('--base', type=Path, required=True)
    q.add_argument('--ambito', type=Path, required=True)
    q.add_argument('--solicitud', required=True, help='UUID de la solicitud; repetirlo la reanuda')
    q.add_argument('--receta', default='sofascore-pilot-16599363-v1')
    q.add_argument('--url', default=os.environ.get('RJ_ACQUISITION_URL', 'http://acquisition:8002'))
    q.add_argument('--secreto', type=Path, default=os.environ.get('RJ_ACQUISITION_SECRET_FILE', '/run/secrets/acquisition-token'))
    args = ap.parse_args()
    comando = ' '.join(sys.argv)
    try:
        if args.orden == 'cargar':
            informe = cargar(args.importacion, args.base, comando)
        elif args.orden == 'importar':
            if not (args.entrada or args.exportacion or args.calendario or args.fichas_jugadores):
                ap.error('indica al menos un fichero')
            informe = sincronizacion.importar_evidencia(args.base, args.exportacion, args.calendario,
                                                        args.fichas_jugadores, args.entrada, comando)
        elif args.orden == 'verificar':
            informe = sincronizacion.verificar(args.base)
        elif args.orden == 'reconstruir':
            informe = sincronizacion.reconstruir(args.base, args.confirmar, comando)
        elif args.orden == 'sustituciones':
            informe = cargar_sustituciones(args.base, args.archivo, args.ambito, comando)
        elif args.orden == 'migrar':
            informe = sincronizacion.migrar(args.base)
        elif args.orden == 'copiar':
            informe = sincronizacion.copiar(args.base, args.motivo)
        elif args.orden == 'adquirir':
            from persistencia.actualizacion import Coordinador
            from persistencia.adquisicion import Cliente
            with Coordinador(args.base, args.ambito) as coordinador:
                informe = Cliente(args.url, args.secreto).publicar(coordinador, args.solicitud, args.receta)
        else:
            if not args.confirmar:
                ap.error('restaurar sustituye la base: añade --confirmar')
            informe = sincronizacion.restaurar(args.copia, args.base, args.revision)
    except (CargaRechazada, DecisionRechazada, CopiaNoValida, BaseEnUso, RuntimeError, ValueError) as e:
        ap.exit(1, f'Rechazado: {e}\n')
    print(json.dumps(informe, ensure_ascii=False, indent=2))
    if args.orden == 'verificar' and not informe['ok']:
        sys.exit(1)
    if args.orden in CAMBIAN_LA_BASE and not informe.get('en_seco'):
        print('La API lee la base al arrancar: reiníciela para servir el estado nuevo.', file=sys.stderr)


if __name__ == '__main__':
    main()
