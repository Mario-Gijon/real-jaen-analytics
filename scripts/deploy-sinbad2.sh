#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
export LC_ALL=C

fail() {
  printf 'SINBAD2 deploy: %s\n' "$1" >&2
  exit 1
}

mode=${1:-}
case "$mode" in
  deploy|bootstrap) ;;
  *) fail 'internal mode must be deploy or bootstrap' ;;
esac

for tool in bash git ssh ssh-keygen tar mktemp install tr; do
  command -v "$tool" >/dev/null 2>&1 || fail "required runner command '$tool' is missing"
done

require_file_variable() {
  local name=$1 path=${!1:-}
  [[ -n "$path" && -f "$path" && ! -L "$path" && -s "$path" ]] \
    || fail "$name must be a non-empty GitLab File variable"
}

require_file_variable ENV_PROD
require_file_variable APP_ACCESS_PASSWORD
require_file_variable APP_UPDATE_PASSWORD
require_file_variable APP_SESSION_SECRET
require_file_variable APP_CONTROL_TOKEN
require_file_variable APP_ACQUISITION_TOKEN

[[ ${SSH_HOST:-} =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ ]] || fail 'SSH_HOST must be a hostname or IPv4 address'
[[ ${REMOTE_USER:-} =~ ^[A-Za-z_][A-Za-z0-9_.-]*$ ]] || fail 'REMOTE_USER has an invalid format'
[[ -n ${SSH_PRIVATE_KEY:-} ]] || fail 'SSH_PRIVATE_KEY is missing; provide a private key as a File variable or multiline Variable'
_rja_ssh_key_input=$SSH_PRIVATE_KEY
unset SSH_PRIVATE_KEY
[[ ${CI_COMMIT_SHA:-} =~ ^[[:xdigit:]]{40,64}$ ]] || fail 'CI_COMMIT_SHA is missing or invalid'
[[ ${CI_PIPELINE_ID:-} =~ ^[0-9]+$ ]] || fail 'CI_PIPELINE_ID is missing or invalid'
[[ ${CI_JOB_ID:-} =~ ^[0-9]+$ ]] || fail 'CI_JOB_ID is missing or invalid'

check_secret_file() {
  local name=$1 path=$2 minimum=$3 value
  value=$(<"$path")
  (( ${#value} >= minimum && ${#value} <= 256 )) \
    || fail "$name must contain between $minimum and 256 characters"
  [[ $value =~ ^[!-~]+$ ]] || fail "$name must contain printable ASCII without whitespace"
  printf -v "$4" '%s' "$value"
}

check_secret_file APP_ACCESS_PASSWORD "$APP_ACCESS_PASSWORD" 12 access_value
check_secret_file APP_UPDATE_PASSWORD "$APP_UPDATE_PASSWORD" 9 update_value
check_secret_file APP_SESSION_SECRET "$APP_SESSION_SECRET" 32 session_value
check_secret_file APP_CONTROL_TOKEN "$APP_CONTROL_TOKEN" 32 control_value
check_secret_file APP_ACQUISITION_TOKEN "$APP_ACQUISITION_TOKEN" 32 acquisition_value
[[ $access_value != "$update_value" ]] || fail 'APP_ACCESS_PASSWORD and APP_UPDATE_PASSWORD must differ'
[[ $control_value != "$acquisition_value" ]] || fail 'APP_CONTROL_TOKEN and APP_ACQUISITION_TOKEN must differ'
unset access_value update_value session_value control_value acquisition_value

release_id="${CI_COMMIT_SHA}-${CI_PIPELINE_ID}-${CI_JOB_ID}"
target="${REMOTE_USER}@${SSH_HOST}"
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
[[ -f "$script_dir/deploy-sinbad2-remote.sh" ]] || fail 'remote deployment script is missing from the checkout'

work_dir=$(mktemp -d "${TMPDIR:-/tmp}/rja-sinbad2.XXXXXXXX")
cleanup() {
  rm -rf -- "$work_dir"
}
trap cleanup EXIT

private_bundle="$work_dir/private"
mkdir -m 700 -p "$private_bundle/secrets/session"
install -m 600 -- "$ENV_PROD" "$private_bundle/.env.prod"
install -m 600 -- "$APP_ACCESS_PASSWORD" "$private_bundle/secrets/access-password"
install -m 600 -- "$APP_UPDATE_PASSWORD" "$private_bundle/secrets/update-password"
install -m 600 -- "$APP_SESSION_SECRET" "$private_bundle/secrets/session/session-secret"
install -m 600 -- "$APP_CONTROL_TOKEN" "$private_bundle/secrets/control-token"
install -m 600 -- "$APP_ACQUISITION_TOKEN" "$private_bundle/secrets/acquisition-token"

ssh_dir="$work_dir/ssh"
mkdir -m 700 "$ssh_dir"
ssh_key_path="$ssh_dir/deploy_key"
ssh_known_hosts_path="$ssh_dir/known_hosts"

if [[ -f "$_rja_ssh_key_input" ]]; then
  [[ ! -L "$_rja_ssh_key_input" && -r "$_rja_ssh_key_input" ]] \
    || fail 'SSH_PRIVATE_KEY File variable must be a readable regular file'
  tr -d '\r' < "$_rja_ssh_key_input" > "$ssh_key_path"
elif [[ "$_rja_ssh_key_input" == *'-----BEGIN '* && "$_rja_ssh_key_input" == *'PRIVATE KEY-----'* ]]; then
  private_key_text=${_rja_ssh_key_input//$'\r'/}
  printf '%s' "$private_key_text" > "$ssh_key_path"
else
  fail 'SSH_PRIVATE_KEY must be a File path or contain a multiline private key'
fi
unset _rja_ssh_key_input private_key_text
chmod 600 "$ssh_key_path"
ssh-keygen -y -f "$ssh_key_path" </dev/null >/dev/null 2>&1 \
  || fail 'SSH_PRIVATE_KEY is invalid or requires a passphrase'

known_hosts_value=${SSH_KNOWN_HOSTS:-}
if [[ -n "$known_hosts_value" ]]; then
  if [[ -f "$known_hosts_value" ]]; then
    [[ ! -L "$known_hosts_value" && -r "$known_hosts_value" ]] \
      || fail 'SSH_KNOWN_HOSTS File variable must be a readable regular file'
    tr -d '\r' < "$known_hosts_value" > "$ssh_known_hosts_path"
  else
    known_hosts_text=${known_hosts_value//$'\r'/}
    printf '%s' "$known_hosts_text" > "$ssh_known_hosts_path"
    unset known_hosts_text
  fi
elif [[ -n ${HOME:-} && -f "$HOME/.ssh/known_hosts" && -r "$HOME/.ssh/known_hosts" ]]; then
  tr -d '\r' < "$HOME/.ssh/known_hosts" > "$ssh_known_hosts_path"
else
  fail 'No trusted SSH host key is configured; register the server public key after verifying its fingerprint out of band'
fi
unset SSH_KNOWN_HOSTS known_hosts_value
chmod 600 "$ssh_known_hosts_path"
ssh-keygen -lf "$ssh_known_hosts_path" >/dev/null 2>&1 \
  || fail 'SSH_KNOWN_HOSTS is not a valid known_hosts file'
ssh-keygen -F "$SSH_HOST" -f "$ssh_known_hosts_path" >/dev/null 2>&1 \
  || fail 'No trusted SSH host key matches SSH_HOST; register the verified server public key before deploying'

ssh_remote() {
  ssh \
    -i "$ssh_key_path" \
    -o IdentitiesOnly=yes \
    -o BatchMode=yes \
    -o ConnectTimeout=15 \
    -o ServerAliveInterval=20 \
    -o ServerAliveCountMax=3 \
    -o StrictHostKeyChecking=yes \
    -o UserKnownHostsFile="$ssh_known_hosts_path" \
    -o GlobalKnownHostsFile=/dev/null \
    "$@"
}

git archive --format=tar "$CI_COMMIT_SHA" |
  ssh_remote "$target" \
    "set -eu; root=\"\$HOME/deploy_real-jaen-analytics\"; test ! -L \"\$root\" || { echo 'Deployment root must not be a symbolic link' >&2; exit 1; }; mkdir -p \"\$root/releases\"; chmod 700 \"\$root\"; test ! -L \"\$root/releases\" || { echo 'Release directory must not be a symbolic link' >&2; exit 1; }; release=\"\$root/releases/$release_id\"; temporary=\"\$root/releases/.$release_id.tmp\"; test ! -e \"\$release\" && test ! -e \"\$temporary\" || { echo 'Release path already exists' >&2; exit 1; }; mkdir \"\$temporary\"; trap 'rm -rf \"\$temporary\"' EXIT HUP INT TERM; tar -xf - -C \"\$temporary\"; mv \"\$temporary\" \"\$release\"; trap - EXIT HUP INT TERM"

tar -C "$private_bundle" -cf - . |
  ssh_remote "$target" \
    "set -eu; root=\"\$HOME/deploy_real-jaen-analytics\"; test ! -L \"\$root\" || { echo 'Deployment root must not be a symbolic link' >&2; exit 1; }; mkdir -p \"\$root/shared/incoming\"; test ! -L \"\$root/shared\" && test ! -L \"\$root/shared/incoming\" || { echo 'Private staging path must not be a symbolic link' >&2; exit 1; }; chmod 700 \"\$root\" \"\$root/shared\" \"\$root/shared/incoming\"; incoming=\"\$root/shared/incoming/$release_id\"; temp=\"\$root/shared/incoming/.$release_id.tmp\"; test ! -e \"\$incoming\" && test ! -e \"\$temp\" || { echo 'Private staging path already exists' >&2; exit 1; }; mkdir -m 700 \"\$temp\"; trap 'rm -rf \"\$temp\"' EXIT HUP INT TERM; tar -xf - -C \"\$temp\"; mv \"\$temp\" \"\$incoming\"; trap - EXIT HUP INT TERM"

ssh_remote "$target" sh -s -- "$mode" "$release_id" "$CI_COMMIT_SHA" "$CI_PIPELINE_ID" \
  < "$script_dir/deploy-sinbad2-remote.sh"
