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

Sports Data puede arrancar en modo de consulta con `SPORTS_DATA_STORAGE=postgresql` y `SPORTS_DATA_CONTROL=0`. Carga la generación deportiva y los comentarios seleccionados en una misma transacción `REPEATABLE READ READ ONLY`, y la conserva en memoria durante la vida del proceso. No habilita actualizaciones, no combina PostgreSQL con el coordinador SQLite y no cambia la persistencia habitual ni las fotografías locales.

Desde `sports-data`, carga solo `SPORTS_DATA_DATABASE_URL` de `.env.neon` y arranca con:

```sh
SPORTS_DATA_STORAGE=postgresql SPORTS_DATA_CONTROL=0 \
  .venv/bin/uvicorn app:app --host 127.0.0.1 --port 8002
```

La conexión de ejecución verifica TLS con `verify-full`. El lector valida una firma de columnas y la carga inicial confirmada sin consultar `alembic_version`, que permanece inaccesible para el rol de ejecución. Para instalar dependencias y cargar la variable sin evaluar el archivo como shell, consulta [README de Sports Data](../sports-data/README.md#lectura-temporal-desde-postgresql-de-desarrollo).
