# Real Jaén Analytics

Real Jaén Analytics es una plataforma para consultar información deportiva del Real Jaén CF, preparar el análisis de sus rivales y relacionar partidos con retransmisiones de vídeo.

## Funcionalidades

- Preparación del próximo partido con el calendario y los encuentros recientes del rival disponibles en la fuente.
- Consulta de partidos, alineaciones, estadísticas, equipos, jugadores y comentarios narrativos.
- Perfiles de equipo y jugador con sus partidos y métricas disponibles.
- Reproducción de referencias de YouTube asociadas explícitamente a partidos y localización temporal basada en calibraciones OCR.
- Actualización deportiva manual de SofaScore desde el control autenticado del Dashboard.

## Tecnologías

Next.js, React y TypeScript para el Dashboard; Python y FastAPI para las APIs; SQLite para la persistencia deportiva y de servicios; y Docker Compose para el entorno conjunto. Video Service utiliza OpenCV, Tesseract, FFmpeg y yt-dlp en sus trabajos de sincronización de retransmisiones de YouTube.

## Servicios

| Servicio | Responsabilidad |
| --- | --- |
| Dashboard | Interfaz web, sesión y BFF de acceso a las APIs internas. |
| Sports Data | Consultas deportivas, comentarios, persistencia SQLite y publicación de actualizaciones verificadas. |
| Acquisition | Captura controlada de datos de SofaScore a petición de Sports Data. |
| Video Service | Referencias de YouTube, trabajos OCR explícitos y sincronización entre minuto de partido y vídeo. |

La arquitectura y sus límites se explican en [docs/arquitectura.md](docs/arquitectura.md).

## Inicio rápido

Requisitos: Docker Engine, Docker Compose v2 y la imagen auditada de Chromium indicada por la configuración de Acquisition.

```sh
cd deploy/dev
cp -n .env.example .env
# Completa las rutas privadas descritas en la documentación de despliegue.
docker compose config -q
docker compose up -d --build
```

El Dashboard queda en `http://127.0.0.1:33201` por defecto. El ejemplo mantiene Acquisition real desactivado y no inicia consultas al proveedor al arrancar. Para parar y reanudar los servicios sin eliminar sus volúmenes, usa `docker compose stop` y `docker compose start` desde `deploy/dev`.

## Documentación

- [Arquitectura](docs/arquitectura.md): servicios, comunicaciones y persistencia.
- [Despliegue](docs/despliegue.md): configuración de desarrollo y producción, CI/CD y operaciones.
- [Datos y actualizaciones](docs/datos-y-actualizaciones.md): SofaScore, publicaciones, comentarios y respaldos.
