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
