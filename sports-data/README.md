# Sports Data

Backend FastAPI del dashboard. Contiene las consultas y contratos deportivos, el importador, la persistencia SQLite, comentarios, enrichment y el coordinador de actualizaciones.

El servicio escucha en 8002 y guarda su base, registros de actualización y procedencia en `/state`. El control se habilita en Compose con un worker; usa el contrato HTTP de Acquisition descrito en [contrato-adquisición](docs/contrato-adquisicion-interno.md). Las actualizaciones siguen solicitándose desde el mecanismo autenticado del Dashboard.

Las instrucciones de ejecución con Compose están en el [README raíz](../README.md). Las pruebas unitarias e integración de consulta están en `tests/`; pueden ejecutarse desde esta carpeta con los requisitos Python instalados. Las operaciones de base y migración siguen siendo explícitas.

## Esquema PostgreSQL de desarrollo

La aplicación conserva SQLite. El esquema PostgreSQL de desarrollo tiene una historia Alembic separada y una revisión inicial equivalente a SQLite 0008. Para ejecutarla, desde `sports-data`, cargue sin evaluar el archivo `.env.neon` como código de shell y use solo la URL administrativa:

```sh
python -m venv .venv
.venv/bin/pip install -r requirements-sql.txt
while IFS='=' read -r key value; do
  case "$key" in
    SPORTS_DATA_MIGRATION_DATABASE_URL) export "$key=$value" ;;
  esac
done < ../.env.neon
.venv/bin/alembic -c alembic-postgresql.ini upgrade head
```

La configuración rechaza otro destino que no sea `sports_data` en PostgreSQL 18 con `neondb_owner`, y exige que una base sin versión Alembic esté vacía. Usa el endpoint directo de Neon, `verify-full` y el bundle de CA del sistema. El snapshot convierte las banderas SQLite en `BOOLEAN` nativo y mantiene los campos JSON como texto, con validación JSON del servidor. El rol `sports_data_app` recibe DML solo en tablas deportivas, `SELECT` en `call_up_vigente` y uso de secuencias; no puede crear objetos de esquema ni leer la tabla de control Alembic. La app aún no cambia a PostgreSQL.
