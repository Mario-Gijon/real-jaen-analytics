# Real Jaén Analytics

Plataforma para analizar partidos, equipos y jugadores del Real Jaén CF, con actualización deportiva manual y reproducción de vídeos sincronizada.

## Desarrollo recomendado: PostgreSQL 18

Sports Data y Video Service utilizan PostgreSQL 18; Acquisition conserva su SQLite local para trabajos y perfiles. La instalación SQLite deportiva y audiovisual se mantiene como ruta histórica de transición. Consulta [PostgreSQL autogestionado](docs/postgresql-self-hosted.md) para crear una instalación DEV aislada, restaurar respaldos y preparar la instalación administrada.

Los respaldos PostgreSQL usan el formato verificado v3. Antes de cambiar un esquema, crea y verifica un respaldo con `scripts/backup-postgresql.sh` y `scripts/verify-postgresql-backup.sh`; el detalle y el proceso de recuperación están en [despliegue](docs/despliegue.md). Los respaldos permanecen privados y no se incluyen en Git.

## Reproducir la instalación SQLite histórica

Para utilizar la instalación con los datos actuales, Álvaro recibe aparte el paquete privado `real-jaen-analytics-dev-alvaro.tar.gz`. Guárdalo en la raíz del repositorio clonado, descomprímelo y arranca los servicios:

```sh
tar -xzf real-jaen-analytics-dev-alvaro.tar.gz -C .
docker compose --env-file .env.dev -f compose.dev.yaml up -d --build
```

El archivo incluye `.env.dev`, las contraseñas de desarrollo y los datos iniciales. No contiene vídeos originales ni cookies. El Dashboard queda en `http://localhost:33201`. La primera ejecución carga los datos solo en volúmenes vacíos; la lectura inicial de Sports Data puede tardar varios minutos y Compose espera antes de iniciar Dashboard. Los volúmenes que ya contienen datos no se sustituyen. Después, los cambios persisten en Docker.

La instalación anterior conserva SQLite deportiva y audiovisual para reproducir el entorno de transición. No uses su paquete ni sus comandos para una instalación PostgreSQL. Acquisition sigue usando SQLite local en ambos perfiles.

## Producción PostgreSQL

SINBAD² usa la arquitectura PostgreSQL 18. La administración prepara la configuración, los cinco volúmenes persistentes y una restauración inicial explícita; el despliegue normal falla de forma segura si la instalación no está lista y nunca ejecuta la restauración. No ejecutes `docker compose up` desde una release sin seguir el procedimiento documentado. Apache sirve el Dashboard bajo `/real-jaen` y reenvía al puerto local `8090`.

El paquete privado, las bases de datos, el catálogo, las calibraciones y los secretos no se publican en Git. Acquisition no inicia trabajos al arrancar; las actualizaciones deportivas y los trabajos de vídeo solo comienzan mediante acciones explícitas desde el Dashboard.

Para preparar el primer despliegue administrado, revisar fallos o realizar respaldos, consulta [despliegue](docs/despliegue.md). La arquitectura de los cuatro servicios se describe en [arquitectura](docs/arquitectura.md).
