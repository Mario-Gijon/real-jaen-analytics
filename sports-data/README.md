# Sports Data

Backend FastAPI del dashboard. Contiene las consultas y contratos deportivos, el importador, la persistencia SQLite, comentarios, enrichment y el coordinador de actualizaciones.

El servicio escucha en 8002 y guarda su base, registros de actualización y procedencia en `/state`. El control se habilita en Compose con un worker; usa el contrato HTTP de Acquisition descrito en [contrato-adquisición](docs/contrato-adquisicion-interno.md). Las actualizaciones siguen solicitándose desde el mecanismo autenticado del Dashboard.

Las instrucciones de ejecución con Compose están en el [README raíz](../README.md). Las pruebas unitarias e integración de consulta están en `tests/`; pueden ejecutarse desde esta carpeta con los requisitos Python instalados. Las operaciones de base y migración siguen siendo explícitas.

## PostgreSQL autogestionado

La configuración vigente de PostgreSQL 18, roles, TLS, tareas Alembic y orden de importación está en la [guía principal de PostgreSQL autogestionado](../docs/postgresql-self-hosted.md). Las conexiones usan `SPORTS_DATA_DATABASE_URL` para ejecución y `SPORTS_DATA_MIGRATION_DATABASE_URL` para migraciones e importación, siempre con roles distintos.

El importador de datos deportivos conserva las comprobaciones de SQLite 0008, su inventario de 40 tablas, claves, integridad, recuentos, hashes, secuencias y rollback. Solo acepta `sports_data` en PostgreSQL 18, el rol `sports_data_owner`, un host incluido en `SPORTS_DATA_MIGRATION_ALLOWED_HOSTS`, la revisión `pg0001` y tablas destino vacías. La confirmación `--authorize-destination` evita ejecutar la carga por accidente. Para preservar el orden correcto, importa SQLite después de `pg0001` y antes de actualizar a `pg0005`.

Las lecturas de PostgreSQL mantienen `SPORTS_DATA_STORAGE=postgresql`; para ejecutar el servicio en una instalación remota se requieren TLS verificado y un acceso privado autorizado. No se habilita una transferencia de datos en este hito. La guía describe también cuándo importar las fotografías después de `pg0005`.
