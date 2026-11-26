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
case "$mode" in deploy) ;; *) fail 'only ordinary PostgreSQL code deployments are supported' ;; esac
case "$release_id" in *[!a-zA-Z0-9_-]*|'') fail 'invalid release identifier' ;; esac
case "$commit_sha" in *[!a-fA-F0-9]*|'') fail 'invalid commit identifier' ;; esac
case "$pipeline_id" in *[!0-9]*|'') fail 'invalid pipeline identifier' ;; esac

root="$HOME/deploy_real-jaen-analytics"
release="$root/releases/$release_id"
incoming="$root/shared/incoming/$release_id"
config_base="$root/shared/config"
secrets_base="$root/shared/secrets"
imports="$root/shared/imports"
postgres_secret_root="$root/shared/postgresql/secrets"
state_dir="$root/shared/deploy-state"
# A restore marker is created before the first destination volume. Check before any directory, release, symlink or
# container mutation so an interrupted restore cannot be mistaken for a deployable installation.
restore_marker="$imports/.real-jaen-restore-incomplete"
if [ -e "$restore_marker" ] || [ -L "$restore_marker" ]; then
  fail 'an incomplete restore is recorded in shared/imports; inspect the listed target volumes and recover it manually before deploying'
fi
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
preserve_candidate_state=0
recovery_complete=0
deployment_ready=0
config_temp="$config_base/versions/.$release_id.tmp"
secret_temp="$secrets_base/versions/.$release_id.tmp"
metadata_tmp="$state_dir/.last-good-$release_id"
postgres_env_file=
postgres_secret_dir=
postgres_volume=
sports_operational_volume=
video_operational_volume=
profile_volume=
jobs_volume=
postgres_container=
admission_closed=0
previous_rollback_compatible=0
previous_is_sqlite=0
dashboard_was_present=0

safe_link() {
  base=$1
  target=$2
  case "$target" in versions/*) ;; *) return 1 ;; esac
  case "/$target/" in */../*|*//*) return 1 ;; esac
  [ -d "$base/$target" ] && [ ! -L "$base/$target" ]
}

release_has_container_bind_mount() {
  candidate_path=$1
  containers=$(docker ps -aq 2>/dev/null) || return 0
  for container_id in $containers; do
    sources=$(docker inspect --format '{{range .Mounts}}{{println .Source}}{{end}}' "$container_id" 2>/dev/null) || return 0
    if printf '%s\n' "$sources" | awk -v root="$candidate_path" \
      '$0 == root || index($0, root "/") == 1 { found=1 } END { exit !found }'; then
      return 0
    fi
  done
  return 1
}

prune_old_releases() {
  releases_dir="$root/releases"
  if [ ! -d "$root" ] || [ -L "$root" ] || [ ! -d "$releases_dir" ] || [ -L "$releases_dir" ] \
      || [ "$releases_dir" != "$root/releases" ]; then
    printf 'SINBAD2 deploy: warning: release cleanup skipped because the releases path is unsafe\n' >&2
    return 0
  fi
  if ! command -v sort >/dev/null 2>&1; then
    printf 'SINBAD2 deploy: warning: release cleanup skipped because sort is unavailable\n' >&2
    return 0
  fi

  ordered=$(for candidate_path in "$releases_dir"/*; do
    [ -d "$candidate_path" ] && [ ! -L "$candidate_path" ] || continue
    candidate=${candidate_path##*/}
    case "$candidate" in *[!a-fA-F0-9_-]*|'') continue ;; esac
    candidate_sha=${candidate%%-*}
    case "$candidate_sha" in *[!a-fA-F0-9]*|'') continue ;; esac
    case "${#candidate_sha}" in 40|64) ;; *) continue ;; esac
    candidate_rest=${candidate#*-}
    case "$candidate_rest" in *-*) ;; *) continue ;; esac
    candidate_pipeline=${candidate_rest%%-*}
    candidate_job=${candidate_rest#*-}
    case "$candidate_pipeline:$candidate_job" in *[!0-9:]*|:*|*:) continue ;; esac
    [ -f "$candidate_path/compose.prod.yaml" ] \
      && [ -f "$candidate_path/compose.postgresql.yaml" ] \
      && [ -f "$candidate_path/compose.postgresql.prod.yaml" ] || continue
    printf '%s %s %s\n' "$candidate_pipeline" "$candidate_job" "$candidate"
  done | sort -k 1,1nr -k 2,2nr) || {
    printf 'SINBAD2 deploy: warning: release cleanup could not list old releases\n' >&2
    return 0
  }

  [ -n "$ordered" ] || return 0
  printf '%s\n' "$ordered" | while read -r candidate_pipeline candidate_job candidate; do
    case "$candidate_pipeline:$candidate_job" in *[!0-9:]*|:*|*:) continue ;; esac
    case "$candidate" in *[!a-fA-F0-9_-]*|'') continue ;; esac
    release_number=${release_number:-0}
    release_number=$((release_number + 1))
    [ "$release_number" -le 5 ] && continue
    [ "$candidate" = "$release_id" ] && continue
    [ -n "$previous_release" ] && [ "$candidate" = "$previous_release" ] && continue
    candidate_path="$releases_dir/$candidate"
    case "$candidate_path" in "$releases_dir"/*) ;; *)
      printf 'SINBAD2 deploy: warning: release cleanup skipped an invalid candidate path\n' >&2
      continue
    esac
    [ -d "$candidate_path" ] && [ ! -L "$candidate_path" ] || continue
    [ -f "$candidate_path/compose.prod.yaml" ] \
      && [ -f "$candidate_path/compose.postgresql.yaml" ] \
      && [ -f "$candidate_path/compose.postgresql.prod.yaml" ] || continue
    if release_has_container_bind_mount "$candidate_path"; then
      printf 'SINBAD2 deploy: preserving release %s because a container has a bind mount in it\n' "$candidate" >&2
      continue
    fi
    if ! rm -rf -- "$candidate_path"; then
      printf 'SINBAD2 deploy: warning: could not remove old code release %s\n' "$candidate" >&2
    fi
  done
  return 0
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
    [ -d "$path" ] && [ ! -L "$path" ] || fail 'administrator-prepared shared/imports path is not a real directory'
    return 0
  fi
  fail 'an administrator-prepared shared directory is missing; no directory was created'
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
    if [ "$containers_mutated" -eq 1 ] && [ "$has_previous" -eq 1 ] \
        && [ "$previous_rollback_compatible" -eq 1 ]; then
      rollback_candidate || preserve_candidate_state=1
    elif [ "$containers_mutated" -eq 1 ]; then
      preserve_candidate_state=1
      dc "$release" stop dashboard >/dev/null 2>&1 || true
      wait_for_idle_jobs || true
      printf 'SINBAD2 deploy: automatic rollback is disabled because the prior release is not PostgreSQL-compatible; admission remains closed for inspection\n' >&2
    elif [ "$maintenance_active" -eq 1 ] && [ "$preserve_candidate_state" -eq 0 ]; then
      if clear_maintenance; then
        resume_dashboard || {
          preserve_candidate_state=1
          printf 'SINBAD2 deploy: could not restore service admission; admission remains closed for inspection\n' >&2
        }
      else
        preserve_candidate_state=1
        printf 'SINBAD2 deploy: maintenance fence could not be cleared; admission remains closed for inspection\n' >&2
      fi
    elif [ "$admission_closed" -eq 1 ] && [ "$preserve_candidate_state" -eq 0 ]; then
      resume_dashboard || {
        preserve_candidate_state=1
        printf 'SINBAD2 deploy: could not restore service admission; admission remains closed for inspection\n' >&2
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
  "$config_base/versions" "$secrets_base/versions" "$imports" "$postgres_secret_root" \
  "$root/shared/incoming" "$state_dir"; do
  [ ! -L "$path" ] || fail 'deployment path contains a symbolic link'
done
mkdir -p "$root/releases" "$root/shared" "$config_base/versions" "$secrets_base/versions" \
  "$root/shared/incoming" "$state_dir"
chmod 700 "$root" "$root/shared" "$config_base" "$secrets_base" \
  "$config_base/versions" "$secrets_base/versions" "$root/shared/incoming" "$state_dir"
ensure_uid_read_directory "$imports"
[ -d "$postgres_secret_root" ] && [ ! -L "$postgres_secret_root" ] \
  || fail 'PostgreSQL credentials must be provisioned by administration in shared/postgresql/secrets'
[ "$(stat -c '%a' -- "$postgres_secret_root" 2>/dev/null)" = 700 ] \
  || fail 'shared/postgresql/secrets must be a private mode-0700 directory'

[ -d "$release" ] && [ ! -L "$release" ] || fail 'code release directory is missing'
[ -f "$release/compose.prod.yaml" ] \
  && [ -f "$release/compose.postgresql.yaml" ] \
  && [ -f "$release/compose.postgresql.prod.yaml" ] \
  || fail 'one or more PostgreSQL production Compose files are missing from the release'
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
  previous_database_family=$(sed -n 's/^database_family=//p' "$last_good" | tail -n 1)
  if [ "$previous_database_family" != postgresql ]; then
    previous_is_sqlite=1
  fi
  previous_config_dir="$config_base/$previous_config_target"
  previous_release_dir="$root/releases/$previous_release"
  if [ -f "$previous_config_dir/.env.postgresql.prod" ] \
      && [ -f "$previous_release_dir/compose.postgresql.yaml" ] \
      && [ -f "$previous_release_dir/compose.postgresql.prod.yaml" ] \
      && grep -Fqx 'database_family=postgresql' "$last_good" \
      && grep -Fqx 'sports_schema_revision=pg0005' "$last_good" \
      && grep -Fqx 'video_schema_revision=vg0002' "$last_good" \
      && grep -Fqx 'PG_VOLUME_NAME=rja-sinbad2-postgresql-data' "$previous_config_dir/.env.postgresql.prod" \
      && grep -Fqx 'PG_NETWORK_NAME=rja-sinbad2-prod-postgresql-private' "$previous_config_dir/.env.postgresql.prod"; then
    previous_rollback_compatible=1
  fi
  if [ "$pipeline_id" -lt "$previous_pipeline" ]; then
    printf 'Skipping stale pipeline %s; pipeline %s is already healthy.\n' "$pipeline_id" "$previous_pipeline"
    deployment_finalized=1
    exit 0
  fi
fi

bundle_env="$incoming/.env.prod"
bundle_postgres_env="$incoming/.env.postgresql.prod"
bundle_secrets="$incoming/secrets"
[ -f "$bundle_env" ] && [ ! -L "$bundle_env" ] || fail 'ENV_PROD is missing from the private bundle'
[ -f "$bundle_postgres_env" ] && [ ! -L "$bundle_postgres_env" ] \
  || fail 'ENV_POSTGRESQL_PROD is missing from the private bundle'
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

require_postgres_line() {
  expected=$1
  key=${expected%%=*}
  declarations=$(grep -Ec "^[[:space:]]*(export[[:space:]]+)?${key}[[:space:]]*=" "$bundle_postgres_env" || true)
  [ "$declarations" = 1 ] || fail "ENV_POSTGRESQL_PROD declares critical key $key more than once"
  count=$(grep -Fxc "$expected" "$bundle_postgres_env" || true)
  [ "$count" = 1 ] || fail "ENV_POSTGRESQL_PROD critical setting $key is invalid"
}

require_line 'COMPOSE_PROJECT_NAME=real-jaen-analytics-sinbad2-prod'
require_line 'RJ_PROD_NETWORK_PREFIX=rja-sinbad2-prod'
require_line 'RJ_PROD_ACQUISITION_PROFILES_VOLUME=rja-sinbad2-acquisition-profiles'
require_line 'RJ_PROD_ACQUISITION_JOBS_VOLUME=rja-sinbad2-acquisition-jobs'
require_line 'RJ_DASHBOARD_BIND=127.0.0.1'
require_line 'RJ_DASHBOARD_PORT=8090'
require_line 'RJ_ACQUISITION_ENABLE_REAL=1'
require_line 'RJ_PROD_SECRET_DIR=${RJA_DEPLOY_ROOT:?Set by the deployment job}/shared/secrets/current'
require_line 'RJ_IMPORT_DIR=${RJA_DEPLOY_ROOT:?Set by the deployment job}/shared/imports'
require_line 'RJ_VIDEO_CATALOG_DIR=${RJA_DEPLOY_ROOT:?Set by the deployment job}/shared/video-catalog'
if grep -Eq '^(APP_ACCESS_PASSWORD|APP_UPDATE_PASSWORD|APP_SESSION_SECRET|APP_CONTROL_TOKEN|APP_ACQUISITION_TOKEN)=' "$bundle_env"; then
  fail 'application credentials belong in File variables, not ENV_PROD'
fi
require_postgres_line 'PG_PROJECT_NAME=real-jaen-analytics-sinbad2-prod'
require_postgres_line 'PG_NETWORK_NAME=rja-sinbad2-prod-postgresql-private'
require_postgres_line 'PG_VOLUME_NAME=rja-sinbad2-postgresql-data'
require_postgres_line 'PG_SECRET_DIR=${RJA_DEPLOY_ROOT:?Set by the deployment job}/shared/postgresql/secrets'
require_postgres_line 'PG_TASK_IMAGE_TAG=prod'
require_postgres_line 'RJ_PROD_SPORTS_OPERATIONAL_VOLUME=rja-sinbad2-sports-operational-state'
require_postgres_line 'RJ_PROD_VIDEO_OPERATIONAL_VOLUME=rja-sinbad2-video-operational-state'
secret_gid=$(sed -n 's/^RJ_APP_SECRET_GID=//p' "$bundle_postgres_env" | tail -n 1)
secret_gid_count=$(grep -Ec '^[[:space:]]*(export[[:space:]]+)?RJ_APP_SECRET_GID[[:space:]]*=' "$bundle_postgres_env" || true)
[ "$secret_gid_count" = 1 ] || fail 'ENV_POSTGRESQL_PROD must declare the administrator-provisioned GID exactly once'
case "$secret_gid" in ''|*[!0-9]*) fail 'ENV_POSTGRESQL_PROD must contain the GID provisioned by administration' ;; esac
[ "$secret_gid" -gt 0 ] || fail 'the PostgreSQL runtime secret GID must be non-privileged and nonzero'
if grep -Eq '^(POSTGRES_PASSWORD|SPORTS_DATA_(OWNER|APP)_PASSWORD|VIDEO_SERVICE_(OWNER|APP)_PASSWORD|SPORTS_DATA_.*DATABASE_URL|VIDEO_SERVICE_.*DATABASE_URL)=' "$bundle_postgres_env"; then
  fail 'PostgreSQL credentials belong in administrator-provisioned files, not ENV_POSTGRESQL_PROD'
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
if [ -L "$config_current" ] \
    && cmp -s "$config_current/.env.prod" "$bundle_env" \
    && cmp -s "$config_current/.env.postgresql.prod" "$bundle_postgres_env"; then
  config_target=$(readlink "$config_current")
  config_changed=0
else
  config_target="versions/$release_id"
  [ ! -e "$config_base/$config_target" ] || fail 'config version already exists'
  [ ! -e "$config_temp" ] || fail 'temporary config version already exists'
  mkdir -m 700 "$config_temp"
  cp "$bundle_env" "$config_temp/.env.prod"
  cp "$bundle_postgres_env" "$config_temp/.env.postgresql.prod"
  chmod 600 "$config_temp/.env.prod" "$config_temp/.env.postgresql.prod"
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
postgres_env_file="$config_base/$config_target/.env.postgresql.prod"
secret_dir="$secrets_base/$secret_target"
postgres_secret_dir="$postgres_secret_root"
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
network_prefix=$(sed -n 's/^RJ_PROD_NETWORK_PREFIX=//p' "$env_file" | tail -n 1)
case "$network_prefix" in *[!a-zA-Z0-9_.-]*|'') fail 'production network prefix is invalid' ;; esac

compose_version=$(docker compose version --short 2>/dev/null) || fail 'Docker Compose is unavailable on the host'
version=${compose_version#v}
major=${version%%.*}
rest=${version#*.}
minor=${rest%%.*}
patch=${rest#*.}
patch=${patch%%.*}
case "$major:$minor:$patch" in *[!0-9:]*|:*|*::*|*:) fail 'Docker Compose version could not be parsed' ;; esac
if [ "$major" -lt 2 ] || { [ "$major" -eq 2 ] \
    && { [ "$minor" -lt 24 ] || { [ "$minor" -eq 24 ] && [ "$patch" -lt 4 ]; }; }; }; then
  fail 'Docker Compose 2.24.4 or newer is required for !override and !reset'
fi
docker info >/dev/null 2>&1 || fail 'Docker daemon is unavailable to the SSH account'
command -v python3 >/dev/null 2>&1 || fail 'python3 is required for production Compose and mount verification'

dc() {
  compose_dir=$1
  shift
  docker compose --project-directory "$compose_dir" --env-file "$env_file" \
    --env-file "$postgres_env_file" \
    -f "$compose_dir/compose.prod.yaml" \
    -f "$compose_dir/compose.postgresql.yaml" \
    -f "$compose_dir/compose.postgresql.prod.yaml" "$@"
}

service_storage_mode() {
  storage_service=$1
  storage_variable=$2
  storage_ids=$(dc "$release" ps --all -q "$storage_service") \
    || fail 'could not inspect the existing application containers before deployment'
  [ -n "$storage_ids" ] || { printf 'absent'; return 0; }
  set -- $storage_ids
  [ "$#" -eq 1 ] || fail 'multiple existing service containers require manual inspection before deployment'
  storage_environment=$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$1") \
    || fail 'could not inspect the existing service storage mode'
  storage_count=$(printf '%s\n' "$storage_environment" | grep -Ec "^${storage_variable}=" || true)
  [ "$storage_count" -le 1 ] || fail 'existing service storage mode is ambiguous; deployment stopped'
  if [ "$storage_count" -eq 0 ]; then
    if [ "$storage_variable" = VIDEO_SERVICE_STORAGE ]; then
      printf 'sqlite'
    else
      printf 'unknown'
    fi
    return 0
  fi
  storage_value=$(printf '%s\n' "$storage_environment" | sed -n "s/^${storage_variable}=//p")
  case "$storage_variable:$storage_value" in
    SPORTS_DATA_STORAGE:sql|VIDEO_SERVICE_STORAGE:sqlite) printf 'sqlite' ;;
    SPORTS_DATA_STORAGE:postgresql|VIDEO_SERVICE_STORAGE:postgresql) printf 'postgresql' ;;
    *) printf 'unknown' ;;
  esac
}

sports_storage_mode=$(service_storage_mode sports-data SPORTS_DATA_STORAGE)
video_storage_mode=$(service_storage_mode video-service VIDEO_SERVICE_STORAGE)
if [ "$sports_storage_mode" = sqlite ] || [ "$video_storage_mode" = sqlite ]; then
  fail 'a legacy SQLite application is installed in this Compose project; the PostgreSQL code deployment was refused without stopping or changing it'
fi
if { [ "$sports_storage_mode" != absent ] && [ "$sports_storage_mode" != postgresql ]; } \
    || { [ "$video_storage_mode" != absent ] && [ "$video_storage_mode" != postgresql ]; }; then
  fail 'an existing application container does not declare PostgreSQL storage; inspect the installation before deployment'
fi

dc "$release" config -q >/dev/null 2>&1 || fail 'PostgreSQL production Compose configuration is invalid'
dc "$release" --profile migrations config --format json 2>/dev/null \
  | python3 "$release/scripts/verify-postgresql-prod-compose.py" >/dev/null 2>&1 \
  || fail 'effective production Compose does not preserve the PostgreSQL network, secret and loopback boundaries'

volume_value() {
  value=$(sed -n "s/^$1=//p" "$env_file" | tail -n 1)
  case "$value" in
    ''|*[!a-zA-Z0-9_.-]*) fail 'a persistent volume name is missing or invalid' ;;
  esac
  printf '%s' "$value"
}

volume_value_from_file() {
  value=$(sed -n "s/^$2=//p" "$1" | tail -n 1)
  case "$value" in
    ''|*[!a-zA-Z0-9_.-]*) fail 'a persistent PostgreSQL volume name is missing or invalid' ;;
  esac
  printf '%s' "$value"
}

postgres_volume=$(volume_value_from_file "$postgres_env_file" PG_VOLUME_NAME)
sports_operational_volume=$(volume_value_from_file "$postgres_env_file" RJ_PROD_SPORTS_OPERATIONAL_VOLUME)
video_operational_volume=$(volume_value_from_file "$postgres_env_file" RJ_PROD_VIDEO_OPERATIONAL_VOLUME)
profile_volume=$(volume_value RJ_PROD_ACQUISITION_PROFILES_VOLUME)
jobs_volume=$(volume_value RJ_PROD_ACQUISITION_JOBS_VOLUME)
present=0
missing=0
for volume in "$postgres_volume" "$sports_operational_volume" "$video_operational_volume" "$profile_volume" "$jobs_volume"; do
  if docker volume inspect "$volume" >/dev/null 2>&1; then
    present=$((present + 1))
  else
    missing=$((missing + 1))
  fi
done

if [ "$missing" -gt 0 ]; then
  if [ "$present" -eq 0 ]; then
    if [ "$previous_is_sqlite" -eq 1 ]; then
      fail 'the last healthy deployment is SQLite and no PostgreSQL resources are prepared; run the separate administrator-led PostgreSQL restoration before ordinary code deployment'
    fi
    fail 'PostgreSQL installation is not prepared; administration must provision all five persistent volumes before deployment'
  fi
  if [ "$previous_is_sqlite" -eq 1 ]; then
    fail 'the previous installation is SQLite and PostgreSQL preparation is incomplete; deployment stopped without creating, restoring, or removing any volume'
  fi
  fail 'PostgreSQL installation is incomplete; deployment stopped without creating, restoring, or removing any volume'
fi

if [ "$code_changed" -eq 1 ]; then
  dc "$release" build sports-data acquisition video-service dashboard \
    || fail 'image build failed; running services were not changed'
fi

sports_image="local/real-jaen-analytics/sports-data:$commit_sha"
acquisition_image="local/real-jaen-analytics/acquisition:$commit_sha"
video_image="local/real-jaen-analytics/video-service:$commit_sha"
dashboard_image="local/real-jaen-analytics/dashboard:$commit_sha"
deployment_check="$release/scripts/postgresql-deployment-check.py"

[ -f "$deployment_check" ] && [ ! -L "$deployment_check" ] \
  || fail 'read-only PostgreSQL deployment checks are missing from the release'

secret_mode_and_group() {
  stat -c '%a:%g' -- "$1" 2>/dev/null
}

for secret_name in postgres_admin_password sports_data_owner_password sports_data_app_password \
  video_service_owner_password video_service_app_password \
  sports_data_migration_database_url video_service_migration_database_url; do
  secret_path="$postgres_secret_dir/$secret_name"
  [ -f "$secret_path" ] && [ ! -L "$secret_path" ] \
    || fail 'an administrator-provisioned PostgreSQL credential file is missing'
  metadata=$(secret_mode_and_group "$secret_path") || fail 'PostgreSQL credential metadata is unavailable'
  case "$metadata" in 600:*) ;; *) fail 'PostgreSQL administrator and migration credentials must remain mode 0600' ;; esac
done
for secret_name in sports_data_database_url video_service_database_url; do
  secret_path="$postgres_secret_dir/$secret_name"
  [ -f "$secret_path" ] && [ ! -L "$secret_path" ] \
    || fail 'an administrator-provisioned PostgreSQL application URL is missing'
  metadata=$(secret_mode_and_group "$secret_path") || fail 'PostgreSQL application URL metadata is unavailable'
  [ "$metadata" = "640:$secret_gid" ] \
    || fail 'PostgreSQL application URLs must be mode 0640 and use the administrator-provisioned application GID'
done

pg_network_name=$(sed -n 's/^PG_NETWORK_NAME=//p' "$postgres_env_file" | tail -n 1)
case "$pg_network_name" in *[!a-zA-Z0-9_.-]*|'') fail 'PostgreSQL private network name is invalid' ;; esac
docker network inspect "$pg_network_name" >/dev/null 2>&1 \
  || fail 'the prepared private PostgreSQL network is missing; deployment will not create it'
postgres_container=$(dc "$release" ps -q postgresql)
[ -n "$postgres_container" ] || fail 'the prepared PostgreSQL 18 service is not present in this Compose project'
postgres_state=$(docker inspect --format '{{.State.Status}}' "$postgres_container") \
  || fail 'could not inspect PostgreSQL state'
postgres_health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' "$postgres_container") \
  || fail 'could not inspect PostgreSQL health'
[ "$postgres_state" = running ] && [ "$postgres_health" = healthy ] \
  || fail 'PostgreSQL must already be running and healthy; the deployment will not start or recreate it'
postgres_mount=$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql"}}{{.Name}}:{{.RW}}{{end}}{{end}}' "$postgres_container") \
  || fail 'could not verify the PostgreSQL data-volume attachment'
[ "$postgres_mount" = "$postgres_volume:true" ] \
  || fail 'PostgreSQL is not using the administrator-prepared persistent data volume'
postgres_bindings=$(docker inspect --format '{{json .HostConfig.PortBindings}}' "$postgres_container") \
  || fail 'could not verify PostgreSQL port publication'
case "$postgres_bindings" in '{}'|'null'|'map[]'|'') ;; *) fail 'PostgreSQL must not publish a host port' ;; esac
pg18=$(docker exec "$postgres_container" psql --no-psqlrc --set ON_ERROR_STOP=1 \
  --username postgres --dbname postgres --tuples-only --no-align \
  --command "SELECT current_setting('server_version_num')::integer BETWEEN 180000 AND 189999" 2>/dev/null) \
  || fail 'PostgreSQL version could not be verified'
[ "$pg18" = t ] || fail 'the prepared database service is not PostgreSQL 18'

check_postgresql() {
  service=$1
  mode=$2
  case "$service:$mode" in
    migrate-sports-data:sports-schema|migrate-sports-data:sports-active)
      credential="$postgres_secret_dir/sports_data_migration_database_url"
      variable=SPORTS_DATA_MIGRATION_DATABASE_URL_FILE
      image=$sports_image
      run_user='0:0'
      extra_group=
      ;;
    migrate-video-service:video-schema|migrate-video-service:video-active)
      credential="$postgres_secret_dir/video_service_migration_database_url"
      variable=VIDEO_SERVICE_MIGRATION_DATABASE_URL_FILE
      image=$video_image
      run_user='0:0'
      extra_group=
      ;;
    sports-app:sports-app)
      credential="$postgres_secret_dir/sports_data_database_url"
      variable=SPORTS_DATA_DATABASE_URL_FILE
      image=$sports_image
      run_user='10001:10001'
      extra_group=$secret_gid
      ;;
    video-app:video-app)
      credential="$postgres_secret_dir/video_service_database_url"
      variable=VIDEO_SERVICE_DATABASE_URL_FILE
      image=$video_image
      run_user='10001:10001'
      extra_group=$secret_gid
      ;;
    *) fail 'invalid internal PostgreSQL preflight check' ;;
  esac
  if [ -n "$extra_group" ]; then
    docker run --rm --network "$pg_network_name" --user "$run_user" --group-add "$extra_group" \
      -v "$credential:/run/secrets/database-url:ro" \
      -v "$deployment_check:/rja-postgresql-check.py:ro" \
      -e "$variable=/run/secrets/database-url" --entrypoint python "$image" -B \
      /rja-postgresql-check.py "$mode"
  else
    docker run --rm --network "$pg_network_name" --user "$run_user" \
      -v "$credential:/run/secrets/database-url:ro" \
      -v "$deployment_check:/rja-postgresql-check.py:ro" \
      -e "$variable=/run/secrets/database-url" --entrypoint python "$image" -B \
      /rja-postgresql-check.py "$mode"
  fi
}

sports_schema=$(check_postgresql migrate-sports-data sports-schema 2>/dev/null) \
  || fail 'Sports Data PostgreSQL owner connection or pg0005 schema preflight failed'
[ "$sports_schema" = ok ] || fail 'Sports Data PostgreSQL schema is not at pg0005'
video_schema=$(check_postgresql migrate-video-service video-schema 2>/dev/null) \
  || fail 'Video Service PostgreSQL owner connection or vg0002 schema preflight failed'
[ "$video_schema" = ok ] || fail 'Video Service PostgreSQL schema is not at vg0002'
app_db_check=$(check_postgresql sports-app sports-app 2>/dev/null) \
  || fail 'Sports Data app credentials, database access or cross-database isolation preflight failed'
[ "$app_db_check" = ok ] || fail 'Sports Data PostgreSQL runtime role failed its isolation check'
app_db_check=$(check_postgresql video-app video-app 2>/dev/null) \
  || fail 'Video Service app credentials, database access or cross-database isolation preflight failed'
[ "$app_db_check" = ok ] || fail 'Video Service PostgreSQL runtime role failed its isolation check'

docker run --rm --network none --user 10001:10001 -v "$imports:/imports:ro" \
  --entrypoint python "$sports_image" -B -c "import os; os.listdir('/imports')" \
  >/dev/null 2>&1 || fail 'administrator-prepared shared/imports is not readable by UID 10001'

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
  printf 'Acquisition is healthy and owns its profile; using its startup integrity check while it runs.\n'
else
  docker run --rm --network none --user 10001:10001 -v "$profile_volume:/profiles" \
    --entrypoint python "$acquisition_image" -B -m acquisition.admin verify-profile \
    >/dev/null 2>&1 || fail 'stopped Acquisition profile verification failed; existing profile data was left untouched'
fi

docker run --rm --network none --user 10001:10001 -v "$jobs_volume:/acquisition-jobs:ro" \
  --entrypoint python "$acquisition_image" -B -c \
  "import sqlite3,sys; from pathlib import Path; db=Path('/acquisition-jobs/jobs.sqlite'); sys.exit(1) if not db.is_file() else None; c=sqlite3.connect(f'file:{db}?mode=ro',uri=True); ok=c.execute('PRAGMA integrity_check').fetchone()[0]=='ok' and c.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name='job'\").fetchone() is not None; sys.exit(0 if ok else 1)" \
  >/dev/null 2>&1 || fail 'acquisition jobs database is missing or invalid'
maintenance_marker=$(docker run --rm --network none --user 10001:10001 -v "$jobs_volume:/acquisition-jobs:ro" \
  --entrypoint python "$acquisition_image" -B -c \
  "from pathlib import Path; p=Path('/acquisition-jobs/deployment-maintenance'); print('present' if p.exists() or p.is_symlink() else 'absent')" \
  2>/dev/null) || fail 'could not inspect the Acquisition deployment-maintenance fence'
[ "$maintenance_marker" = absent ] \
  || fail 'an existing Acquisition maintenance fence requires administrator inspection before deployment'
docker run --rm --network none --user 10001:10001 -v "$jobs_volume:/acquisition-jobs" \
  --entrypoint python "$acquisition_image" -B -c \
  "import os,sys; sys.exit(0 if os.access('/acquisition-jobs', os.W_OK | os.X_OK) else 1)" \
  >/dev/null 2>&1 || fail 'acquisition jobs volume is not writable by UID 10001'
docker run --rm --network none --user 10001:10001 -v "$sports_operational_volume:/state" \
  --entrypoint python "$sports_image" -B -c \
  "import os,sys; sys.exit(0 if os.access('/state', os.W_OK | os.X_OK) else 1)" \
  >/dev/null 2>&1 || fail 'Sports Data operational volume is not writable by UID 10001'
docker run --rm --network none --user 10001:10001 -v "$video_operational_volume:/state" \
  --entrypoint python "$video_image" -B -c \
  "import os,sys; sys.exit(0 if os.access('/state', os.W_OK | os.X_OK) else 1)" \
  >/dev/null 2>&1 || fail 'Video Service operational volume is not writable by UID 10001'

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
  for service in dashboard sports-data acquisition video-service postgresql; do
    container_id=$(dc "$compose_dir" ps -q "$service")
    [ -n "$container_id" ] || return 1
    state=$(docker inspect --format '{{.State.Status}}' "$container_id") || return 1
    health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' "$container_id") || return 1
    [ "$state" = running ] && [ "$health" = healthy ] || return 1
  done
}

check_dashboard_endpoint() {
  dashboard_image=$1
  docker run --rm --network host --user node --entrypoint node "$dashboard_image" -e \
    "fetch('http://127.0.0.1:8090/real-jaen/api/v1/session',{redirect:'manual'}).then(async r=>{const b=await r.json();if(!r.ok||b.required!==true)process.exit(1)}).catch(()=>process.exit(1))" \
    >/dev/null 2>&1
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

resume_dashboard() {
  [ "$admission_closed" -eq 1 ] || return 0
  if [ "$dashboard_was_present" -eq 0 ]; then
    admission_closed=0
    return 0
  fi
  dc "$release" start dashboard >/dev/null 2>&1 || return 1
  check_service_health "$release" dashboard || return 1
  check_dashboard_endpoint "local/real-jaen-analytics/dashboard:$RJ_IMAGE_TAG" || return 1
  admission_closed=0
}

wait_for_idle_jobs() {
  elapsed=0
  idle=0
  while [ "$elapsed" -lt 1800 ]; do
    sports_count=$(check_postgresql migrate-sports-data sports-active 2>/dev/null) || return 1
    video_count=$(check_postgresql migrate-video-service video-active 2>/dev/null) || return 1
    acquisition_count=$(active_jobs) || return 1
    case "$sports_count:$video_count:$acquisition_count" in *[!0-9:]*|:*|*:) return 1 ;; esac
    if [ "$sports_count" -eq 0 ] && [ "$video_count" -eq 0 ] && [ "$acquisition_count" -eq 0 ]; then
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

close_admission() {
  admission_closed=1
  dashboard_id=$(dc "$release" ps -q dashboard) || return 1
  if [ -z "$dashboard_id" ]; then
    dashboard_was_present=0
    return 0
  fi
  dashboard_was_present=1
  dc "$release" stop dashboard >/dev/null 2>&1
}

create_maintenance_fence() {
  create_maintenance || return 1
  maintenance_active=1
  wait_for_idle_jobs
}

rollback_candidate() {
  [ "$has_previous" -eq 1 ] && [ "$previous_rollback_compatible" -eq 1 ] || return 1
  previous_dir="$root/releases/$previous_release"
  [ -d "$previous_dir" ] || return 1
  env_file="$config_base/$previous_config_target/.env.prod"
  postgres_env_file="$config_base/$previous_config_target/.env.postgresql.prod"
  secret_dir="$secrets_base/$previous_secret_target"
  export RJ_PROD_SECRET_DIR="$secret_dir" RJ_IMAGE_TAG="$previous_commit"
  export RJ_APP_SECRET_GID="$secret_gid"
  dc "$previous_dir" stop dashboard >/dev/null 2>&1 || return 1
  admission_closed=1
  wait_for_idle_jobs || return 1
  if [ "$maintenance_active" -eq 0 ]; then
    create_maintenance || return 1
    maintenance_active=1
  fi
  sports_schema=$(check_postgresql migrate-sports-data sports-schema 2>/dev/null) || return 1
  video_schema=$(check_postgresql migrate-video-service video-schema 2>/dev/null) || return 1
  [ "$sports_schema" = ok ] && [ "$video_schema" = ok ] || return 1

  pre_services=
  post_services=
  for service in $service_list; do
    case "$service" in
      sports-data|dashboard) post_services="${post_services}${post_services:+ }$service" ;;
      *) pre_services="${pre_services}${pre_services:+ }$service" ;;
    esac
  done
  if [ -n "$pre_services" ]; then
    up_services "$previous_dir" 1 $pre_services || return 1
  fi
  check_service_health "$previous_dir" acquisition || return 1
  check_service_health "$previous_dir" video-service || return 1
  clear_maintenance || return 1
  if [ -n "$post_services" ]; then
    up_services "$previous_dir" 1 $post_services || return 1
  fi
  check_health "$previous_dir" || return 1
  check_dashboard_endpoint "local/real-jaen-analytics/dashboard:$previous_commit" || return 1
  restore_previous_links || return 1
  admission_closed=0
  docker image tag "local/real-jaen-analytics/sports-data:$previous_commit" local/real-jaen-analytics/sports-data:prod >/dev/null 2>&1 || true
  docker image tag "local/real-jaen-analytics/acquisition:$previous_commit" local/real-jaen-analytics/acquisition:prod >/dev/null 2>&1 || true
  docker image tag "local/real-jaen-analytics/video-service:$previous_commit" local/real-jaen-analytics/video-service:prod >/dev/null 2>&1 || true
  docker image tag "local/real-jaen-analytics/dashboard:$previous_commit" local/real-jaen-analytics/dashboard:prod >/dev/null 2>&1 || true
  maintenance_active=0
  containers_mutated=0
  recovery_complete=1
  return 0
}

if [ "$needs_update" -eq 1 ]; then
  close_admission || fail 'could not stop Dashboard to close update and video-job admission'
  wait_for_idle_jobs || fail 'active PostgreSQL or Acquisition jobs did not drain; deployment cancelled without replacing services'
  create_maintenance_fence || fail 'could not establish and verify the Acquisition deployment maintenance fence'
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
  if check_health "$release" \
      && check_dashboard_endpoint "local/real-jaen-analytics/dashboard:$commit_sha"; then
    candidate_ok=1
  fi
else
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
      && check_service_health "$release" video-service \
      && clear_maintenance \
      && { [ -z "$post_services" ] || up_services "$release" "$force_recreate" $post_services; } \
      && check_health "$release" \
      && check_dashboard_endpoint "$dashboard_image"; then
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
    printf 'database_family=postgresql\n'
    printf 'sports_schema_revision=pg0005\n'
    printf 'video_schema_revision=vg0002\n'
    printf 'config_target=%s\n' "$config_target"
    printf 'secret_target=%s\n' "$secret_target"
  } > "$metadata_tmp"
  chmod 600 "$metadata_tmp"
  deployment_ready=1
  if mv -f "$metadata_tmp" "$last_good"; then
    deployment_finalized=1
    maintenance_active=0
    admission_closed=0
    prune_old_releases
    printf 'Deployment %s is healthy; persistent data volumes were preserved.\n' "$commit_sha"
    exit 0
  fi
  if [ "$previous_rollback_compatible" -eq 1 ] && rollback_candidate; then
    fail 'last-good metadata could not be committed; the previous PostgreSQL-compatible code/config and services were restored'
  fi
  preserve_candidate_state=1
  recovery_complete=1
  if [ "$has_previous" -eq 1 ]; then
    fail 'last-good metadata could not be committed; candidate paths remain current for inspection and no SQLite fallback was started'
  fi
  fail 'last-good metadata could not be committed and no prior release exists; the healthy candidate remains current for inspection'
fi

if [ "$containers_mutated" -eq 0 ]; then
  fail 'deployment health check failed; active services and current configuration were not changed'
fi

if [ "$has_previous" -eq 1 ] && [ "$previous_rollback_compatible" -eq 1 ]; then
  if rollback_candidate; then
    fail 'deployment failed; previous PostgreSQL-compatible code/config was restarted. Database schemas and volumes were left unchanged'
  fi
  preserve_candidate_state=1
  recovery_complete=1
  fail 'deployment and prior-code recovery both failed; candidate paths remain current for inspection. Database volumes were not removed'
fi
fail 'deployment failed and no PostgreSQL-compatible previous release is recorded; Dashboard remains stopped for inspection. PostgreSQL was not restarted or modified'
