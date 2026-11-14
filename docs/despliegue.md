# Despliegue

## Requisitos

- Docker Engine y Docker Compose v2, con soporte para BuildKit y `additional_contexts`.
- La imagen de runtime de Chromium auditada que indica `RJ_SCRAPER_RUNTIME_IMAGE`. Acquisition la necesita como imagen base y su construcción no descarga dependencias.
- Directorios privados de configuración, secretos, importaciones y catálogo de vídeo fuera del repositorio.

No se versionan archivos `.env`, secretos, bases SQLite, respaldos ni datos operativos de vídeo.

## Desarrollo

Desde la raíz del repositorio:

```sh
cd deploy/dev
cp -n .env.example .env
```

Edita `.env` y cambia las rutas de ejemplo:

- `RJ_DEV_SECRET_DIR`: directorio privado con `access-password`, `update-password`, `session/session-secret`, `control-token` y `acquisition-token`.
- `RJ_DEV_IMPORT_DIR`: copia separada de los archivos de importación, montada en solo lectura.
- `RJ_SCRAPER_RUNTIME_IMAGE`: nombre de la imagen auditada disponible en Docker.

No escribas los valores secretos en `.env`: el archivo contiene rutas, mientras que cada secreto se guarda en su propio archivo externo. El ejemplo deja desactivada la adquisición real (`RJ_ACQUISITION_ENABLE_REAL=0`). No se consulta SofaScore al iniciar Compose.

Valida y arranca los cuatro servicios:

```sh
docker compose config -q
docker compose up -d --build
docker compose ps
```

El Dashboard se publica en `http://127.0.0.1:33201` por defecto. Solo ese servicio publica un puerto; los demás se comunican por redes internas.

Operaciones habituales desde `deploy/dev`:

```sh
docker compose ps
docker compose logs -f dashboard
docker compose stop
docker compose start
```

`stop` conserva contenedores y volúmenes para poder continuar con `start`.

## Producción

El ejemplo de `deploy/prod/.env.example` está preparado para el Dashboard en `0.0.0.0:8120` y recursos con prefijo `rja-sinbad2-*`. Sports Data, Acquisition y Video Service no publican puertos. Mantiene `RJ_ACQUISITION_ENABLE_REAL=0`; solo habilita el proveedor real cuando se vaya a permitir una operación manual. La configuración del ejemplo no confirma que exista una instalación activa en SINBAD².

Para preparar una instalación nueva, una sola vez:

1. Crea `~/deploy_real-jaen-analytics/bootstrap` y copia el código versionado allí.
2. Copia `deploy/prod/.env.example` a `~/deploy_real-jaen-analytics/.env.prod`. Configura nombres estables y únicos para el proyecto, redes y volúmenes; directorios externos para secretos, importaciones y catálogo de vídeo; y las etiquetas de imagen que usará la instalación. Conserva `RJ_DASHBOARD_BIND=0.0.0.0` y `RJ_DASHBOARD_PORT=8120`.
3. Prepara `RJ_PROD_SECRET_DIR` con los archivos de acceso, actualización y sesión del Dashboard, más los tokens internos de control y Acquisition. La clave de acceso y la de actualización son valores distintos; no se guardan en Git.
4. Instala en Docker la imagen de navegador auditada indicada por `RJ_SCRAPER_RUNTIME_IMAGE`. Prepara el catálogo operativo de vídeo en `RJ_VIDEO_CATALOG_DIR` y el directorio de importaciones configurado por `RJ_IMPORT_DIR`.
5. Copia el respaldo verificado dentro de `RJ_IMPORT_DIR`. Desde `bootstrap/deploy/prod`, valida y construye las imágenes sin iniciar servicios:

   ```sh
   docker compose --env-file ~/deploy_real-jaen-analytics/.env.prod config -q
   docker compose --env-file ~/deploy_real-jaen-analytics/.env.prod build
   ```

6. Desde la raíz de `bootstrap`, restaura el respaldo en los volúmenes nuevos:

   ```sh
   ENV_FILE=~/deploy_real-jaen-analytics/.env.prod \
     sh dashboard/scripts/restaurar-instalacion.sh \
     /srv/real-jaen-analytics/imports/RESPALDO_VERIFICADO
   ```

   El respaldo debe estar dentro de `RJ_IMPORT_DIR`. El script comprueba `SHA256SUMS`, la integridad SQLite y la generación; se niega a escribir si alguno de los volúmenes ya existe y no arranca servicios. No repitas la restauración para actualizaciones de código.

La pipeline valida el Compose, la configuración privada, la existencia de los cuatro volúmenes persistentes y la imagen de Chromium antes de ejecutar `docker compose up -d --build --wait` para los cuatro servicios. No importa ni restaura la base. Si el puerto 8120 está ocupado, Compose informa del conflicto; el job no detiene proyectos ajenos.

En una instalación de producción operada manualmente, y solo después de la restauración inicial, el arranque habitual desde `deploy/prod` es:

```sh
docker compose --env-file .env up -d --build --wait
```

En SINBAD², los cambios de código se aplican mediante la pipeline.

## GitLab CI/CD

El job único `deploy_to_sinbad2` de `.gitlab-ci.yml` está configurado para ejecutarse al publicar en `master` del repositorio autorizado de la UJA. Esto describe la configuración versionada, no acredita que SINBAD² esté desplegado ni que el job haya terminado.

GitLab debe proporcionar `SSH_HOST`, `REMOTE_USER`, `SSH_PRIVATE_KEY` y `SSH_KNOWN_HOSTS`. La clave privada admite una variable de tipo archivo o texto multilínea. La clave de host debe estar configurada de antemano; SSH exige `StrictHostKeyChecking=yes` y no acepta claves automáticamente. Si las variables están protegidas, la rama `master` debe estar protegida para que el runner pueda recibirlas.

El job transfiere un `git archive` del commit a un directorio de release dentro de `~/deploy_real-jaen-analytics/releases/`. El `.env.prod` privado queda en `~/deploy_real-jaen-analytics/.env.prod`, fuera de las releases. Cada push construye desde el código versionado y actualiza los servicios de esa instalación. No ejecuta `docker compose down`, no elimina volúmenes y no restaura datos.

Para revisar una release en el servidor, entra en su directorio `deploy/prod` y usa el mismo archivo privado:

```sh
docker compose --env-file ~/deploy_real-jaen-analytics/.env.prod -f compose.yaml ps
docker compose --env-file ~/deploy_real-jaen-analytics/.env.prod -f compose.yaml logs --tail=100 dashboard
docker compose --env-file ~/deploy_real-jaen-analytics/.env.prod -f compose.yaml stop
docker compose --env-file ~/deploy_real-jaen-analytics/.env.prod -f compose.yaml start
```

## Código y persistencia

Las releases contienen código; los datos permanecen en volúmenes Docker estables y en directorios externos. En desarrollo, los recursos usan el prefijo `rja-dev-*`; el ejemplo de producción usa `rja-sinbad2-*`. Usa la misma configuración privada y el mismo proyecto Compose en cada operación para volver a montar esos volúmenes.

Las actualizaciones de código no sustituyen la SQLite, no ejecutan una importación y no migran datos por sí solas. El respaldo inicial es una operación aparte. Para el contenido del respaldo y sus limitaciones, consulta [datos y actualizaciones](datos-y-actualizaciones.md).

## Respaldos y restauración excepcional

Desde la raíz del repositorio, crea cada respaldo en un directorio nuevo:

```sh
ENV_FILE=/ruta/privada/prod.env \
  sh dashboard/scripts/respaldo-instalacion.sh /ruta/nueva/para/respaldo
```

El script comprueba que no haya una actualización activa, detiene temporalmente los cuatro servicios para obtener una copia consistente y vuelve a arrancarlos. Genera `SHA256SUMS`. El respaldo incluye la SQLite deportiva, su historial de actualización y publicaciones, los trabajos de Acquisition y el estado persistente/calibraciones de Video Service. No incluye secretos, cookies ni el perfil del navegador. El catálogo externo de vídeos de `RJ_VIDEO_CATALOG_DIR` debe conservarse por separado.

Para restaurar de forma excepcional, utiliza un respaldo verificado y volúmenes nuevos:

```sh
ENV_FILE=/ruta/privada/prod.env \
  sh dashboard/scripts/restaurar-instalacion.sh \
  /ruta/de/importaciones/RESPALDO_VERIFICADO
```

La restauración rechaza volúmenes existentes y deja los servicios detenidos. Revisa las sumas y la generación antes del primer arranque.
