# Real Jaén Analytics

Monorepositorio que reúne cuatro componentes independientes: Dashboard (Next.js y BFF), Sports Data (FastAPI y SQLite), Acquisition (servicio FastAPI controlado por Sports Data) y Video Service (FastAPI, OCR y sincronización).

## Estructura

```text
dashboard/       Next.js, BFF, contratos y pruebas
sports-data/     API deportiva, control, importador, persistencia y pruebas
acquisition/     servicio HTTP de adquisición, Dockerfile y pruebas
video-service/   API de vídeo, OCR, sincronización y pruebas
deploy/dev/      Compose y ejemplo de variables para desarrollo
deploy/prod/     Compose y ejemplo de variables para producción
.gitlab-ci.yml   despliegue remoto opcional en SINBAD²
```

El botón «Actualizar» sigue el flujo Dashboard → Sports Data (`control.py`) → Acquisition. El servicio Acquisition queda a la espera de solicitudes y no inicia trabajos al arrancar. Sports Data mantiene el control, los contratos, la persistencia y las operaciones de comentarios. Las bases, los vídeos, las calibraciones operativas y los secretos no forman parte del repositorio.

Los Compose de raíz son puntos de entrada compatibles con instalaciones existentes; la operativa nueva se ejecuta desde `deploy/dev` o `deploy/prod`. El único perfil conservado oculta el contenedor administrativo de mantenimiento, que se usa solo durante una restauración excepcional.

## Desarrollo

Desde `deploy/dev`, crea el archivo privado a partir del ejemplo, completa las rutas locales de secretos/importaciones y selecciona la imagen auditada de Chromium:

```sh
cd deploy/dev
cp -n .env.example .env
docker compose up -d --build
```

Este Compose mantiene el proyecto `real-jaen-analytics-dev` y los volúmenes/redes `rja-dev-*`, por lo que conserva el estado existente de desarrollo. Acquisition arranca junto con los otros tres servicios, pero no inicia trabajos al arrancar. El modo real permanece desactivado por defecto. Las actualizaciones usan tres jornadas y un máximo de 50 envíos físicos.

## Producción local y respaldo inicial

Desde `deploy/prod`, prepara el `.env` privado con secretos y directorios externos. El ejemplo publica únicamente el Dashboard en `0.0.0.0:8120` y usa recursos `rja-sinbad2-*`, aislados de las instalaciones locales en `33201` y `33301`:

```sh
cd deploy/prod
cp -n .env.example .env
docker compose config -q
docker compose build
```

Para una instalación nueva, copia el respaldo verificado al directorio configurado para importaciones y restáuralo una sola vez, antes de arrancar servicios:

```sh
cd ../..
ENV_FILE=deploy/prod/.env sh dashboard/scripts/restaurar-instalacion.sh /srv/real-jaen-analytics/imports/RESPALDO_VERIFICADO
```

La restauración crea volúmenes únicamente si aún no existen, valida sumas, SQLite y generación, y repone historial de actualizaciones, trabajos de Acquisition y estado/calibraciones de Video Service. Inicializa un perfil de navegador nuevo; no copia cookies. Tras restaurar, el arranque local habitual es:

```sh
cd deploy/prod
docker compose up -d --build
```

Los archivos `.env` quedan fuera de Git. Las actualizaciones de código no ejecutan importaciones ni restauraciones. No uses `docker compose down -v`.

## GitLab CI/CD en SINBAD²

El único job, `deploy_to_sinbad2`, se ejecuta automáticamente en cada push a `master` del repositorio autorizado de la UJA. Configura `SSH_HOST`, `REMOTE_USER`, `SSH_PRIVATE_KEY` y `SSH_KNOWN_HOSTS` en GitLab. La clave privada acepta una variable de tipo archivo o texto multilínea; los hosts conocidos también pueden ser archivo o texto. Si estas variables están protegidas, `master` debe estar protegida para que el job las reciba. El job exige `StrictHostKeyChecking=yes` con el archivo `SSH_KNOWN_HOSTS`; no aprende ni acepta claves de host automáticamente. La clave se guarda solo en el runner y no se copia al servidor.

El job transfiere `git archive` del commit, con solo archivos versionados, a un directorio de versión bajo `~/deploy_real-jaen-analytics/releases/`. Cada despliegue construye desde un snapshot limpio, así que archivos eliminados de commits anteriores no quedan en los contextos. El `.env.prod` privado vive en `~/deploy_real-jaen-analytics/.env.prod`; no se sincroniza ni sobrescribe. Las etiquetas locales de imagen se mantienen en ese archivo, por lo que los scripts de respaldo siguen usando la misma imagen Sports Data. Compose valida la configuración y la existencia de los volúmenes restaurados antes de construir/actualizar los cuatro servicios y comprobar sus healthchecks. No se ejecuta `down`, no se borran volúmenes y no se vuelve a importar el respaldo.

La preparación inicial se hace una vez antes del primer push que despliegue: crea `~/deploy_real-jaen-analytics/bootstrap`, copia el ejemplo a `~/deploy_real-jaen-analytics/.env.prod` y configura secretos, rutas externas y nombres únicos. El archivo privado debe usar `RJ_DASHBOARD_BIND=0.0.0.0` y `RJ_DASHBOARD_PORT=8120`. Instala en Docker la imagen de runtime auditada y coloca el respaldo verificado dentro de `RJ_IMPORT_DIR`. Construye sin iniciar servicios desde `bootstrap/deploy/prod` con `docker compose --env-file ~/deploy_real-jaen-analytics/.env.prod build`. Luego ejecuta desde la raíz de `bootstrap` `ENV_FILE=~/deploy_real-jaen-analytics/.env.prod sh dashboard/scripts/restaurar-instalacion.sh /srv/real-jaen-analytics/imports/RESPALDO_VERIFICADO`. La restauración comprueba las sumas y la generación antes de crear y llenar los volúmenes nuevos. El primer job automático comprobará la configuración privada, la existencia de los volúmenes restaurados y la imagen de runtime antes de arrancar los servicios; no restaura ni importa datos. Docker Compose informará si el puerto 8120 está ocupado y el job no detendrá aplicaciones de otros proyectos. No hagas el primer push de despliegue hasta terminar la restauración inicial.

Para respaldos y restauraciones excepcionales, los scripts existentes usan por defecto `deploy/prod/.env` y `deploy/prod/compose.yaml`:

```sh
ENV_FILE=deploy/prod/.env sh dashboard/scripts/respaldo-instalacion.sh /ruta/nueva/para/respaldo
ENV_FILE=deploy/prod/.env sh dashboard/scripts/restaurar-instalacion.sh /srv/real-jaen-analytics/imports/RESPALDO_VERIFICADO
```

La base deportiva y su generación confirmada, comentarios/estadísticas e historial de actualizaciones permanecen en volúmenes. Las referencias de vídeo y calibraciones se conservan en sus ubicaciones/volúmenes configurados. No hay actualizaciones programadas: Acquisition solo trabaja tras una solicitud manual autorizada.
