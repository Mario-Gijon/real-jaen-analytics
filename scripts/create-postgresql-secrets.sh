#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

target=${1:?Uso: create-postgresql-secrets.sh DIRECTORIO_SEGURO}
if ! command -v openssl >/dev/null 2>&1; then
  printf '%s\n' 'Se necesita openssl para generar secretos aleatorios.' >&2
  exit 1
fi

mkdir -p -- "$target"
chmod 700 -- "$target"
for name in \
  postgres_admin_password \
  sports_data_owner_password sports_data_app_password \
  video_service_owner_password video_service_app_password \
  sports_data_database_url sports_data_migration_database_url \
  video_service_database_url video_service_migration_database_url; do
  if [[ -e "$target/$name" || -L "$target/$name" ]]; then
    printf '%s\n' 'El directorio ya contiene credenciales; no se sobrescribirá ningún secreto.' >&2
    exit 1
  fi
done

temporary=$(mktemp -d "$target/.postgresql-secrets.XXXXXX")
trap 'rm -rf -- "$temporary"' EXIT

postgres_admin_password=$(openssl rand -hex 32)
sports_data_owner_password=$(openssl rand -hex 32)
sports_data_app_password=$(openssl rand -hex 32)
video_service_owner_password=$(openssl rand -hex 32)
video_service_app_password=$(openssl rand -hex 32)

write_secret() {
  printf '%s' "$2" > "$temporary/$1"
  chmod 600 -- "$temporary/$1"
}

write_secret postgres_admin_password "$postgres_admin_password"
write_secret sports_data_owner_password "$sports_data_owner_password"
write_secret sports_data_app_password "$sports_data_app_password"
write_secret video_service_owner_password "$video_service_owner_password"
write_secret video_service_app_password "$video_service_app_password"
write_secret sports_data_database_url \
  "postgresql://sports_data_app:${sports_data_app_password}@postgresql:5432/sports_data"
write_secret sports_data_migration_database_url \
  "postgresql://sports_data_owner:${sports_data_owner_password}@postgresql:5432/sports_data"
write_secret video_service_database_url \
  "postgresql://video_service_app:${video_service_app_password}@postgresql:5432/video_service"
write_secret video_service_migration_database_url \
  "postgresql://video_service_owner:${video_service_owner_password}@postgresql:5432/video_service"

for name in "$temporary"/*; do
  mv -- "$name" "$target/"
done
rmdir -- "$temporary"
trap - EXIT
chmod 700 -- "$target"
