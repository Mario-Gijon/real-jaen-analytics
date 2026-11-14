# Real Jaén Analytics

Plataforma para analizar partidos, equipos y jugadores del Real Jaén CF, con actualización deportiva manual y reproducción de vídeos sincronizada.

## Desarrollo

Mario proporciona `.env.dev` y los archivos privados necesarios. Desde la raíz:

```sh
docker compose --env-file .env.dev -f compose.dev.yaml up -d --build
```

El Dashboard queda en `http://127.0.0.1:33201`. Los datos reales permanecen en los volúmenes `rja-dev-*`; para instalarlo en otro ordenador hay que transferir y restaurar un respaldo verificado una sola vez.

## Producción

Configura `.env.prod`, los secretos externos y los volúmenes persistentes de la instalación. En SINBAD² el Dashboard utiliza el puerto `8120`.

```sh
docker compose --env-file .env.prod -f compose.prod.yaml up -d --build
```

El código y los archivos privados se entregan por separado. Los secretos, bases de datos, respaldos, el catálogo de vídeos y los datos operativos no se publican en Git. Las actualizaciones deportivas y los trabajos de vídeo solo comienzan mediante acciones explícitas desde el Dashboard.

Para la preparación inicial, la copia de datos o las operaciones de respaldo, consulta [despliegue](docs/despliegue.md). La arquitectura de los cuatro servicios se describe en [arquitectura](docs/arquitectura.md).
