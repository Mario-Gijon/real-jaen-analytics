#!/usr/bin/env bash
set -Eeuo pipefail

database_secret_dir=${1:?Uso: prepare-postgresql-app-secrets.sh PG_SECRET_DIR [APP_SECRET_DIR]}
app_secret_dir=${2:-}
files=(
  "$database_secret_dir/sports_data_database_url"
  "$database_secret_dir/video_service_database_url"
)
if [[ -n "$app_secret_dir" ]]; then
  files+=("$app_secret_dir/control-token" "$app_secret_dir/acquisition-token")
fi

secret_gid=
for path in "${files[@]}"; do
  if [[ ! -f "$path" || -L "$path" ]] || [[ "$(stat -c '%u' -- "$path")" != "$EUID" ]]; then
    printf '%s\n' 'Se requieren archivos regulares propiedad del usuario actual; no se cambiaron permisos.' >&2
    exit 1
  fi
  file_gid=$(stat -c '%g' -- "$path")
  if [[ -z "$secret_gid" ]]; then
    secret_gid=$file_gid
  elif [[ "$file_gid" != "$secret_gid" ]]; then
    printf '%s\n' 'Los archivos deben compartir un grupo privado para usar un GID suplementario único.' >&2
    exit 1
  fi
done

group_members=$(getent group "$secret_gid" | cut -d: -f4)
primary_group_is_private=$(getent passwd | awk -F: -v gid="$secret_gid" -v uid="$EUID" \
  '$4 == gid { count++; if ($3 == uid) owner = 1 } END { print (count == 1 && owner == 1) ? "yes" : "no" }')
if [[ -n "$group_members" || "$primary_group_is_private" != yes ]]; then
  printf '%s\n' 'El grupo de los secretos no es privado para el usuario actual; no se cambiaron permisos.' >&2
  exit 1
fi

for path in "${files[@]}"; do
  chmod 0640 -- "$path"
done

printf 'RJ_APP_SECRET_GID=%s\n' "$secret_gid"
