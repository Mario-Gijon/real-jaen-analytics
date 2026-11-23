# Real Jaén Analytics

Plataforma para analizar partidos, equipos y jugadores del Real Jaén CF, con actualización deportiva manual y reproducción de vídeos sincronizada.

## Desarrollo

Para utilizar la instalación con los datos actuales, Álvaro recibe aparte el paquete privado `real-jaen-analytics-dev-alvaro.tar.gz`. Guárdalo en la raíz del repositorio clonado, descomprímelo y arranca los servicios:

```sh
tar -xzf real-jaen-analytics-dev-alvaro.tar.gz -C .
docker compose --env-file .env.dev -f compose.dev.yaml up -d --build
```

El archivo incluye `.env.dev`, las contraseñas de desarrollo y los datos iniciales. No contiene vídeos originales ni cookies. El Dashboard queda en `http://localhost:33201`. La primera ejecución carga los datos solo en volúmenes vacíos; la lectura inicial de Sports Data puede tardar varios minutos y Compose espera antes de iniciar Dashboard. Los volúmenes que ya contienen datos no se sustituyen. Después, los cambios persisten en Docker.

## Producción

La producción de SINBAD² se actualiza mediante GitLab CI/CD después de fusionar cambios en `main`. La administración prepara la configuración y los cuatro volúmenes persistentes; la pipeline normal falla de forma segura si la instalación no está lista y nunca ejecuta la restauración inicial. No ejecutes `docker compose up` desde una release sin seguir el procedimiento documentado. Apache sirve el Dashboard bajo `/real-jaen` y reenvía al puerto local `8090`.

El paquete privado, las bases de datos, el catálogo, las calibraciones y los secretos no se publican en Git. Acquisition no inicia trabajos al arrancar; las actualizaciones deportivas y los trabajos de vídeo solo comienzan mediante acciones explícitas desde el Dashboard.

Para preparar el primer despliegue administrado, revisar fallos o realizar respaldos, consulta [despliegue](docs/despliegue.md). La arquitectura de los cuatro servicios se describe en [arquitectura](docs/arquitectura.md).
