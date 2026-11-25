# Funciones de respaldo-instalacion.sh (POSIX sh, se cargan con `.`).
# Requieren ROOT_DIR y la función dc() (docker compose con el --env-file y los archivos de la instalación).

# Nombres efectivos de la instalación, de la configuración que Compose resuelve (archivos, --env-file, entorno y
# valores por defecto), nunca supuestos: proyecto, imágenes, volúmenes, redes, puerto y directorios externos. La salida
# de `config` se analiza con
# sports-data/scripts/instalacion_compose.py en un contenedor efímero sin red de la imagen de Sports Data, sin volúmenes.
resolver_instalacion() {
  _helper="$ROOT_DIR/sports-data/scripts/instalacion_compose.py"
  test -f "$_helper" || { echo "No existe $_helper" >&2; exit 1; }
  _image=$(dc config --images sports-data) || { echo "Compose no pudo resolver la configuración de la instalación" >&2; exit 1; }
  case "$_image" in ''|*[[:space:]]*) echo "No se pudo determinar la imagen de sports-data: $_image" >&2; exit 1 ;; esac
  docker image inspect "$_image" >/dev/null 2>&1 || { echo "No existe la imagen $_image" >&2; exit 1; }
  _config=$(dc config --format json) || { echo "Compose no pudo resolver la configuración de la instalación" >&2; exit 1; }
  case "$(uname -s)" in MINGW*|MSYS*) _helper=$(cygpath -m "$_helper") ;; esac
  _resolved=$(printf '%s\n' "$_config" | MSYS_NO_PATHCONV=1 docker run --rm -i --network none \
    -v "$_helper:/opt/rja/instalacion_compose.py:ro" --entrypoint python "$_image" -I -B /opt/rja/instalacion_compose.py) ||
    { echo "La configuración de Compose no describe una instalación válida" >&2; exit 1; }
  _value() { printf '%s\n' "$_resolved" | sed -n "s/^$1=//p"; }
  PROJECT=$(_value project)
  SPORTS_DATA_IMAGE=$(_value sports_data_image)
  ACQUISITION_IMAGE=$(_value acquisition_image)
  VIDEO_SERVICE_IMAGE=$(_value video_service_image)
  STATE_VOLUME=$(_value sports_state)
  JOBS_VOLUME=$(_value acquisition_jobs)
  PROFILE_VOLUME=$(_value acquisition_profiles)
  VIDEO_VOLUME=$(_value video_state)
  IMPORT_DIR=$(_value imports)
  VIDEO_CATALOG_DIR=$(_value video_catalog_dir)
  VIDEO_MANIFEST=$(_value video_manifest)
  VOLUME_NAMES=$(_value volume_names)
  NETWORK_NAMES=$(_value network_names)
  DASHBOARD_BIND=$(_value dashboard_bind)
  DASHBOARD_PORT=$(_value dashboard_port)
  for _v in "$PROJECT" "$SPORTS_DATA_IMAGE" "$ACQUISITION_IMAGE" "$VIDEO_SERVICE_IMAGE" "$STATE_VOLUME" \
    "$JOBS_VOLUME" "$PROFILE_VOLUME" "$VIDEO_VOLUME" "$IMPORT_DIR" "$VIDEO_CATALOG_DIR" "$VIDEO_MANIFEST" \
    "$VOLUME_NAMES" "$NETWORK_NAMES" "$DASHBOARD_BIND" "$DASHBOARD_PORT"; do
    test -n "$_v" || { echo "No se pudieron determinar los nombres de la instalación" >&2; exit 1; }
  done
  test "$SPORTS_DATA_IMAGE" = "$_image" || { echo "Imagen de sports-data incoherente: $SPORTS_DATA_IMAGE / $_image" >&2; exit 1; }
  echo "Instalación: proyecto $PROJECT; volúmenes $STATE_VOLUME $JOBS_VOLUME $PROFILE_VOLUME $VIDEO_VOLUME"
}

# Un volumen de datos que se va a leer: debe existir (nunca se crea uno vacío al montarlo) y pertenecer al proyecto,
# por la etiqueta de Compose (volumen creado por `up`) o una etiqueta `rj.installation` de la restauración existente.
comprobar_volumen() {
  _labels=$(docker volume inspect -f '{{index .Labels "com.docker.compose.project"}}|{{index .Labels "rj.installation"}}' "$1" 2>/dev/null) ||
    { echo "No existe el volumen $1 de la instalación $PROJECT: no se crea ni se respalda" >&2; exit 1; }
  if [ "${_labels%%|*}" != "$PROJECT" ] && [ "${_labels#*|}" != "$PROJECT" ]; then
    echo "El volumen $1 no pertenece a la instalación $PROJECT (etiquetas: ${_labels:-ninguna}): no se toca" >&2
    exit 1
  fi
}
