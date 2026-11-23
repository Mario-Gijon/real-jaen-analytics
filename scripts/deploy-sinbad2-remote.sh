#!/bin/sh
set -eu
umask 077

fail() {
  printf 'SINBAD2 deploy: %s\n' "$1" >&2
  exit 1
}

mode=${1:-}
release_id=${2:-}
commit_sha=${3:-}
pipeline_id=${4:-}
case "$mode" in deploy|bootstrap) ;; *) fail 'invalid deployment mode' ;; esac
case "$release_id" in *[!a-zA-Z0-9_-]*|'') fail 'invalid release identifier' ;; esac
case "$commit_sha" in *[!a-fA-F0-9]*|'') fail 'invalid commit identifier' ;; esac
case "$pipeline_id" in *[!0-9]*|'') fail 'invalid pipeline identifier' ;; esac

root="$HOME/deploy_real-jaen-analytics"
release="$root/releases/$release_id"
incoming="$root/shared/incoming/$release_id"
config_base="$root/shared/config"
secrets_base="$root/shared/secrets"
imports="$root/shared/imports"
catalog="$root/shared/video-catalog"
state_dir="$root/shared/deploy-state"
last_good="$state_dir/last-good"
deployment_finalized=0
has_previous=0
previous_config_target=
previous_secret_target=
previous_release=
previous_commit=
links_promoted=0
containers_mutated=0
maintenance_active=0
network_detached=0
acquisition_drain=0
preserve_candidate_state=0
recovery_complete=0
deployment_ready=0
config_temp="$config_base/versions/.$release_id.tmp"
secret_temp="$secrets_base/versions/.$release_id.tmp"
metadata_tmp="$state_dir/.last-good-$release_id"

safe_link() {
  base=$1
  target=$2
  case "$target" in versions/*) ;; *) return 1 ;; esac
  case "/$target/" in */../*|*//*) return 1 ;; esac
  [ -d "$base/$target" ] && [ ! -L "$base/$target" ]
}

set_current_link() {
  base=$1
  target=$2
  temporary="$base/.current-$release_id"
  rm -f "$temporary"
  ln -s "$target" "$temporary"
  mv -Tf "$temporary" "$base/current"
}

restore_previous_links() {
  [ "$has_previous" -eq 1 ] || return 0
  if safe_link "$config_base" "$previous_config_target"; then
    set_current_link "$config_base" "$previous_config_target"
  fi
  if safe_link "$secrets_base" "$previous_secret_target"; then
    set_current_link "$secrets_base" "$previous_secret_target"
  fi
  links_promoted=0
}

ensure_uid_read_directory() {
  path=$1
  if [ -e "$path" ]; then
    [ -d "$path" ] && [ ! -L "$path" ] || fail 'shared imports/catalog path is not a real directory'
    return 0
  fi
  mkdir -m 700 "$path" || fail 'could not create shared imports/catalog directory'
  chmod 755 "$path" || fail 'could not make new shared imports/catalog directory readable'
}

cleanup() {
  status=$?
  trap - EXIT
  if [ ! -L "$root" ] && [ ! -L "$root/shared" ] && [ ! -L "$root/shared/incoming" ]; then
    rm -rf "$incoming" || true
  fi
  if [ ! -L "$root" ] && [ ! -L "$root/shared" ] && [ ! -L "$config_base" ] \
      && [ ! -L "$config_base/versions" ]; then
    rm -rf "$config_temp" || true
  fi
  if [ ! -L "$root" ] && [ ! -L "$root/shared" ] && [ ! -L "$secrets_base" ] \
      && [ ! -L "$secrets_base/versions" ]; then
    rm -rf "$secret_temp" || true
  fi
  if [ ! -L "$root" ] && [ ! -L "$root/shared" ] && [ ! -L "$state_dir" ]; then
    rm -f "$metadata_tmp" || true
  fi
  if [ "$deployment_ready" -eq 1 ] && [ -f "$last_good" ] \
      && grep -Fqx "release_id=$release_id" "$last_good"; then
    deployment_finalized=1
  fi
  if [ "$deployment_finalized" -eq 0 ] && [ "$recovery_complete" -eq 0 ]; then
    if [ "$containers_mutated" -eq 1 ] && [ "$has_previous" -eq 1 ]; then
      rollback_candidate || preserve_candidate_state=1
    elif [ "$maintenance_active" -eq 1 ] && [ "$preserve_candidate_state" -eq 0 ]; then
      if clear_maintenance; then
        resume_previous_sports_data || {
          preserve_candidate_state=1
          printf 'SINBAD2 deploy: could not restore the Sports Data network; admission remains closed for inspection\n' >&2
        }
      else
        preserve_candidate_state=1
        printf 'SINBAD2 deploy: maintenance fence could not be cleared; admission remains closed for inspection\n' >&2
      fi
    elif [ "$network_detached" -eq 1 ] && [ "$preserve_candidate_state" -eq 0 ]; then
      resume_previous_sports_data || {
        preserve_candidate_state=1
        printf 'SINBAD2 deploy: could not restore the Sports Data network; admission remains closed for inspection\n' >&2
      }
    fi
  fi
  if [ "$deployment_finalized" -eq 0 ] && [ "$links_promoted" -eq 1 ] \
      && [ "$preserve_candidate_state" -eq 0 ]; then
    restore_previous_links || true
  fi
  exit "$status"
}
trap cleanup EXIT

for path in "$root" "$root/releases" "$root/shared" "$config_base" "$secrets_base" \
  "$config_base/versions" "$secrets_base/versions" "$imports" "$catalog" \
  "$root/shared/incoming" "$state_dir"; do
  [ ! -L "$path" ] || fail 'deployment path contains a symbolic link'
done
mkdir -p "$root/releases" "$root/shared" "$config_base/versions" "$secrets_base/versions" \
  "$root/shared/incoming" "$state_dir"
chmod 700 "$root" "$root/shared" "$config_base" "$secrets_base" \
  "$config_base/versions" "$secrets_base/versions" "$root/shared/incoming" "$state_dir"
ensure_uid_read_directory "$imports"
ensure_uid_read_directory "$catalog"

[ -d "$release" ] && [ ! -L "$release" ] || fail 'code release directory is missing'
[ -f "$release/compose.prod.yaml" ] || fail 'production Compose file is missing from the release'
[ -f "$release/dashboard/scripts/restaurar-instalacion.sh" ] || fail 'restore script is missing from the release'
[ -f "$release/video-service/data/videos.example.json" ] || fail 'empty video catalog fixture is missing'
[ -d "$incoming" ] && [ ! -L "$incoming" ] || fail 'private configuration bundle is missing'

if [ -f "$last_good" ]; then
  previous_pipeline=$(sed -n 's/^pipeline_id=//p' "$last_good" | tail -n 1)
  previous_release=$(sed -n 's/^release_id=//p' "$last_good" | tail -n 1)
  previous_commit=$(sed -n 's/^commit_sha=//p' "$last_good" | tail -n 1)
  previous_config_target=$(sed -n 's/^config_target=//p' "$last_good" | tail -n 1)
  previous_secret_target=$(sed -n 's/^secret_target=//p' "$last_good" | tail -n 1)
  case "$previous_pipeline:$previous_release:$previous_commit" in
    *[!a-zA-Z0-9:_-]*|'') fail 'last healthy deployment metadata is invalid' ;;
  esac
  safe_link "$config_base" "$previous_config_target" || fail 'last healthy config version is missing'
  safe_link "$secrets_base" "$previous_secret_target" || fail 'last healthy secret version is missing'
  case "$previous_pipeline" in *[!0-9]*|'') fail 'last healthy pipeline identifier is invalid' ;; esac
  has_previous=1
  if [ "$pipeline_id" -lt "$previous_pipeline" ]; then
    printf 'Skipping stale pipeline %s; pipeline %s is already healthy.\n' "$pipeline_id" "$previous_pipeline"
    deployment_finalized=1
    exit 0
  fi
fi

bundle_env="$incoming/.env.prod"
bundle_secrets="$incoming/secrets"
[ -f "$bundle_env" ] && [ ! -L "$bundle_env" ] || fail 'ENV_PROD is missing from the private bundle'
[ -d "$bundle_secrets" ] && [ ! -L "$bundle_secrets" ] || fail 'secret files are missing from the private bundle'
for secret_file in access-password update-password control-token acquisition-token; do
  [ -f "$bundle_secrets/$secret_file" ] && [ ! -L "$bundle_secrets/$secret_file" ] \
    || fail 'a required application secret is missing from the private bundle'
done
[ -d "$bundle_secrets/session" ] && [ ! -L "$bundle_secrets/session" ] \
  || fail 'session secret directory is missing from the private bundle'
[ -f "$bundle_secrets/session/session-secret" ] \
  && [ ! -L "$bundle_secrets/session/session-secret" ] \
  || fail 'session secret is missing from the private bundle'

require_line() {
  expected=$1
  key=${expected%%=*}
  declarations=$(grep -Ec "^[[:space:]]*(export[[:space:]]+)?${key}[[:space:]]*=" "$bundle_env" || true)
  [ "$declarations" = 1 ] || fail "ENV_PROD declares critical key $key more than once"
  count=$(grep -Fxc "$expected" "$bundle_env" || true)
  [ "$count" = 1 ] || fail "ENV_PROD critical setting $key does not match the required production value"
}

require_line 'COMPOSE_PROJECT_NAME=real-jaen-analytics-sinbad2-prod'
require_line 'RJ_PROD_NETWORK_PREFIX=rja-sinbad2-prod'
require_line 'RJ_PROD_SPORTS_STATE_VOLUME=rja-sinbad2-sports-state'
require_line 'RJ_PROD_ACQUISITION_PROFILES_VOLUME=rja-sinbad2-acquisition-profiles'
require_line 'RJ_PROD_ACQUISITION_JOBS_VOLUME=rja-sinbad2-acquisition-jobs'
require_line 'RJ_PROD_VIDEO_STATE_VOLUME=rja-sinbad2-video-state'
require_line 'RJ_DASHBOARD_BIND=0.0.0.0'
require_line 'RJ_DASHBOARD_PORT=8120'
require_line 'RJ_PROD_SECRET_DIR=${RJA_DEPLOY_ROOT:?Set by the deployment job}/shared/secrets/current'
require_line 'RJ_IMPORT_DIR=${RJA_DEPLOY_ROOT:?Set by the deployment job}/shared/imports'
require_line 'RJ_VIDEO_CATALOG_DIR=${RJA_DEPLOY_ROOT:?Set by the deployment job}/shared/video-catalog'
require_line 'RJ_BOOTSTRAP_BACKUP_DIR=${RJA_DEPLOY_ROOT:?Set by the deployment job}/shared/imports/initial-backup'
require_line 'RJ_VIDEO_MANIFEST=videos.json'
if grep -Eq '^(APP_ACCESS_PASSWORD|APP_UPDATE_PASSWORD|APP_SESSION_SECRET|APP_CONTROL_TOKEN|APP_ACQUISITION_TOKEN)=' "$bundle_env"; then
  fail 'application credentials belong in File variables, not ENV_PROD'
fi

config_current="$config_base/current"
secret_current="$secrets_base/current"
config_target=
secret_target=
config_changed=1
access_changed=1
update_changed=1
session_changed=1
control_changed=1
acquisition_changed=1
if [ -e "$config_current" ] || [ -L "$config_current" ]; then
  [ -L "$config_current" ] || fail 'current config path is not a managed symlink'
  safe_link "$config_base" "$(readlink "$config_current")" || fail 'current config link is invalid'
fi
if [ -e "$secret_current" ] || [ -L "$secret_current" ]; then
  [ -L "$secret_current" ] || fail 'current secret path is not a managed symlink'
  safe_link "$secrets_base" "$(readlink "$secret_current")" || fail 'current secret link is invalid'
fi
if [ "$has_previous" -eq 1 ]; then
  [ -L "$config_current" ] && [ "$(readlink "$config_current")" = "$previous_config_target" ] \
    || fail 'current config does not match last-good; inspect service and metadata state before retrying'
  [ -L "$secret_current" ] && [ "$(readlink "$secret_current")" = "$previous_secret_target" ] \
    || fail 'current secrets do not match last-good; inspect service and metadata state before retrying'
fi
if [ -L "$config_current" ] && cmp -s "$config_current/.env.prod" "$bundle_env"; then
  config_target=$(readlink "$config_current")
  config_changed=0
else
  config_target="versions/$release_id"
  [ ! -e "$config_base/$config_target" ] || fail 'config version already exists'
  [ ! -e "$config_temp" ] || fail 'temporary config version already exists'
  mkdir -m 700 "$config_temp"
  cp "$bundle_env" "$config_temp/.env.prod"
  chmod 600 "$config_temp/.env.prod"
  mv "$config_temp" "$config_base/$config_target"
fi

same_secrets=0
if [ -L "$secret_current" ]; then
  same_secrets=1
  cmp -s "$secret_current/access-password" "$bundle_secrets/access-password" && access_changed=0 || same_secrets=0
  cmp -s "$secret_current/update-password" "$bundle_secrets/update-password" && update_changed=0 || same_secrets=0
  cmp -s "$secret_current/session/session-secret" "$bundle_secrets/session/session-secret" && session_changed=0 || same_secrets=0
  cmp -s "$secret_current/control-token" "$bundle_secrets/control-token" && control_changed=0 || same_secrets=0
  cmp -s "$secret_current/acquisition-token" "$bundle_secrets/acquisition-token" && acquisition_changed=0 || same_secrets=0
fi
secrets_changed=$((access_changed + update_changed + session_changed + control_changed + acquisition_changed))
if [ "$same_secrets" -eq 1 ]; then
  secret_target=$(readlink "$secret_current")
else
  secret_target="versions/$release_id"
  [ ! -e "$secrets_base/$secret_target" ] || fail 'secret version already exists'
  [ ! -e "$secret_temp" ] || fail 'temporary secret version already exists'
  mkdir -m 700 "$secret_temp"
  mv "$bundle_secrets/access-password" "$secret_temp/access-password"
  mv "$bundle_secrets/update-password" "$secret_temp/update-password"
  mv "$bundle_secrets/control-token" "$secret_temp/control-token"
  mv "$bundle_secrets/acquisition-token" "$secret_temp/acquisition-token"
  mkdir -m 700 "$secret_temp/session"
  mv "$bundle_secrets/session/session-secret" "$secret_temp/session/session-secret"
  chmod 444 "$secret_temp/access-password" "$secret_temp/update-password" \
    "$secret_temp/control-token" "$secret_temp/acquisition-token" \
    "$secret_temp/session/session-secret"
  chmod 555 "$secret_temp/session"
  chmod 700 "$secret_temp"
  mv "$secret_temp" "$secrets_base/$secret_target"
fi

export RJA_DEPLOY_ROOT="$root"
export RJ_IMAGE_TAG="$commit_sha"
env_file="$config_base/$config_target/.env.prod"
secret_dir="$secrets_base/$secret_target"
export RJ_PROD_SECRET_DIR="$secret_dir"

service_list=
force_recreate=0
code_changed=1
if [ "$has_previous" -eq 1 ] && [ "$previous_commit" = "$commit_sha" ]; then
  code_changed=0
fi
append_service() {
  case " $service_list " in
    *" $1 "*) ;;
    *) service_list="${service_list}${service_list:+ }$1" ;;
  esac
}
if [ "$code_changed" -eq 1 ] || [ "$config_changed" -eq 1 ]; then
  service_list='dashboard sports-data acquisition video-service'
  force_recreate=1
else
  if [ "$access_changed" -eq 1 ] || [ "$update_changed" -eq 1 ] || [ "$session_changed" -eq 1 ]; then
    append_service dashboard
  fi
  if [ "$control_changed" -eq 1 ]; then
    append_service dashboard
    append_service sports-data
  fi
  if [ "$acquisition_changed" -eq 1 ]; then
    append_service sports-data
    append_service acquisition
  fi
  if [ "$secrets_changed" -gt 0 ]; then
    force_recreate=1
  fi
fi
needs_update=1
if [ "$code_changed" -eq 0 ] && [ "$config_changed" -eq 0 ] && [ "$secrets_changed" -eq 0 ]; then
  needs_update=0
fi
if [ -z "$service_list" ]; then
  service_list='dashboard sports-data acquisition video-service'
fi
case " $service_list " in *' acquisition '*) recreate_acquisition=1 ;; *) recreate_acquisition=0 ;; esac
if [ "$force_recreate" -eq 0 ]; then
  recreate_acquisition=0
fi
network_prefix=$(sed -n 's/^RJ_PROD_NETWORK_PREFIX=//p' "$env_file" | tail -n 1)
case "$network_prefix" in *[!a-zA-Z0-9_.-]*|'') fail 'production network prefix is invalid' ;; esac
control_network="$network_prefix-acquisition-control"

compose_version=$(docker compose version --short 2>/dev/null) || fail 'Docker Compose is unavailable on the host'
version=${compose_version#v}
major=${version%%.*}
rest=${version#*.}
minor=${rest%%.*}
case "$major:$minor" in *[!0-9:]*|:*) fail 'Docker Compose version could not be parsed' ;; esac
if [ "$major" -lt 2 ] || { [ "$major" -eq 2 ] && [ "$minor" -lt 17 ]; }; then
  fail 'Docker Compose 2.17 or newer is required'
fi
docker info >/dev/null 2>&1 || fail 'Docker daemon is unavailable to the SSH account'

dc() {
  compose_dir=$1
  shift
  docker compose --project-directory "$compose_dir" --env-file "$env_file" \
    -f "$compose_dir/compose.prod.yaml" "$@"
}

dc "$release" config -q >/dev/null 2>&1 || fail 'production Compose configuration is invalid'

volume_value() {
  value=$(sed -n "s/^$1=//p" "$env_file" | tail -n 1)
  case "$value" in
    ''|*[!a-zA-Z0-9_.-]*) fail 'a persistent volume name is missing or invalid' ;;
  esac
  printf '%s' "$value"
}

sports_volume=$(volume_value RJ_PROD_SPORTS_STATE_VOLUME)
profile_volume=$(volume_value RJ_PROD_ACQUISITION_PROFILES_VOLUME)
jobs_volume=$(volume_value RJ_PROD_ACQUISITION_JOBS_VOLUME)
video_volume=$(volume_value RJ_PROD_VIDEO_STATE_VOLUME)
present=0
missing=0
for volume in "$sports_volume" "$profile_volume" "$jobs_volume" "$video_volume"; do
  if docker volume inspect "$volume" >/dev/null 2>&1; then
    present=$((present + 1))
  else
    missing=$((missing + 1))
  fi
done

backup_dir="$imports/initial-backup"
if [ "$mode" = bootstrap ]; then
  [ "$present" -eq 0 ] || fail 'bootstrap refused: at least one target volume already exists; no volume was restored or removed'
  [ -d "$backup_dir" ] || fail 'bootstrap backup is missing; place a verified backup in shared/imports/initial-backup before retrying'
  for required in real-jaen.sqlite actualizaciones.tar acquisition-jobs.tar video-service-state.tar manifest.json SHA256SUMS; do
    [ -f "$backup_dir/$required" ] || fail 'bootstrap backup is incomplete; no volume was created'
  done
  (cd "$backup_dir" && sha256sum -c SHA256SUMS >/dev/null 2>&1) \
    || fail 'bootstrap backup checksum verification failed; no volume was created'
else
  if [ "$missing" -gt 0 ]; then
    if [ "$present" -eq 0 ]; then
      fail 'installation is not initialized; use the separate manual bootstrap after placing the verified backup under shared/imports/initial-backup; no volume was created'
    fi
    fail 'only some persistent volumes exist; deployment stopped without creating, restoring, or removing any volume'
  fi
fi

if [ "$code_changed" -eq 1 ]; then
  dc "$release" build sports-data acquisition video-service dashboard \
    || fail 'image build failed; running services were not changed'
fi

sports_image="local/real-jaen-analytics/sports-data:$commit_sha"
acquisition_image="local/real-jaen-analytics/acquisition:$commit_sha"
video_image="local/real-jaen-analytics/video-service:$commit_sha"
dashboard_image="local/real-jaen-analytics/dashboard:$commit_sha"

docker run --rm --network none --user 10001:10001 -v "$imports:/imports:ro" \
  --entrypoint python "$sports_image" -B -c \
  "import os; os.listdir('/imports')" \
  >/dev/null 2>&1 || fail 'shared/imports is not readable and traversable by UID 10001'

if [ "$mode" = bootstrap ]; then
  docker run --rm --network none --user 10001:10001 -v "$imports:/imports:ro" \
    --entrypoint python "$sports_image" -B -c \
    "import os; root='/imports'; backup=root+'/initial-backup'; os.listdir(root); os.listdir(backup); names=['real-jaen.sqlite','actualizaciones.tar','acquisition-jobs.tar','video-service-state.tar','manifest.json','SHA256SUMS']; names += ['procedencia.tar'] if os.path.isfile(backup+'/procedencia.tar') else []; [os.close(os.open(backup+'/'+name, os.O_RDONLY)) for name in names]" \
    >/dev/null 2>&1 || fail 'bootstrap backup is not readable by UID 10001; no volume was restored'
fi

docker run --rm --network none --user 10001:10001 -v "$catalog:/app/data:ro" \
  --entrypoint python "$video_image" -B -c \
  "import os; os.listdir('/app/data')" \
  >/dev/null 2>&1 || fail 'shared/video-catalog is not readable and traversable by UID 10001'

manifest="$catalog/videos.json"
if [ -e "$manifest" ] || [ -L "$manifest" ]; then
  [ -f "$manifest" ] && [ ! -L "$manifest" ] \
    || fail 'existing video catalog manifest is not a regular file; existing catalog data was left untouched'
else
  first_entry=$(find "$catalog" -mindepth 1 -print -quit)
  [ -z "$first_entry" ] || fail 'video catalog has files but no videos.json; existing catalog data was left untouched'
  temporary="$catalog/.videos.json-$release_id"
  [ ! -e "$temporary" ] && [ ! -L "$temporary" ] || fail 'temporary video manifest path already exists'
  cp "$release/video-service/data/videos.example.json" "$temporary"
  chmod 444 "$temporary"
  mv "$temporary" "$manifest"
fi

# VideoCatalog.load reads the manifest and its calibration files; it does not need state.
docker run --rm --network none --user 10001:10001 \
  -v "$catalog:/app/data:ro" \
  --entrypoint python "$video_image" -B -c \
  "from pathlib import Path; from video_service.catalog import VideoCatalog; VideoCatalog.load(Path('/app/data'), 'videos.json')" \
  >/dev/null 2>&1 || fail 'video catalog is invalid or unreadable by UID 10001; existing catalog data was left untouched'

if [ "$mode" = bootstrap ]; then
  for volume in "$sports_volume" "$profile_volume" "$jobs_volume" "$video_volume"; do
    if docker volume inspect "$volume" >/dev/null 2>&1; then
      fail 'bootstrap target volume appeared during preflight; restore was not started'
    fi
  done
  if ! (cd "$release" && ENV_FILE="$env_file" COMPOSE_FILE=compose.prod.yaml \
      RJ_IMAGE_TAG="$commit_sha" sh dashboard/scripts/restaurar-instalacion.sh "$backup_dir"); then
    fail 'bootstrap restore failed; volumes were not removed. Inspect every target volume before any retry'
  fi
  for volume in "$sports_volume" "$profile_volume" "$jobs_volume" "$video_volume"; do
    docker volume inspect "$volume" >/dev/null 2>&1 \
      || fail 'bootstrap did not create all expected volumes; no automatic cleanup was attempted'
  done
fi

docker run --rm --network none --user 10001:10001 -v "$sports_volume:/state:ro" \
  --entrypoint python "$sports_image" -B -m persistencia verificar --base /state/real-jaen.sqlite \
  >/dev/null 2>&1 || fail 'sports database preflight failed; existing data was left untouched'

docker run --rm --network none --user 10001:10001 -v "$profile_volume:/profiles:ro" \
  --entrypoint python "$acquisition_image" -B -c \
  "from pathlib import Path; import sys; p=Path('/profiles/control'); sys.exit(0 if (p/'state.json').is_file() and (p/'owner.lock').is_file() else 1)" \
  >/dev/null 2>&1 || fail 'acquisition profile is not initialized; existing profile data was left untouched'
acquisition_container=$(dc "$release" ps -q acquisition)
if [ -n "$acquisition_container" ]; then
  acquisition_state=$(docker inspect --format '{{.State.Status}}' "$acquisition_container") \
    || fail 'could not inspect the running Acquisition service'
  acquisition_health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' "$acquisition_container") \
    || fail 'could not inspect Acquisition health'
  [ "$acquisition_state" = running ] && [ "$acquisition_health" = healthy ] \
    || fail 'Acquisition is present but not healthy; profile verification cannot safely compete with its owner lock'
  acquisition_was_running=1
  printf 'Acquisition is healthy and owns its profile; using its startup integrity check while it runs.\n'
else
  acquisition_was_running=0
  docker run --rm --network none --user 10001:10001 -v "$profile_volume:/profiles" \
    --entrypoint python "$acquisition_image" -B -m acquisition.admin verify-profile \
    >/dev/null 2>&1 || fail 'stopped Acquisition profile verification failed; existing profile data was left untouched'
fi

docker run --rm --network none --user 10001:10001 -v "$jobs_volume:/acquisition-jobs:ro" \
  --entrypoint python "$acquisition_image" -B -c \
  "import sqlite3,sys; from pathlib import Path; db=Path('/acquisition-jobs/jobs.sqlite'); sys.exit(1) if not db.is_file() else None; c=sqlite3.connect(f'file:{db}?mode=ro',uri=True); ok=c.execute('PRAGMA integrity_check').fetchone()[0]=='ok' and c.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name='job'\").fetchone() is not None; sys.exit(0 if ok else 1)" \
  >/dev/null 2>&1 || fail 'acquisition jobs database is missing or invalid'
docker run --rm --network none --user 10001:10001 -v "$jobs_volume:/acquisition-jobs" \
  --entrypoint python "$acquisition_image" -B -c \
  "import os,sys; sys.exit(0 if os.access('/acquisition-jobs', os.W_OK | os.X_OK) else 1)" \
  >/dev/null 2>&1 || fail 'acquisition jobs volume is not writable by UID 10001'
docker run --rm --network none --user 10001:10001 -v "$sports_volume:/state" \
  --entrypoint python "$sports_image" -B -c \
  "import os,sys; sys.exit(0 if os.access('/state', os.W_OK | os.X_OK) else 1)" \
  >/dev/null 2>&1 || fail 'sports state volume is not writable by UID 10001'
docker run --rm --network none --user 10001:10001 -v "$video_volume:/state:ro" \
  --entrypoint python "$video_image" -B -c \
  "import sqlite3,sys; from pathlib import Path; db=Path('/state/video-service.sqlite'); sys.exit(1) if not db.is_file() else None; c=sqlite3.connect(f'file:{db}?mode=ro',uri=True); tables={r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")}; expected={'video_job','video_association','match_video_override'}; sys.exit(0 if c.execute('PRAGMA integrity_check').fetchone()[0]=='ok' and expected <= tables else 1)" \
  >/dev/null 2>&1 || fail 'video state database is missing or invalid'
docker run --rm --network none --user 10001:10001 -v "$video_volume:/state" \
  --entrypoint python "$video_image" -B -c \
  "import os,sys; sys.exit(0 if os.access('/state', os.W_OK | os.X_OK) else 1)" \
  >/dev/null 2>&1 || fail 'video state volume is not writable by UID 10001'

docker run --rm --network none --user 10001:10001 -v "$secret_dir/control-token:/run/secrets/control-token:ro" \
  -v "$secret_dir/acquisition-token:/run/secrets/acquisition-token:ro" \
  --entrypoint python "$sports_image" -B -c \
  "from pathlib import Path; import sys; fs=[Path('/run/secrets/control-token'),Path('/run/secrets/acquisition-token')]; sys.exit(0 if all(len(p.read_bytes().strip()) >= 32 for p in fs) else 1)" \
  >/dev/null 2>&1 || fail 'Sports Data cannot read its required secret files'
docker run --rm --network none --user 10001:10001 -v "$secret_dir/acquisition-token:/run/secrets/acquisition-token:ro" \
  --entrypoint python "$acquisition_image" -B -c \
  "from pathlib import Path; import sys; p=Path('/run/secrets/acquisition-token'); sys.exit(0 if len(p.read_bytes().strip()) >= 32 else 1)" \
  >/dev/null 2>&1 || fail 'Acquisition cannot read its required secret file'
docker run --rm --network none --user node \
  -v "$secret_dir/access-password:/run/secrets/access-password:ro" \
  -v "$secret_dir/update-password:/run/secrets/update-password:ro" \
  -v "$secret_dir/control-token:/run/secrets/control-token:ro" \
  -v "$secret_dir/session:/run/secrets/session:ro" \
  --entrypoint node "$dashboard_image" -e \
  "const fs=require('node:fs'); for (const p of ['/run/secrets/access-password','/run/secrets/update-password','/run/secrets/control-token','/run/secrets/session/session-secret']) { if (!fs.readFileSync(p,'latin1').trim()) process.exit(1); }" \
  >/dev/null 2>&1 || fail 'Dashboard cannot read its required secret files'

check_health() {
  compose_dir=$1
  for service in dashboard sports-data acquisition video-service; do
    container_id=$(dc "$compose_dir" ps -q "$service")
    [ -n "$container_id" ] || return 1
    state=$(docker inspect --format '{{.State.Status}}' "$container_id") || return 1
    health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' "$container_id") || return 1
    [ "$state" = running ] && [ "$health" = healthy ] || return 1
  done
}

up_services() {
  compose_dir=$1
  recreate=$2
  shift 2
  if [ "$recreate" -eq 1 ]; then
    dc "$compose_dir" up -d --wait --wait-timeout 300 --force-recreate "$@"
  else
    dc "$compose_dir" up -d --wait --wait-timeout 300 "$@"
  fi
}

check_service_health() {
  compose_dir=$1
  service=$2
  container_id=$(dc "$compose_dir" ps -q "$service") || return 1
  [ -n "$container_id" ] || return 1
  state=$(docker inspect --format '{{.State.Status}}' "$container_id") || return 1
  health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' "$container_id") || return 1
  [ "$state" = running ] && [ "$health" = healthy ]
}

create_maintenance() {
  docker run --rm --network none --user 10001:10001 -v "$jobs_volume:/acquisition-jobs" \
    --entrypoint python "$acquisition_image" -B -c \
    "import os; p='/acquisition-jobs/deployment-maintenance'; fd=os.open(p, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600); os.write(fd, b'$release_id\\n'); os.fsync(fd); os.close(fd); d=os.open('/acquisition-jobs', os.O_RDONLY|os.O_DIRECTORY); os.fsync(d); os.close(d)" \
    >/dev/null 2>&1
}

clear_maintenance() {
  [ "$maintenance_active" -eq 1 ] || return 0
  docker run --rm --network none --user 10001:10001 -v "$jobs_volume:/acquisition-jobs" \
    --entrypoint python "$acquisition_image" -B -c \
    "import os; from pathlib import Path; p=Path('/acquisition-jobs/deployment-maintenance'); present=p.exists() or p.is_symlink(); value=p.read_text(encoding='ascii').strip() if p.is_file() and not p.is_symlink() else ''; (present and value != '$release_id') and (_ for _ in ()).throw(RuntimeError('foreign maintenance marker')); present and p.unlink(); d=os.open('/acquisition-jobs', os.O_RDONLY|os.O_DIRECTORY) if present else None; os.fsync(d) if d is not None else None; os.close(d) if d is not None else None" \
    >/dev/null 2>&1 || return 1
  maintenance_active=0
}

active_jobs() {
  docker run --rm --network none --user 10001:10001 -v "$jobs_volume:/acquisition-jobs:ro" \
    --entrypoint python "$acquisition_image" -B -c \
    "import sqlite3; c=sqlite3.connect('file:/acquisition-jobs/jobs.sqlite?mode=ro',uri=True,timeout=30); print(c.execute(\"SELECT COUNT(*) FROM job WHERE state IN ('queued','running','cancelling')\").fetchone()[0])" \
    2>/dev/null
}

detach_sports_data() {
  compose_dir=$1
  sports_id=$(dc "$compose_dir" ps -q sports-data) || return 1
  [ -n "$sports_id" ] || return 0
  network_detached=1
  if docker network disconnect "$control_network" "$sports_id" >/dev/null 2>&1; then
    return 0
  fi
  attached=$(docker inspect --format '{{json .NetworkSettings.Networks}}' "$sports_id" 2>/dev/null) || return 1
  case "$attached" in
    *"$control_network"*) network_detached=0; return 1 ;;
  esac
  return 0
}

resume_previous_sports_data() {
  [ "$network_detached" -eq 1 ] || return 0
  [ "$has_previous" -eq 1 ] || return 1
  sports_id=$(dc "$root/releases/$previous_release" ps -q sports-data) || return 1
  [ -n "$sports_id" ] || return 1
  docker network connect "$control_network" "$sports_id" >/dev/null 2>&1 || {
    attached=$(docker inspect --format '{{json .NetworkSettings.Networks}}' "$sports_id" 2>/dev/null) || return 1
    case "$attached" in *"$control_network"*) ;; *) return 1 ;; esac
  }
  network_detached=0
}

wait_for_idle_jobs() {
  elapsed=0
  idle=0
  while [ "$elapsed" -lt 1500 ]; do
    count=$(active_jobs) || return 1
    case "$count" in ''|*[!0-9]*) return 1 ;; esac
    if [ "$count" -eq 0 ]; then
      idle=$((idle + 1))
      [ "$idle" -ge 5 ] && return 0
    else
      idle=0
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done
  return 1
}

rollback_candidate() {
  [ "$has_previous" -eq 1 ] || return 1
  previous_dir="$root/releases/$previous_release"
  [ -d "$previous_dir" ] || return 1
  env_file="$config_base/$previous_config_target/.env.prod"
  secret_dir="$secrets_base/$previous_secret_target"
  export RJ_PROD_SECRET_DIR="$secret_dir" RJ_IMAGE_TAG="$previous_commit"
  acquisition_image="local/real-jaen-analytics/acquisition:$previous_commit"

  if [ "$containers_mutated" -eq 0 ]; then
    restore_previous_links || return 1
    recovery_complete=1
    return 0
  fi

  if [ "$acquisition_drain" -eq 1 ]; then
    detach_sports_data "$release" || {
      preserve_candidate_state=1
      recovery_complete=1
      return 1
    }
    pre_services=
    for service in $service_list; do
      case "$service" in sports-data|dashboard) ;; *) pre_services="${pre_services}${pre_services:+ }$service" ;; esac
    done
    if [ -n "$pre_services" ]; then
      # Intentional word splitting: service names are the fixed list above.
      up_services "$previous_dir" 1 $pre_services || {
        set_current_link "$config_base" "$config_target"
        set_current_link "$secrets_base" "$secret_target"
        preserve_candidate_state=1
        recovery_complete=1
        return 1
      }
    fi
    check_service_health "$previous_dir" acquisition || {
      set_current_link "$config_base" "$config_target"
      set_current_link "$secrets_base" "$secret_target"
      preserve_candidate_state=1
      recovery_complete=1
      return 1
    }
    clear_maintenance || {
      set_current_link "$config_base" "$config_target"
      set_current_link "$secrets_base" "$secret_target"
      preserve_candidate_state=1
      recovery_complete=1
      return 1
    }
    post_services=
    for service in $service_list; do
      case "$service" in sports-data|dashboard) post_services="${post_services}${post_services:+ }$service" ;; esac
    done
    if [ -n "$post_services" ]; then
      up_services "$previous_dir" 1 $post_services || {
        set_current_link "$config_base" "$config_target"
        set_current_link "$secrets_base" "$secret_target"
        preserve_candidate_state=1
        recovery_complete=1
        return 1
      }
    fi
    network_detached=0
  else
    up_services "$previous_dir" "$force_recreate" $service_list || {
      set_current_link "$config_base" "$config_target"
      set_current_link "$secrets_base" "$secret_target"
      preserve_candidate_state=1
      recovery_complete=1
      return 1
    }
  fi

  check_health "$previous_dir" || {
    set_current_link "$config_base" "$config_target"
    set_current_link "$secrets_base" "$secret_target"
    preserve_candidate_state=1
    recovery_complete=1
    return 1
  }
  restore_previous_links || return 1
  docker image tag "local/real-jaen-analytics/sports-data:$previous_commit" local/real-jaen-analytics/sports-data:prod >/dev/null 2>&1 || true
  docker image tag "local/real-jaen-analytics/acquisition:$previous_commit" local/real-jaen-analytics/acquisition:prod >/dev/null 2>&1 || true
  docker image tag "local/real-jaen-analytics/video-service:$previous_commit" local/real-jaen-analytics/video-service:prod >/dev/null 2>&1 || true
  docker image tag "local/real-jaen-analytics/dashboard:$previous_commit" local/real-jaen-analytics/dashboard:prod >/dev/null 2>&1 || true
  maintenance_active=0
  network_detached=0
  containers_mutated=0
  recovery_complete=1
  return 0
}

if [ "$recreate_acquisition" -eq 1 ] && [ "$acquisition_was_running" -eq 1 ]; then
  [ "$has_previous" -eq 1 ] || fail 'Acquisition is running without a last-good recovery target; no service or network was changed'
  acquisition_drain=1
  detach_sports_data "$release" || fail 'could not close the Sports Data admission path; active containers were left running'
  maintenance_active=1
  create_maintenance || fail 'could not establish the Acquisition deployment maintenance fence'
  wait_for_idle_jobs || fail 'Acquisition did not become idle; the deployment was cancelled without restarting Acquisition'
elif [ "$recreate_acquisition" -eq 1 ]; then
  active_count=$(active_jobs) || fail 'could not inspect active Acquisition jobs'
  case "$active_count" in ''|*[!0-9]*) fail 'active Acquisition job count was invalid' ;; esac
  [ "$active_count" -eq 0 ] || fail 'Acquisition is stopped while jobs remain marked active; inspect before deployment'
fi

# All non-mutating preflights have completed. Promote changed pointers only
# now; Compose receives these immutable version directories directly.
if [ ! -L "$secret_current" ] || [ "$(readlink "$secret_current")" != "$secret_target" ]; then
  set_current_link "$secrets_base" "$secret_target"
  links_promoted=1
fi
if [ ! -L "$config_current" ] || [ "$(readlink "$config_current")" != "$config_target" ]; then
  set_current_link "$config_base" "$config_target"
  links_promoted=1
fi

candidate_ok=0
if [ "$needs_update" -eq 0 ]; then
  if check_health "$release"; then candidate_ok=1; fi
elif [ "$acquisition_drain" -eq 1 ]; then
  pre_services=
  post_services=
  for service in $service_list; do
    case "$service" in
      sports-data|dashboard) post_services="${post_services}${post_services:+ }$service" ;;
      *) pre_services="${pre_services}${pre_services:+ }$service" ;;
    esac
  done
  containers_mutated=1
  if { [ -z "$pre_services" ] || up_services "$release" "$force_recreate" $pre_services; } \
      && check_service_health "$release" acquisition \
      && clear_maintenance \
      && { [ -z "$post_services" ] || up_services "$release" "$force_recreate" $post_services; } \
      && check_health "$release"; then
    network_detached=0
    candidate_ok=1
  fi
else
  containers_mutated=1
  if up_services "$release" "$force_recreate" $service_list && check_health "$release"; then
    candidate_ok=1
  fi
fi

if [ "$candidate_ok" -eq 1 ] \
    && docker image tag "$sports_image" local/real-jaen-analytics/sports-data:prod \
    && docker image tag "$acquisition_image" local/real-jaen-analytics/acquisition:prod \
    && docker image tag "$video_image" local/real-jaen-analytics/video-service:prod \
    && docker image tag "$dashboard_image" local/real-jaen-analytics/dashboard:prod; then
  {
    printf 'pipeline_id=%s\n' "$pipeline_id"
    printf 'release_id=%s\n' "$release_id"
    printf 'commit_sha=%s\n' "$commit_sha"
    printf 'image_tag=%s\n' "$commit_sha"
    printf 'config_target=%s\n' "$config_target"
    printf 'secret_target=%s\n' "$secret_target"
  } > "$metadata_tmp"
  chmod 600 "$metadata_tmp"
  deployment_ready=1
  if mv -f "$metadata_tmp" "$last_good"; then
    deployment_finalized=1
    maintenance_active=0
    network_detached=0
    printf 'Deployment %s is healthy; persistent data volumes were preserved.\n' "$commit_sha"
    exit 0
  fi
  if rollback_candidate; then
    fail 'last-good metadata could not be committed; the previous code/config and services were restored'
  fi
  if [ "$has_previous" -eq 1 ]; then
    fail 'last-good metadata could not be committed and prior-code recovery failed; candidate paths remain current for inspection'
  fi
  fail 'last-good metadata could not be committed and no prior release exists; the healthy candidate remains current for inspection'
fi

if [ "$containers_mutated" -eq 0 ]; then
  fail 'deployment health check failed; active services and current configuration were not changed'
fi

if [ "$has_previous" -eq 1 ]; then
  if rollback_candidate; then
    fail 'deployment failed; previous code/config was restarted and no volume was deleted or automatically restored. Data compatibility depends on schema changes'
  fi
  fail 'deployment and prior-code recovery both failed; candidate paths remain current for inspection. Database volumes were not removed'
fi
fail 'deployment failed and no previous healthy release is recorded; inspect services before retrying. No volume was removed'
