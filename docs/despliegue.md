# Despliegue

El repositorio tiene dos configuraciones Compose en la raíz. Ambas levantan Dashboard, Sports Data, Acquisition y Video Service. Acquisition no inicia trabajos al arrancar; las consultas deportivas y los trabajos de vídeo requieren una acción manual.

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

## Producción en SINBAD²

### Publicación y proxy

La URL pública acordada es `https://sinbad2.ujaen.es/real-jaen`. Apache termina TLS y reenvía al Dashboard por `http://127.0.0.1:8090`; el contenedor no configura TLS y el puerto de producción no escucha en interfaces externas. Solo Dashboard publica un puerto. Sports Data, Acquisition y Video Service permanecen en redes internas.

El Dashboard de producción se compila con Next.js `basePath=/real-jaen`; el desarrollo conserva la raíz `/` y el puerto `33201`. El prefijo forma parte de las rutas que Apache entrega a Next: el proxy debe preservar `/real-jaen` en la ruta y no quitarlo ni añadirlo una segunda vez. Next.js prefija los enlaces internos; el cliente del Dashboard prefija API, recursos, redirecciones y cookies. La ruta de salud del contenedor es `/real-jaen/api/v1/session`.

Antes del despliegue, administración debe confirmar que Apache reenvía el encabezado `Host` público y envía `X-Forwarded-Proto: https`. Esto permite que la validación de origen compare el mismo host y que la cookie de sesión incluya `Secure`. También debe confirmar cómo conserva o sobrescribe `X-Forwarded-For`. La aplicación no confía en cabeceras de IP por defecto; si se configura `RJ_TRUSTED_CLIENT_IP_HEADER`, Apache debe sobrescribir esa cabecera con un valor confiable. La configuración efectiva del proxy, sus reglas de ruta y su certificado no se han inspeccionado desde este repositorio.

### Pipeline y rutas del host

La pipeline contiene un único job de producción. `deploy_to_sinbad2` se ejecuta solo por un `push` a `main`, después de la fusión aprobada; no corre al abrir una merge request ni en ramas de desarrollo. Mantiene `resource_group: sinbad2-production`, no es interrumpible y el host omite pipelines obsoletas. No hay job de bootstrap en la pipeline.

El despliegue usa el home de la cuenta SSH sin sudo y conserva estas rutas existentes:

```text
$HOME/deploy_real-jaen-analytics/
├── releases/<commit>-<pipeline>-<job>/
└── shared/
    ├── config/versions/<release>/.env.prod
    ├── config/current -> versions/<release>
    ├── secrets/versions/<release>/
    ├── secrets/current -> versions/<release>
    ├── imports/
    ├── video-catalog/
    ├── incoming/                     # staging temporal de archivos privados
    └── deploy-state/last-good
```

Administración prepara `.env.prod`, los secretos y el estado persistente por el canal acordado. Debe confirmar que sus rutas y permisos corresponden a `$HOME/deploy_real-jaen-analytics/shared/` y que los cuatro volúmenes tienen exactamente los nombres de `.env.prod.example`. No se mueve nada a `/etc` o `/srv`. Las carpetas privadas de configuración y secretos quedan restringidas; `.env.prod` queda en `0600`, y los secretos se montan como solo lectura.

El job normal exige que existan los cuatro volúmenes, sean íntegros y compatibles, y que las rutas externas sean accesibles por UID `10001`. Nunca crea, inicializa, reemplaza ni restaura esos volúmenes. Si falta alguno o falla una comprobación, el preflight termina antes de cambiar los contenedores. `dashboard/scripts/restaurar-instalacion.sh` permanece como herramienta independiente para la preparación administrada; la pipeline no la invoca. No restaures sobre volúmenes existentes.

Los preflights comprueban la base deportiva, los trabajos de Acquisition, la base SQLite de Video Service, los secretos y el catálogo. Si Acquisition está levantado, el job respeta el bloqueo de su perfil y comprueba su healthcheck e integridad estructural sin abrir el perfil por segunda vez. Al recrear Acquisition, cierra temporalmente la admisión de nuevos trabajos y espera a que terminen los aceptados; no cancela trabajos. Una rotación de `ENV_PROD` o de secretos fuerza la recreación de los servicios consumidores aunque el SHA no cambie. Un fallo de build o preflight deja los contenedores activos; un fallo tras promover o iniciar la release intenta restaurar el estado saludable anterior sin tocar volúmenes.

Tras confirmar `last-good`, se conservan al menos las cinco releases de código recientes y además la activa y la release previa de recuperación. La limpieza solo elimina directorios de releases antiguas y valida que sean hijos directos de `releases/`; no toca configuración, secretos, importaciones, catálogo, redes, imágenes ni volúmenes. Si falla, escribe una advertencia y mantiene saludable el despliegue. No se ejecuta `docker system prune` ni `docker volume prune`.

### Variables y requisitos de GitLab/Runner

Las variables SSH existentes `SSH_PRIVATE_KEY`, `SSH_HOST` y `REMOTE_USER` pueden reutilizarse sin cambiar sus nombres ni tipos. `SSH_PRIVATE_KEY` acepta texto multilínea o variable File; el job normaliza CRLF, crea una copia temporal `0600` y valida la clave sin imprimirla. La clave pública correspondiente debe estar autorizada para `REMOTE_USER`.

Administración entregará la host key previamente verificada en `SSH_KNOWN_HOSTS` como File o texto. El job conserva `StrictHostKeyChecking=yes`; si falta una entrada confiable para `SSH_HOST`, falla antes de conectar. No usa `ssh-keyscan` para decidir en quién confiar.

En el proyecto GitLab `despliegues-autorizados/real-jaen-analytics`, deben configurarse como variables File protegidas, con ámbito `production` y sin expansión de referencias:

| Variable | Archivo/contenido esperado |
|---|---|
| `ENV_PROD` | Archivo completo basado en `.env.prod.example`, con bind `127.0.0.1`, puerto `8090`, nombres fijos de proyecto/red/volumen y rutas bajo `shared/`. No debe contener credenciales. |
| `APP_ACCESS_PASSWORD` | Contraseña de acceso, al menos 12 caracteres ASCII imprimibles. |
| `APP_UPDATE_PASSWORD` | Contraseña administrativa distinta, al menos 9 caracteres ASCII imprimibles. |
| `APP_SESSION_SECRET` | Secreto de sesión con al menos 32 caracteres ASCII imprimibles. |
| `APP_CONTROL_TOKEN` | Token interno con al menos 32 caracteres ASCII imprimibles. |
| `APP_ACQUISITION_TOKEN` | Token de Acquisition distinto del token de control, con al menos 32 caracteres ASCII imprimibles. |

Los cinco secretos aceptan una línea de hasta 256 caracteres y un salto de línea final opcional. No se escriben en Git, artifacts, logs, argumentos o imágenes.

El Runner necesita `bash`, `git`, `ssh`, `ssh-keygen`, `tar`, `mktemp`, `install` y `tr`, poder recibir variables protegidas de `production` y conectarse al host por SSH. La cuenta remota necesita escritura en `$HOME` y acceso directo a Docker; no se usa sudo. El host requiere Docker Engine, Compose v2.17 o posterior, permiso para builds/redes/volúmenes y salida a registros e índices de dependencias. El job mantiene sin cambios el proyecto, seis redes y cuatro volúmenes persistentes ya definidos.

### Requisitos que administración debe confirmar

- Que las variables protegidas llegan al entorno `production` y el Runner asignado dispone de las herramientas y conectividad indicadas.
- La disponibilidad y autorización de las tres variables SSH existentes y la entrega de `SSH_KNOWN_HOSTS` con la clave verificada.
- Que `$HOME/deploy_real-jaen-analytics/shared/`, sus permisos y los cuatro volúmenes son las ubicaciones preparadas, con datos íntegros y compatibles.
- Que Apache conserva `/real-jaen`, envía `Host` y `X-Forwarded-Proto: https` como se indica y publica únicamente el proxy TLS acordado.
- La creación de `ENV_PROD` y los cinco `APP_*` protegidos como File.

No se ha conectado con GitLab privado ni con SINBAD², no se ha ejecutado un despliegue y la configuración del Apache, Runner, variables, volúmenes y secretos sigue pendiente de validación por administración. CI Lint debe ejecutarse en el GitLab autohospedado antes de la primera publicación allí.

## Operaciones de Docker en producción

Para inspeccionar la release saludable desde el servidor, usa la carpeta indicada por `release_id` en `shared/deploy-state/last-good`; `.env.prod` se encuentra en el enlace estable `shared/config/current/.env.prod`. Carga el root de despliegue y el SHA de la última release antes de invocar Compose:

```sh
export RJA_DEPLOY_ROOT="$HOME/deploy_real-jaen-analytics"
RJ_IMAGE_TAG=$(sed -n 's/^commit_sha=//p' "$RJA_DEPLOY_ROOT/shared/deploy-state/last-good")
export RJ_IMAGE_TAG
cd "$RJA_DEPLOY_ROOT/releases/$(sed -n 's/^release_id=//p' "$RJA_DEPLOY_ROOT/shared/deploy-state/last-good")"
docker compose --project-directory "$PWD" \
  --env-file "$RJA_DEPLOY_ROOT/shared/config/current/.env.prod" \
  -f compose.prod.yaml ps
docker compose --project-directory "$PWD" \
  --env-file "$RJA_DEPLOY_ROOT/shared/config/current/.env.prod" \
  -f compose.prod.yaml logs --tail=100 dashboard
```

El `RJ_IMAGE_TAG` usado en operación manual debe ser el `commit_sha` registrado en `shared/deploy-state/last-good`; no confíes en `:prod` para identificar una release concreta. No publiques los puertos internos de Sports Data, Acquisition o Video Service. Mantén fijos proyecto, redes y volúmenes y no ejecutes `docker compose down -v`.

## Respaldos y restauración excepcional

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

En producción, ejecútalo desde la release que corresponde a la instalación activa y usa su `.env.prod` privado:

```sh
ENV_FILE=.env.prod sh dashboard/scripts/respaldo-instalacion.sh \
  "$HOME/backups/real-jaen-analytics/$(date +%Y%m%d-%H%M%S)"
```

El preflight resuelve Compose con esos archivos y variables; exige que los cuatro volúmenes persistentes existan, pertenezcan al proyecto y coincidan con los mounts de los contenedores existentes. Comprueba que no haya una restauración incompleta, una actualización deportiva, un trabajo de Acquisition o un trabajo de vídeo activos. Detiene primero Dashboard, repite las comprobaciones y, si la instalación está inactiva, detiene solo los contenedores que estaban en marcha. Al terminar —también ante fallos— inicia los mismos IDs con `docker start`, primero los servicios de datos y al final Dashboard. No ejecuta `compose up`, no reconstruye ni recrea servicios. Si una comprobación previa falla, no detiene contenedores ni crea una carpeta de respaldo.

El formato actual es la versión `2` del manifiesto e incluye `real-jaen.sqlite`, el diario `actualizaciones.tar`, `procedencia.tar` si existe, `acquisition-jobs.tar`, `video-service-state.tar`, `fotos.tar` si existe, `video-catalog.tar`, `copiar.json`, `manifest.json` y `SHA256SUMS`. El almacén fotográfico completo se copia desde `/state/player-photos/`, incluida `fotos.sqlite`, el historial y los blobs WebP; se excluye solo `tmp/`. `video-catalog.tar` conserva únicamente el manifiesto configurado y las calibraciones que este referencia desde `/app/data`; nunca incluye vídeos. Se comprueban la integridad SQLite, referencias, hash y tamaño de cada blob, y la igualdad de los archivos archivados. Los archivos de estado y los contenedores de origen permanecen sin cambios, salvo la copia de seguridad SQLite adicional que `persistencia copiar` crea en `/state/copias`.

No se incluyen secretos ni archivos de configuración con credenciales, el volumen de perfil de Acquisition y sus cookies, la caché `.next`, el resto de archivos de `VIDEO_SERVICE_DATA_DIR`, las colecciones originales de fotos ni los vídeos originales externos. Para una restauración habrá que aportar por separado la configuración, secretos, directorios externos de importaciones, colección original de fotos y archivos de vídeo. Las calibraciones creadas por trabajos se conservan en `video-service-state.tar`; el manifiesto y las calibraciones registradas se incluyen en `video-catalog.tar`.

La restauración del formato v2 está pendiente de la siguiente iteración. No uses todavía `restaurar-instalacion.sh` con estos respaldos: su versión actual no restaura `/state/player-photos`.

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
