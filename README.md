# Real Jaén Analytics

Monorepositorio que reúne cuatro componentes independientes: Dashboard (Next.js y BFF), Sports Data (FastAPI y SQLite), Acquisition (servicio FastAPI controlado por Sports Data) y Video Service (FastAPI, OCR y sincronización).

## Estructura

```text
dashboard/       Next.js, BFF, contratos y pruebas
sports-data/     API deportiva, control, importador, persistencia y pruebas
acquisition/     servicio HTTP de adquisición, Dockerfile y pruebas
video-service/   API de vídeo, OCR, sincronización y pruebas
compose.dev.yaml desarrollo con hot reload
compose.prod.yaml producción con imágenes independientes versionadas
```

El botón «Actualizar» sigue el flujo Dashboard → Sports Data (`control.py`) → Acquisition. El servicio Acquisition queda a la espera de solicitudes y no inicia trabajos al arrancar. Sports Data mantiene el control, los contratos, la persistencia y las operaciones de comentarios. Las bases, los vídeos, las calibraciones operativas y los secretos no forman parte del repositorio.

## Compose

Desarrollo usa almacenamiento y redes con nombres `rja-dev-*`; producción usa `rja-prod-*`. Ninguna API publica puertos al host. Solo Dashboard publica un puerto configurable. Los valores `LOCAL_DATA_API_URL=http://sports-data:8002` y `VIDEO_SERVICE_URL=http://video-service:8010` se resuelven dentro de las redes privadas.

1. Copia `.env.dev.example` a `.env.dev` y ajusta los directorios externos para secretos de desarrollo, copias de importación y la imagen auditada del runtime de navegador.
2. Prepara esos archivos y directorios fuera del repositorio. Usa exclusivamente copias de datos para desarrollo.
3. Valida la configuración y arranca con:

   ```sh
   docker compose --env-file .env.dev -f compose.dev.yaml config
   docker compose --env-file .env.dev -f compose.dev.yaml up --build
   ```

En desarrollo `RJ_ACQUISITION_ENABLE_REAL=0` por defecto. Activar el proveedor real requiere cambiarlo explícitamente a `1`; arrancar Acquisition solo inicia su API y no inicia adquisiciones.

Para producción, configura `.env.prod` a partir de `.env.prod.example`, incluyendo imágenes con tags independientes, secretos externos, imports y el directorio externo del catálogo de vídeos. El volumen `rja-prod-video-state` conserva la SQLite de Video Service y las calibraciones creadas por el servicio; `rja-prod-sports-state` conserva la base deportiva.

Los scripts `dashboard/scripts/respaldo-instalacion.sh` y `dashboard/scripts/restaurar-instalacion.sh` usan `.env.prod`, volúmenes nuevos del monorepositorio y conservan tanto el estado deportivo como el estado de vídeo. La restauración se limita a volúmenes que todavía no existan.

Acquisition conserva el runtime auditado de Chromium como imagen base separada, indicado por `RJ_SCRAPER_RUNTIME_IMAGE` en desarrollo. Compose no construye ni descarga el runtime de navegador.

## Datos operativos

Se preparó sin alterar los checkouts de referencia. No se copiaron bases SQLite, importaciones, secretos, vídeos, manifiestos operativos ni calibraciones. No se ejecutaron adquisiciones, descargas, OCR, migraciones ni actualizaciones deportivas durante la consolidación.
