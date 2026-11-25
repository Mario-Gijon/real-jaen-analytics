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

La revisión PostgreSQL inicial `pg0001`, equivalente al esquema SQLite 0008, está aplicada en `sports_data` de la rama `development`. El Hito 02 transfirió desde una copia completa de desarrollo validada 430.773 filas en las 40 tablas de negocio. No se transfirieron fotografías ni datos de Video Service. La aplicación continúa usando SQLite. Las instrucciones para repetir la transferencia sobre un destino vacío están en [README de Sports Data](../sports-data/README.md#transferencia-de-datos-a-neon).
