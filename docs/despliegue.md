# Despliegue

El desarrollo mantiene su configuración SQLite habitual. El despliegue de SINBAD² combina `compose.prod.yaml`, `compose.postgresql.yaml` y `compose.postgresql.prod.yaml` para ejecutar los cinco servicios con PostgreSQL 18 persistente. Acquisition no inicia trabajos al arrancar; las actualizaciones deportivas y los trabajos audiovisuales requieren una acción manual.

Los archivos privados `.env.dev` y `.env.prod` se suministran aparte. Los ejemplos versionados no contienen contraseñas, tokens ni datos reales. El anfitrión Docker necesita acceso de red durante las compilaciones para obtener imágenes base y dependencias.

Acquisition construye su runtime de Chromium desde el Dockerfile. La revisión aprobada del SDK de SofaScore está vendorizada en `acquisition/vendor/sofascore_scraper.tar.gz`; el Dockerfile verifica su hash, revisión y manifiesto de fuentes. No hace falta descargar o etiquetar por separado una imagen de runtime.

## Desarrollo

Mario entrega a Álvaro un paquete privado que se extrae en la raíz del repositorio clonado. Incluye `.env.dev`, secretos de desarrollo, catálogo/calibraciones de vídeo y estados SQLite para reproducir la instalación. No incluye vídeos originales, perfiles del navegador ni cookies. Las rutas de desarrollo son relativas al repositorio.

```sh
docker compose --env-file .env.dev -f compose.dev.yaml up -d --build
docker compose --env-file .env.dev -f compose.dev.yaml ps
```

El Dashboard se publica en `http://localhost:33201`. Sports Data y Video Service copian sus semillas privadas solo si sus volúmenes están vacíos. Un volumen con datos no se restaura encima. Acquisition crea un perfil nuevo, sin sesiones del navegador. La lectura inicial de Sports Data puede tardar varios minutos; Compose espera a que finalice antes de iniciar Dashboard. Sports Data, Acquisition y Video Service no publican puertos al anfitrión.

Para detener o reanudar los contenedores sin eliminar datos:

```sh
docker compose --env-file .env.dev -f compose.dev.yaml stop
docker compose --env-file .env.dev -f compose.dev.yaml start
```

Las carpetas y claves del paquete están excluidas de Git. Los secretos de desarrollo no se comparten con producción. No se consulta SofaScore, descarga vídeo ni ejecuta OCR al iniciar.

## Producción en SINBAD²: PostgreSQL 18

La URL acordada es `https://sinbad2.ujaen.es/real-jaen`. Apache termina TLS y reenvía al Dashboard por `http://127.0.0.1:8090`; conserva `/real-jaen` en las rutas. Dashboard compila con Next.js `basePath=/real-jaen`. Solo Dashboard publica un puerto y únicamente en loopback; Sports Data, Acquisition, Video Service y PostgreSQL no publican puertos.

La pipeline contiene un solo job de producción y conserva la regla existente: solo un `push` a `main`, después de la solicitud de fusión aprobada, puede iniciar el despliegue. `resource_group: sinbad2-production` serializa los jobs, el job no es interrumpible y el host omite pipelines obsoletas. Los desarrolladores trabajan en ramas `feat`; solo administradores pueden fusionar a `main`. La protección de rama y de variables debe configurarla GitLab.

Compose combina siempre, en este orden, `compose.prod.yaml`, `compose.postgresql.yaml` y `compose.postgresql.prod.yaml`. Se requiere Docker Compose 2.24.4 o posterior por las etiquetas `!override` y `!reset` ([documentación de Docker](https://docs.docker.com/reference/compose-file/merge/)). El proyecto, red PostgreSQL y cinco volúmenes usan identidades estables de producción definidas por los ejemplos `.env.prod.example` y `.env.postgresql.prod.example`.

La cuenta de despliegue usa su `$HOME`, escritura allí y acceso directo a Docker; no requiere sudo. El árbol conserva releases y configuración versionada, además de estado compartido:

```text
$HOME/deploy_real-jaen-analytics/
├── releases/<commit>-<pipeline>-<job>/
└── shared/
    ├── config/versions/<release>/{.env.prod,.env.postgresql.prod}
    ├── config/current -> versions/<release>
    ├── secrets/versions/<release>/       # solo credenciales de la aplicación
    ├── secrets/current -> versions/<release>
    ├── postgresql/secrets/               # permanente, fuera de releases y rotaciones
    ├── imports/                          # administrador; lectura para UID 10001
    ├── incoming/                         # staging temporal
    └── deploy-state/last-good
```

### Preparación administrada previa

El pipeline rechaza el destino si falta cualquiera de estos volúmenes preparados: datos de PostgreSQL 18, estado operativo de Sports Data, estado operativo de Video Service, trabajos SQLite de Acquisition y perfil Chromium de Acquisition. No crea volúmenes, no restaura copias ni importa datos. Requiere PostgreSQL ya saludable, bases `sports_data` y `video_service`, revisión `pg0005`/`vg0002` y volúmenes montados con las identidades configuradas. Acquisition conserva legítimamente su SQLite de trabajos y perfil; Sports Data y Video Service no requieren sus antiguos archivos SQLite.

Administración debe preparar `shared/postgresql/secrets` con la cuenta de despliegue como propietaria y modo `0700`, de forma que el pipeline pueda validar los permisos sin transportar las credenciales. Incluye contraseña administrativa, contraseñas de los cuatro roles y las cuatro URLs (dos de ejecución y dos owner). Contraseñas y URLs owner/migración: `0600`. Solo las URLs de ejecución son legibles por UID `10001` mediante un GID complementario no privilegiado provisionado por administración: modo `0640` y GID indicado en `ENV_POSTGRESQL_PROD`. Sports Data y Video Service reciben ese grupo; Acquisition no lo recibe. El despliegue comprueba esos permisos sin cambiarlos. No se presupone un grupo privado, ACL ni sudo.

PostgreSQL recibe sus contraseñas de inicialización, las tareas de comprobación reciben únicamente la URL owner de su base y cada servicio recibe solo su URL de ejecución. Los secretos administrativos y owner nunca pasan por el Runner ni se montan en Dashboard, Acquisition o el microservicio ajeno. Las URLs internas usan el host de servicio `postgresql` en la red Docker privada; PostgreSQL no publica puertos. Las futuras conexiones remotas DEV requieren autorización separada, conectividad privada y TLS `verify-full`.

Administración también debe preparar `shared/imports`, volúmenes, red privada, credenciales del servidor, perfil y restauración inicial. El directorio de imports debe ser legible/traversable por UID `10001`. El directorio `shared/video-catalog` del env heredado solo satisface la interpolación del Compose base: el overlay PostgreSQL sustituye ese bind mount, por lo que el catálogo vive en la base y no requiere ese directorio. La restauración inicial se implementará y verificará en Hito 09A; no forma parte del job ordinario.

### Secuencia protegida del despliegue

1. Comprueba Compose efectivo, permisos de secretos, las cinco identidades persistentes, PostgreSQL 18 saludable, rol owner de cada base, revisiones esperadas, conexión de cada rol app a su base y rechazo de acceso a la base ajena. Las comprobaciones de base son de solo lectura.
2. Construye únicamente Sports Data, Acquisition, Video Service y Dashboard. PostgreSQL no se construye, inicia, detiene, actualiza ni fuerza a recrear.
3. Detiene Dashboard, que es el único punto de entrada publicado, para cerrar nuevas solicitudes deportivas y audiovisuales. Consulta en PostgreSQL los trabajos compartidos de Sports Data y Video Service y en solo lectura los trabajos SQLite de Acquisition. Espera a que terminen y exige cinco lecturas consecutivas sin trabajos. Si no se vacían en 30 minutos, cancela antes de reemplazar servicios y reabre Dashboard.
4. Escribe el marcador de mantenimiento durable de Acquisition, que impide aceptar trabajo nuevo, y vuelve a comprobar el estado compartido. No modifica leases ni fencing tokens y no bloquea heartbeat ni publicaciones.
5. Recrea primero Acquisition y Video Service; comprueba salud; retira el marcador y recrea Sports Data y Dashboard. El Dashboard se valida en `http://127.0.0.1:8090/real-jaen/api/v1/session`. Actualiza `last-good` solo tras comprobar los cinco servicios saludables.

No se ejecuta Alembic ni se hacen downgrades automáticos. Las futuras migraciones de esquema requieren un procedimiento explícito y una recuperación compatible. El rollback de código solo usa una release PostgreSQL marcada como compatible con `pg0005`/`vg0002`; no reinicia una release SQLite contra PostgreSQL y nunca restaura bases de datos. Si la revisión difiere, detiene el rollback automático y deja Dashboard cerrado para inspección.

Tras `last-good`, se conservan las cinco releases más recientes y las releases en uso por contenedores o bind mounts. PostgreSQL monta scripts desde la release con la que fue creado; el limpiador inspecciona los mounts de contenedores activos y conserva esa release. La limpieza no elimina volúmenes, backups, secretos ni ejecuta `docker system prune`/`docker volume prune`.

### Variables y responsabilidades pendientes

El Runner protegido necesita `bash`, `git`, `ssh`, `ssh-keygen`, `tar`, `mktemp`, `install` y `tr`. Administración entrega `SSH_KNOWN_HOSTS` verificado fuera de banda; se conserva `StrictHostKeyChecking=yes` y no se confía en `ssh-keyscan`. La clave SSH solo está disponible en ramas protegidas. No se necesita terminal interactiva ni sudo en SINBAD².

Como archivos protegidos de GitLab con ámbito `production` y sin expansión de referencias, configure:

| Variable | Uso |
|---|---|
| `ENV_PROD` | Configuración basada en `.env.prod.example`; sin credenciales. |
| `ENV_POSTGRESQL_PROD` | Configuración basada en `.env.postgresql.prod.example`; sustituir el GID por el valor confirmado por administración. Sin contraseñas ni URLs. |
| `APP_ACCESS_PASSWORD`, `APP_UPDATE_PASSWORD`, `APP_SESSION_SECRET`, `APP_CONTROL_TOKEN`, `APP_ACQUISITION_TOKEN` | Credenciales de aplicación en archivos File protegidos. |

Las credenciales PostgreSQL permanecen en el servidor y no se transportan en cada pipeline. Administración debe confirmar el host Docker, Compose 2.24.4+, red y volúmenes preparados, permisos de carpetas/secretos, restauración inicial y configuración de Apache (`Host`, `X-Forwarded-Proto: https`, preservación de `/real-jaen`). La autorización para alojamiento DEV compartido y su conectividad privada sigue pendiente.

No se accedió a SINBAD² ni GitLab, no se ejecutó CI Lint ni despliegue real en este hito. Antes del primer push a `main`, administración debe aplicar estas variables y preparar el destino; el código no supone que esos recursos existen.

### Inspección de una release

Desde el servidor, inspecciona `shared/deploy-state/last-good` y opera con los tres archivos Compose y los dos env files versionados de esa release. Los comandos siguientes solo consultan estado y logs:

```sh
export RJA_DEPLOY_ROOT="$HOME/deploy_real-jaen-analytics"
release_id=$(sed -n 's/^release_id=//p' "$RJA_DEPLOY_ROOT/shared/deploy-state/last-good")
export RJ_IMAGE_TAG=$(sed -n 's/^commit_sha=//p' "$RJA_DEPLOY_ROOT/shared/deploy-state/last-good")
config="$RJA_DEPLOY_ROOT/shared/config/current"
cd "$RJA_DEPLOY_ROOT/releases/$release_id"
docker compose --project-directory "$PWD" --env-file "$config/.env.prod" \
  --env-file "$config/.env.postgresql.prod" \
  -f compose.prod.yaml -f compose.postgresql.yaml -f compose.postgresql.prod.yaml ps
docker compose --project-directory "$PWD" --env-file "$config/.env.prod" \
  --env-file "$config/.env.postgresql.prod" \
  -f compose.prod.yaml -f compose.postgresql.yaml -f compose.postgresql.prod.yaml logs --tail=100 dashboard
```

No publiques los puertos internos, cambies nombres de proyecto/volúmenes ni ejecutes `docker compose down -v`.

## Respaldos y restauración excepcional

Los comandos SQLite de esta sección sirven solo para instalaciones SQLite antiguas y ensayos aislados. No los uses en el despliegue PostgreSQL de SINBAD². El backup/restauración PostgreSQL y la restauración inicial administrada corresponden al Hito 09A; el pipeline de código no invoca esos procedimientos.

El generador crea cada respaldo en una ubicación nueva y publica la carpeta solo cuando todos los archivos se han copiado, validado y sumado. Por defecto usa `.env.prod` y `compose.prod.yaml`. `COMPOSE_FILE` acepta varios archivos separados por `:` en Linux; Docker Compose aplica los overlays en ese orden y el script toma de la configuración resuelta el proyecto, la imagen y los nombres efectivos de los volúmenes.

Para la instalación local de desarrollo que usa los overlays existentes, ejecuta desde la raíz del repositorio. Exporta también `RJ_PHOTO_COLLECTIONS_DIR` con el mismo directorio externo que utiliza Acquisition al arrancar:

```sh
COMPOSE_FILE="compose.dev.yaml:$HOME/.config/real-jaen-analytics/compose.existing-state.yaml:compose.photos.yaml" \
ENV_FILE="$HOME/.config/real-jaen-analytics/dev.env" \
RJ_PHOTO_COLLECTIONS_DIR="$HOME/.config/real-jaen-analytics/photo-collections" \
RJ_DEV_VIDEO_CATALOG_DIR="$HOME/.config/real-jaen-analytics/video-data" \
  sh dashboard/scripts/respaldo-instalacion.sh \
  "$HOME/.config/real-jaen-analytics/backups/$(date +%Y%m%d-%H%M%S)"
```

Para una instalación SQLite antigua, ejecútalo desde su release y configuración privada. No es válido para el perfil PostgreSQL:

```sh
ENV_FILE=.env.prod sh dashboard/scripts/respaldo-instalacion.sh \
  "$HOME/backups/real-jaen-analytics/$(date +%Y%m%d-%H%M%S)"
```

El preflight resuelve Compose con esos archivos y variables; exige que los cuatro volúmenes persistentes existan, pertenezcan al proyecto y coincidan con los mounts de los contenedores existentes. Comprueba que no haya una restauración incompleta, una actualización deportiva, un trabajo de Acquisition o un trabajo de vídeo activos. Detiene primero Dashboard, repite las comprobaciones y, si la instalación está inactiva, detiene solo los contenedores que estaban en marcha. Al terminar —también ante fallos— inicia los mismos IDs con `docker start`, primero los servicios de datos y al final Dashboard. No ejecuta `compose up`, no reconstruye ni recrea servicios. Si una comprobación previa falla, no detiene contenedores ni crea una carpeta de respaldo.

El formato actual es la versión `2` del manifiesto e incluye `real-jaen.sqlite`, el diario `actualizaciones.tar`, `procedencia.tar` si existe, `acquisition-jobs.tar`, `video-service-state.tar`, `fotos.tar` si existe, `video-catalog.tar`, `copiar.json`, `manifest.json` y `SHA256SUMS`. El almacén fotográfico completo se copia desde `/state/player-photos/`, incluida `fotos.sqlite`, el historial y los blobs WebP; se excluye solo `tmp/`. `video-catalog.tar` conserva únicamente el manifiesto configurado y las calibraciones que este referencia desde `/app/data`; nunca incluye vídeos. Se comprueban la integridad SQLite, referencias, hash y tamaño de cada blob, y la igualdad de los archivos archivados. Los archivos de estado y los contenedores de origen permanecen sin cambios, salvo la copia de seguridad SQLite adicional que `persistencia copiar` crea en `/state/copias`.

No se incluyen secretos ni archivos de configuración con credenciales, el volumen de perfil de Acquisition y sus cookies, la caché `.next`, el resto de archivos de `VIDEO_SERVICE_DATA_DIR`, las colecciones originales de fotos ni los vídeos originales externos. Para una restauración habrá que aportar por separado la configuración, secretos, directorios externos de importaciones, colección original de fotos y archivos de vídeo. Las calibraciones creadas por trabajos se conservan en `video-service-state.tar`; el manifiesto y las calibraciones registradas se incluyen en `video-catalog.tar`.

La restauración admite el formato v2 completo y resuelve el proyecto, imágenes, puertos, redes, volúmenes y rutas externas desde la configuración efectiva de Compose. Antes de crear volúmenes, valida manifiesto, inventario, SHA-256, SQLite y todos los archivos tar. Se niega si hay contenedores del proyecto, cualquier volumen o red con los nombres efectivos, un montaje activo que se solape con `imports` o el catálogo externo, o si el puerto publicado por Dashboard ya está ocupado. Los backups anteriores a v2 se rechazan porque no acreditan la colección fotográfica ni el catálogo externo; genera un backup v2 para evitar perder datos silenciosamente.

La restauración SQLite exige un destino de catálogo de vídeo nuevo, elegido explícitamente con `RJ_RESTORE_VIDEO_CATALOG_DIR`, que coincida con `VIDEO_SERVICE_DATA_DIR`/`RJ_DEV_VIDEO_CATALOG_DIR` resuelto por Compose. No altera un directorio de catálogo existente. En el bootstrap de instalaciones SQLite antiguas, el manifiesto de ejemplo se valida dentro de la imagen y no se crea en esa ruta: el catálogo v2 recuperado ocupa el destino nuevo. Los vídeos originales no se incluyen: solo se recuperan el manifiesto y las calibraciones referenciadas.

Antes de crear el primer volumen, el script crea `imports/.real-jaen-restore-incomplete` con el proyecto, los volúmenes y el destino externo. Escribe además guardas dentro de los estados de Sports Data y Video Service. Si algo falla o se interrumpe, conserva el marcador, los volúmenes parciales y cualquier área de extracción para inspección; no borra datos automáticamente. Sports Data, Video Service, los preflights de backup y el despliegue normal se niegan a continuar mientras detecten la restauración incompleta. Solo tras validar la base y la generación aceptada por el coordinador, los trabajos SQLite, las fotos/blobs, el catálogo real con `VideoCatalog.load` y un perfil de Acquisition recién inicializado se retiran las guardas. El script nunca arranca los servicios.

### Ensayo aislado del backup v2 actual

Este ejemplo conserva intactos el backup original, los volúmenes y las carpetas de la instalación de desarrollo. Prepara un nuevo proyecto y prefijo de volúmenes, copia el backup a un directorio de importación aislado y da acceso de lectura solo a esa copia para UID `10001`. El overlay local existente fija el montaje de imports; se genera una copia temporal del overlay con ese único origen dirigido al ensayo. El directorio de semillas queda vacío: como las bases restauradas ya existen, los entrypoints de desarrollo solo las validan y no copian semillas encima.

```sh
set -eu
backup="$HOME/.config/real-jaen-analytics/backups/20261124-231513"
restore_root="$HOME/.config/real-jaen-analytics/restore-test"
imports="$restore_root/imports"
catalog="$restore_root/video-catalog-parent/catalog"
original_overlay="$HOME/.config/real-jaen-analytics/compose.existing-state.yaml"
overlay="$restore_root/compose.existing-state.yaml"
test -d "$backup" && test -f "$original_overlay"
test ! -e "$restore_root" || { echo "Ya existe $restore_root; inspecciona el ensayo anterior" >&2; exit 1; }
mkdir -p "$imports" "$restore_root/seed/sports" "$restore_root/seed/video" \
  "$restore_root/video-catalog-parent" "$restore_root/photo-collections"
chmod 755 "$imports"
cp -a "$backup" "$imports/backup-v2"
chmod -R a+rX "$imports/backup-v2"
sed "s#${HOME}/.config/real-jaen-analytics/imports#${imports}#g" \
  "$original_overlay" > "$overlay"
grep -Fq "$imports:/imports:ro" "$overlay"
test ! -e "$catalog" && test ! -L "$catalog"
python3 -c "import socket; s=socket.socket(); s.bind(('127.0.0.1',33401)); s.close()"

export ENV_FILE="$HOME/.config/real-jaen-analytics/dev.env"
export COMPOSE_FILES="compose.dev.yaml:$overlay:compose.photos.yaml"
export RJ_DEV_PROJECT_NAME=real-jaen-analytics-restore-test
export RJ_DEV_VOLUME_PREFIX=rja-restore-test
export RJ_DEV_DASHBOARD_PORT=33401
export RJ_DEV_IMPORT_DIR="$imports"
export RJ_DEV_SEED_DIR="$restore_root/seed"
export RJ_DEV_VIDEO_CATALOG_DIR="$catalog"
export RJ_PHOTO_COLLECTIONS_DIR="$restore_root/photo-collections"
export RJ_RESTORE_VIDEO_CATALOG_DIR="$catalog"

sh dashboard/scripts/restaurar-instalacion.sh "$imports/backup-v2"
```

Usa `dev.env` solo para secretos de desarrollo. Si alguna imagen local resuelta por Compose no está construida para el código de este checkout, constrúyela con las mismas variables exportadas antes de restaurar. La restauración no crea redes ni el volumen de caché de Next; Compose los crea al iniciar el ensayo. Tras revisar el mensaje de éxito, se puede iniciar manualmente el proyecto aislado y esperar healthchecks:

```sh
COMPOSE_FILE="$COMPOSE_FILES" docker compose --project-directory "$PWD" --env-file "$ENV_FILE" \
  up -d --build --wait
COMPOSE_FILE="$COMPOSE_FILES" docker compose --project-directory "$PWD" --env-file "$ENV_FILE" ps
```

`restaurar-instalacion.sh` comprueba también el puerto `33401`; la prueba de bind anterior permite detectar el conflicto antes de invocarla. Mantén las rutas y secretos de desarrollo originales fuera de los directorios de imports, catálogo, semillas y colecciones del ensayo.

Si una restauración SQLite falla o se interrumpe, no quites el marcador ni repitas sobre los mismos nombres. Léelo para obtener el proyecto, los cuatro volúmenes y la ruta externa; inspecciona esos recursos y conserva los logs. Los datos parciales quedan protegidos. Para un nuevo ensayo SQLite, prepara otro directorio externo, otro proyecto y otro prefijo de volúmenes; no borres los parciales hasta que hayas comprobado y preservado lo que contienen. Este procedimiento no corresponde al despliegue PostgreSQL de SINBAD².

En SINBAD², ejecútalo desde una release para que Compose use el mismo SHA de imágenes que la instalación activa y entrega la ruta privada de configuración explícitamente:

```sh
export RJA_DEPLOY_ROOT="$HOME/deploy_real-jaen-analytics"
RJ_IMAGE_TAG=$(sed -n 's/^commit_sha=//p' "$RJA_DEPLOY_ROOT/shared/deploy-state/last-good")
export RJ_IMAGE_TAG
release_id=$(sed -n 's/^release_id=//p' "$RJA_DEPLOY_ROOT/shared/deploy-state/last-good")
cd "$RJA_DEPLOY_ROOT/releases/$release_id"
ENV_FILE="$RJA_DEPLOY_ROOT/shared/config/current/.env.prod" \
  sh dashboard/scripts/respaldo-instalacion.sh \
  "$RJA_DEPLOY_ROOT/shared/imports/backups/$(date +%Y%m%d-%H%M%S)"
```

La restauración es únicamente para volúmenes nuevos. Comprueba sumas, integridad y generación, restaura la SQLite y estados persistentes, inicializa un perfil nuevo y deja los servicios detenidos. Nunca la ejecutes sobre volúmenes existentes ni como parte de una actualización normal.
