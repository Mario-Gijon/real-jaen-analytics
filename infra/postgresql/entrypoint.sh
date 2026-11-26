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

exec /usr/local/bin/docker-entrypoint.sh "$@"
