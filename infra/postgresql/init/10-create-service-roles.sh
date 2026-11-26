#!/usr/bin/env bash
set -Eeuo pipefail

secret_value() {
  local path="$1" value
  [[ -r "$path" ]] || { printf '%s\n' 'Falta un secreto PostgreSQL de inicialización.' >&2; exit 1; }
  IFS= read -r value < "$path" || [[ -n "$value" ]]
  [[ -n "$value" ]] || { printf '%s\n' 'Un secreto PostgreSQL de inicialización está vacío.' >&2; exit 1; }
  printf '%s' "$value"
}

export SPORTS_DATA_OWNER_PASSWORD="$(secret_value /run/postgresql-init-secrets/sports_data_owner_password)"
export SPORTS_DATA_APP_PASSWORD="$(secret_value /run/postgresql-init-secrets/sports_data_app_password)"
export VIDEO_SERVICE_OWNER_PASSWORD="$(secret_value /run/postgresql-init-secrets/video_service_owner_password)"
export VIDEO_SERVICE_APP_PASSWORD="$(secret_value /run/postgresql-init-secrets/video_service_app_password)"

passwords=(
  "$SPORTS_DATA_OWNER_PASSWORD" "$SPORTS_DATA_APP_PASSWORD"
  "$VIDEO_SERVICE_OWNER_PASSWORD" "$VIDEO_SERVICE_APP_PASSWORD"
)
for ((i = 0; i < ${#passwords[@]}; i++)); do
  for ((j = i + 1; j < ${#passwords[@]}; j++)); do
    [[ "${passwords[i]}" != "${passwords[j]}" ]] || {
      printf '%s\n' 'Los secretos de roles PostgreSQL deben ser distintos.' >&2
      exit 1
    }
  done
done

psql --no-psqlrc --set ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres --quiet <<'SQL'
\getenv sports_data_owner_password SPORTS_DATA_OWNER_PASSWORD
\getenv sports_data_app_password SPORTS_DATA_APP_PASSWORD
\getenv video_service_owner_password VIDEO_SERVICE_OWNER_PASSWORD
\getenv video_service_app_password VIDEO_SERVICE_APP_PASSWORD

SELECT format('CREATE ROLE sports_data_owner LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT', :'sports_data_owner_password') \gexec
SELECT format('CREATE ROLE sports_data_app LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT', :'sports_data_app_password') \gexec
SELECT format('CREATE ROLE video_service_owner LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT', :'video_service_owner_password') \gexec
SELECT format('CREATE ROLE video_service_app LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT', :'video_service_app_password') \gexec

SELECT 'CREATE DATABASE sports_data OWNER sports_data_owner TEMPLATE template0 ENCODING ''UTF8''' \gexec
SELECT 'CREATE DATABASE video_service OWNER video_service_owner TEMPLATE template0 ENCODING ''UTF8''' \gexec

REVOKE ALL PRIVILEGES ON DATABASE sports_data FROM PUBLIC;
REVOKE ALL PRIVILEGES ON DATABASE video_service FROM PUBLIC;
GRANT CONNECT ON DATABASE sports_data TO sports_data_owner, sports_data_app;
GRANT CONNECT ON DATABASE video_service TO video_service_owner, video_service_app;

\connect sports_data
REVOKE ALL PRIVILEGES ON SCHEMA public FROM PUBLIC;
\connect video_service
REVOKE ALL PRIVILEGES ON SCHEMA public FROM PUBLIC;
SQL

touch "$PGDATA/.rja-bootstrap-complete"
chmod 600 "$PGDATA/.rja-bootstrap-complete"
