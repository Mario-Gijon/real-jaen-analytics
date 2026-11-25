#!/bin/sh
# Restore a complete v2 backup into new Compose-resolved persistent volumes. Does not start services.
set -eu
umask 077
SRC=${1:?Indica la carpeta del respaldo}
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
case "$(uname -s)" in MINGW*|MSYS*) SEP=';' ;; *) SEP=':' ;; esac
COMPOSE_FILES=${COMPOSE_FILES:-${COMPOSE_FILE:-compose.prod.yaml}}
case "$COMPOSE_FILES" in
  compose.dev.yaml|compose.dev.yaml:*|compose.dev.yaml\;*) DEFAULT_ENV=.env.dev ;;
  compose.prod.yaml|compose.prod.yaml:*|compose.prod.yaml\;*) DEFAULT_ENV=.env.prod ;;
  *) echo 'COMPOSE_FILES debe empezar por compose.dev.yaml o compose.prod.yaml' >&2; exit 1 ;;
esac
ENV_FILE=${ENV_FILE:-$DEFAULT_ENV}
case "$ENV_FILE" in /*) ;; *) ENV_FILE="$ROOT_DIR/$ENV_FILE" ;; esac
test -f "$ENV_FILE" && test ! -L "$ENV_FILE" || { echo "ENV_FILE no es un archivo regular: $ENV_FILE" >&2; exit 1; }
ENV_FILE=$(CDPATH= cd -- "$(dirname -- "$ENV_FILE")" && pwd -P)/$(basename -- "$ENV_FILE")
old_ifs=$IFS; IFS=$SEP; set -f
for compose_file in $COMPOSE_FILES; do
  case "$compose_file" in /*) compose_path=$compose_file ;; *) compose_path="$ROOT_DIR/$compose_file" ;; esac
  test -f "$compose_path" && test ! -L "$compose_path" || { echo "Falta Compose: $compose_path" >&2; exit 1; }
done
set +f; IFS=$old_ifs
dc() (cd "$ROOT_DIR" && COMPOSE_FILE="$COMPOSE_FILES" docker compose --project-directory "$ROOT_DIR" --env-file "$ENV_FILE" "$@")
. "$SCRIPT_DIR/instalacion-compose.sh"
resolver_instalacion
case "$(uname -s)" in MINGW*|MSYS*) IMPORT_DIR=$(cygpath -u "$IMPORT_DIR"); VIDEO_CATALOG_DIR=$(cygpath -u "$VIDEO_CATALOG_DIR") ;; esac
test -d "$IMPORT_DIR" && test ! -L "$IMPORT_DIR" && test -w "$IMPORT_DIR" && test -x "$IMPORT_DIR" || {
  echo "RJ_IMPORT_DIR debe ser una carpeta real y escribible para el marcador: $IMPORT_DIR" >&2; exit 1;
}
test ! -L "$SRC" || { echo 'La ruta del backup no puede ser un enlace' >&2; exit 1; }
src_abs=$(CDPATH= cd -- "$SRC" 2>/dev/null && pwd -P) || { echo "No se puede abrir el backup: $SRC" >&2; exit 1; }
imp_abs=$(CDPATH= cd -- "$IMPORT_DIR" && pwd -P)
case "$src_abs/" in "$imp_abs"/*) REL=${src_abs#"$imp_abs"/} ;; *)
  echo "El backup debe estar dentro del directorio de importación resuelto por Compose ($imp_abs)" >&2; exit 1 ;;
esac
case "$REL" in *'..'*|*\\*) echo 'La ruta relativa del backup no es segura' >&2; exit 1 ;; esac

RESTORE_VIDEO_DIR=${RJ_RESTORE_VIDEO_CATALOG_DIR:-}
test -n "$RESTORE_VIDEO_DIR" || { echo 'Define RJ_RESTORE_VIDEO_CATALOG_DIR para elegir el catálogo externo' >&2; exit 1; }
case "$(uname -s)" in MINGW*|MSYS*) RESTORE_VIDEO_DIR=$(cygpath -u "$RESTORE_VIDEO_DIR") ;; esac
catalog_parent=$(CDPATH= cd -- "$(dirname -- "$RESTORE_VIDEO_DIR")" 2>/dev/null && pwd -P) || {
  echo 'El directorio padre del catálogo externo debe existir' >&2; exit 1;
}
test ! -L "$(dirname -- "$RESTORE_VIDEO_DIR")" || { echo 'El padre del catálogo externo no puede ser un enlace' >&2; exit 1; }
catalog_target="$catalog_parent/$(basename -- "$RESTORE_VIDEO_DIR")"
resolved_video=$(CDPATH= cd -- "$(dirname -- "$VIDEO_CATALOG_DIR")" 2>/dev/null && pwd -P)/$(basename -- "$VIDEO_CATALOG_DIR")
test "$catalog_target" = "$resolved_video" || {
  echo "RJ_RESTORE_VIDEO_CATALOG_DIR debe coincidir con la ruta de Compose ($resolved_video)" >&2; exit 1;
}
test ! -e "$catalog_target" && test ! -L "$catalog_target" || { echo "No se sobrescribe el catálogo existente: $catalog_target" >&2; exit 1; }
MARKER="$imp_abs/.real-jaen-restore-incomplete"
test ! -e "$MARKER" && test ! -L "$MARKER" || { echo "Ya existe $MARKER; recupera la restauración anterior" >&2; exit 1; }

# Read-only preflight. No destination volume is mounted or created until the complete v2 package is verified.
docker info >/dev/null 2>&1 || { echo 'No se puede acceder al daemon Docker; el destino no se ha modificado' >&2; exit 1; }
for image in "$SPORTS_DATA_IMAGE" "$ACQUISITION_IMAGE" "$VIDEO_SERVICE_IMAGE"; do
  docker image inspect "$image" >/dev/null 2>&1 || { echo "Falta la imagen resuelta: $image" >&2; exit 1; }
done
compose_json=$(dc config --format json) || { echo 'Compose no pudo resolver la configuración efectiva' >&2; exit 1; }
printf '%s\n' "$compose_json" | python3 "$ROOT_DIR/sports-data/scripts/instalacion_compose.py" --check-dashboard-port >/dev/null || {
  echo 'La configuración de Compose no es válida o el puerto del Dashboard está ocupado' >&2; exit 1;
}
project_containers=$(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT") || { echo 'No se pudieron comprobar contenedores' >&2; exit 1; }
test -z "$project_containers" || { echo "El proyecto $PROJECT ya tiene contenedores" >&2; exit 1; }
old_ifs=$IFS; IFS=','
for volume in $VOLUME_NAMES; do
  if docker volume inspect "$volume" >/dev/null 2>&1; then IFS=$old_ifs; echo "El volumen destino ya existe: $volume" >&2; exit 1; fi
done
for network in $NETWORK_NAMES; do
  if docker network inspect "$network" >/dev/null 2>&1; then IFS=$old_ifs; echo "La red destino ya existe: $network" >&2; exit 1; fi
done
IFS=$old_ifs
all_containers=$(docker ps -aq) || { echo 'No se pudieron comprobar montajes de contenedores' >&2; exit 1; }
paths_overlap() {
  left=$1
  right=$2
  [ -n "$left" ] && [ -n "$right" ] || return 1
  case "$left:$right" in /:*) return 0 ;; *:/) return 0 ;; esac
  case "$left" in "$right"|"$right"/*) return 0 ;; esac
  case "$right" in "$left"|"$left"/*) return 0 ;; esac
  return 1
}
if [ -n "$all_containers" ]; then
  for container in $all_containers; do
    sources=$(docker inspect -f '{{range .Mounts}}{{println .Source}}{{end}}' "$container") || { echo "No se pudo inspeccionar $container" >&2; exit 1; }
    while IFS= read -r source; do
      [ -n "$source" ] || continue
      if paths_overlap "$imp_abs" "$source"; then
        echo "El directorio imports se solapa con un montaje activo ($source)" >&2; exit 1
      fi
      if paths_overlap "$catalog_target" "$source"; then
        echo "El catálogo se solapa con un montaje activo ($source)" >&2; exit 1
      fi
    done <<EOF
$sources
EOF
  done
fi
case "$(uname -s)" in
  MINGW*|MSYS*) backup_docker=$(cygpath -m "$src_abs"); helper_docker=$(cygpath -m "$ROOT_DIR/sports-data/scripts/restauracion_backup.py") ;;
  *) backup_docker=$src_abs; helper_docker="$ROOT_DIR/sports-data/scripts/restauracion_backup.py" ;;
esac
docker run --rm --network none --user 10001:10001 \
  --mount "type=bind,source=$backup_docker,target=/backup,readonly" \
  --mount "type=bind,source=$helper_docker,target=/app/restauracion_backup.py,readonly" \
  --env PYTHONPATH=/app --entrypoint python "$SPORTS_DATA_IMAGE" -B /app/restauracion_backup.py validar --backup /backup >/dev/null || {
  echo 'El backup v2 no supera las comprobaciones de manifiesto, SQLite, SHA-256 o tar' >&2; exit 1;
}
PHOTO_INCLUDED=$(python3 -c 'import json,sys; print(str(json.load(open(sys.argv[1],encoding="utf-8"))["fotos"]["incluidas"]).lower())' "$src_abs/manifest.json")
VIDEO_MANIFEST=$(printf '%s\n' "$compose_json" | python3 -c 'import json,sys; c=json.load(sys.stdin); print(c["services"]["video-service"].get("environment",{}).get("VIDEO_SERVICE_MANIFEST","videos.json"))')
case "$VIDEO_MANIFEST" in /*|*\\*|../*|*/../*|*/..) echo 'VIDEO_SERVICE_MANIFEST no es una ruta relativa segura' >&2; exit 1 ;; esac
photo_helper="$ROOT_DIR/sports-data/scripts/fotos_respaldo.py"
video_helper="$ROOT_DIR/dashboard/scripts/video-catalog-respaldo.py"
case "$(uname -s)" in MINGW*|MSYS*) photo_helper=$(cygpath -m "$photo_helper"); video_helper=$(cygpath -m "$video_helper") ;; esac
if [ "$PHOTO_INCLUDED" = true ]; then
  docker run --rm --network none --user 10001:10001 --mount "type=bind,source=$backup_docker,target=/backup,readonly" \
    --mount "type=bind,source=$photo_helper,target=/app/fotos_respaldo.py,readonly" \
    --entrypoint python "$SPORTS_DATA_IMAGE" -B /app/fotos_respaldo.py validar --tar /backup/fotos.tar --manifest /backup/manifest.json >/dev/null || {
      echo 'fotos.tar no coincide con el manifiesto o tiene blobs/SQLite no válidos' >&2; exit 1;
    }
fi
docker run --rm --network none --user 10001:10001 --mount "type=bind,source=$backup_docker,target=/backup,readonly" \
  --mount "type=bind,source=$video_helper,target=/app/video-catalog-respaldo.py,readonly" \
  --entrypoint python "$SPORTS_DATA_IMAGE" -B /app/video-catalog-respaldo.py validar --tar /backup/video-catalog.tar --manifest /backup/manifest.json >/dev/null || {
    echo 'video-catalog.tar no coincide con el manifiesto o no es un catálogo válido' >&2; exit 1;
  }

# This external gate is created before the first volume. The trap deliberately leaves it and all partial volumes.
(set -C; printf 'project=%s\nbackup=%s\nvolumes=%s %s %s %s\nvideo_catalog=%s\n' \
  "$PROJECT" "$REL" "$STATE_VOLUME" "$JOBS_VOLUME" "$PROFILE_VOLUME" "$VIDEO_VOLUME" "$catalog_target" > "$MARKER") || {
  echo 'No se pudo crear el marcador; no se ha creado ningún volumen' >&2; exit 1;
}
restore_failed=1
trap 'status=$?; if [ "$restore_failed" -eq 1 ]; then echo "Restauración incompleta: conserva marcador y volúmenes para recuperación manual ($MARKER)" >&2; fi; exit "$status"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
create_volume() {
  docker volume create --label "com.docker.compose.project=$PROJECT" --label "com.docker.compose.volume=$2" \
    --label "rj.installation=$PROJECT" "$1" >/dev/null
  echo "Creado volumen nuevo: $1"
}
create_volume "$STATE_VOLUME" sports-state
docker run --rm --network none --user 0:0 --mount "type=volume,source=$STATE_VOLUME,target=/state,volume-nocopy" \
  --entrypoint python "$SPORTS_DATA_IMAGE" -c "from pathlib import Path; p=Path('/state/real-jaen.sqlite.restauracion-incompleta'); p.open('x').write('restore incomplete'); p.chmod(0o444)"
create_volume "$VIDEO_VOLUME" video-state
docker run --rm --network none --user 0:0 --mount "type=volume,source=$VIDEO_VOLUME,target=/state,volume-nocopy" \
  --entrypoint python "$VIDEO_SERVICE_IMAGE" -c "from pathlib import Path; p=Path('/state/.real-jaen-restore-incomplete'); p.open('x').write('restore incomplete'); p.chmod(0o444)"
create_volume "$JOBS_VOLUME" acquisition-jobs
create_volume "$PROFILE_VOLUME" acquisition-profiles
docker run --rm --network none --user 0:0 \
  --mount "type=volume,source=$STATE_VOLUME,target=/state,volume-nocopy" --mount "type=volume,source=$JOBS_VOLUME,target=/jobs,volume-nocopy" \
  --mount "type=volume,source=$PROFILE_VOLUME,target=/profiles,volume-nocopy" --mount "type=volume,source=$VIDEO_VOLUME,target=/video,volume-nocopy" \
  --entrypoint python "$SPORTS_DATA_IMAGE" -c "import os; [os.chown(p,10001,10001) for p in ('/state','/jobs','/profiles','/video')]"

docker run --rm --network none --user 10001:10001 --mount "type=volume,source=$STATE_VOLUME,target=/state" \
  --mount "type=bind,source=$backup_docker,target=/backup,readonly" \
  --entrypoint python "$SPORTS_DATA_IMAGE" -B -m scripts.initialize_snapshot /backup/real-jaen.sqlite /state/real-jaen.sqlite
docker run --rm --network none --user 10001:10001 --mount "type=volume,source=$STATE_VOLUME,target=/state" \
  --mount "type=bind,source=$backup_docker,target=/backup,readonly" \
  --mount "type=bind,source=$helper_docker,target=/app/restauracion_backup.py,readonly" \
  --entrypoint python "$SPORTS_DATA_IMAGE" -B /app/restauracion_backup.py extraer \
  --archive /backup/actualizaciones.tar --destination /state/.restore-actualizaciones \
  --prefix real-jaen.sqlite.actualizaciones --strip-prefix
docker run --rm --network none --user 10001:10001 --mount "type=volume,source=$STATE_VOLUME,target=/state" \
  --entrypoint python "$SPORTS_DATA_IMAGE" -c "from pathlib import Path; Path('/state/.restore-actualizaciones').rename('/state/real-jaen.sqlite.actualizaciones')"
if [ -f "$src_abs/procedencia.tar" ]; then
  docker run --rm --network none --user 10001:10001 --mount "type=volume,source=$STATE_VOLUME,target=/state" \
    --mount "type=bind,source=$backup_docker,target=/backup,readonly" --mount "type=bind,source=$helper_docker,target=/app/restauracion_backup.py,readonly" \
    --entrypoint python "$SPORTS_DATA_IMAGE" -B /app/restauracion_backup.py extraer \
    --archive /backup/procedencia.tar --destination /state/.restore-procedencia --prefix procedencia --strip-prefix
  docker run --rm --network none --user 10001:10001 --mount "type=volume,source=$STATE_VOLUME,target=/state" \
    --entrypoint python "$SPORTS_DATA_IMAGE" -c "from pathlib import Path; s=Path('/state/.restore-procedencia'); [p.chmod(p.stat().st_mode & ~0o222) for p in s.rglob('*') if p.is_file()]; [p.chmod(p.stat().st_mode & ~0o222) for p in s.rglob('*') if p.is_dir()]; s.chmod(s.stat().st_mode & ~0o222); s.rename('/state/procedencia')"
fi
if [ "$PHOTO_INCLUDED" = true ]; then
  docker run --rm --network none --user 10001:10001 --mount "type=volume,source=$STATE_VOLUME,target=/state" \
    --mount "type=bind,source=$backup_docker,target=/backup,readonly" --mount "type=bind,source=$photo_helper,target=/app/fotos_respaldo.py,readonly" \
    --entrypoint python "$SPORTS_DATA_IMAGE" -B /app/fotos_respaldo.py restaurar --state /state --tar /backup/fotos.tar --manifest /backup/manifest.json
fi
docker run --rm --network none --user 10001:10001 --mount "type=volume,source=$STATE_VOLUME,target=/state" \
  --entrypoint python "$SPORTS_DATA_IMAGE" -B -m persistencia verificar --base /state/real-jaen.sqlite >/dev/null
docker run --rm --network none --user 10001:10001 --mount "type=volume,source=$JOBS_VOLUME,target=/acquisition-jobs" \
  --mount "type=bind,source=$backup_docker,target=/backup,readonly" --mount "type=bind,source=$helper_docker,target=/app/restauracion_backup.py,readonly" \
  --entrypoint python "$SPORTS_DATA_IMAGE" -B /app/restauracion_backup.py extraer --archive /backup/acquisition-jobs.tar --destination /acquisition-jobs --strip-root
docker run --rm --network none --user 10001:10001 --mount "type=volume,source=$STATE_VOLUME,target=/state" \
  --mount "type=volume,source=$JOBS_VOLUME,target=/jobs" --mount "type=bind,source=$backup_docker,target=/backup,readonly" \
  --entrypoint python "$SPORTS_DATA_IMAGE" -B -c "
import json, sqlite3
from contextlib import closing
from persistencia.actualizacion import Coordinador
expected = json.load(open('/backup/manifest.json', encoding='utf-8'))['generacion']
with Coordinador('/state/real-jaen.sqlite', '/app/ambito.json', reserva=0) as coordinator:
    actual = coordinator.capturar().id
assert actual == expected, (actual, expected)
for path in ('/jobs/jobs.sqlite', '/state/real-jaen.sqlite.actualizaciones/control/jobs.sqlite'):
    with closing(sqlite3.connect('file:' + path + '?mode=ro', uri=True)) as database:
        assert database.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', path
print('Generation accepted; Acquisition SQLite integrity verified.')"
docker run --rm --network none --user 10001:10001 --mount "type=volume,source=$VIDEO_VOLUME,target=/video" \
  --mount "type=bind,source=$backup_docker,target=/backup,readonly" --mount "type=bind,source=$helper_docker,target=/app/restauracion_backup.py,readonly" \
  --entrypoint python "$SPORTS_DATA_IMAGE" -B /app/restauracion_backup.py extraer --archive /backup/video-service-state.tar --destination /video/.restore-state --strip-root
docker run --rm --network none --user 10001:10001 --mount "type=volume,source=$VIDEO_VOLUME,target=/video" \
  --entrypoint python "$SPORTS_DATA_IMAGE" -c "
from pathlib import Path
root = Path('/video')
stage = root / '.restore-state'
for item in sorted(stage.iterdir()):
    target = root / item.name
    if target.exists() or target.is_symlink():
        raise FileExistsError(target)
    item.rename(target)
stage.rmdir()"
docker run --rm --network none --user 10001:10001 --mount "type=volume,source=$VIDEO_VOLUME,target=/state:ro" \
  --entrypoint python "$VIDEO_SERVICE_IMAGE" -c "import sqlite3; d=sqlite3.connect('file:/state/video-service.sqlite?mode=ro',uri=True); assert d.execute('PRAGMA integrity_check').fetchone()[0]=='ok'"
docker run --rm --network none --user 10001:10001 --mount "type=volume,source=$JOBS_VOLUME,target=/jobs:ro" \
  --entrypoint python "$SPORTS_DATA_IMAGE" -c "import sqlite3; d=sqlite3.connect('file:/jobs/jobs.sqlite?mode=ro',uri=True); assert d.execute('PRAGMA integrity_check').fetchone()[0]=='ok'"

case "$(uname -s)" in MINGW*|MSYS*) video_helper=$(cygpath -m "$video_helper"); catalog_parent_docker=$(cygpath -m "$catalog_parent") ;; *) catalog_parent_docker=$catalog_parent ;; esac
docker run --rm --network none --user 0:0 --mount "type=bind,source=$backup_docker,target=/backup,readonly" \
  --mount "type=bind,source=$catalog_parent_docker,target=/catalog-parent" --mount "type=bind,source=$video_helper,target=/app/video-catalog-respaldo.py,readonly" \
  --entrypoint python "$SPORTS_DATA_IMAGE" -B /app/video-catalog-respaldo.py restaurar --tar /backup/video-catalog.tar \
  --target "/catalog-parent/$(basename -- "$catalog_target")" --manifest /backup/manifest.json
docker run --rm --network none --user 10001:10001 --mount "type=bind,source=$catalog_parent_docker,target=/catalog-parent,readonly" \
  --env "VIDEO_SERVICE_MANIFEST=$VIDEO_MANIFEST" --entrypoint python "$VIDEO_SERVICE_IMAGE" -c \
  "import os; from pathlib import Path; from video_service.catalog import VideoCatalog; c=VideoCatalog.load(Path('/catalog-parent/$(basename -- "$catalog_target")'), os.environ['VIDEO_SERVICE_MANIFEST']); print(len(c.list()))"
docker run --rm --network none --user 10001:10001 --mount "type=volume,source=$PROFILE_VOLUME,target=/profiles" \
  --entrypoint python "$ACQUISITION_IMAGE" -B -m acquisition.admin initialize-profile --confirm-new-profile

# Guards are removed only after successful validation; the external marker is the last gate removed.
docker run --rm --network none --user 10001:10001 --mount "type=volume,source=$STATE_VOLUME,target=/state" \
  --entrypoint python "$SPORTS_DATA_IMAGE" -B -m persistencia verificar --base /state/real-jaen.sqlite >/dev/null
docker run --rm --network none --user 0:0 --mount "type=volume,source=$STATE_VOLUME,target=/state" \
  --entrypoint python "$SPORTS_DATA_IMAGE" -c "from pathlib import Path; Path('/state/real-jaen.sqlite.restauracion-incompleta').unlink()"
docker run --rm --network none --user 0:0 --mount "type=volume,source=$VIDEO_VOLUME,target=/state" \
  --entrypoint python "$VIDEO_SERVICE_IMAGE" -c "from pathlib import Path; Path('/state/.real-jaen-restore-incomplete').unlink()"
rm -f -- "$MARKER"
trap - EXIT INT TERM HUP
echo "Restauración v2 validada sin arrancar servicios. Proyecto: $PROJECT. Catálogo: $catalog_target."
echo 'Se han creado únicamente los cuatro volúmenes persistentes; Compose creará redes y caché del Dashboard al iniciar.'
