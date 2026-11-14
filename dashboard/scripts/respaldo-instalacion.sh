#!/bin/sh
# Respaldo consistente de una instalación Docker (README.md).
#
#   ENV_FILE=.env.prod sh dashboard/scripts/respaldo-instalacion.sh DESTINO
#   COMPOSE_FILE=compose.dev.yaml ENV_FILE=.env.dev sh dashboard/scripts/respaldo-instalacion.sh DESTINO
#
# Desde la raíz del monorepositorio. DESTINO es una carpeta nueva del anfitrión. Contenido:
#   real-jaen.sqlite          copia SQLite consistente (persistencia copiar: integridad y huella comprobadas)
#   actualizaciones.tar       <base>.actualizaciones: generación confirmada, trabajos, solicitudes y bundles sellados
#   procedencia.tar           /state/procedencia (evidencia archivada), si existe
#   acquisition-jobs.tar      volumen persistente de trabajos de adquisición
#   video-service-state.tar   SQLite y calibraciones persistentes de Video Service
#   manifest.json, SHA256SUMS
# No incluye secretos ni el perfil del navegador (sus cookies no se copian: en el destino se inicializa otro).
#
# Detiene Dashboard y los servicios de datos durante la copia y luego los vuelve a arrancar.
# Se niega si hay una actualización en curso. Nunca toca los datos de origen.
set -eu
DEST=${1:?Indica una carpeta de destino nueva}
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
COMPOSE_FILE=${COMPOSE_FILE:-compose.prod.yaml}
case "$COMPOSE_FILE" in
  compose.dev.yaml) DEFAULT_ENV=.env.dev ;;
  compose.prod.yaml) DEFAULT_ENV=.env.prod ;;
  *) echo "COMPOSE_FILE must be compose.dev.yaml or compose.prod.yaml" >&2; exit 1 ;;
esac
ENV_FILE=${ENV_FILE:-$DEFAULT_ENV}
case "$ENV_FILE" in /*) ;; *) ENV_FILE="$ROOT_DIR/$ENV_FILE" ;; esac
test -f "$ENV_FILE" || { echo "No existe $ENV_FILE" >&2; exit 1; }
test -f "$ROOT_DIR/$COMPOSE_FILE" || { echo "No existe $ROOT_DIR/$COMPOSE_FILE" >&2; exit 1; }
ENV_FILE=$(cd "$(dirname "$ENV_FILE")" && pwd)/$(basename "$ENV_FILE")
test ! -e "$DEST" || { echo "El destino ya existe: $DEST" >&2; exit 1; }
var() { sed -n "s/^$1=//p" "$ENV_FILE" | tail -1; }
case "$COMPOSE_FILE" in
  compose.dev.yaml)
    STATE_VOLUME=rja-dev-sports-state
    JOBS_VOLUME=rja-dev-acquisition-jobs
    VIDEO_VOLUME=rja-dev-video-state
    SPORTS_DATA_IMAGE=local/real-jaen-analytics/sports-data:dev
    ;;
  compose.prod.yaml)
    STATE_VOLUME=$(var RJ_PROD_SPORTS_STATE_VOLUME)
    JOBS_VOLUME=$(var RJ_PROD_ACQUISITION_JOBS_VOLUME)
    VIDEO_VOLUME=$(var RJ_PROD_VIDEO_STATE_VOLUME)
    STATE_VOLUME=${STATE_VOLUME:-rja-prod-sports-state}
    JOBS_VOLUME=${JOBS_VOLUME:-rja-prod-acquisition-jobs}
    VIDEO_VOLUME=${VIDEO_VOLUME:-rja-prod-video-state}
    SPORTS_DATA_IMAGE=local/real-jaen-analytics/sports-data:prod
    ;;
esac
test -n "$STATE_VOLUME" && test -n "$JOBS_VOLUME" && test -n "$VIDEO_VOLUME" && test -n "$SPORTS_DATA_IMAGE" || { echo "No se pudieron determinar los volúmenes de datos para $COMPOSE_FILE" >&2; exit 1; }
dc() (cd "$ROOT_DIR" && docker compose --project-directory "$ROOT_DIR" --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@")
# Ninguna actualización en curso (lectura inmutable: no toca los archivos).
active=$(docker run --rm --network none -v "$STATE_VOLUME:/state:ro" --entrypoint python "$SPORTS_DATA_IMAGE" -c "
import os, sqlite3
p = '/state/real-jaen.sqlite.actualizaciones/control/jobs.sqlite'
print(sqlite3.connect(f'file:{p}?mode=ro&immutable=1', uri=True).execute(
    \"SELECT count(*) FROM job WHERE state IN ('queued','running')\").fetchone()[0] if os.path.exists(p) else 0)")
test "$active" = 0 || { echo "Hay una actualización en curso: espera a que termine" >&2; exit 1; }

mkdir -p "$DEST"
OUT=$(cd "$DEST" && pwd)
# Git Bash (Windows): paths for Docker in Windows form, and no automatic conversion of container paths.
case "$(uname -s)" in MINGW*|MSYS*) OUT=$(cd "$DEST" && pwd -W); ENV_FILE=$(cygpath -m "$ENV_FILE"); ROOT_DIR=$(cygpath -m "$ROOT_DIR"); export MSYS_NO_PATHCONV=1 ;; esac

services="dashboard sports-data acquisition video-service"
echo "Deteniendo: $services"
# shellcheck disable=SC2086
dc stop $services
restart() {
  echo "Arrancando de nuevo"
  dc up -d --no-build --wait --wait-timeout 180
}
trap restart EXIT

report=$(dc run --rm --no-deps -T sports-data -m persistencia copiar --base /state/real-jaen.sqlite --motivo respaldo-instalacion)
printf '%s\n' "$report" > "$OUT/copiar.json"
copy=$(printf '%s' "$report" | sed -n 's/.*"ruta": "\([^"]*\)".*/\1/p' | head -1)
test -n "$copy" || { echo "persistencia copiar no informó la copia" >&2; exit 1; }

docker run --rm --network none -v "$STATE_VOLUME:/state:ro" -v "$OUT:/out" --entrypoint sh "$SPORTS_DATA_IMAGE" -c "
set -eu
cp '$copy' /out/real-jaen.sqlite
tar -cf /out/actualizaciones.tar -C /state real-jaen.sqlite.actualizaciones
if [ -d /state/procedencia ]; then tar -cf /out/procedencia.tar -C /state procedencia; fi"
docker run --rm --network none -v "$JOBS_VOLUME:/jobs:ro" -v "$OUT:/out" --entrypoint tar "$SPORTS_DATA_IMAGE" -cf /out/acquisition-jobs.tar -C /jobs .
docker run --rm --network none -v "$VIDEO_VOLUME:/video:ro" -v "$OUT:/out" --entrypoint tar "$SPORTS_DATA_IMAGE" -cf /out/video-service-state.tar -C /video .
trap - EXIT
restart

# Manifiesto: generación confirmada, huella, revisión y última publicación, leídos del propio respaldo.
docker run --rm --network none -v "$OUT:/out" --entrypoint python "$SPORTS_DATA_IMAGE" -c "
import hashlib, io, json, sqlite3, tarfile
from persistencia import sqlite as s
d = s.describir('/out/real-jaen.sqlite')
with tarfile.open('/out/actualizaciones.tar') as t:
    meta = sqlite3.connect(':memory:')
    raw = t.extractfile('real-jaen.sqlite.actualizaciones/jobs.sqlite').read()
    open('/tmp/c.sqlite', 'wb').write(raw)
    confirmed = json.loads(sqlite3.connect('/tmp/c.sqlite').execute('SELECT detalle FROM confirmado').fetchone()[0])
    open('/tmp/j.sqlite', 'wb').write(t.extractfile('real-jaen.sqlite.actualizaciones/control/jobs.sqlite').read())
    jobs = sqlite3.connect('/tmp/j.sqlite').execute(\"SELECT count(*), max(json_extract(data,'$.finished_at')) FROM job WHERE state='completed'\").fetchone()
runs = sqlite3.connect('file:/out/real-jaen.sqlite?mode=ro', uri=True).execute('SELECT count(*), max(finished_at) FROM import_run').fetchone()
json.dump(dict(revision=d['revision'], huella=d['huella'], integridad=d['integridad'], generacion=confirmed.get('generacion'),
               import_runs=runs[0], ultima_importacion=runs[1], actualizaciones_publicadas=jobs[0], ultima_publicacion=jobs[1]),
          open('/out/manifest.json', 'w'), indent=2)
print(open('/out/manifest.json').read())"
(cd "$OUT" && sha256sum ./*.sqlite ./*.tar ./*.json > SHA256SUMS && cat SHA256SUMS)
echo "Respaldo completo en $OUT"
