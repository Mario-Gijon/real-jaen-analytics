# PostgreSQL autogestionado

Esta es la guía para PostgreSQL 18 autogestionado. DEV y PROD usan proyectos Compose, volúmenes, redes y credenciales distintos. La integración PostgreSQL es opt-in: el arranque habitual de `compose.dev.yaml` sigue usando SQLite, `compose.prod.yaml` y el despliegue GitLab siguen sin cambios, y el override de producción se reserva para un cambio de despliegue futuro.

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

Las tareas están bajo un perfil y no se ejecutan al arrancar PostgreSQL ni los microservicios. Los comandos equivalentes pueden invocarse después como tareas explícitas del mecanismo automatizado, una vez adaptado en el Hito 08C; ese mecanismo no se ha cambiado aquí.

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

Los overrides opt-in no cambian los archivos SQLite habituales. Compose 2.24 o posterior es necesario para `!override` y `!reset`; la configuración se verificó con Docker Compose 5.6.1.

### DEV autónomo: aplicaciones y PostgreSQL local

1. Copia `.env.dev.example` a `.env.dev` y `.env.postgresql.dev.example` a `.env.postgresql.dev`. Ajusta los nombres de proyecto, red, volúmenes y las etiquetas `RJ_DEV_IMAGE_TAG`/`PG_TASK_IMAGE_TAG` para que sean únicos respecto a cualquier instalación SQLite activa. `PG_SECRET_DIR` debe señalar un directorio privado fuera del repositorio. `RJ_DEV_SECRET_DIR` puede usar el directorio `.rja-private/secrets` ya ignorado por Git o una ruta privada externa. La ruta de importación se conserva montada en solo lectura para la protección de restauración incompleta. Las variables antiguas de semilla/catálogo aún deben estar definidas para que Compose pueda interpolar `compose.dev.yaml`, aunque el override elimina esos mounts.
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

### PROD preparado, no desplegado

La combinación `compose.prod.yaml`, `compose.postgresql.yaml` y `compose.postgresql.prod.yaml` prepara Sports Data, Acquisition, Video Service, Dashboard y PostgreSQL en el mismo servidor. Usa el env protegido PROD, los nombres y secretos PROD separados, rutas de datos que preparen los administradores y el siguiente orden de archivos:

```bash
docker compose --env-file /ruta/segura/prod.env \
  -f compose.prod.yaml -f compose.postgresql.yaml -f compose.postgresql.prod.yaml \
  config --services
```

El overlay conserva el Dashboard en `127.0.0.1:8090`, fija `NEXT_PUBLIC_BASE_PATH=/real-jaen` (también está compilado así en `dashboard/Dockerfile`), no publica PostgreSQL y no publica directamente los otros tres servicios. Solo Sports Data y Video Service se conectan a `postgresql-private`. Los nombres de volúmenes, red y secretos deben tener el prefijo/raíz PROD configurados en el env protegido y no reutilizar DEV. Esta modalidad es solo configuración revisable: no se levantó, desplegó ni cambió GitLab.

### Healthcheck, puertos y límites

El healthcheck de PostgreSQL requiere el marcador `.rja-bootstrap-complete` y `pg_isready`. El entrypoint escribe ese marcador solo después de que la imagen oficial haya salido del proceso de inicialización y el servidor definitivo acepte conexiones; lo borra al comenzar cada inicio. Así no cuenta el postmaster temporal que ejecuta los scripts de primer arranque.

La publicación opcional por loopback (`compose.postgresql.local.yaml`) es para inspección desde el host; no se necesita para la comunicación entre contenedores. En el ensayo de 08B, Docker registró la declaración de publicación en `HostConfig.PortBindings`, pero no instaló un endpoint en `NetworkSettings.Ports`; una conexión TCP al puerto loopback elegido fue rechazada mientras `pg_isready` dentro del contenedor seguía respondiendo. La causa operativa apunta al daemon/runtime Docker de este entorno, no a PostgreSQL. Se mantiene la base en su red privada; no se abrió el puerto ni se modificó el aislamiento.

### Validación local de 08B

La validación integral utilizó un proyecto, volúmenes, red y secretos temporales propios con PostgreSQL 18, sin tocar la instalación habitual. Los cinco servicios (PostgreSQL, Sports Data, Acquisition, Video Service y Dashboard) terminaron saludables. Tras recrear los contenedores conservando los volúmenes, los esquemas seguían en `pg0005` y `vg0002`; persistían un comentario narrativo, un enriquecimiento, una fotografía, dos vídeos y dos calibraciones. El Dashboard superó inicio de sesión, sesión, fichas de equipo/jugador/partido, comentarios, enriquecimientos, recuperación HTTP de la fotografía, vídeo asociado, locate, sincronización y consulta del estado de actualizaciones.

Una segunda instancia de Sports Data y Video Service, con volúmenes operativos separados, pudo leer el perfil de jugador con foto, el enriquecimiento compartido y el mismo catálogo/calibraciones, incluida sincronización y locate. Los roles app no pudieron conectar a la base ajena; cada servicio leyó solo su URL y no recibió URLs owner/admin. Acquisition no recibió secretos PostgreSQL. El Sports Data secundario validó también disponibilidad y el contrato de autenticación de Acquisition en modo de prueba: petición sin credencial `401` y petición autenticada a un recurso inexistente `404`.

No se envió ninguna solicitud de actualización ni se hizo scraping. `ACQ_ENABLE_REAL=0`; se consultó el estado con la receta piloto oficial en modo fixture. El fixture deportivo contiene tres equipos, mientras que el perfil Group 2 exige el ámbito validado de veinte equipos, y no hay un paquete sellado de Acquisition apto para esta prueba. Por tanto, la ejecución completa de importación/publicación mediante un paquete sintético de Acquisition queda pendiente. Tampoco se descargaron vídeos ni se ejecutó OCR real.

En el camino PostgreSQL, `/state` de Sports Data conservó únicamente `jobs.sqlite` como journal operativo local del coordinador; no aparecieron `real-jaen.sqlite`, `fotos.sqlite` ni blobs. Video Service no creó SQLite ni calibraciones JSON en `/state`; conserva su espacio temporal aislado. Acquisition mantiene legítimamente su SQLite local de trabajos y el perfil local del navegador.

### Instalación real local (Hito 08C.1)

La instalación permanente usa los mismos tres archivos Compose del modo DEV, pero con proyecto, prefijo de volúmenes, red, puerto y etiquetas de imagen propios. Guarda los dos env files, los secretos y las copias de importación en ubicaciones privadas fuera del repositorio. `RJ_DEV_IMAGE_TAG` y `PG_TASK_IMAGE_TAG` evitan mover las etiquetas `:dev` que pueden seguir usando contenedores SQLite activos. Mantén `RJ_ACQUISITION_ENABLE_REAL=0` mientras se comprueban los datos migrados.

Para esta instalación, Acquisition usa además un overlay privado en `$HOME/.config/real-jaen-analytics/postgresql/dev-08c1/compose.acquisition-fixture.yaml` y un manifiesto de rutas fixture vacío. El overlay monta el módulo oficial `sdk_fixture`, fija `ACQ_PROVIDER=fixture-site`, `ACQ_TEST_MODE=1` y `ACQ_ENABLE_REAL=0`, y configura Sports Data con `RJ_ACQUISITION_TEST=1`. No se enviaron trabajos de adquisición. Al reiniciar o recrear esta instalación, incluye ese overlay:

```bash
CONFIG="$HOME/.config/real-jaen-analytics/postgresql/dev-08c1"
docker compose --project-directory "$PWD" \
  --env-file "$CONFIG/.env.dev" --env-file "$CONFIG/.env.postgresql.dev" \
  -f compose.dev.yaml -f compose.postgresql.yaml -f compose.postgresql.dev.yaml \
  -f "$CONFIG/compose.acquisition-fixture.yaml" \
  up -d --wait
```

El Dashboard queda publicado solo en `127.0.0.1:33202`. Para detener esta instalación y conservar sus volúmenes, ejecuta desde la raíz del repositorio:

```bash
CONFIG="$HOME/.config/real-jaen-analytics/postgresql/dev-08c1"
docker compose --project-directory "$PWD" \
  --env-file "$CONFIG/.env.dev" --env-file "$CONFIG/.env.postgresql.dev" \
  -f compose.dev.yaml -f compose.postgresql.yaml -f compose.postgresql.dev.yaml \
  -f "$CONFIG/compose.acquisition-fixture.yaml" \
  down
```

No añadas `-v`.

Para reconstruir solo Sports Data, Video Service, Acquisition o Dashboard, usa `docker compose ... build NOMBRE_SERVICIO` y luego `docker compose ... up -d --no-deps --force-recreate NOMBRE_SERVICIO`, conservando el mismo env y todos los archivos Compose indicados arriba.

El 26 de noviembre de 2026 se migró desde el backup local v2 del 24 de noviembre, después de verificar `SHA256SUMS`, integridad SQLite y equivalencia lógica completa con la fuente activa. Fotos, estado de Video Service y catálogo estático coincidieron byte a byte con esa copia. El backup del 9 de noviembre quedó descartado por ser anterior. Los archivos originales y la copia de migración permanecen en el directorio privado de datos y no se incluyen en Git.

La instalación PostgreSQL quedó saludable con los esquemas `pg0005` y `vg0002`. Los roles de aplicación no son superusuario ni pueden conectar a la base del otro servicio. Se validaron en lectura el login, el equipo Real Jaén, jugadores y foto WebP, un partido observado, comentarios y enriquecimientos, asociaciones de vídeo, sincronización, locate y el estado de actualizaciones sin trabajo activo. No se inició ninguna adquisición ni scraping; la publicación mediante un paquete sellado queda pendiente. Sports Data no creó SQLite deportiva ni almacén local de fotos, y Video Service no creó SQLite ni calibraciones JSON persistentes. Acquisition conserva su SQLite de trabajos.

La instalación SQLite anterior sigue activa e intacta. Su Video Service anterior permanece no saludable porque la imagen ya existente no incluye SQLAlchemy, aunque sus archivos de datos se verificaron; el Video Service nuevo de esta instalación PostgreSQL está saludable.

## Estado de SINBAD²

No se accedió ni se desplegó en SINBAD². Continúan pendientes la conectividad DEV compartida, el aprovisionamiento y entrega segura de secretos, el procedimiento de despliegue por release y la coordinación con los administradores. Los scripts y la configuración predeterminada de GitLab no se modifican en este hito.
