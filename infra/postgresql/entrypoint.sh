#!/usr/bin/env bash
set -Eeuo pipefail

# Docker Compose's local file-backed secrets retain host ownership and mode.
# Copy them to tmpfs as postgres-readable files before the official entrypoint
# drops privileges. Nothing is written into the persistent database volume.
if [[ "$(id -u)" == 0 ]]; then
  install -d -o postgres -g postgres -m 0700 /run/postgresql-init-secrets
  for name in \
    postgres_admin_password \
    sports_data_owner_password sports_data_app_password \
    video_service_owner_password video_service_app_password; do
    install -o postgres -g postgres -m 0400 \
      "/run/secrets/$name" "/run/postgresql-init-secrets/$name"
  done
  export POSTGRES_PASSWORD_FILE=/run/postgresql-init-secrets/postgres_admin_password
fi

rm -f -- "${PGDATA}/.rja-bootstrap-complete"
/usr/local/bin/docker-entrypoint.sh "$@" &
entrypoint_pid=$!
forward_signal() {
  kill -s "$1" "$entrypoint_pid" 2>/dev/null || true
}
trap 'forward_signal TERM' TERM
trap 'forward_signal INT' INT
trap 'forward_signal QUIT' QUIT

# The official entrypoint keeps its shell process while it runs initdb scripts
# and the temporary postmaster. It replaces that process with the definitive
# server only after initialization and shutdown of the temporary server.
while kill -0 "$entrypoint_pid" 2>/dev/null; do
  if [[ -r "/proc/${entrypoint_pid}/comm" ]] \
      && [[ "$(cat "/proc/${entrypoint_pid}/comm")" == postgres ]] \
      && pg_isready --host=127.0.0.1 --username="${POSTGRES_USER:-postgres}" --dbname=postgres >/dev/null 2>&1; then
    touch "${PGDATA}/.rja-bootstrap-complete"
    chmod 600 "${PGDATA}/.rja-bootstrap-complete"
    break
  fi
  sleep 0.2
done

wait "$entrypoint_pid"
