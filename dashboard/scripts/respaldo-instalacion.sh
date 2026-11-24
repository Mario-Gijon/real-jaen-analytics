#!/bin/sh
# Backup consistente de una instalación Docker. Ejecutar desde cualquier directorio.
# COMPOSE_FILE admite la lista separada por el separador nativo (':' en Unix) para aplicar overlays.
# Ejemplo: COMPOSE_FILE='compose.dev.yaml:compose.existing-state.yaml:compose.photos.yaml' ENV_FILE=... sh ... DESTINO
set -eu
umask 077
DEST=${1:?Indica una carpeta de destino nueva}
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
case "$(uname -s)" in MINGW*|MSYS*) COMPOSE_SEPARATOR=';' ;; *) COMPOSE_SEPARATOR=':' ;; esac
COMPOSE_FILES=${COMPOSE_FILES:-${COMPOSE_FILE:-compose.prod.yaml}}
case "$COMPOSE_FILES" in
  compose.dev.yaml|compose.dev.yaml:*|compose.dev.yaml\;*) DEFAULT_ENV=.env.dev ;;
  compose.prod.yaml|compose.prod.yaml:*|compose.prod.yaml\;*) DEFAULT_ENV=.env.prod ;;
  *) echo "La lista Compose debe empezar por compose.dev.yaml o compose.prod.yaml" >&2; exit 1 ;;
esac
ENV_FILE=${ENV_FILE:-$DEFAULT_ENV}
case "$ENV_FILE" in /*) ;; *) ENV_FILE="$ROOT_DIR/$ENV_FILE" ;; esac
test -f "$ENV_FILE" || { echo "No existe $ENV_FILE" >&2; exit 1; }
ENV_FILE=$(CDPATH= cd -- "$(dirname -- "$ENV_FILE")" && pwd)/$(basename -- "$ENV_FILE")
# Validate every Compose file without guessing what its overlays contain.
_old_ifs=$IFS
IFS=$COMPOSE_SEPARATOR
set -f
for _compose_file in $COMPOSE_FILES; do
  case "$_compose_file" in /*) _compose_path=$_compose_file ;; *) _compose_path="$ROOT_DIR/$_compose_file" ;; esac
  test -f "$_compose_path" || { echo "No existe $_compose_path" >&2; exit 1; }
done
set +f
IFS=$_old_ifs
DEST_PARENT=$(CDPATH= cd -- "$(dirname -- "$DEST")" && pwd) || { echo "No existe la carpeta padre del destino" >&2; exit 1; }
DEST="$DEST_PARENT/$(basename -- "$DEST")"
test ! -e "$DEST" && test ! -L "$DEST" || { echo "El destino ya existe: $DEST" >&2; exit 1; }
PHOTO_HELPER=sports-data/scripts/fotos_respaldo.py
test -f "$ROOT_DIR/$PHOTO_HELPER" || { echo "No existe $ROOT_DIR/$PHOTO_HELPER" >&2; exit 1; }
VIDEO_HELPER=dashboard/scripts/video-catalog-respaldo.py
test -f "$ROOT_DIR/$VIDEO_HELPER" || { echo "No existe $ROOT_DIR/$VIDEO_HELPER" >&2; exit 1; }
# Compose resolves project, image, names and overlays; never infer volume names from an environment file.
dc() (cd "$ROOT_DIR" && COMPOSE_FILE="$COMPOSE_FILES" docker compose --project-directory "$ROOT_DIR" --env-file "$ENV_FILE" "$@")
. "$SCRIPT_DIR/instalacion-compose.sh"
resolver_instalacion
# Do not let a direct docker bind mount create a missing external catalog implicitly.
case "$VIDEO_MANIFEST" in /*|*\\*|../*|*/../*|*/..) echo "VIDEO_SERVICE_MANIFEST no es una ruta relativa segura" >&2; exit 1 ;; esac
test -d "$VIDEO_CATALOG_DIR" && test -r "$VIDEO_CATALOG_DIR" || {
  echo "No se puede leer el catálogo externo de vídeo ($VIDEO_CATALOG_DIR)" >&2; exit 1;
}
test -f "$VIDEO_CATALOG_DIR/$VIDEO_MANIFEST" && test -r "$VIDEO_CATALOG_DIR/$VIDEO_MANIFEST" || {
  echo "No se puede leer el manifiesto de vídeo configurado ($VIDEO_MANIFEST)" >&2; exit 1;
}
# Only inspect/mount volumes after their effective names and project ownership have been verified.
for vol in "$STATE_VOLUME" "$JOBS_VOLUME" "$PROFILE_VOLUME" "$VIDEO_VOLUME"; do comprobar_volumen "$vol"; done
services="dashboard sports-data acquisition video-service"
RUNNING=""
for svc in $services; do
  ids=$(dc ps -a -q "$svc") || { echo "No se pudieron consultar los contenedores de $PROJECT" >&2; exit 1; }
  test -n "$ids" || continue
  test "$(printf '%s\n' "$ids" | wc -l)" -eq 1 || { echo "Hay más de un contenedor de $svc en $PROJECT" >&2; exit 1; }
  info=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}|{{.State.Running}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}|{{range .Mounts}}{{if eq .Type "volume"}};{{.Destination}}={{.Name}}{{else if eq .Destination "/app/data"}};bind:/app/data={{.Source}}{{end}}{{end}};' "$ids") ||
    { echo "No se pudo inspeccionar el contenedor $ids de $svc" >&2; exit 1; }
  test "${info%%|*}" = "$PROJECT" || { echo "El contenedor $ids de $svc no pertenece a $PROJECT" >&2; exit 1; }
  case "$svc" in
    sports-data) expected="/state=$STATE_VOLUME" ;;
    acquisition) expected="/profiles=$PROFILE_VOLUME;/acquisition-jobs=$JOBS_VOLUME" ;;
    video-service) expected="/state=$VIDEO_VOLUME;bind:/app/data=$VIDEO_CATALOG_DIR" ;;
    *) expected="" ;;
  esac
  if test -n "$expected"; then
    _rest_mounts=${info#*|}; _rest_mounts=${_rest_mounts#*|}; _mounts=${_rest_mounts#*|}
    old_ifs=$IFS; IFS=';'
    for _expected_mount in $expected; do
      case "$_mounts;" in *";$_expected_mount;"*) ;; *)
        IFS=$old_ifs
        echo "El contenedor de $svc no monta $_expected_mount: la configuración ($ENV_FILE) no coincide con la instalación en marcha" >&2
        exit 1 ;;
      esac
    done
    IFS=$old_ifs
  fi
  if [ "$svc" = sports-data ]; then
    running_image=$(docker inspect -f '{{.Image}}' "$ids") || { echo "No se pudo inspeccionar el contenedor $ids de $svc" >&2; exit 1; }
    config_image=$(docker image inspect -f '{{.Id}}' "$SPORTS_DATA_IMAGE") || { echo "No se pudo inspeccionar la imagen $SPORTS_DATA_IMAGE" >&2; exit 1; }
    if [ -z "$running_image" ] || [ "$running_image" != "$config_image" ]; then
      echo "La imagen de Sports Data en marcha ($running_image) no es la que Compose usaría para el respaldo ($SPORTS_DATA_IMAGE = $config_image): no se respalda." >&2
      exit 1
    fi
  fi
  rest=${info#*|}
  if [ "${rest%%|*}" = true ]; then rest=${rest#*|}; RUNNING="$RUNNING $svc:$ids:${rest%%|*}"; fi
done
# Read-only guard: fail closed on an unfinished restore, an active sports update, or an unreadable control database.
check_update() {
active=$(docker run --rm --network none -v "$STATE_VOLUME:/state:ro" -v "$JOBS_VOLUME:/jobs:ro" \
  -v "$VIDEO_VOLUME:/video:ro" --entrypoint python "$SPORTS_DATA_IMAGE" -c "
import os, sqlite3
try:
    os.lstat('/state/real-jaen.sqlite.restauracion-incompleta')
    print('restauracion-incompleta')
    raise SystemExit
except FileNotFoundError:
    pass
def count(path, query):
    if not os.path.isfile(path):
        return 0
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=2) as db:
        return db.execute(query).fetchone()[0]
active = (
    ('actualizacion-deportiva-activa', count('/state/real-jaen.sqlite.actualizaciones/control/jobs.sqlite',
     \"SELECT count(*) FROM job WHERE state IN ('queued','running')\")),
    ('adquisicion-activa', count('/jobs/jobs.sqlite',
     \"SELECT count(*) FROM job WHERE state IN ('queued','running','cancelling')\")),
    ('trabajo-de-video-activo', count('/video/video-service.sqlite',
     \"SELECT count(*) FROM video_job WHERE status IN ('pending','downloading','calibrating','saving')\")),
)
for label, total in active:
    if total:
        print(label)
        raise SystemExit
print('0')") ||
  { echo "No se pudo comprobar el estado de la instalación: no se respalda" >&2; exit 1; }
case "$active" in
  0) ;;
  restauracion-incompleta) echo "Restauración incompleta (/state/real-jaen.sqlite.restauracion-incompleta): no se respalda." >&2; exit 1 ;;
  actualizacion-deportiva-activa) echo "Hay una actualización deportiva en curso: espera a que termine" >&2; exit 1 ;;
  adquisicion-activa) echo "Hay un trabajo de Acquisition en curso: espera a que termine" >&2; exit 1 ;;
  trabajo-de-video-activo) echo "Hay un trabajo de vídeo en curso: espera a que termine" >&2; exit 1 ;;
  *) echo "No se pudo comprobar el estado de la instalación: no se respalda" >&2; exit 1 ;;
esac
}
check_update

WORK=$(mktemp -d "$DEST.partial.XXXXXX") || { echo "No se pudo crear el área temporal junto al destino" >&2; exit 1; }
OUT=$(CDPATH= cd -- "$WORK" && pwd)
case "$(uname -s)" in MINGW*|MSYS*) OUT=$(cd "$WORK" && pwd -W); ENV_FILE=$(cygpath -m "$ENV_FILE"); ROOT_DIR=$(cygpath -m "$ROOT_DIR"); export MSYS_NO_PATHCONV=1 ;; esac
STOPPED=""
START_TIMEOUT=${RJ_BACKUP_START_TIMEOUT:-360}
started() {
  _deadline=$(( $(date +%s) + START_TIMEOUT ))
  while :; do
    _state=$(docker inspect -f '{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$1" 2>/dev/null) || _state="?|"
    case "$_state" in
      running\|healthy) return 0 ;;
      running\|*) [ "$2" != healthy ] && return 0 ;;
      exited\|*|dead\|*|\?\|*) return 1 ;;
    esac
    [ "$(date +%s)" -lt "$_deadline" ] || return 1
    sleep 5
  done
}
restart() {
  _rc=0
  for _group in "sports-data" "video-service" "acquisition" "dashboard"; do
    _waiting=""
    for _entry in $STOPPED; do
      case "$_entry" in "$_group":*) ;;
        *) continue ;;
      esac
      _rest=${_entry#*:}; _id=${_rest%%:*}
      echo "Arrancando de nuevo $_group (docker start $_id)"
      docker start "$_id" >/dev/null || _rc=1
      _waiting="$_waiting $_entry"
    done
    for _entry in $_waiting; do
      _svc=${_entry%%:*}; _rest=${_entry#*:}; _id=${_rest%%:*}; _health=${_rest#*:}
      started "$_id" "$_health" || { echo "$_svc no ha vuelto a su estado anterior ($_health)" >&2; _rc=1; }
    done
  done
  [ "$_rc" -ne 0 ] || STOPPED=""
  return "$_rc"
}
cleanup() {
  _status=$?
  trap - EXIT INT TERM HUP
  if [ -n "$STOPPED" ]; then
    restart || { echo "Revisa la instalación: algún contenedor no ha vuelto a arrancar (no se recrea ninguno)" >&2; [ "$_status" -ne 0 ] || _status=1; }
  fi
  if [ -n "${WORK:-}" ] && [ -d "$WORK" ]; then rm -rf -- "$WORK"; fi
  exit "$_status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
# Stop Dashboard first so no new update can be submitted, then check once more before stopping data services.
for svc in dashboard; do
  for entry in $RUNNING; do
    case "$entry" in "$svc":*) rest=${entry#*:}; id=${rest%%:*} ;;
      *) continue ;;
    esac
    echo "Deteniendo $svc (docker stop $id)"
    docker stop "$id" >/dev/null
    STOPPED="$STOPPED $entry"
  done
done
check_update
# Stop only the exact containers that were running, in dependency-safe order; record only successful stops.
for svc in acquisition video-service sports-data; do
  for entry in $RUNNING; do
    case "$entry" in "$svc":*) rest=${entry#*:}; id=${rest%%:*} ;;
      *) continue ;;
    esac
    echo "Deteniendo $svc (docker stop $id)"
    docker stop "$id" >/dev/null
    STOPPED="$STOPPED $entry"
  done
done

# Consistent SQLite snapshot and binary state archives; all source volumes are read-only except `persistencia copiar`
# which creates its standard verified safety copy in /state/copias and reports that path here.
report=$(dc run --rm --no-deps -T --entrypoint python sports-data -B -m persistencia copiar --base /state/real-jaen.sqlite --motivo respaldo-instalacion)
printf '%s\n' "$report" > "$OUT/copiar.json"
copy=$(printf '%s' "$report" | sed -n 's/.*"ruta": "\([^"]*\)".*/\1/p' | head -1)
test -n "$copy" || { echo "persistencia copiar no informó la copia" >&2; exit 1; }
expected_copy_sha=$(printf '%s\n' "$report" | sed -n 's/.*"sha256_archivo": "\([^"]*\)".*/\1/p' | head -1)
case "$expected_copy_sha" in ''|*[!0-9a-f]*) echo "persistencia copiar no informó un SHA-256 válido" >&2; exit 1 ;; esac
test "${#expected_copy_sha}" -eq 64 || { echo "persistencia copiar no informó un SHA-256 válido" >&2; exit 1; }
actual_copy_sha=$(docker run --rm --network none -v "$STATE_VOLUME:/state:ro" --entrypoint sha256sum \
  "$SPORTS_DATA_IMAGE" "$copy") || { echo "No se pudo verificar la copia SQLite transferida" >&2; exit 1; }
actual_copy_sha=${actual_copy_sha%% *}
test "$actual_copy_sha" = "$expected_copy_sha" || { echo "La copia SQLite transferida no coincide con la copia verificada de Sports Data" >&2; exit 1; }
docker run --rm --network none -v "$STATE_VOLUME:/state:ro" --entrypoint sh "$SPORTS_DATA_IMAGE" \
  -c "cat '$copy'" > "$OUT/real-jaen.sqlite"
transferred_copy_sha=$(sha256sum "$OUT/real-jaen.sqlite")
transferred_copy_sha=${transferred_copy_sha%% *}
test "$transferred_copy_sha" = "$expected_copy_sha" || { echo "La copia SQLite transferida no coincide con la copia verificada de Sports Data" >&2; exit 1; }
docker run --rm --network none -v "$STATE_VOLUME:/state:ro" --entrypoint sh "$SPORTS_DATA_IMAGE" -c "
set -eu
tar -cf - -C /state real-jaen.sqlite.actualizaciones" > "$OUT/actualizaciones.tar"
if docker run --rm --network none -v "$STATE_VOLUME:/state:ro" --entrypoint sh "$SPORTS_DATA_IMAGE" \
  -c 'test -d /state/procedencia'; then
  docker run --rm --network none -v "$STATE_VOLUME:/state:ro" --entrypoint tar "$SPORTS_DATA_IMAGE" \
    -cf - -C /state procedencia > "$OUT/procedencia.tar"
fi
docker run --rm --network none -v "$JOBS_VOLUME:/jobs:ro" --entrypoint tar "$SPORTS_DATA_IMAGE" \
  -cf - -C /jobs . > "$OUT/acquisition-jobs.tar"
docker run --rm --network none -v "$VIDEO_VOLUME:/video:ro" --entrypoint tar "$SPORTS_DATA_IMAGE" \
  -cf - -C /video . > "$OUT/video-service-state.tar"
if ! docker run --rm --network none -v "$STATE_VOLUME:/state:ro" \
  -v "$ROOT_DIR/$PHOTO_HELPER:/opt/rja/fotos_respaldo.py:ro" \
  --entrypoint sh "$SPORTS_DATA_IMAGE" -c "
set -eu
python -B /opt/rja/fotos_respaldo.py respaldar --state /state --out /tmp/rja-player-photos > /tmp/rja-photo-result.json
if [ -f /tmp/rja-player-photos/fotos.tar ]; then cat /tmp/rja-player-photos/fotos.tar; fi
cat /tmp/rja-player-photos/fotos.json >&2" > "$OUT/.fotos.tar.partial" 2> "$OUT/fotos.json"; then
  cat "$OUT/fotos.json" >&2
  exit 1
fi
if test -s "$OUT/.fotos.tar.partial"; then mv "$OUT/.fotos.tar.partial" "$OUT/fotos.tar"; else rm -f "$OUT/.fotos.tar.partial"; fi
if ! docker run --rm --network none --mount "type=bind,source=$VIDEO_CATALOG_DIR,target=/catalog,readonly" \
  --mount "type=bind,source=$ROOT_DIR/$VIDEO_HELPER,target=/opt/rja/video_catalog_respaldo.py,readonly" \
  --entrypoint sh "$SPORTS_DATA_IMAGE" -c '
set -eu
python -B /opt/rja/video_catalog_respaldo.py respaldar --data /catalog --manifest "$1" --out /tmp/rja-video-catalog > /tmp/rja-video-catalog.json
cat /tmp/rja-video-catalog/video-catalog.tar
cat /tmp/rja-video-catalog.json >&2' sh "$VIDEO_MANIFEST" > "$OUT/.video-catalog.tar.partial" 2> "$OUT/video-catalog.json"; then
  cat "$OUT/video-catalog.json" >&2
  exit 1
fi
mv "$OUT/.video-catalog.tar.partial" "$OUT/video-catalog.tar"

# Keep services stopped while recording metadata for the exact same consistent snapshot.
PHOTO_SUMMARY=$(cat "$OUT/fotos.json")
VIDEO_CATALOG_SUMMARY=$(cat "$OUT/video-catalog.json")
docker run --rm --network none -v "$STATE_VOLUME:/state:ro" \
  -e "RJA_PHOTO_SUMMARY=$PHOTO_SUMMARY" -e "RJA_VIDEO_CATALOG_SUMMARY=$VIDEO_CATALOG_SUMMARY" \
  --entrypoint python "$SPORTS_DATA_IMAGE" -c "
import json, os, sqlite3
from persistencia import sqlite as s
fotos = json.loads(os.environ['RJA_PHOTO_SUMMARY'])
video_catalog = json.loads(os.environ['RJA_VIDEO_CATALOG_SUMMARY'])
d = s.describir('/state/real-jaen.sqlite')
confirmed_db = sqlite3.connect('file:/state/real-jaen.sqlite.actualizaciones/jobs.sqlite?mode=ro&immutable=1', uri=True)
confirmed = json.loads(confirmed_db.execute('SELECT detalle FROM confirmado').fetchone()[0])
confirmed_db.close()
jobs_db = sqlite3.connect('file:/state/real-jaen.sqlite.actualizaciones/control/jobs.sqlite?mode=ro&immutable=1', uri=True)
jobs = jobs_db.execute(\"SELECT count(*), max(json_extract(data,'$.finished_at')) FROM job WHERE state='completed'\").fetchone()
jobs_db.close()
runs = sqlite3.connect('file:/state/real-jaen.sqlite?mode=ro&immutable=1', uri=True).execute('SELECT count(*), max(finished_at) FROM import_run').fetchone()
json.dump(dict(formato_respaldo=2, revision=d['revision'], huella=d['huella'], integridad=d['integridad'],
               generacion=confirmed.get('generacion'), import_runs=runs[0], ultima_importacion=runs[1],
               actualizaciones_publicadas=jobs[0], ultima_publicacion=jobs[1], fotos=fotos,
               catalogo_video=video_catalog),
          __import__('sys').stdout, ensure_ascii=False, indent=2)
print()" > "$OUT/manifest.json"
# Restore the exact original service IDs before accepting the archive as complete.
restart || { echo "No se acepta el respaldo: algún contenedor no ha vuelto a su estado anterior" >&2; exit 1; }
rm -f "$OUT/fotos.json" "$OUT/video-catalog.json"
(cd "$OUT" && sha256sum ./*.sqlite ./*.tar ./*.json > SHA256SUMS && sha256sum -c SHA256SUMS)
# Atomic publication: DEST was required to be absent and WORK is its sibling on the same filesystem.
test ! -e "$DEST" && test ! -L "$DEST" || { echo "El destino apareció durante la copia: no se sobrescribe" >&2; exit 1; }
mv "$WORK" "$DEST"
WORK=""
trap - EXIT INT TERM HUP
echo "Respaldo completo en $DEST"
