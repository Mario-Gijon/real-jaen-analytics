#!/bin/sh
# Recuperación conjunta de un respaldo de scripts/respaldo-instalacion.sh en volúmenes NUEVOS
# Sirve también para una primera carga del servidor.
#
#   ENV_FILE=deploy/prod/.env sh dashboard/scripts/restaurar-instalacion.sh RESPALDO
#
# RESPALDO es una carpeta dentro de RJ_IMPORT_DIR, configurado en relación con la raíz del workspace; el backend la lee en /imports, en solo lectura.
# Comprueba SHA256SUMS; se niega si algún volumen de destino ya existe; restaura la base, sus registros de
# actualización, los trabajos de Acquisition y el estado/calibraciones de Video Service; inicializa un perfil
# de navegador nuevo y verifica que el coordinador acepta la generación del respaldo. No arranca servicios.
set -eu
SRC=${1:?Indica la carpeta del respaldo}
ENV_FILE=${ENV_FILE:-deploy/prod/.env}
test -f "$ENV_FILE" || { echo "No existe $ENV_FILE" >&2; exit 1; }
COMPOSE_DIR=$(cd deploy/prod && pwd)
ENV_FILE=$(cd "$(dirname "$ENV_FILE")" && pwd)/$(basename "$ENV_FILE")
COMPOSE_FILES=${COMPOSE_FILES:--f compose.yaml}
var() { sed -n "s/^$1=//p" "$ENV_FILE" | tail -1; }
STATE_VOLUME=$(var RJ_PROD_SPORTS_STATE_VOLUME)
JOBS_VOLUME=$(var RJ_PROD_ACQUISITION_JOBS_VOLUME)
PROFILE_VOLUME=$(var RJ_PROD_ACQUISITION_PROFILES_VOLUME)
VIDEO_VOLUME=$(var RJ_PROD_VIDEO_STATE_VOLUME)
IMPORT_DIR=$(var RJ_IMPORT_DIR)
PROJECT=$(var COMPOSE_PROJECT_NAME)
PROJECT=${PROJECT:-real-jaen-analytics-prod}
# shellcheck disable=SC2086
dc() (cd "$COMPOSE_DIR" && docker compose --env-file "$ENV_FILE" $COMPOSE_FILES "$@")
# The backup must be inside the import directory, which the backend mounts read-only at /imports.
src_abs=$(cd "$SRC" && pwd)
imp_abs=$(cd "$IMPORT_DIR" && pwd)
case "$src_abs/" in "$imp_abs"/*) REL=${src_abs#"$imp_abs"/} ;; *) echo "El respaldo debe estar dentro de RJ_IMPORT_DIR ($imp_abs)" >&2; exit 1 ;; esac
(cd "$src_abs" && sha256sum -c SHA256SUMS) || { echo "Las sumas del respaldo no coinciden" >&2; exit 1; }
# Git Bash (Windows): paths for Docker in Windows form, and no automatic conversion of container paths.
case "$(uname -s)" in MINGW*|MSYS*) ENV_FILE=$(cygpath -m "$ENV_FILE"); imp_abs=$(cygpath -m "$imp_abs"); export MSYS_NO_PATHCONV=1 ;; esac

volumes="$STATE_VOLUME $JOBS_VOLUME $PROFILE_VOLUME $VIDEO_VOLUME"
for vol in $volumes; do
  if docker volume inspect "$vol" >/dev/null 2>&1; then echo "Volumen existente: $vol (se restaura solo en volúmenes nuevos)" >&2; exit 1; fi
done
for vol in $volumes; do docker volume create --label "rj.installation=$PROJECT" "$vol" >/dev/null; echo "Creado $vol"; done

dc run --rm --no-deps --user 0:0 --cap-add CHOWN --entrypoint chown sports-data 10001:10001 /state
dc run --rm --no-deps --user 0:0 --cap-add CHOWN --entrypoint chown acquisition 10001:10001 /profiles /acquisition-jobs
dc run --rm --no-deps --user 0:0 --cap-add CHOWN --entrypoint chown video-service 10001:10001 /state
dc run --rm --no-deps sports-data -m scripts.initialize_snapshot "/imports/$REL/real-jaen.sqlite" /state/real-jaen.sqlite
dc run --rm --no-deps --entrypoint sh sports-data -c "set -eu
tar -xf '/imports/$REL/actualizaciones.tar' -C /state
if [ -f '/imports/$REL/procedencia.tar' ]; then tar -xf '/imports/$REL/procedencia.tar' -C /state && chmod -R a-w /state/procedencia; fi"
dc run --rm --no-deps sports-data -m persistencia verificar --base /state/real-jaen.sqlite >/dev/null
# The coordinator must accept the base with the generation recorded in the backup (same content, same records).
dc run --rm --no-deps --entrypoint python sports-data -B -c "
import json
from persistencia.actualizacion import Coordinador
expected = json.load(open('/imports/$REL/manifest.json'))['generacion']
with Coordinador('/state/real-jaen.sqlite', '/app/ambito.json', reserva=0) as c:
    g = c.capturar()
assert g.id == expected, (g.id, expected)
print('Coordinador: generación', g.id, 'aceptada')"

SPORTS_DATA_IMAGE=$(var RJ_SPORTS_DATA_IMAGE)
docker run --rm --network none --user 10001:10001 -v "$JOBS_VOLUME:/acquisition-jobs" -v "$imp_abs:/imports:ro" \
  --entrypoint tar "$SPORTS_DATA_IMAGE" -xf "/imports/$REL/acquisition-jobs.tar" -C /acquisition-jobs
docker run --rm --network none --user 10001:10001 -v "$VIDEO_VOLUME:/video" -v "$imp_abs:/imports:ro" \
  --entrypoint tar "$SPORTS_DATA_IMAGE" -xf "/imports/$REL/video-service-state.tar" -C /video
dc --profile admin run --rm --no-deps acquisition-admin initialize-profile --confirm-new-profile
echo "Restaurado sin arrancar servicios. Consulta README.md para iniciar deploy/prod/compose.yaml."
