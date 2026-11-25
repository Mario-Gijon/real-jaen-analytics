# Sports Data

Backend FastAPI del dashboard. Contiene las consultas y contratos deportivos, el importador, la persistencia SQLite, comentarios, enrichment y el coordinador de actualizaciones.

El servicio escucha en 8002 y guarda su base, registros de actualización y procedencia en `/state`. El control se habilita en Compose con un worker; usa el contrato HTTP de Acquisition descrito en [contrato-adquisición](docs/contrato-adquisicion-interno.md). Las actualizaciones siguen solicitándose desde el mecanismo autenticado del Dashboard.

Las instrucciones de ejecución con Compose están en el [README raíz](../README.md). Las pruebas unitarias e integración de consulta están en `tests/`; pueden ejecutarse desde esta carpeta con los requisitos Python instalados. Las operaciones de base y migración siguen siendo explícitas.

## Lectura temporal desde PostgreSQL de desarrollo

Para arrancar Sports Data leyendo directamente `sports_data` en Neon, instala los requisitos SQL, carga únicamente la
variable de ejecución desde `../.env.neon` y deja deshabilitado el control de actualizaciones:

```sh
.venv/bin/pip install -r requirements-fastapi.txt -r requirements-sql.txt
while IFS='=' read -r key value; do
  case "$key" in
    SPORTS_DATA_DATABASE_URL) export "$key=$value" ;;
  esac
done < ../.env.neon
SPORTS_DATA_WORKDIR=/ruta/local/sports-data-dev \
SPORTS_DATA_STORAGE=postgresql SPORTS_DATA_CONTROL=0 SPORTS_DATA_ENRICHMENT_ENABLED=1 \
  .venv/bin/uvicorn app:app --host 127.0.0.1 --port 8002
```

El directorio de trabajo debe ser escribible y exclusivo de esta instalación; guarda solo el journal operativo y
staging. El coordinador carga los datos y comentarios dentro de una transacción `REPEATABLE READ READ ONLY` y revisa
bajo demanda `sports_data_publication_state`. `SPORTS_DATA_GENERATION_CHECK_INTERVAL_SECONDS` fija la comprobación
normal (5 segundos por defecto y como mínimo); también puede forzarse antes de operaciones de actualización. No hay
polling en segundo plano. Con `SPORTS_DATA_ENRICHMENT_ENABLED=1`, el endpoint de enriquecimientos consulta la misma
base y verifica la generación en una transacción de lectura coherente; ignora `SPORTS_DATA_ENRICHMENT_DB`. Este modo
no habilita actualizaciones. La instalación habitual conserva su modo actual y las fotografías siguen usando el
almacenamiento local.

## Esquema PostgreSQL de desarrollo

El esquema PostgreSQL de desarrollo tiene una historia Alembic separada y una revisión inicial equivalente a SQLite 0008. Para aplicarla, desde `sports-data`, cargue sin evaluar el archivo `.env.neon` como código de shell y use solo la URL administrativa:

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

La configuración rechaza otro destino que no sea `sports_data` en PostgreSQL 18 con `neondb_owner`, y exige que una base sin versión Alembic esté vacía. Usa el endpoint directo de Neon, `verify-full` y el bundle de CA del sistema. El snapshot convierte las banderas SQLite en `BOOLEAN` nativo y mantiene los campos JSON como texto, con validación JSON del servidor. El rol `sports_data_app` recibe DML solo en tablas deportivas, `SELECT` en `call_up_vigente` y uso de secuencias; no puede crear objetos de esquema ni leer la tabla de control Alembic. El modo de consulta PostgreSQL no consulta esa tabla.

## Transferencia de datos a Neon

Usa una copia SQLite consistente y explícita, no una base activa. El importador abre el origen en solo lectura, exige la revisión 0008 y el inventario de 40 tablas, y valida el proyecto/rama Neon fijados, `pg0001` y que todas las tablas destino estén vacías. No borra ni sobrescribe un destino con datos.

Desde `sports-data`, carga la URL administrativa y ejecuta:

```sh
while IFS='=' read -r key value; do
  case "$key" in
    SPORTS_DATA_MIGRATION_DATABASE_URL) export "$key=$value" ;;
  esac
done < ../.env.neon
.venv/bin/python -m scripts.migrate_sqlite_to_postgresql \
  "/ruta/a/la-copia-consistente.sqlite" --authorize-external-hosting
```

El comando requiere autorización explícita de alojamiento externo, importa por lotes con Psycopg `COPY`, conserva las restricciones y ejecuta toda la carga en una transacción. Compara los recuentos y las huellas de todas las filas, y ajusta las secuencias al máximo ID. Si se necesita repetirla, el destino debe ser una base vacía con `pg0001` y un endpoint de desarrollo previamente verificado; el script se detiene ante un destino poblado y nunca lo reinicia.
