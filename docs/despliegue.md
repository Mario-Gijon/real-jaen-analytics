# Despliegue

El repositorio tiene dos configuraciones Compose en la raíz. Ambas levantan Dashboard, Sports Data, Acquisition y Video Service. Acquisition permanece disponible, pero no inicia trabajos al arrancar; las consultas deportivas y los trabajos de vídeo requieren una acción manual.

Los archivos privados `.env.dev` y `.env.prod` se proporcionan aparte. Los ejemplos versionados muestran sus variables de ruta y operación, pero no contienen contraseñas, tokens ni datos reales. Los secretos montados como archivos también se entregan fuera de Git. El anfitrión Docker necesita acceso de red al construir para descargar imágenes base, paquetes y Chromium.

Acquisition construye su propio runtime de Chromium desde el Dockerfile. La revisión aprobada del SDK de SofaScore está en `acquisition/vendor/sofascore_scraper.tar.gz`; el Dockerfile verifica el archivo, la revisión y el manifiesto de fuentes, incluida su licencia PolyForm Noncommercial 1.0.0. [El repositorio upstream](https://github.com/tunjayoff/sofascore_scraper) describe las condiciones de uso. No hace falta descargar o etiquetar una imagen de runtime local por separado.

## Desarrollo

Usa el archivo privado `.env.dev` y mantén los volúmenes existentes `rja-dev-*`. Sus rutas externas deben apuntar a los secretos, archivos de importación y catálogo de vídeo de esa instalación.

```sh
docker compose --env-file .env.dev -f compose.dev.yaml up -d --build
docker compose --env-file .env.dev -f compose.dev.yaml ps
```

El Dashboard se publica en `http://127.0.0.1:33201`. Dashboard, Acquisition y Video Service conservan la recarga de código durante el desarrollo. Sports Data, Acquisition y Video Service no publican puertos al anfitrión.

Para detener o reanudar los contenedores sin eliminar datos:

```sh
docker compose --env-file .env.dev -f compose.dev.yaml stop
docker compose --env-file .env.dev -f compose.dev.yaml start
```

En otro ordenador, copia el repositorio, el `.env.dev` y los archivos privados. Transfiere el respaldo verificado de la instalación de origen dentro del directorio de importación indicado por `RJ_DEV_IMPORT_DIR` y restaura una sola vez sobre volúmenes nuevos:

```sh
COMPOSE_FILE=compose.dev.yaml ENV_FILE=.env.dev \
  sh dashboard/scripts/restaurar-instalacion.sh \
  /ruta/de/importaciones/RESPALDO_VERIFICADO
```

La restauración valida las sumas y la generación deportiva; no se repite al actualizar el código. El catálogo externo de vídeos se transfiere por separado. No se copian cookies ni perfiles del navegador: al restaurar se inicializa un perfil nuevo.

## Producción

El ejemplo `.env.prod.example` publica solamente el Dashboard en `0.0.0.0:8120` y utiliza nombres estables `rja-sinbad2-*`. La configuración privada de una instalación local puede mantener sus propios nombres `rja-smoke-*`. No cambies el nombre del proyecto, redes ni volúmenes al actualizar una instalación existente.

La preparación inicial crea los directorios externos para secretos, importaciones y catálogo de vídeo, y configura `.env.prod` con sus rutas. Restaura el respaldo verificado una sola vez, antes del primer arranque. En una instalación ya inicializada, las actualizaciones de código no importan ni sustituyen datos:

```sh
docker compose --env-file .env.prod -f compose.prod.yaml up -d --build
```

Sports Data, Acquisition y Video Service solo están accesibles en las redes internas de Docker. No publiques sus puertos. No uses `docker compose down -v`.

## GitLab CI/CD

El job `deploy_to_sinbad2` de `.gitlab-ci.yml` utiliza el Compose de producción de la raíz. Tras un push a la rama `master` del repositorio autorizado, transfiere un `git archive` a una nueva release dentro de `~/deploy_real-jaen-analytics/releases/`, valida `.env.prod`, comprueba la existencia de los cuatro volúmenes y ejecuta `docker compose -f compose.prod.yaml up -d --build --wait` para los cuatro servicios.

El archivo privado `~/deploy_real-jaen-analytics/.env.prod`, los directorios externos y los volúmenes están fuera de las releases. La pipeline no restaura datos, no ejecuta `docker compose down` y no elimina volúmenes. Solo publica el Dashboard en el puerto 8120. Las variables SSH y la verificación estricta del servidor siguen administradas por GitLab UJA.

Para revisar una release en SINBAD², entra en su directorio y utiliza la configuración privada estable:

```sh
docker compose --project-directory "$PWD" \
  --env-file ~/deploy_real-jaen-analytics/.env.prod \
  -f compose.prod.yaml ps
docker compose --project-directory "$PWD" \
  --env-file ~/deploy_real-jaen-analytics/.env.prod \
  -f compose.prod.yaml logs --tail=100 dashboard
```

## Respaldos y restauración excepcional

Desde la raíz del repositorio, crea cada respaldo en una ubicación nueva. Por defecto, los scripts usan `.env.prod` y `compose.prod.yaml`; para desarrollo indica ambos explícitamente.

```sh
ENV_FILE=.env.prod sh dashboard/scripts/respaldo-instalacion.sh /ruta/nueva/para/respaldo
COMPOSE_FILE=compose.dev.yaml ENV_FILE=.env.dev \
  sh dashboard/scripts/respaldo-instalacion.sh /ruta/nueva/para/respaldo-dev
```

El respaldo comprueba que no haya una actualización activa, detiene temporalmente los cuatro servicios para copiar una base consistente y luego los vuelve a arrancar. Incluye la SQLite deportiva, el diario de coordinación, los trabajos de Acquisition y el estado/calibraciones persistentes de Video Service. Genera `SHA256SUMS`; no contiene secretos, cookies ni el perfil del navegador. El catálogo de vídeos externo debe respaldarse por separado.

La restauración es solo para volúmenes nuevos. Comprueba sumas, integridad y generación, restaura la SQLite y los estados persistentes, inicializa un perfil de navegador nuevo y deja los servicios detenidos. Nunca la ejecutes sobre volúmenes existentes ni como parte de un despliegue de código.
