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
  if [ "$deployment_finalized" -eq 0 ]; then
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
  "$imports" "$catalog" "$root/shared/incoming" "$state_dir"
chmod 700 "$root" "$root/shared" "$config_base" "$secrets_base" \
  "$config_base/versions" "$secrets_base/versions" "$root/shared/incoming" "$state_dir"
chmod 755 "$imports" "$catalog"

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
  count=$(grep -Fxc "$expected" "$bundle_env" || true)
  [ "$count" = 1 ] || fail 'ENV_PROD is missing a required production setting or contains a duplicate'
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
if [ -e "$config_current" ] || [ -L "$config_current" ]; then
  [ -L "$config_current" ] || fail 'current config path is not a managed symlink'
  safe_link "$config_base" "$(readlink "$config_current")" || fail 'current config link is invalid'
fi
if [ -e "$secret_current" ] || [ -L "$secret_current" ]; then
  [ -L "$secret_current" ] || fail 'current secret path is not a managed symlink'
  safe_link "$secrets_base" "$(readlink "$secret_current")" || fail 'current secret link is invalid'
fi
if [ -L "$config_current" ] && cmp -s "$config_current/.env.prod" "$bundle_env"; then
  config_target=$(readlink "$config_current")
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
  for name in access-password update-password control-token acquisition-token; do
    cmp -s "$secret_current/$name" "$bundle_secrets/$name" || same_secrets=0
  done
  cmp -s "$secret_current/session/session-secret" "$bundle_secrets/session/session-secret" || same_secrets=0
fi
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
set_current_link "$secrets_base" "$secret_target"
set_current_link "$config_base" "$config_target"
env_file="$config_current/.env.prod"
secret_dir="$secret_current"

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

manifest="$catalog/videos.json"
if [ ! -f "$manifest" ]; then
  first_entry=$(find "$catalog" -mindepth 1 -print -quit)
  [ -z "$first_entry" ] || fail 'video catalog has files but no videos.json; existing catalog data was left untouched'
  temporary="$catalog/.videos.json-$release_id"
  cp "$release/video-service/data/videos.example.json" "$temporary"
  chmod 444 "$temporary"
  mv "$temporary" "$manifest"
fi
chmod 444 "$manifest"

dc "$release" build sports-data acquisition video-service dashboard \
  || fail 'image build failed; running services were not changed'

if [ "$mode" = bootstrap ]; then
  if ! (cd "$release" && ENV_FILE="$env_file" COMPOSE_FILE=compose.prod.yaml \
      RJ_IMAGE_TAG="$commit_sha" sh dashboard/scripts/restaurar-instalacion.sh "$backup_dir"); then
    fail 'bootstrap restore failed; volumes were not removed. Inspect every target volume before any retry'
  fi
  for volume in "$sports_volume" "$profile_volume" "$jobs_volume" "$video_volume"; do
    docker volume inspect "$volume" >/dev/null 2>&1 \
      || fail 'bootstrap did not create all expected volumes; no automatic cleanup was attempted'
  done
fi

sports_image="local/real-jaen-analytics/sports-data:$commit_sha"
acquisition_image="local/real-jaen-analytics/acquisition:$commit_sha"
video_image="local/real-jaen-analytics/video-service:$commit_sha"
dashboard_image="local/real-jaen-analytics/dashboard:$commit_sha"

docker run --rm --network none --user 10001:10001 -v "$sports_volume:/state:ro" \
  --entrypoint python "$sports_image" -B -m persistencia verificar --base /state/real-jaen.sqlite \
  >/dev/null 2>&1 || fail 'sports database preflight failed; existing data was left untouched'

docker run --rm --network none --user 10001:10001 -v "$profile_volume:/profiles:ro" \
  --entrypoint python "$acquisition_image" -B -c \
  "from pathlib import Path; import sys; p=Path('/profiles/control'); sys.exit(0 if (p/'state.json').is_file() and (p/'owner.lock').is_file() else 1)" \
  >/dev/null 2>&1 || fail 'acquisition profile is not initialized; existing profile data was left untouched'
docker run --rm --network none --user 10001:10001 -v "$profile_volume:/profiles" \
  --entrypoint python "$acquisition_image" -B -m acquisition.admin verify-profile \
  >/dev/null 2>&1 || fail 'acquisition profile verification failed; existing profile data was left untouched'

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

docker run --rm --network none --user 10001:10001 \
  -v "$video_volume:/state" -v "$catalog:/app/data:ro" \
  --entrypoint python "$video_image" -B -c \
  "from pathlib import Path; from video_service.catalog import VideoCatalog; VideoCatalog.load(Path('/app/data'), 'videos.json')" \
  >/dev/null 2>&1 || fail 'video catalog validation failed; existing catalog data was left untouched'

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

if dc "$release" up -d --wait --wait-timeout 300 dashboard sports-data acquisition video-service \
    && check_health "$release" \
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
  mv -f "$metadata_tmp" "$last_good"
  deployment_finalized=1
  printf 'Deployment %s is healthy; persistent data volumes were preserved.\n' "$commit_sha"
  exit 0
fi

if [ "$has_previous" -eq 1 ]; then
  restore_previous_links || fail 'deployment failed and previous configuration could not be restored'
  export RJ_IMAGE_TAG="$previous_commit"
  previous_dir="$root/releases/$previous_release"
  if [ -d "$previous_dir" ] && dc "$previous_dir" up -d --wait --wait-timeout 300 dashboard sports-data acquisition video-service \
      && check_health "$previous_dir"; then
    docker image tag "local/real-jaen-analytics/sports-data:$previous_commit" local/real-jaen-analytics/sports-data:prod >/dev/null 2>&1 || true
    docker image tag "local/real-jaen-analytics/acquisition:$previous_commit" local/real-jaen-analytics/acquisition:prod >/dev/null 2>&1 || true
    docker image tag "local/real-jaen-analytics/video-service:$previous_commit" local/real-jaen-analytics/video-service:prod >/dev/null 2>&1 || true
    docker image tag "local/real-jaen-analytics/dashboard:$previous_commit" local/real-jaen-analytics/dashboard:prod >/dev/null 2>&1 || true
    fail 'deployment failed; previous code/config was restarted and no volume was deleted or automatically restored. Data compatibility depends on schema changes'
  fi
  fail 'deployment and prior-code recovery both failed; inspect service state before retrying. Database volumes were not removed'
fi
fail 'deployment failed and no previous healthy release is recorded; inspect services before retrying. No volume was removed'
