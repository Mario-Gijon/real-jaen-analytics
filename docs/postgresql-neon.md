# PostgreSQL de desarrollo en Neon

## Proyecto

- Proyecto: `real-jaen-analytics-dev` (`red-leaf-83438333`).
- Región: AWS Europe Central 1 (Frankfurt), `aws-eu-central-1`.
- PostgreSQL 18, plan gratuito.
- La rama predeterminada existente se renombró de `production` a `development`.
- Se conserva la base existente `neondb`.

## Bases y roles

| Base | Rol de ejecución | Acceso |
| --- | --- | --- |
| `sports_data` | `sports_data_app` | Lectura y escritura limitada a esta base. |
| `video_service` | `video_service_app` | Lectura y escritura limitada a esta base. |

Los roles de ejecución se crearon como roles con acceso `read_write`, sin pertenencia a `neon_superuser`. Se revocó `CONNECT` a `PUBLIC` en las dos bases de servicio y se concedió a cada rol de ejecución solo en la base que le corresponde. Se conserva el rol administrativo existente `neondb_owner` para las futuras migraciones de esquema.

## Conexiones locales

Las cuatro cadenas son conexiones directas, no agrupadas, con TLS. Se guardan localmente en `.env.neon`, que está excluido de Git y tiene permisos `600`. La configuración PostgreSQL de Sports Data eleva la validación del certificado y hostname a `verify-full` con el bundle de CA del sistema:

- `SPORTS_DATA_DATABASE_URL`
- `VIDEO_SERVICE_DATABASE_URL`
- `SPORTS_DATA_MIGRATION_DATABASE_URL`
- `VIDEO_SERVICE_MIGRATION_DATABASE_URL`

Las dos variables `*_MIGRATION_DATABASE_URL` apuntan a su base correspondiente con el rol administrativo `neondb_owner`; las variables de ejecución usan las credenciales de los roles de servicio. No guardar valores de estas variables en documentación, commits ni registros.

## Esquema Sports Data

La revisión PostgreSQL inicial `pg0001`, equivalente al esquema SQLite 0008, está aplicada en `sports_data` de la rama `development`. El Hito 02 transfirió desde una copia completa de desarrollo validada 430.773 filas en las 40 tablas de negocio. No se transfirieron fotografías ni datos de Video Service. Las instrucciones para repetir la transferencia sobre un destino vacío están en [README de Sports Data](../sports-data/README.md#transferencia-de-datos-a-neon).

## Lectura provisional de Sports Data

Sports Data puede arrancar en modo de consulta con `SPORTS_DATA_STORAGE=postgresql` y `SPORTS_DATA_CONTROL=0`. Tanto este modo como el modo PostgreSQL con control comparten `CoordinadorPostgreSQL`: cada instalación carga datos y comentarios desde una misma transacción `REPEATABLE READ READ ONLY`, y conserva esa generación en memoria. No se mantienen transacciones durante su vida. Con `SPORTS_DATA_ENRICHMENT_ENABLED=1`, `/api/v1/enrichment/comments` coteja la generación capturada con `sports_data_publication_state` y ejecuta validación, recuento, página y participantes dentro de una transacción de lectura coherente; ignora `SPORTS_DATA_ENRICHMENT_DB`. El modo de solo consulta no habilita actualizaciones ni combina PostgreSQL con el coordinador SQLite.

Desde `sports-data`, carga solo `SPORTS_DATA_DATABASE_URL` de `.env.neon` y arranca con:

```sh
SPORTS_DATA_WORKDIR=/ruta/local/sports-data-dev \
SPORTS_DATA_STORAGE=postgresql SPORTS_DATA_CONTROL=0 SPORTS_DATA_ENRICHMENT_ENABLED=1 \
  .venv/bin/uvicorn app:app --host 127.0.0.1 --port 8002
```

La conexión de ejecución verifica TLS con `verify-full`. El lector valida una firma de columnas y la carga inicial confirmada sin consultar `alembic_version`, que permanece inaccesible para el rol de ejecución. Para instalar dependencias y cargar la variable sin evaluar el archivo como shell, consulta [README de Sports Data](../sports-data/README.md#lectura-temporal-desde-postgresql-de-desarrollo).

## Importación deportiva transaccional (Hito 04A)

`persistencia.sincronizacion.importar_evidencia_postgresql(conexion, ...)` reutiliza `Presentacion`, `estado_deseado`, `planificar`, `aplicar` y el registro `import_run`. Recibe una `sqlalchemy.Connection` PostgreSQL con una transacción ya abierta. No abre conexiones ni confirma o revierte la transacción; quien la llama debe dejar que los errores salgan del bloque transaccional para revertir toda la operación. El camino SQLite y su comando habitual siguen disponibles.

Ejemplo desde `sports-data`:

```python
from persistencia.postgresql import crear_motor_postgresql
from persistencia.sincronizacion import importar_evidencia_postgresql

motor = crear_motor_postgresql()
with motor.begin() as conexion:
    resultado = importar_evidencia_postgresql(
        conexion,
        exportaciones=[('/ruta/export.json', 123456)],
        comando='importar evidencia',
    )
motor.dispose()
```

Los IDs nuevos del plan son marcadores internos; al aplicar, PostgreSQL asigna los IDs definitivos mediante los defaults de sus secuencias y se traducen las referencias dentro del mismo plan. El plan en seco no consume secuencias. Si una secuencia heredada devuelve un ID ya ocupado, la inserción detecta el conflicto de PK y la adelanta mediante `nextval` bajo el bloqueo, sin requerir `setval` ni permisos administrativos. La escritura adquiere el bloqueo transaccional `pg_advisory_xact_lock(1380075856, 4)` antes de validar y planificar. `CoordinadorPostgreSQL` adquiere ese mismo bloqueo antes de leer o modificar el estado deportivo y lo conserva hasta confirmar la publicación conjunta.

La coherencia deportiva se sigue verificando antes y después de la importación; PostgreSQL aplica sus claves foráneas y restricciones nativas durante las escrituras. La comprobación `PRAGMA foreign_key_check` queda solo para SQLite. La ordenación de filas usa las claves primarias; en SQLite, la PK entera conserva el orden equivalente al antiguo `rowid`.

## Importación transaccional de comentarios (Hito 04B)

`persistencia.comentarios.import_group_bundle_postgresql(conexion, response_file)` importa el paquete JSONL de Acquisition usando una conexión PostgreSQL SQLAlchemy con una transacción externa abierta y el rol `sports_data_app`. Reutiliza la validación de procedencia española, SHA-256, normalización y deduplicación del camino SQLite; guarda las capturas y comentarios inmutables y actualiza la selección vigente. Las inserciones de comentarios se agrupan, y la captura nueva obtiene su ID mediante `INSERT ... RETURNING id`.

La función no abre conexiones ni confirma o revierte la transacción externa. Adquiere el bloqueo `pg_advisory_xact_lock(1380075856, 4)` y usa un savepoint por intento: un paquete individual inválido queda registrado como fallido sin cancelar los intentos válidos del mismo lote. Los errores de conexión, integridad y transacción se propagan para revertir la publicación completa. El coordinador mantiene la misma transacción a través de importación deportiva y comentarios; `load_current_comments_postgresql(conexion)` permite leer la selección resultante mediante una consulta agrupada.

## Persistencia transaccional de enriquecimientos (Hito 04C)

`enrichment.store.persist_selected_enrichments_postgresql(conexion, version)` persiste las anotaciones de la captura española verificada vigente dentro de una transacción SQLAlchemy externa. Reutiliza `enrich_comment_pipeline`, conserva versiones anteriores y falla ante conflictos de la misma versión. Carga las anotaciones y participantes existentes en consultas agrupadas y agrupa las inserciones nuevas. La función adquiere el mismo bloqueo de escritura y usa un savepoint interno; no confirma ni revierte la transacción principal. `ensure_selected_enrichments_complete_postgresql(conexion, version)` valida que todos los comentarios de capturas seleccionadas con disponibilidad `available` tengan su anotación.

## Coordinador PostgreSQL (Hito 04D)

`persistencia.actualizacion.CoordinadorPostgreSQL` conserva las interfaces de `Coordinador` que usa Acquisition y Actualizaciones. En ambos modos PostgreSQL, abre un motor de ejecución y exige `SPORTS_DATA_WORKDIR`, un directorio local explícito que guarda únicamente journals operativos, staging y el bloqueo de instalación. Cada instalación debe usar su propio directorio de trabajo; se permite una única instancia activa por directorio. El archivo deportivo SQLite y las instantáneas deportivas no se usan.

Cada paquete que Acquisition ya verificó se congela localmente antes de abrir la transacción. El coordinador adquiere primero `pg_advisory_xact_lock(1380075856, 4)`, comprueba la generación vigente, importa datos deportivos, comentarios y enriquecimientos, valida completitud y coherencia, prepara la generación de API desde esa misma conexión y registra la publicación. PostgreSQL confirma los datos, el registro y la nueva generación en un solo `COMMIT`; solo entonces se cambia la generación en memoria. Un fallo anterior al `COMMIT` revierte todo y conserva la generación anterior. Las consultas en curso mantienen su generación capturada.

El nuevo esquema `pg0002` crea `sports_data_publication` (ID idempotente, generaciones de origen y resultado, huella SHA-256, fecha y resultado) y la fila única `sports_data_publication_state` (generación vigente y huellas de código y ámbito). La generación inicial es estable. `sports_data_app` solo puede leer e insertar publicaciones y leer y actualizar la fila de estado; la revisión de Alembic la conserva inaccesible. Ante un resultado de `COMMIT` incierto o un reinicio, el coordinador consulta este registro durable y reconstruye la generación vigente desde PostgreSQL.

## Sincronización de generaciones (Hito 05A)

Cada coordinador consulta bajo demanda la fila única `sports_data_publication_state`, usando un reloj monotónico local. `SPORTS_DATA_GENERATION_CHECK_INTERVAL_SECONDS` define el intervalo de caché y tiene un mínimo de 5 segundos; su valor predeterminado es 5. Las lecturas normales respetan ese intervalo. Antes de iniciar una adquisición, construir su inventario o verificar una publicación, el control fuerza una consulta inmediata. No hay un hilo de polling.

Si el número de generación no cambia, se reutilizan los objetos de datos y comentarios en memoria. Si avanza, el coordinador carga la generación identificada dentro de una transacción corta `REPEATABLE READ READ ONLY` y cambia la referencia en memoria bajo un cerrojo. Un único cerrojo de refresco evita reconstrucciones duplicadas; el número durable impide instalar una reconstrucción antigua. Las peticiones que ya capturaron la generación anterior conservan sus referencias. Si la comprobación falla, las respuestas de datos cierran el paso con 503 y las publicaciones no comienzan; una comprobación posterior puede recuperarse cuando vuelva PostgreSQL.

El endpoint de enriquecimientos compara ID y número de generación dentro de la misma transacción de lectura que ejecuta la consulta y fuerza el refresco si encuentra una vista desactualizada. La recuperación durable conserva por separado la generación producida por un trabajo histórico y la que está vigente; reconocer un `COMMIT` confirmado no hace retroceder la generación servida. Si el esquema de comentarios PostgreSQL está incompleto, la publicación se rechaza como conjunto.

La huella general de código almacenada por pg0002 es informativa: puede cambiar al editar código de persistencia. La compatibilidad efectiva sigue protegida por la validación de reglas deportivas y por la huella del ámbito, sin bloquear lecturas compatibles por cambios internos de persistencia.

El camino SQLite habitual conserva su coordinador, candidata, journal y recuperación por copia.

`pg0002` no se aplica a Neon en este hito. Sus validaciones se ejecutan exclusivamente en PostgreSQL 18 local mediante un contenedor temporal; no se utiliza Neon ni se cambia su estado.

## Trabajos compartidos entre instalaciones (Hito 05B)

La revisión `pg0003` añade `sports_data_update_state`, `sports_data_update_job` y `sports_data_update_idempotency`. La fila única de estado asigna fencing tokens crecientes; el registro de trabajo conserva estado, fase, propietario, vencimiento del lease, heartbeat, cancelación, IDs de Acquisition, publicación en curso y última publicación confirmada, fechas y progreso/resultado JSONB. `pg0004` sustituye el índice parcial de `pg0003` por uno único sobre una expresión constante: PostgreSQL no permite que convivan dos trabajos `queued`/`running`, incluso si sus estados son diferentes. Las claves de idempotencia se guardan en una tabla aparte, por lo que claves distintas pueden apuntar al mismo trabajo activo. Las revisiones no modifican pg0001 ni pg0002 y conceden a `sports_data_app` solo lectura y actualización del contador en la fila de estado, lectura/escritura de trabajos y lectura/inserción de claves.

Cada proceso tiene un UUID de propietario. El lease predeterminado dura 60 segundos y se renueva cada 15 segundos mientras el trabajo siga activo y pertenezca a ese proceso. Se pueden configurar `SPORTS_DATA_UPDATE_LEASE_SECONDS` y `SPORTS_DATA_UPDATE_HEARTBEAT_SECONDS`; ambos valores deben ser números positivos y el intervalo de heartbeat debe ser menor que el lease. PostgreSQL calcula los vencimientos. La creación toma brevemente la fila contadora para asignar el fencing token y serializar solicitudes simultáneas; no mantiene transacciones abiertas durante Acquisition.

Antes de cada petición de Acquisition, lote, verificación o publicación se comprueba la propiedad. La instancia propietaria es la única que llama a su Acquisition local. Si pierde el lease, no inicia más trabajo ni puede guardar progreso o publicar: las escrituras usan propietario y fencing token, y pg0003 se vuelve a comprobar dentro de la transacción de publicación. El orden de publicación es bloqueo asesor deportivo, fila del trabajo y fila de estado deportivo. La recuperación bloquea primero el contador y luego el trabajo, y nunca toma el bloqueo asesor, por lo que no puede adelantarse a una publicación que ya posee la fila del trabajo.

Una segunda instalación observa los trabajos y su progreso en PostgreSQL sin interrumpir un lease vigente. Al expirar un lease, primero consulta pg0002: una publicación confirmada se marca completada y no se repite. Solo un trabajo aún `queued`, sin ID de Acquisition ni publicación, se puede reasignar automáticamente con un token nuevo. Un trabajo ya enviado a Acquisition o en verificación se marca interrumpido y no se repite. La fase `publishing` con ID consulta la publicación durable después de bloquear la fila; si pg0002 confirma, se completa; si la transacción terminó sin registro, se interrumpe. Si falta el ID o PostgreSQL no permite determinar el resultado, el trabajo queda pendiente y conserva el único cupo activo para no declarar fallido un COMMIT incierto.

Una cancelación remota se guarda en el trabajo compartido. El propietario la detecta en su siguiente avance/heartbeat y llama a su Acquisition local; la política HTTP sigue limitando la cancelación a `queued` y `acquiring`. Cada instalación consulta el trabajo activo, el último finalizado y la última publicación mediante consultas acotadas, sin cargar el historial ni mantener polling cuando no hay publicaciones pendientes.

En modo PostgreSQL ya no se crea ni se consulta `control/jobs.sqlite`: el estado autoritativo reside en pg0003. El journal SQLite que conserva `CoordinadorPostgreSQL` es local al propietario y sirve para staging operativo y para esperar/reconciliar una publicación local; no coordina instalaciones ni prevalece sobre el registro pg0002. Los paquetes y recibos de Acquisition también siguen siendo locales. No se importan automáticamente trabajos históricos del journal de control. Para cambiar una instalación de SQLite a PostgreSQL, primero se dejan terminar o se cancelan explícitamente los trabajos SQLite activos; si alguno sigue activo, se mantiene esa instalación en modo SQLite hasta resolverlo. Después se configura pg0003 y un directorio de trabajo PostgreSQL independiente. Esto conserva la posibilidad de revisar el journal anterior sin descartar silenciosamente trabajos.

La coordinación y pg0003 se validan solo con PostgreSQL 18 local. Neon permanece sin cambios. No se transfiere Acquisition local entre ordenadores ni se recupera automáticamente un paquete que solo exista en otro disco.

## Fotografías de jugadores (Hito 06)

`pg0005` añade el catálogo `sports_data_photo_*` a `sports_data`, con bytes originales en BYTEA y deduplicación global
por SHA-256. Sports Data usa el motor compartido tanto con control como en modo de consulta y no crea ni consulta
`fotos.sqlite` o directorios de blobs en PostgreSQL. `sofascore_id` conserva el identificador externo de photos-v1 y
no se enlaza a la clave interna de `player`.

El protocolo photos-v1, las rutas HTTP y sus encabezados no cambian. Base64 se conserva en el transporte, pero los bytes
verificados se guardan sin transformar. Las consultas para `photo_url` leen metadatos; el servicio recupera BYTEA y
metadatos desde una misma consulta PostgreSQL. Cada importación toma el bloqueo asesor transaccional compartido
`pg_advisory_xact_lock(1380601928, 1330925361)` (claves ASCII `RJPH`/`OTO1`) y revalida recibo e inventario dentro
de la transacción.

La transferencia desde el almacén anterior es manual: desde `sports-data`, con `SPORTS_DATA_MIGRATION_DATABASE_URL`
administrativa configurada en el entorno, ejecuta
`scripts/migrate_photos_to_postgresql.py --source-root /ruta/player-photos`. El importador valida SQLite en solo
lectura y cada blob, exige vacías las tablas de destino, preserva IDs e historial, copia todo en una transacción y deja
intacto el origen. Los blobs no indexados se informan aparte y no se borran. Los backups generales no cambian en este
hito; un `pg_dump` de `sports_data` contendrá fotos, y el procedimiento operativo se integrará en el Hito 09.
Las validaciones de pg0004 y pg0005 se realizan en PostgreSQL 18 local con fixtures sintéticos; Neon no se modifica.

## Video Service independiente (Hitos 07A y 07B)

Video Service usa la base lógica `video_service`, con `VIDEO_SERVICE_DATABASE_URL` para la lectura habitual y `VIDEO_SERVICE_MIGRATION_DATABASE_URL` para Alembic y la importación administrativa. Las dos variables deben contener credenciales separadas. El lector exige el rol `video_service_app`; `vg0001` restringe `CONNECT` a ese rol y concede solo `USAGE` en el esquema y `SELECT` en las tablas cuando ya existe. También retira `CREATE` del esquema público. El aprovisionamiento del rol pertenece a la infraestructura, no a la migración. El importador y la revisión no contienen hostname, propietario o proveedor específico de Neon.

Su Alembic está aislado en `video-service/alembic.ini` y mantiene `video_service_alembic_version`, separado de `alembic_version` y de las revisiones `pg0001`–`pg0005` de Sports Data. El esquema incluye `video_service_job`, `video_service_association`, `video_service_catalog_video`, `video_service_calibration` y `video_service_match_video_override`. No hay claves foráneas entre las dos bases; `match_ref` es una referencia externa opaca.

Para una conexión remota se fuerza TLS `verify-full`, CA del sistema y channel binding requerido. Los ensayos locales requieren declarar `VIDEO_SERVICE_ALLOW_INSECURE_LOCAL=1`, y esa excepción se admite solo con loopback. El pool de Video Service es de dos conexiones como máximo. `VIDEO_SERVICE_STORAGE=postgresql` habilita ahora el lector y las escrituras; SQLite sigue siendo el modo predeterminado.

Tras preparar una base vacía `video_service` y sus roles con permisos mínimos, desde `video-service` aplica y ejecuta manualmente:

```bash
alembic -c alembic.ini upgrade head
python scripts/migrate_to_postgresql.py --state-dir /ruta/estado-antiguo --catalog-dir /ruta/catalogo-estatico
```

El importador usa `VIDEO_SERVICE_MIGRATION_DATABASE_URL`, abre SQLite en solo lectura y toma el manifiesto indicado (por defecto `videos.json`) y sus calibraciones JSON. Preserva IDs, fechas textuales, eventos, metadatos, asociaciones históricas y tombstones; las calibraciones estáticas y las generadas permanecen identificadas por separado en JSONB. Valida integridad, claves foráneas, rutas y documentos; rechaza trabajos activos y destinos no vacíos; realiza una sola transacción y no altera el origen. No inventa trabajos para las entradas estáticas.

`vg0002` añade orden determinista, heartbeat, secuencias de fencing e índice de propietario, además del `youtube_video_id` del catálogo estático. `video_service_app` puede leer el catálogo estático pero no modificarlo; recibe solo los permisos de lectura y escritura de trabajos, calibraciones, asociaciones y overrides que requiere el runtime. No recibe permisos administrativos, de esquema ni acceso a `sports_data`.

Cada instalación dispone de un UUID de propietario, leases de 60 segundos y heartbeats de 15 segundos por defecto (`VIDEO_SERVICE_JOB_LEASE_SECONDS` y `VIDEO_SERVICE_JOB_HEARTBEAT_SECONDS`). Un bloqueo asesor transaccional protege el máximo global de dos trabajos, la disponibilidad de partidos y vídeos, la publicación y las desactivaciones. Fencing tokens impiden publicar o modificar trabajos desde propietarios antiguos. El arranque recupera solo leases vencidos. La publicación confirma calibración JSONB, metadatos, asociación, override y estado `completed` en una transacción. El listado lee metadatos sin obtener JSONB; las consultas de detalle usan una instantánea coherente.

Con `VIDEO_SERVICE_STORAGE=postgresql`, FastAPI usa `VIDEO_SERVICE_DATABASE_URL` y no abre `video-service.sqlite`, carga `videos.json`, lee calibraciones JSON persistentes ni ejecuta Alembic al arrancar. El proceso conserva un worker local por instalación. MP4, informes OCR y JSON intermedio permanecen en el directorio temporal local y se eliminan antes de publicar; no se transfieren archivos entre instalaciones. Si el resultado de un `COMMIT` es incierto, el servicio consulta el estado durable y no marca fallida una publicación ya confirmada.

PostgreSQL se alojará posteriormente en SINBAD². El Hito 07B se valida únicamente con PostgreSQL 18 local en un contenedor temporal y fixtures sintéticos; Neon y SINBAD² permanecen sin cambios.
