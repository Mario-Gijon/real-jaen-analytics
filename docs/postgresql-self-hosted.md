# PostgreSQL autogestionado

Esta es la guía para PostgreSQL 18 autogestionado. DEV y PROD usan proyectos Compose, volúmenes, redes y credenciales distintos. El arranque habitual de desarrollo en `compose.dev.yaml` sigue usando SQLite; el despliegue de producción combina los tres Compose PostgreSQL y conserva la infraestructura estable fuera de los cambios de código.

## Bases y roles

Cada instancia contiene dos bases independientes:

| Base | Propietario y rol de migración | Rol de ejecución |
| --- | --- | --- |
| `sports_data` | `sports_data_owner` | `sports_data_app` |
| `video_service` | `video_service_owner` | `video_service_app` |

El rol administrativo `postgres` solo inicializa la instancia y administra el servidor. No se entrega a los servicios ni a sus tareas de migración. Los roles owner tienen login para Alembic y los importadores de su propia base. Los roles app no son superusuarios, no pueden crear roles ni bases, no pueden crear objetos en `public` y no pueden conectarse a la otra base. Las revisiones Alembic quedan sin permisos para las aplicaciones.

`scripts/create-postgresql-secrets.sh` genera fuera del repositorio credenciales aleatorias para los cuatro roles, el administrador y las cuatro URLs. Configura todos los secretos y URLs con permisos `0600` y rechaza sobrescribirlos. El archivo administrativo solo lo monta PostgreSQL; las tareas reciben únicamente la URL owner de su base. Los contenedores pueden leer `*_DATABASE_URL_FILE` además de las variables `*_DATABASE_URL` ya existentes.

## Perfiles DEV y PROD

Usa los ejemplos `.env.postgresql.dev.example` y `.env.postgresql.prod.example` como punto de partida para archivos privados fuera del repositorio. Define una ruta absoluta en `PG_SECRET_DIR`. Los proyectos, redes y volúmenes deben tener nombres diferentes; no reutilices sus nombres entre entornos.

Prepara un directorio de secretos nuevo y privado. El comando no muestra los valores generados:

```bash
scripts/create-postgresql-secrets.sh /ruta/privada/postgresql/dev
```

La misma operación para PROD debe apuntar a un directorio PROD independiente. Conserva las credenciales existentes al actualizar una instalación: el inicializador solo está pensado para crear un volumen nuevo y no rota contraseñas.

## Arranque local

La configuración independiente está en `compose.postgresql.yaml`. No publica puertos. Una red marcada `internal` conecta PostgreSQL con las tareas administrativas. PostgreSQL usa `restart: unless-stopped`, un healthcheck y un volumen nombrado montado en `/var/lib/postgresql`, que es el punto de volumen de la imagen oficial desde PostgreSQL 18; su `PGDATA` predeterminado para la versión 18 es `/var/lib/postgresql/18/docker` ([documentación de la imagen oficial](https://github.com/docker-library/docs/blob/master/postgres/content.md#pgdata)).

Arranca solo el perfil elegido y la base:

```bash
docker compose --env-file /ruta/privada/postgresql/dev.env \
  -f compose.postgresql.yaml up -d postgresql
```

La red no publica PostgreSQL. Para una comprobación desde el host puedes añadir `compose.postgresql.local.yaml`; publica solo en `127.0.0.1` y usa `PG_LOCAL_PORT` para elegir el puerto. No uses ese override para exponer la base a una interfaz pública.

El script de `docker-entrypoint-initdb.d` crea roles y bases al inicializar un volumen vacío. Las imágenes oficiales ignoran estos scripts si el directorio de datos ya está inicializado. La salud también requiere el marcador de inicialización, por lo que un primer arranque incompleto no se considera listo. Los reinicios y la recreación del contenedor conservan el volumen. `docker compose down` conserva los volúmenes nombrados; `down -v` los elimina y no debe usarse con datos que quieras mantener.

## TLS y destinos

Las cuatro variables de conexión se mantienen: `SPORTS_DATA_DATABASE_URL`, `SPORTS_DATA_MIGRATION_DATABASE_URL`, `VIDEO_SERVICE_DATABASE_URL` y `VIDEO_SERVICE_MIGRATION_DATABASE_URL`. En conexiones remotas, ambos clientes fuerzan `sslmode=verify-full`, la CA del sistema y `channel_binding=require`. Los parámetros de consulta solo admiten valores TLS que el cliente sustituye por su política; opciones adicionales como `host`, `hostaddr`, `service` u `options` se rechazan para impedir que cambien el destino o debiliten TLS.

Las conexiones sin TLS requieren una excepción explícita y acotada:

- Para pruebas desde el host, `POSTGRES_ALLOW_INSECURE_LOCAL=1` solo se acepta con `localhost`, `127.0.0.1` o `::1`.
- Para el enlace entre contenedores, `POSTGRES_ALLOW_PRIVATE_DOCKER_NO_TLS=1` requiere además el host exacto `postgresql` en `POSTGRES_PRIVATE_DOCKER_HOSTS`. La red Compose que lo usa está marcada `internal` y el servicio no publica puertos.

La excepción de Docker no se aplica a otros nombres ni a hosts remotos. Las migraciones requieren además `SPORTS_DATA_MIGRATION_ALLOWED_HOSTS` o `VIDEO_SERVICE_MIGRATION_ALLOWED_HOSTS`, con una lista exacta de hosts autorizados y sin comodines. El Compose privado establece `postgresql`; si se opera desde otro entorno, configura la lista para su destino explícito.

Una futura instalación remota deberá conectarse por una red privada expresamente autorizada y conservar TLS verificado. Aún no se conoce qué redes privadas admite SINBAD²; no se presupone conectividad externa ni Tailscale. No se accedió ni se desplegó en SINBAD² en este hito.

## Migraciones explícitas

Las tareas administrativas reutilizan las imágenes de Sports Data y Video Service, que incluyen Alembic y sus scripts. Construye solo esas dos imágenes cuando sea necesario:

```bash
docker compose --env-file /ruta/privada/postgresql/dev.env \
  -f compose.postgresql.yaml --profile migrations build migrate-sports-data migrate-video-service
```

Aplica las migraciones cuando PostgreSQL esté saludable. Cada tarea usa exclusivamente su URL owner:

```bash
docker compose --env-file /ruta/privada/postgresql/dev.env \
  -f compose.postgresql.yaml --profile migrations run --rm migrate-sports-data
docker compose --env-file /ruta/privada/postgresql/dev.env \
  -f compose.postgresql.yaml --profile migrations run --rm migrate-video-service
```

Las tareas están bajo un perfil y no se ejecutan al arrancar PostgreSQL ni los microservicios. La pipeline de SINBAD² no activa ese perfil ni ejecuta migraciones: comprueba en solo lectura las revisiones ya restauradas.

Las imágenes de Sports Data, Acquisition y Video Service ejecutan con UID `10001`. El sistema de archivos temporal usado en Arch en este entorno no admite ACL POSIX, así que el procedimiento reproducible usa el grupo primario privado propietario de los secretos: ejecuta `scripts/prepare-postgresql-app-secrets.sh PG_SECRET_DIR APP_SECRET_DIR` (solo el primer argumento para el modo remoto), que comprueba que sea un grupo privado del usuario actual y deja en modo `0640` exclusivamente las dos URLs de ejecución y, si se pasa, `control-token`/`acquisition-token`. Configura `RJ_APP_SECRET_GID` con el GID numérico que imprime y vuelve a ejecutar el helper después de crear o rotar secretos. Los servicios reciben ese GID como grupo suplementario, pero cada contenedor monta solo su URL; URLs owner/admin siguen en `0600` y no se montan en las aplicaciones. No uses un grupo compartido ni hagas archivos legibles por otros.

## Orden de importación

Para un destino deportivo vacío, aplica `pg0001` con `docker compose --env-file /ruta/privada/postgresql/dev.env -f compose.postgresql.yaml --profile migrations run --rm migrate-sports-data -m alembic -c alembic-postgresql.ini upgrade pg0001`.

1. Ejecuta `sports-data` hasta `pg0001` con `sports_data_owner` usando el comando anterior.
2. Importa una copia SQLite consistente en `pg0001`, usando `sports_data_owner`, `SPORTS_DATA_MIGRATION_ALLOWED_HOSTS` y la autorización explícita del destino.
3. Ejecuta `sports-data` hasta `pg0005`.
4. Si hay un almacén de fotografías por transferir, importa `fotos.sqlite` y sus blobs después de `pg0005` con `sports_data_owner`.
5. Ejecuta Video Service hasta `vg0002` con `video_service_owner`.
6. Si hay un estado y catálogo antiguos que importar, ejecútalo después de `vg0002` con `video_service_owner`.

Ejemplo de importación deportiva dentro de la tarea administrativa, montando el origen en solo lectura:

```bash
docker compose --env-file /ruta/privada/postgresql/dev.env \
  -f compose.postgresql.yaml --profile migrations run --rm \
  -v /ruta/privada/imports:/imports:ro migrate-sports-data \
  scripts/migrate_sqlite_to_postgresql.py /imports/real-jaen.sqlite --authorize-destination
```

Para separar el paso de esquema `pg0001` del resto, invoca la tarea con `-m alembic -c alembic-postgresql.ini upgrade pg0001`, realiza la importación y después ejecuta `upgrade head`. El importador rechaza destinos distintos de `sports_data`, PostgreSQL 18, `sports_data_owner`, host autorizado y revisión `pg0001`; también conserva validación SQLite, inventario, claves, recuentos, hashes, secuencias, rollback y rechazo de tablas destino pobladas.

El importador de fotografías exige `pg0005` y conserva la validación de hashes, dimensiones, claves, recuentos, secuencias, transacción y origen intacto. El importador de Video Service exige `video_service`, PostgreSQL 18, `video_service_owner` y `vg0002`; exige un destino vacío y no altera sus archivos fuente. Ambos comandos aceptan sus orígenes en solo lectura:

```bash
docker compose --env-file /ruta/privada/postgresql/dev.env -f compose.postgresql.yaml --profile migrations run --rm -v /ruta/privada/photo-store:/imports/photos:ro migrate-sports-data scripts/migrate_photos_to_postgresql.py --source-root /imports/photos
```

```bash
docker compose --env-file /ruta/privada/postgresql/dev.env \
  -f compose.postgresql.yaml --profile migrations run --rm \
  -v /ruta/privada/video-state:/imports/state:ro \
  -v /ruta/privada/video-catalog:/imports/catalog:ro migrate-video-service \
  scripts/migrate_to_postgresql.py --state-dir /imports/state --catalog-dir /imports/catalog
```

No se importaron datos reales ni se modificaron backups durante el Hito 08A.

## Cambio acotado en pg0001

La revisión `pg0001` solo cambia el rol al que aplica los privilegios predeterminados de tablas y secuencias: de `neondb_owner` a `sports_data_owner`. No cambia el esquema deportivo ni sus restricciones. Es necesario para que las migraciones futuras, ejecutadas con el nuevo propietario, concedan permisos a `sports_data_app` en los objetos que creen.

Alembic no vuelve a ejecutar una revisión que la base ya registra. Por ello, cambiar el archivo no modifica una instalación que ya aplicó `pg0001`: sus privilegios predeterminados anteriores siguen ligados a `neondb_owner`. Neon no se ha tocado. Antes de migrar el propietario de una base ya existente habrá que planificar y revisar sus propietarios y privilegios por separado; no se debe asumir que esta edición los transforma.

## Integración de los cuatro servicios (Hito 08B)

Los overrides opt-in no cambian los archivos SQLite habituales. Compose 2.24.4 o posterior es necesario para `!override` y `!reset` ([documentación de Docker](https://docs.docker.com/reference/compose-file/merge/)); la configuración se verificó con Docker Compose 5.6.1.

### DEV autónomo: aplicaciones y PostgreSQL local

1. Copia `.env.dev.example` a `.env.dev` y `.env.postgresql.dev.example` a `.env.postgresql.dev`. Ajusta los nombres de proyecto, red, volúmenes y la etiqueta de ejecución `RJ_DEV_IMAGE_TAG` para que sean únicos respecto a cualquier instalación SQLite activa. Las tareas administrativas usan etiquetas independientes: `PG_ADMIN_SPORTS_IMAGE_TAG` y `PG_ADMIN_VIDEO_IMAGE_TAG`. Nunca asignes a una etiqueta administrativa la etiqueta de una imagen de ejecución. `PG_SECRET_DIR` debe señalar un directorio privado fuera del repositorio. `RJ_DEV_SECRET_DIR` puede usar el directorio `.rja-private/secrets` ya ignorado por Git o una ruta privada externa. La ruta de importación se conserva montada en solo lectura para la protección de restauración incompleta. Las variables antiguas de semilla/catálogo aún deben estar definidas para que Compose pueda interpolar `compose.dev.yaml`, aunque el override elimina esos mounts.
2. Prepara los tokens y las contraseñas de Dashboard en el directorio privado de desarrollo como para el modo SQLite. Crea los secretos de PostgreSQL y el acceso de solo lectura a los secretos que usan los procesos UID `10001`:

   ```bash
   scripts/create-postgresql-secrets.sh "$HOME/.local/share/real-jaen-analytics/postgresql/dev"
   scripts/prepare-postgresql-app-secrets.sh \
     "$HOME/.local/share/real-jaen-analytics/postgresql/dev" ./.rja-private/secrets
   ```

   Copia `RJ_APP_SECRET_GID` que imprime el helper a `.env.postgresql.dev`; el valor de ejemplo `1000` debe reemplazarse por el GID real de tu grupo privado.

3. Arranca PostgreSQL y prepara las imágenes de las tareas de migración. En una instalación nueva, todavía no inicies las aplicaciones porque el esquema aún no existe:

   ```bash
   docker compose --env-file .env.dev --env-file .env.postgresql.dev \
     -f compose.dev.yaml -f compose.postgresql.yaml -f compose.postgresql.dev.yaml \
     up -d --wait postgresql
   docker compose --env-file .env.dev --env-file .env.postgresql.dev \
     -f compose.dev.yaml -f compose.postgresql.yaml -f compose.postgresql.dev.yaml \
     --profile migrations build migrate-sports-data migrate-video-service
   ```

4. Inicializa el esquema y, solo cuando corresponda, importa una copia explícita desde el directorio externo montado en `/imports`. El destino deportivo debe estar primero en `pg0001`; después de importar, se aplica el resto de migraciones. El catálogo/calibraciones de Video Service se importa con su tarea owner explícita. Estos comandos no se ejecutan al iniciar los servicios:

   ```bash
   docker compose --env-file .env.dev --env-file .env.postgresql.dev \
     -f compose.dev.yaml -f compose.postgresql.yaml -f compose.postgresql.dev.yaml \
     --profile migrations run --rm migrate-sports-data \
     -m alembic -c alembic-postgresql.ini upgrade pg0001
   docker compose --env-file .env.dev --env-file .env.postgresql.dev \
     -f compose.dev.yaml -f compose.postgresql.yaml -f compose.postgresql.dev.yaml \
     --profile migrations run --rm -v /ruta/privada/imports:/imports:ro \
     migrate-sports-data scripts/migrate_sqlite_to_postgresql.py \
     /imports/real-jaen.sqlite --authorize-destination
   docker compose --env-file .env.dev --env-file .env.postgresql.dev \
     -f compose.dev.yaml -f compose.postgresql.yaml -f compose.postgresql.dev.yaml \
     --profile migrations run --rm migrate-sports-data
   docker compose --env-file .env.dev --env-file .env.postgresql.dev \
     -f compose.dev.yaml -f compose.postgresql.yaml -f compose.postgresql.dev.yaml \
     --profile migrations run --rm migrate-video-service -m alembic -c alembic.ini upgrade vg0002
   docker compose --env-file .env.dev --env-file .env.postgresql.dev \
     -f compose.dev.yaml -f compose.postgresql.yaml -f compose.postgresql.dev.yaml \
     --profile migrations run --rm \
     -v /ruta/privada/video-catalog:/imports/catalog:ro \
     migrate-video-service scripts/migrate_to_postgresql.py \
     --catalog-dir /imports/catalog
   ```

   Si hay fotografías legacy, impórtalas únicamente después de `pg0005`, con una copia explícita de `fotos.sqlite` y `blobs/`, usando el comando de importación de fotos descrito arriba. Para el estado operativo de Video Service el comando de importación puede recibir además `--state-dir`; no es necesario para catálogo y calibraciones estáticas.

5. Con ambos esquemas preparados y los datos iniciales cargados, arranca los cuatro servicios. El puerto del Dashboard debe estar libre; el puerto opcional de PostgreSQL no forma parte del perfil integrado:

   ```bash
   docker compose --env-file .env.dev --env-file .env.postgresql.dev \
     -f compose.dev.yaml -f compose.postgresql.yaml -f compose.postgresql.dev.yaml \
     up -d --build --wait
   ```

   Comprueba Dashboard en `http://127.0.0.1:${RJ_DEV_DASHBOARD_PORT}`. Para detener contenedores conservando los datos: `docker compose ... down` con los mismos archivos; no uses `down -v` salvo que quieras borrar los volúmenes de esa instalación.

El override reemplaza el arranque de seed SQLite de Sports Data por un único worker y `SPORTS_DATA_WORKDIR=/state` en un volumen operativo separado. Conserva `/imports` solo en lectura y la comprobación `RJ_RESTORE_MARKER_FILE`; no monta `real-jaen.sqlite`, `fotos.sqlite` ni blobs. Video Service arranca directamente en PostgreSQL, no carga el catálogo estático ni monta estado SQLite/calibraciones JSON y mantiene un tmpfs propio para temporales. Sus fallos de conexión/esquema impiden el arranque y no producen fallback a SQLite. Acquisition conserva SQLite local de trabajos, perfil del navegador, contratos y control `ACQ_ENABLE_REAL`; no recibe URL PostgreSQL. Dashboard usa únicamente las APIs y no se conecta a PostgreSQL.

### DEV con PostgreSQL remoto

Usa esta modalidad solo cuando el administrador haya confirmado ruta privada, firewall, hostname/certificado TLS y acceso desde la máquina de desarrollo. No se presupone que SINBAD² permita esa conexión. Prepara `.env.dev` y `.env.postgresql.remote` a partir de los ejemplos. Crea fuera del repositorio los archivos `sports_data_database_url` y `video_service_database_url` con URLs de roles de aplicación, cada una para su base y con parámetros de TLS verificado; protege ambos inicialmente con modo `0600` y ejecuta `scripts/prepare-postgresql-app-secrets.sh "$RJ_REMOTE_DATABASE_SECRET_DIR"`. Copia el GID que imprime a `.env.postgresql.remote`. No pongas URLs owner ni admin en ese directorio.

```bash
docker compose --env-file .env.dev --env-file .env.postgresql.remote \
  -f compose.dev.yaml -f compose.postgresql.remote.yaml \
  up -d --build --wait
```

No incluyas `compose.postgresql.yaml`, no se levanta una base local. Solo Sports Data tiene una red de salida para su DB remota; Video Service usa su red de salida existente. La excepción Docker sin TLS se elimina en este perfil. No se incluyen tareas de migración y no se deben ejecutar migraciones/importadores contra un PostgreSQL compartido desde cada instalación local.

### Producción SINBAD²

El despliegue productivo usa conjuntamente `compose.prod.yaml`, `compose.postgresql.yaml` y `compose.postgresql.prod.yaml`, además de `ENV_PROD` y `ENV_POSTGRESQL_PROD` protegidos:

```bash
docker compose --env-file /ruta/segura/.env.prod \
  --env-file /ruta/segura/.env.postgresql.prod \
  -f compose.prod.yaml -f compose.postgresql.yaml -f compose.postgresql.prod.yaml \
  --profile migrations config --services
```

El perfil `migrations` solo describe tareas owner; la pipeline no las inicia. El release verifica PostgreSQL 18 y las revisiones `pg0005` y `vg0002` mediante SELECT de solo lectura. Ningún `upgrade`, downgrade, importador, bootstrap o restauración se ejecuta durante un despliegue de código.

El proyecto y la red privada son estables en PROD. Deben existir los volúmenes nombrados de PostgreSQL, Sports Data, Video Service, perfil de Acquisition y trabajos SQLite de Acquisition. El job rechaza cualquier volumen o credencial que falte. Un marcador de restauración interrumpida, una instalación SQLite detectada o una combinación incompleta de volúmenes se informa por separado y detiene el despliegue; no se crean volúmenes ni se restaura o importa información. La base PostgreSQL debe estar ejecutándose y saludable antes de desplegar; el job no la inicia, recrea ni actualiza. El Dashboard conserva `127.0.0.1:8090` y `basePath=/real-jaen`; solo los microservicios de base acceden a la red privada PostgreSQL y ningún otro servicio publica puertos.

Las credenciales PostgreSQL permanecen bajo `shared/postgresql/secrets`, fuera de las versiones y rotaciones de secretos de aplicación. Administración prepara el directorio como propiedad de la cuenta de despliegue y modo `0700`, para que el job compruebe permisos sin copiar las credenciales al Runner. Contraseñas administrativas y URLs owner: `0600`; solo URLs runtime: `0640` con un GID complementario no privilegiado para UID `10001`. Sports Data y Video Service reciben ese grupo; Acquisition no. No se usa lectura global ni se presupone que SINBAD² permita sudo, ACL o un grupo privado existente. Administración proporciona el GID y prepara las rutas y permisos antes de configurar `ENV_POSTGRESQL_PROD`.

`RJ_ACQUISITION_ENABLE_REAL=1` es obligatorio en PROD porque el proveedor configurado es SofaScore. Solo activa la capacidad del proveedor; Acquisition no inicia solicitudes al arrancar. Los límites de intentos, envíos, jornadas y presupuesto se validan en la configuración efectiva.

Antes de recrear aplicaciones, el job detiene Dashboard (único punto publicado), espera a que terminen trabajos de Sports Data y Video Service almacenados en PostgreSQL y Acquisition en su SQLite, coloca el marcador durable de mantenimiento de Acquisition y vuelve a verificar el drenaje. Esto cierra la carrera entre observación de inactividad y sustitución de servicios. No toca leases/fencing tokens, no bloquea heartbeats ni publicaciones y cancela el despliegue si los trabajos no se vacían. Primero reinicia Acquisition y Video Service; luego retira el marcador y recrea Sports Data y Dashboard. Comprueba salud de cinco servicios y el endpoint `127.0.0.1:8090/real-jaen/api/v1/session` antes de promover `last-good`.

El rollback solo se permite hacia una release registrada como PostgreSQL compatible con los mismos esquemas `pg0005` y `vg0002`. No reinicia una versión SQLite contra PostgreSQL ni restaura bases de datos. Si las revisiones cambian, deja Dashboard detenido y el estado disponible para inspección. PostgreSQL se excluye de build, `up` y `--force-recreate`; los scripts de inicialización están montados desde su release original y esta release se conserva mientras un contenedor PostgreSQL activo use sus bind mounts. El limpiador conserva al menos cinco releases y no ejecuta `docker system prune`.

El backup y la restauración PostgreSQL v3 se completaron y verificaron en un destino aislado en el Hito 09A. La primera instalación en SINBAD² sigue pendiente de preparación y aprobación administrativa; el procedimiento definitivo está en la sección [Preparación inicial administrada de SINBAD²](#preparación-inicial-administrada-de-sinbad2). La futura conectividad DEV compartida requiere autorización independiente.

### Healthcheck, puertos y límites

El healthcheck de PostgreSQL requiere el marcador `.rja-bootstrap-complete` y `pg_isready`. El entrypoint escribe ese marcador solo después de que la imagen oficial haya salido del proceso de inicialización y el servidor definitivo acepte conexiones; lo borra al comenzar cada inicio. Así no cuenta el postmaster temporal que ejecuta los scripts de primer arranque.

La publicación opcional por loopback (`compose.postgresql.local.yaml`) es para inspección desde el host; no se necesita para la comunicación entre contenedores. En el ensayo de 08B, Docker registró la declaración de publicación en `HostConfig.PortBindings`, pero no instaló un endpoint en `NetworkSettings.Ports`; una conexión TCP al puerto loopback elegido fue rechazada mientras `pg_isready` dentro del contenedor seguía respondiendo. La causa operativa apunta al daemon/runtime Docker de este entorno, no a PostgreSQL. Se mantiene la base en su red privada; no se abrió el puerto ni se modificó el aislamiento.

### Validación local de 08B

La validación integral utilizó un proyecto, volúmenes, red y secretos temporales propios con PostgreSQL 18, sin tocar la instalación habitual. Los cinco servicios (PostgreSQL, Sports Data, Acquisition, Video Service y Dashboard) terminaron saludables. Tras recrear los contenedores conservando los volúmenes, los esquemas seguían en `pg0005` y `vg0002`; persistían un comentario narrativo, un enriquecimiento, una fotografía, dos vídeos y dos calibraciones. El Dashboard superó inicio de sesión, sesión, fichas de equipo/jugador/partido, comentarios, enriquecimientos, recuperación HTTP de la fotografía, vídeo asociado, locate, sincronización y consulta del estado de actualizaciones.

Una segunda instancia de Sports Data y Video Service, con volúmenes operativos separados, pudo leer el perfil de jugador con foto, el enriquecimiento compartido y el mismo catálogo/calibraciones, incluida sincronización y locate. Los roles app no pudieron conectar a la base ajena; cada servicio leyó solo su URL y no recibió URLs owner/admin. Acquisition no recibió secretos PostgreSQL. El Sports Data secundario validó también disponibilidad y el contrato de autenticación de Acquisition en modo de prueba: petición sin credencial `401` y petición autenticada a un recurso inexistente `404`.

No se envió ninguna solicitud de actualización ni se hizo scraping. `ACQ_ENABLE_REAL=0`; se consultó el estado con la receta piloto oficial en modo fixture. El fixture deportivo contiene tres equipos, mientras que el perfil Group 2 exige el ámbito validado de veinte equipos, y no hay un paquete sellado de Acquisition apto para esta prueba. Por tanto, la ejecución completa de importación/publicación mediante un paquete sintético de Acquisition queda pendiente. Tampoco se descargaron vídeos ni se ejecutó OCR real.

En el camino PostgreSQL, `/state` de Sports Data conservó únicamente `jobs.sqlite` como journal operativo local del coordinador; no aparecieron `real-jaen.sqlite`, `fotos.sqlite` ni blobs. Video Service no creó SQLite ni calibraciones JSON en `/state`; conserva su espacio temporal aislado. Acquisition mantiene legítimamente su SQLite local de trabajos y el perfil local del navegador.

### Instalación real local (Hito 08C.1)

La instalación permanente usa los tres archivos Compose del modo DEV, con proyecto, prefijo de volúmenes, red, puerto y etiquetas de imagen de ejecución propias. Guarda los env files, secretos y copias de importación fuera del repositorio. `RJ_DEV_IMAGE_TAG` identifica las imágenes runtime por defecto; `RJ_DEV_SPORTS_IMAGE_TAG`, `RJ_DEV_ACQUISITION_IMAGE_TAG`, `RJ_DEV_VIDEO_IMAGE_TAG` y `RJ_DEV_DASHBOARD_IMAGE_TAG` permiten fijarlas por servicio. `PG_ADMIN_SPORTS_IMAGE_TAG` y `PG_ADMIN_VIDEO_IMAGE_TAG` identifican exclusivamente las imágenes de migración y tareas administrativas; sus etiquetas no se comparten con ningún servicio permanente.

Acquisition está configurado para SofaScore en modo real mediante `RJ_ACQUISITION_ENABLE_REAL=1`. No definas `RJ_ACQUISITION_TEST`, `ACQ_TEST_MODE` ni `ACQ_FIXTURE_ROUTES` en esta instalación. **No incluyas `compose.acquisition-fixture.yaml` en los comandos de la instalación permanente**: ese overlay fuerza `fixture-site` y modo de prueba, lo que no coincide con el modo real de Sports Data y deja `available=false`. El overlay privado se conserva únicamente para ensayos fixture aislados.

El perfil administrado de Acquisition debe superar `owner.verify()` al iniciar el servicio. El marcador `$SPORTS_DATA_WORKDIR/.acquisition-test-installation` debe estar ausente para modo real; este marcador bloquea intencionadamente el proveedor real. No lo elimines: si existe en otra instalación, consérvala y prepara un workdir/volumen independiente para el modo real. En esta instalación el marcador está ausente.

Desde la raíz del repositorio, inicia la instalación permanente así:

```bash
CONFIG="$HOME/.config/real-jaen-analytics/postgresql/dev-08c1"
docker compose --project-directory "$PWD" \
  --env-file "$CONFIG/.env.dev" --env-file "$CONFIG/.env.postgresql.dev" \
  -f compose.dev.yaml -f compose.postgresql.yaml -f compose.postgresql.dev.yaml \
  up -d --wait
```

Si cambias el modo de Acquisition, recrea únicamente ambos servicios con los mismos tres archivos, sin el overlay fixture:

```bash
docker compose --project-directory "$PWD" \
  --env-file "$CONFIG/.env.dev" --env-file "$CONFIG/.env.postgresql.dev" \
  -f compose.dev.yaml -f compose.postgresql.yaml -f compose.postgresql.dev.yaml \
  up -d --no-deps --force-recreate --wait acquisition sports-data
```

El Dashboard queda publicado solo en `127.0.0.1:33202`. Para detener esta instalación y conservar sus volúmenes, ejecuta desde la raíz del repositorio:

```bash
CONFIG="$HOME/.config/real-jaen-analytics/postgresql/dev-08c1"
docker compose --project-directory "$PWD" \
  --env-file "$CONFIG/.env.dev" --env-file "$CONFIG/.env.postgresql.dev" \
  -f compose.dev.yaml -f compose.postgresql.yaml -f compose.postgresql.dev.yaml \
  down
```

No añadas `-v`.

Para reconstruir solo Sports Data, Video Service, Acquisition o Dashboard, usa `docker compose ... build NOMBRE_SERVICIO` y luego `docker compose ... up -d --no-deps --force-recreate NOMBRE_SERVICIO`, conservando el mismo env y todos los archivos Compose indicados arriba.

El 26 de noviembre de 2026 se migró desde el backup local v2 del 24 de noviembre, después de verificar `SHA256SUMS`, integridad SQLite y equivalencia lógica completa con la fuente activa. Fotos, estado de Video Service y catálogo estático coincidieron byte a byte con esa copia. El backup del 9 de noviembre quedó descartado por ser anterior. Los archivos originales y la copia de migración permanecen en el directorio privado de datos y no se incluyen en Git.

La instalación PostgreSQL quedó saludable con los esquemas `pg0005` y `vg0002`. Los roles de aplicación no son superusuario ni pueden conectar a la base del otro servicio. Se validaron en lectura el login, el equipo Real Jaén, jugadores y foto WebP, un partido observado, comentarios y enriquecimientos, asociaciones de vídeo, sincronización, locate y el estado de actualizaciones sin trabajo activo. Acquisition usa ahora `sofascore` en modo real y el estado de disponibilidad está activo; no se inició ninguna actualización ni scraping. La primera actualización queda pendiente de la acción manual del usuario desde el Dashboard. Sports Data no creó SQLite deportiva ni almacén local de fotos, y Video Service no creó SQLite ni calibraciones JSON persistentes. Acquisition conserva su SQLite de trabajos.

La instalación SQLite anterior sigue activa e intacta. Su Video Service anterior permanece no saludable porque la imagen ya existente no incluye SQLAlchemy, aunque sus archivos de datos se verificaron; el Video Service nuevo de esta instalación PostgreSQL está saludable.

## Estado de SINBAD²

No se accedió a SINBAD², GitLab ni Neon. El runbook de primera instalación PROD queda preparado en este hito; aún están pendientes el aprovisionamiento del servidor, la carga segura de variables, la restauración inicial y la aprobación de administración. La instancia DEV compartida no está aprobada ni preparada.

## Backup PostgreSQL v3

Genera el backup desde la release y con los env files del proyecto que se respalda. El comando cierra admisión, drena trabajos, establece el fence durable de Acquisition y detiene solo los workers necesarios para una copia coherente. PostgreSQL permanece activo y los contenedores originales se recuperan con sus mismos IDs. El destino debe ser nuevo, fuera del repositorio y privado (`0700` para la carpeta, `0600` para archivos); no se sobrescriben backups.

Ejemplo para la instalación permanente local:

```bash
ENV_FILES="$HOME/.config/real-jaen-analytics/postgresql/dev-08c1/.env.dev:$HOME/.config/real-jaen-analytics/postgresql/dev-08c1/.env.postgresql.dev" \
COMPOSE_FILES="compose.dev.yaml:compose.postgresql.yaml:compose.postgresql.dev.yaml" \
  scripts/backup-postgresql.sh \
  "$HOME/.local/share/real-jaen-analytics/postgresql/backups/$(date -u +%Y%m%dT%H%M%SZ)"
```

La carpeta completa contiene `sports_data.dump`, `video_service.dump`, `acquisition-history.tar`, `manifest.json` y `SHA256SUMS`. Incluye publicaciones/generación, fotos y blobs, calibraciones, asociaciones, secuencias y permisos. De Acquisition conserva una snapshot íntegra de `jobs.sqlite`, trabajos terminales, paquetes completados sellados y presupuestos. No incluye contraseñas, URLs con credenciales, perfiles/cookies, trabajos activos, staging/transporte, logs, cachés, journals SQLite deportivos/audiovisuales, vídeos originales ni fotos externas. Las fotos y calibraciones no se duplican fuera de los dumps.

Valida sumas, ambas listas `pg_restore`, la SQLite y el inventario antes de transferirlo:

```bash
ENV_FILES="$HOME/.config/real-jaen-analytics/postgresql/dev-08c1/.env.dev:$HOME/.config/real-jaen-analytics/postgresql/dev-08c1/.env.postgresql.dev" \
COMPOSE_FILES="compose.dev.yaml:compose.postgresql.yaml:compose.postgresql.dev.yaml" \
  scripts/verify-postgresql-backup.sh "/ruta/privada/backup-v3"
```

Los dumps usan `pg_dump -Fc --no-owner`; la restauración crea cada objeto bajo su rol owner nuevo con `pg_restore --no-owner --role=<owner>`, manteniendo las ACL de los roles runtime. No restaura contraseñas/roles de origen, no usa `--no-acl`, `--clean`, `DROP DATABASE` ni vuelve a aplicar Alembic. Antes de toda futura migración de esquema, genera y verifica un backup v3. Este hito no programa backups periódicos.

### Recuperación excepcional si falta el objeto de imagen activo

El backup ordinario mantiene el preflight estricto: exige que cada contenedor use el ID de imagen que resuelve su referencia Compose. Si el objeto de imagen de Sports Data o Video Service ya no existe en el daemon, no reconstruyas ni retaggees esa imagen para fingir coincidencia. El camino excepcional se habilita con `--recovery-unavailable-runtime-images` y solo tolera la ausencia comprobada del objeto original que falte entre esos servicios; el objeto runtime que siga disponible debe coincidir exactamente con el contenedor. Todas las demás comprobaciones del backup v3 se mantienen.

La excepción usa las imágenes independientes `migrate-sports-data` y `migrate-video-service`, con `PG_ADMIN_SPORTS_IMAGE_TAG` y `PG_ADMIN_VIDEO_IMAGE_TAG`. Antes de cerrar admisión, verifica su ID inmutable, las etiquetas Compose de proyecto/servicio y la huella de código contra este checkout. Captura desde cada contenedor activo su ID real, referencia configurada y huella del código; exige además que la huella activa de Sports Data coincida con la generación publicada. El manifiesto distingue esas imágenes runtime de las imágenes administrativas y registra versión, commit y hashes de la herramienta de backup. No atribuye el código activo al commit del checkout.

El flujo cierra el Dashboard, drena Sports Data, Video Service y Acquisition, establece el fence durable de Acquisition, suspende los tres workers y deja PostgreSQL ejecutándose durante los dumps. Luego reinicia los contenedores existentes por ID, sin recrearlos, comprueba que quedan saludables y compara de nuevo sus IDs y huellas. Si falla cualquier paso tras cerrar admisión, el bloque `finally` intenta recuperar los mismos contenedores y retirar el fence. La operación debe detenerse si no se verificaron de antemano las imágenes administrativas o no existe la huella activa esperada.

Para el caso excepcional, construye únicamente las imágenes administrativas usando los tags separados de los env files y, después de comprobar que no coinciden con las etiquetas runtime, genera y verifica el backup así:

```bash
CONFIG="$HOME/.config/real-jaen-analytics/postgresql/dev-08c1"
docker compose --project-directory "$PWD" \
  --env-file "$CONFIG/.env.dev" --env-file "$CONFIG/.env.postgresql.dev" \
  -f compose.dev.yaml -f compose.postgresql.yaml -f compose.postgresql.dev.yaml \
  --profile migrations build migrate-sports-data migrate-video-service

ENV_FILES="$CONFIG/.env.dev:$CONFIG/.env.postgresql.dev" \
COMPOSE_FILES="compose.dev.yaml:compose.postgresql.yaml:compose.postgresql.dev.yaml" \
  scripts/backup-postgresql.sh \
  "$HOME/.local/share/real-jaen-analytics/postgresql/backups/$(date -u +%Y%m%dT%H%M%SZ)" \
  --recovery-unavailable-runtime-images
```

El comando no modifica ni reemplaza etiquetas runtime. La verificación incluye los hashes del manifiesto, los dumps, el inventario y el journal de Acquisition. Antes de volver a usar las bases, restaura en un proyecto aislado y compara inventarios lógicos y respuestas de lectura con el origen. El modo excepcional no es una opción rutinaria ni autoriza cambios en los datos.

## Preparación inicial administrada de SINBAD²

Esta preparación corresponde a administración, fuera del pipeline. Requiere el backup v3 verificado, una copia del código del commit que se propone fusionar y un destino nuevo. No se reutilizan credenciales DEV, perfiles de navegador ni volúmenes de otra instalación.

### Responsabilidades

- **Desarrolladores:** preparar y revisar feat/integrate-player-events, conservar la referencia del commit candidato, comprobar el backup v3 en origen y entregar a administración la guía y el paquete de backup por un canal autorizado. No publican directamente en main ni ejecutan el despliegue.
- **Administración de SINBAD²:** aprovisionar host, directorios, grupo privado, volúmenes, PostgreSQL y Apache; custodiar los secretos y variables protegidas; restaurar y verificar el backup; comprobar los preflights. Solo administración revisa y fusiona la solicitud a main cuando los requisitos previos estén cumplidos.
- **Estado inicial:** no crear un archivo vacío shared/deploy-state/last-good. Su ausencia es válida para el primer despliegue. Los enlaces current, si se preparan durante el bootstrap, deben ser enlaces simbólicos relativos a una carpeta existente bajo versions; nunca directorios.

### Procedimiento de primera instalación

1. **Preparar la cuenta y el árbol privado.** El servidor debe ser Linux con Docker Engine activo, Docker Compose 2.24.4 o posterior, Python 3 y acceso directo de la cuenta de despliegue al daemon Docker, sin sudo. La cuenta necesita espacio persistente para PostgreSQL, los cinco volúmenes, el backup temporal y las imágenes; el host necesita salida de red durante las compilaciones. La cuenta y el Runner deben intercambiar SSH con clave y host key verificada fuera de banda.

   La ruta fija que usa el script remoto es $HOME/deploy_real-jaen-analytics. Debe ser un directorio real, propiedad de la cuenta de despliegue, modo 0700 y no un enlace simbólico. Antes de ejecutar el bloque, confirme que esa ruta no existe; deténgase si encuentra un árbol previo. Solo en el destino nuevo, prepare los directorios privados con modo 0700; shared/imports debe quedar en 0755 para que el preflight pueda leerlo como UID 10001:

       export RJA_DEPLOY_ROOT="$HOME/deploy_real-jaen-analytics"
       umask 077
       install -d -m 0700 "$RJA_DEPLOY_ROOT" "$RJA_DEPLOY_ROOT/releases" "$RJA_DEPLOY_ROOT/shared" "$RJA_DEPLOY_ROOT/shared/config/versions/bootstrap" "$RJA_DEPLOY_ROOT/shared/secrets/versions/bootstrap/session" "$RJA_DEPLOY_ROOT/shared/postgresql/secrets" "$RJA_DEPLOY_ROOT/shared/incoming" "$RJA_DEPLOY_ROOT/shared/deploy-state"
       install -d -m 0755 "$RJA_DEPLOY_ROOT/shared/imports"

   Si alguno de esos paths ya existe, deténgase y compruebe su contenido y propietario antes de continuar. Proteja los env files con 0600 y no los introduzca en Git.

   Prepare los env files a partir de .env.prod.example y .env.postgresql.prod.example, conservando exactamente los valores críticos de producción que validan los scripts. Mantenga el proyecto real-jaen-analytics-sinbad2-prod, el prefijo de red rja-sinbad2-prod, la red PostgreSQL rja-sinbad2-prod-postgresql-private y estos cinco volúmenes: rja-sinbad2-postgresql-data, rja-sinbad2-sports-operational-state, rja-sinbad2-video-operational-state, rja-sinbad2-acquisition-profiles y rja-sinbad2-acquisition-jobs. Configure RJ_ACQUISITION_ENABLE_REAL=1. RJ_IMPORT_DIR apunta a shared/imports: déjelo vacío, propiedad de la cuenta de despliegue y con modo que permita lectura y recorrido a UID 10001 (por ejemplo 0755). El backup que se restaura permanece en otra ruta privada; no lo deje en el imports montado a Sports Data. Los env files no llevan contraseñas ni URLs.

   Coloque ambos archivos en shared/config/versions/bootstrap/.env.prod y .env.postgresql.prod. Cree solo después el enlace de primera versión, verificando antes que el destino no existe:

       install -d -m 0700 "$RJA_DEPLOY_ROOT/shared/config/versions/bootstrap"
       install -m 0600 /ruta/privada/.env.prod "$RJA_DEPLOY_ROOT/shared/config/versions/bootstrap/.env.prod"
       install -m 0600 /ruta/privada/.env.postgresql.prod "$RJA_DEPLOY_ROOT/shared/config/versions/bootstrap/.env.postgresql.prod"
       ln -s versions/bootstrap "$RJA_DEPLOY_ROOT/shared/config/current"

   Este enlace da las rutas config/current utilizadas por los comandos de restauración y coincide con el contrato del despliegue. No cree shared/config/current como directorio. Use la misma configuración en la variable File ENV_PROD y ENV_POSTGRESQL_PROD de GitLab; mantenga la expresión de ruta de RJA_DEPLOY_ROOT que aparece en los ejemplos, pues el job remoto exporta esa raíz.

2. **Crear y proteger los secretos.** Genere las credenciales PostgreSQL PROD nuevas directamente en shared/postgresql/secrets, usando la cuenta de despliegue y sin copiarlas a GitLab:

       scripts/create-postgresql-secrets.sh "$RJA_DEPLOY_ROOT/shared/postgresql/secrets"

   El helper crea la contraseña admin, contraseñas owner/app y las URLs owner/runtime. Mantenga el directorio en 0700. Las cinco contraseñas admin/owner/app y las dos URLs owner o de migración permanecen en 0600. Para las dos URLs de ejecución, administración debe provisionar para la cuenta de despliegue un GID primario privado: el grupo no tiene miembros adicionales y ese GID es el grupo primario de una sola cuenta. Los dos archivos deben pertenecer a la cuenta actual y compartir ese GID. Ejecute el helper con un solo argumento:

       scripts/prepare-postgresql-app-secrets.sh "$RJA_DEPLOY_ROOT/shared/postgresql/secrets"

   El helper cambia únicamente las dos URLs runtime a 0640 y muestra el GID que debe consignarse como RJ_APP_SECRET_GID en .env.postgresql.prod y ENV_POSTGRESQL_PROD. No le pase un segundo directorio: control-token y acquisition-token no deben recibir el GID PostgreSQL. El despliegue comprueba modo, propietario y grupo de cada archivo sin relajar sus permisos.

   Genere cinco secretos de aplicación independientes bajo shared/secrets/versions/bootstrap: access-password, update-password, control-token, acquisition-token y session/session-secret. Use valores aleatorios ASCII; las contraseñas de acceso y actualización deben ser distintas y los dos tokens deben ser distintos. Este ejemplo no imprime sus contenidos:

       install -d -m 0700 "$RJA_DEPLOY_ROOT/shared/secrets/versions/bootstrap/session"
       umask 077
       openssl rand -hex 32 > "$RJA_DEPLOY_ROOT/shared/secrets/versions/bootstrap/access-password"
       openssl rand -hex 32 > "$RJA_DEPLOY_ROOT/shared/secrets/versions/bootstrap/update-password"
       openssl rand -hex 32 > "$RJA_DEPLOY_ROOT/shared/secrets/versions/bootstrap/control-token"
       openssl rand -hex 32 > "$RJA_DEPLOY_ROOT/shared/secrets/versions/bootstrap/acquisition-token"
       openssl rand -hex 32 > "$RJA_DEPLOY_ROOT/shared/secrets/versions/bootstrap/session/session-secret"
       chmod 0444 "$RJA_DEPLOY_ROOT/shared/secrets/versions/bootstrap/access-password" "$RJA_DEPLOY_ROOT/shared/secrets/versions/bootstrap/update-password" "$RJA_DEPLOY_ROOT/shared/secrets/versions/bootstrap/control-token" "$RJA_DEPLOY_ROOT/shared/secrets/versions/bootstrap/acquisition-token" "$RJA_DEPLOY_ROOT/shared/secrets/versions/bootstrap/session/session-secret"
       chmod 0555 "$RJA_DEPLOY_ROOT/shared/secrets/versions/bootstrap/session"
       ln -s versions/bootstrap "$RJA_DEPLOY_ROOT/shared/secrets/current"

   La ruta de versión y su padre deben seguir privados (0700); los montajes de secretos son de solo lectura. No aplique el GID de las URLs PostgreSQL a estos archivos.

   Configure esos mismos cinco contenidos —sin regenerarlos, transformarlos ni publicarlos en logs— como variables GitLab de tipo File: APP_ACCESS_PASSWORD, APP_UPDATE_PASSWORD, APP_SESSION_SECRET, APP_CONTROL_TOKEN y APP_ACQUISITION_TOKEN. La igualdad exacta evita una rotación accidental al primer despliegue; el script compara los archivos. Si los valores no coinciden, el job crea otra versión de secretos y recrea los servicios que los consumen. El GID suplementario de PostgreSQL solo se usa para las URLs runtime de Sports Data y Video Service. Los tokens de aplicación se leen mediante montajes de solo lectura: el preflight prueba Sports Data y Acquisition como UID 10001 y Dashboard como su usuario node.

3. **Configurar GitLab y el acceso SSH, sin desplegar.** Configure ENV_PROD y ENV_POSTGRESQL_PROD como variables File protegidas, con ámbito production y expansión desactivada. Configure también las cinco APP_* como File protegidas con el mismo ámbito. SSH_HOST, REMOTE_USER, SSH_PRIVATE_KEY y SSH_KNOWN_HOSTS deben corresponder a la cuenta de despliegue y a una host key verificada fuera de banda; no use ssh-keyscan como fuente de confianza. El Runner necesita bash, git, ssh, ssh-keygen, tar, mktemp, install y tr. La clave de despliegue queda limitada a ramas protegidas. Proteja main para que solo administración pueda fusionar. La pipeline solo despliega ante un push a main y serializa el entorno production; una solicitud de fusión no despliega.

4. **Inicializar PostgreSQL y sus cinco volúmenes.** Use una copia del commit candidato revisado en el servidor como release temporal de bootstrap. Antes de crear recursos, confirme que no existen el proyecto, la red PostgreSQL ni ninguno de los cinco volúmenes con esos nombres. Si existe cualquiera, deténgase y no lo reutilice:

       PROJECT=real-jaen-analytics-sinbad2-prod
       for volume in rja-sinbad2-postgresql-data rja-sinbad2-sports-operational-state rja-sinbad2-video-operational-state rja-sinbad2-acquisition-profiles rja-sinbad2-acquisition-jobs; do
         if docker volume inspect "$volume" >/dev/null 2>&1; then printf '%s\n' "Volume already exists: $volume" >&2; exit 1; fi
       done
       if docker network inspect rja-sinbad2-prod-postgresql-private >/dev/null 2>&1; then printf '%s\n' "PostgreSQL network already exists" >&2; exit 1; fi
       docker volume create --label "com.docker.compose.project=$PROJECT" --label com.docker.compose.volume=sports-operational-state rja-sinbad2-sports-operational-state
       docker volume create --label "com.docker.compose.project=$PROJECT" --label com.docker.compose.volume=video-operational-state rja-sinbad2-video-operational-state
       docker volume create --label "com.docker.compose.project=$PROJECT" --label com.docker.compose.volume=acquisition-profiles rja-sinbad2-acquisition-profiles
       docker volume create --label "com.docker.compose.project=$PROJECT" --label com.docker.compose.volume=acquisition-jobs rja-sinbad2-acquisition-jobs

   Fije RJ_IMAGE_TAG al SHA del commit candidato, exporte la ruta y prepare el comando Compose. Construya las imágenes exactas antes de usar la imagen Acquisition para preparar los volúmenes:

       export RJA_DEPLOY_ROOT="$HOME/deploy_real-jaen-analytics"
       export RJ_IMAGE_TAG=sha-del-commit-candidato
       BOOTSTRAP_RELEASE="$RJA_DEPLOY_ROOT/releases/bootstrap-$RJ_IMAGE_TAG"
       compose_prod() {
         docker compose --project-directory "$BOOTSTRAP_RELEASE" \
           --env-file "$RJA_DEPLOY_ROOT/shared/config/current/.env.prod" \
           --env-file "$RJA_DEPLOY_ROOT/shared/config/current/.env.postgresql.prod" \
           -f "$BOOTSTRAP_RELEASE/compose.prod.yaml" \
           -f "$BOOTSTRAP_RELEASE/compose.postgresql.yaml" \
           -f "$BOOTSTRAP_RELEASE/compose.postgresql.prod.yaml" "$@"
       }
       compose_prod build sports-data acquisition video-service dashboard

   Compruebe que los directorios raíz de los cuatro volúmenes operativos están vacíos y asígnelos a UID:GID 10001:10001. El comando falla antes de cambiar un volumen no vacío:

       ACQ_IMAGE="local/real-jaen-analytics/acquisition:$RJ_IMAGE_TAG"
       set -e
       for volume in rja-sinbad2-sports-operational-state rja-sinbad2-video-operational-state rja-sinbad2-acquisition-profiles rja-sinbad2-acquisition-jobs; do
         docker run --rm --network none --user 0:0 -v "$volume:/target" \
           --entrypoint python "$ACQ_IMAGE" -B -c 'from pathlib import Path; import os; p=Path("/target"); assert not any(p.iterdir()), "volume is not empty"; os.chown(p, 10001, 10001)'
       done

   Levante exclusivamente PostgreSQL:

       compose_prod up -d --wait postgresql

   Levante exclusivamente el servicio PostgreSQL con los tres Compose de producción. Compose crea el quinto volumen PostgreSQL y la red privada interna; el inicializador usa las credenciales del directorio PostgreSQL para crear las bases y roles. PostgreSQL 18 debe quedar saludable y sin puerto publicado. No levante servicios de aplicación.

5. **Restaurar el backup v3.** Reciba el directorio completo por un canal aprobado: sports_data.dump, video_service.dump, acquisition-history.tar, manifest.json y SHA256SUMS. Verifique el conjunto y sus hashes antes de transferirlo y mantenga la copia maestra en una ruta privada fuera de shared/imports. El backup se pasa directamente a restore-postgresql.sh; shared/imports es el bind mount de solo lectura de la aplicación, debe permanecer legible por UID 10001 y no es el almacén del backup. Antes de restaurar confirme que PostgreSQL y las dos bases destino están vacíos y que los cuatro volúmenes operativos están vacíos y escribibles. No debe haber contenedores de aplicación activos.

   La herramienta de restauración crea por sí misma shared/imports/.real-jaen-restore-incomplete antes de su primera escritura persistente. Restaura ambas bases bajo sus roles owner y el journal SQLite de trabajos de Acquisition en acquisition-jobs/jobs.sqlite; verifica revisiones pg0005/vg0002, inventarios lógicos, permisos, secuencias, publicaciones, generación, fotos, calibraciones, asociaciones e integridad SQLite. No crees ni borres manualmente el marcador. Si la herramienta falla, consérvalo con los volúmenes parciales para inspección; el preflight de despliegue bloqueará la operación. No limpies ni reutilices ese destino para otro intento.

       cd "$BOOTSTRAP_RELEASE"
       export ENV_FILES="$RJA_DEPLOY_ROOT/shared/config/current/.env.prod:$RJA_DEPLOY_ROOT/shared/config/current/.env.postgresql.prod"
       export COMPOSE_FILES="compose.prod.yaml:compose.postgresql.yaml:compose.postgresql.prod.yaml"
       scripts/restore-postgresql.sh "/ruta/privada/backup-v3"

6. **Preparar y verificar Acquisition.** El backup v3 no contiene un perfil Chromium ni cookies. Con Acquisition detenido, cree un perfil nuevo en el volumen acquisition-profiles y verifíquelo con la imagen exacta de bootstrap, UID 10001, sin red, sin capabilities y con el seccomp del repositorio. No copie un perfil de desarrollo:

       ACQ_IMAGE="local/real-jaen-analytics/acquisition:$RJ_IMAGE_TAG"
       docker run --rm --network none --user 10001:10001 --cap-drop ALL \
         --security-opt no-new-privileges:true \
         --security-opt seccomp=./acquisition/docker/seccomp-linux-amd64.json \
         -v rja-sinbad2-acquisition-profiles:/profiles --entrypoint python "$ACQ_IMAGE" -B \
         -m acquisition.admin --root /profiles initialize-profile --confirm-new-profile
       docker run --rm --network none --user 10001:10001 --cap-drop ALL \
         --security-opt no-new-privileges:true \
         --security-opt seccomp=./acquisition/docker/seccomp-linux-amd64.json \
         -v rja-sinbad2-acquisition-profiles:/profiles --entrypoint python "$ACQ_IMAGE" -B \
         -m acquisition.admin --root /profiles verify-profile

   La restauración ya instala jobs.sqlite; no arranque Acquisition para crearlo. Compruebe en lectura, como UID 10001, PRAGMA integrity_check = ok, la tabla job, cero trabajos activos y ausencia del marcador deployment-maintenance. Compruebe también /profiles/control/state.json, /profiles/control/owner.lock y la propiedad/permisos UID 10001. El preflight realiza estas comprobaciones y, si Acquisition está detenido, vuelve a verificar el perfil antes de admitir la release. Acquisition no recibe ninguna URL ni credencial PostgreSQL.

7. **Comprobar el preflight efectivo antes de pedir el merge.** Resuelva los tres Compose de producción con --profile migrations y pase el JSON a scripts/verify-postgresql-prod-compose.py:

       compose_prod --profile migrations config --format json | python3 "$BOOTSTRAP_RELEASE/scripts/verify-postgresql-prod-compose.py"

   Confirme los cinco volúmenes con sus nombres; red PostgreSQL internal existente; PostgreSQL 18 saludable, sin puerto publicado y montado sobre su volumen persistente; secretos PostgreSQL con modos/GID correctos; conexiones owner en pg0005/vg0002; cada rol runtime conecta solo a su base; imports legible; perfil y SQLite válidos; volúmenes operativos escribibles; y lectura de secretos por UID 10001 y node. Estos son los controles del preflight remoto, no pruebas de adquisición. El job no ejecuta migraciones ni modifica PostgreSQL.

   En el primer despliegue debe seguir sin existir last-good. El script acepta la ausencia de estado previo, y al superar los cinco healthchecks y el endpoint de sesión crea el registro; no requiere un last-good ficticio. El marker de restauración debe estar ausente tras restauración verificada. Mantenga config/current y secrets/current como enlaces a versiones existentes.

8. **Primer despliegue y validación posterior.** Cuando administración confirme todos los requisitos, revisará y fusionará la solicitud de fusión a main. Ese merge inicia la pipeline de despliegue normal; no se ejecuta manualmente deploy-sinbad2-remote.sh. El job vuelve a validar el estado, construye el commit exacto y recrea solo servicios de aplicación. No inicia, actualiza ni recrea PostgreSQL, no migra esquema y conserva los cinco volúmenes.

   Tras el merge, administración verifica que Dashboard, Sports Data, Acquisition, Video Service y PostgreSQL estén saludables, y que http://127.0.0.1:8090/real-jaen/api/v1/session responda. Revisa logs sin mostrar secretos y confirma el nuevo shared/deploy-state/last-good y los enlaces current. No inicia una actualización de datos como parte de la instalación.

9. **Apache y acceso público.** En el VirtualHost HTTPS de sinbad2.ujaen.es habilite proxy_http y headers, conserve el Host y el prefijo /real-jaen y reenvíe únicamente al loopback del Dashboard. La aplicación ya se compila con basePath /real-jaen; no quite el prefijo al pasar la solicitud. La configuración debe incluir equivalentes de:

       ProxyPreserveHost On
       ProxyPass        /real-jaen http://127.0.0.1:8090/real-jaen
       ProxyPassReverse /real-jaen http://127.0.0.1:8090/real-jaen
       RequestHeader set X-Forwarded-Proto "https"

   Mantenga el Dashboard publicado solo en 127.0.0.1:8090; PostgreSQL y los demás servicios no se exponen al anfitrión. Verifique la ruta HTTPS /real-jaen y sus recursos estáticos tras la publicación de la configuración Apache.

### Recuperación

- Si el marcador de restauración permanece, no despliegue, no lo elimine y no reutilice las bases o volúmenes parciales. Administración inspecciona el estado; un nuevo intento requiere un destino nuevo y un backup verificado.
- Si el primer despliegue falla antes de cambiar servicios, la instalación existente permanece intacta. Si ya cambió servicios y no hay last-good, no existe rollback automático: el job deja el candidato para inspección, mantiene Dashboard detenido y no inicia una versión SQLite. Preserve volúmenes, marcadores, logs y releases; no ejecute down -v ni limpieza de Docker. Administración diagnostica y define la recuperación antes de reabrir el Dashboard.
- En despliegues posteriores, el rollback automático solo puede usar una release PostgreSQL compatible con pg0005/vg0002. Revierte código/configuración, no bases ni datos. Una restauración de backup es una operación administrativa separada sobre un destino nuevo, nunca una acción automática del pipeline.

### Entrega a Pedro J. y administradores

**Requisitos pendientes de administración antes de merge:** asignar la cuenta y grupo privado; confirmar Docker/Compose, almacenamiento y acceso directo al daemon; provisionar los directorios y cinco volúmenes PROD; generar credenciales PostgreSQL en destino; establecer variables protegidas y SSH confiable en GitLab; restaurar el backup; preparar/verificar Acquisition; revisar la configuración Apache; y aprobar el estado de la instalación. No se ha aprobado ni preparado todavía una instancia DEV compartida.

**Artefactos de entrega:** este procedimiento y la referencia del commit candidato; el backup v3 completo de cinco archivos transferido por canal seguro y su verificación; los ejemplos versionados de configuración, sin valores privados. Las credenciales PostgreSQL se quedan en el servidor; las credenciales de aplicación se provisionan como File protegidas de GitLab. No se entregan backups, secretos ni archivos privados mediante Git.

**Solicitud de fusión pendiente:** proyecto [real-jaen-analytics en GitLab](https://serezade.ujaen.es:8030/despliegues-autorizados/real-jaen-analytics), origen feat/integrate-player-events, destino main. No se abre hasta que la infraestructura esté preparada; solo administración revisa y fusiona.

**PostgreSQL DEV compartido con Álvaro (solicitud futura independiente):** requerirá aprobación explícita de administración, una instancia y credenciales separadas de PROD, conectividad privada autorizada desde los clientes acordados y certificado/hostname que permita TLS verificado (verify-full). No se presupone que SINBAD² lo haya aprobado ni que se habilite por preparar PROD.
