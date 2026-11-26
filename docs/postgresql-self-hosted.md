# PostgreSQL autogestionado

Esta es la guía vigente para preparar PostgreSQL 18 autogestionado. DEV y PROD usan proyectos Compose, volúmenes, redes y credenciales distintos. Esta configuración no modifica `compose.dev.yaml`, `compose.prod.yaml` ni el despliegue GitLab; la integración de las aplicaciones queda para el Hito 08B.

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

Las imágenes de aplicación ejecutan el servicio con UID `10001`. Al integrar sus secretos de ejecución en el Hito 08B, el archivo `*_DATABASE_URL_FILE` debe llegar legible por ese UID mediante el almacén de secretos del entorno. No montes las credenciales owner ni la contraseña administrativa en el servicio.

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

## Estado de SINBAD²

La configuración y las validaciones se prepararon en local y en GitHub. Quedan pendientes el acceso y las políticas de red de SINBAD², el aprovisionamiento de secretos en el entorno autorizado y la integración de los cuatro servicios con esta base en el Hito 08B. El despliegue automatizado permanece sin cambios hasta el Hito 08C.
