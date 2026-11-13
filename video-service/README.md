# Real Jaén Video Service

Microservicio Python/FastAPI independiente de `api/` y `dashboard/`. Sirve calibraciones existentes y localizaciones OCR seguras, y puede iniciar desde el dashboard un trabajo acotado que descarga temporalmente un vídeo público de YouTube, reutiliza el motor OCR V5.1 y guarda una asociación persistente.

## Estado y límites

- Las peticiones de consulta no ejecutan OCR, FFmpeg, Tesseract ni calibraciones. Solo `POST /api/v1/video-jobs` inicia un procesamiento en segundo plano.
- Los MP4 y las calibraciones de cada instalación son datos operativos; no se versionan. El catálogo de ejemplo está vacío y permite instalar el servicio sin subir asociaciones ni resultados de calibración.
- Los trabajos nuevos aceptan enlaces públicos HTTPS de YouTube reconocidos, no usan cookies y limitan vídeo, tamaño, duración, tiempo de descarga y trabajos activos. El MP4 se crea bajo un directorio temporal del servicio y se elimina antes de publicar la nueva asociación.
- El estado operativo vive en una SQLite propia de Video Service y las nuevas calibraciones en el mismo volumen persistente. No se comparte ni se abre la base SQLite deportiva.
- No se infieren partidos a partir de nombres. Cada `video_id` se enlaza con una `match_ref` opaca y estable en el manifiesto operativo, que se mantiene fuera de Git.
- Una baja desde el dashboard crea una marca persistente que evita que vuelva a aparecer una asociación estática al reiniciar o reconstruir.
- Las escrituras de usuario pasan por el BFF autenticado de Next.js; Video Service solo se expone dentro de la red privada de Compose.

## Estructura

```text
video-service/
├── data/
│   ├── videos.example.json         # manifiesto sintético vacío
│   └── calibrations/               # datos operativos ignorados por Git
├── src/
│   ├── video_service/              # FastAPI, trabajos, estado, adaptadores
│   └── video_sync/                 # motor y contratos copiados de V5.1
├── tests/
├── V5.1_PROVENANCE.json            # hashes y artefactos de origen relativos
├── Dockerfile                     # OCR, descarga y servicio para Compose
├── .dockerignore
├── pyproject.toml
└── requirements.txt
```

`src/video_sync/` contiene las fuentes del motor V5.1, incluido `sync.locate`, el contrato neutral `sports-events` v1, el detector y el código de calibración. La API no consulta Sports Data ni interpreta referencias de partido; el dashboard valida la ficha y transforma los comentarios antes de enviar el contrato neutral. Las rutas `TestOCRMarcador` no se usan en tiempo de ejecución.

La aplicación devuelve campos permitidos del manifiesto, resúmenes de segmentos y resultados del localizador. No serializa el objeto de calibración completo, por lo que no expone rutas internas, scans ni datos de procedencia. El detalle de calibración y comentarios originales permanece dentro del directorio de datos.

## Instalación y arranque local

Requiere Python 3.10 o posterior. La instalación local del flujo completo requiere OpenCV, `yt-dlp`, FFmpeg, FFprobe y Tesseract. FFmpeg y Tesseract son ejecutables del sistema; el Dockerfile los instala en la imagen.

```bash
cd video-service
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
python -m video_service
```

Por defecto escucha solo en `127.0.0.1:8010`. Configuración:

| Variable | Predeterminado | Uso |
|---|---|---|
| `VIDEO_SERVICE_HOST` | `127.0.0.1` | Interfaz de escucha; en un contenedor se configurará normalmente como `0.0.0.0`. |
| `VIDEO_SERVICE_PORT` | `8010` | Puerto HTTP. |
| `VIDEO_SERVICE_DATA_DIR` | `video-service/data` | Directorio de solo lectura con el manifiesto operativo y las calibraciones de la instalación. |
| `VIDEO_SERVICE_MANIFEST` | `videos.json` | Nombre relativo al directorio de datos. No admite rutas absolutas ni `..`. |
| `VIDEO_SERVICE_STATE_DIR` | `video-service/storage/state` | SQLite y calibraciones creadas por trabajos. Montar en almacenamiento persistente. |
| `VIDEO_SERVICE_TEMP_DIR` | `video-service/storage/tmp` | Archivos temporales de trabajo; dentro de Compose se monta en memoria bajo `/tmp/video-service`. |

Se puede copiar `.env.example` para documentar valores locales; la aplicación no carga archivos `.env` automáticamente. Los valores se proporcionan al proceso desde el entorno.

En un checkout nuevo, prepara un manifiesto local vacío antes de arrancar fuera de Docker:

```bash
cp data/videos.example.json data/videos.json
```

Si necesitas asociaciones ya calibradas, crea `data/videos.json` y sus archivos JSON bajo `data/calibrations/` en el almacenamiento privado de la instalación. Esos archivos están ignorados por Git. El Dockerfile proporciona el manifiesto vacío de ejemplo cuando `data/videos.json` no existe.

Para desarrollar localmente, el directorio temporal y el estado deben ser rutas de escritura distintas del catálogo. No ejecutes trabajos de procesamiento en el entorno de pruebas si no quieres descargar el vídeo indicado.

## API

### `GET /health`

```json
{"status":"ok","service":"real-jaen-video-service","version":"0.1.0"}
```

### `GET /api/v1/videos`

Devuelve los registros explícitos, sin rutas de calibración o del servidor. La asociación se devuelve como `match_ref`, sin un proveedor asociado.

### `GET /api/v1/videos/{video_id}/synchronization`

Devuelve el estado de calibración, cobertura, dimensiones de confianza, segmentos y zonas sin cobertura. No incluye rutas del MP4, rutas de calibración ni scans OCR.

### `GET /api/v1/videos/{video_id}/locate`

Parámetros:

- `period`: `first` o `second`.
- `minute`: minuto entero o descuento compatible con el motor, por ejemplo `58`, `45+2` o `90+4`. Se omite solo para `event_kind=match_start` o `period_start`.
- `lead_seconds`: margen previo explícito; por defecto 0 (máximo 120).
- `event_kind`: `other` (por defecto), `match_start` o `period_start`, normalizado por el BFF.
- `event_second`: segundo exacto dentro del minuto (0–59), solo cuando la fuente lo proporciona.

Los eventos ordinarios que solo tienen minuto usan el segundo 40 de ese minuto como punto de reproducción, veinte segundos antes del final: por ejemplo, `58` corresponde al reloj futbolístico `57:40`, y `90+4` a `93:40`. El margen automático es cero; `lead_seconds` solo resta tiempo si un cliente lo solicita explícitamente. `result.video_second` y `playback_video_second` indican dónde empieza la reproducción; `event_video_second` refleja el instante estimado. No es un segundo observado de la acción. `estimated`, `method`, `confidence` y `warnings` conservan la incertidumbre para diagnóstico. Para los comienzos de parte se mantiene la estimación del reloj 00:00 o 45:00, sin margen, extrapolando hacia atrás solo desde el primer segmento verificado, con al menos tres referencias de alta confianza y hasta 90 segundos. Los eventos ordinarios permiten hasta 20 segundos fuera de la cobertura, únicamente con referencias y confianza altas y sin cruzar una discontinuidad conocida. Se conserva el mapeador V5.1 y sus reglas de descuento.

Las respuestas de dominio son `resolved`, `ambiguous`, `uncertain` u `out_of_coverage`. Solo `resolved` contiene una posición de reproducción única; `ambiguous` puede contener posiciones candidatas, nunca una posición única.

### `POST /api/v1/video-jobs`

Inicia un trabajo y responde `202` con su identificador. El BFF envía `match_ref`, `youtube_url` y, opcionalmente, `sports_events` conforme a `video-sync.sports-events` v1. La referencia es opaca. Video Service valida el contrato y la URL, comprueba duplicados y persiste la entrada del trabajo antes de responder; no consulta la API deportiva. Si la ficha existe pero no tiene comentarios adquiridos, el BFF envía `sports_events: null` y la calibración puede continuar solo con OCR.

### `GET /api/v1/video-jobs/{job_id}` y `GET /api/v1/matches/{match_ref}/video-job`

Permiten consultar un trabajo concreto o recuperar el más reciente del partido. Estados: `pending`, `downloading`, `calibrating`, `saving`, `completed` y `failed`. Al reiniciar el servicio, los trabajos que no habían terminado se marcan como fallidos; no se declara una calibración completada sin haber guardado su JSON y la asociación.

### `DELETE /api/v1/matches/{match_ref}/video`

Desactiva de forma persistente la referencia activa del partido mediante una marca en el estado de Video Service. Conserva el informe/calibración histórica y el vídeo remoto de YouTube. Un trabajo activo impide la operación; el catálogo inicial de la imagen no puede reactivar una asociación marcada como eliminada.

### Diagnóstico de descarga

Los trabajos fallidos conservan un `error_code` estable: `video_unavailable`, `network_error`, `extractor_unavailable`, `format_unavailable`, `ffmpeg_error`, `storage_error` o `download_failed`. El mensaje mostrado en el dashboard describe la categoría sin atribuir automáticamente el problema a la privacidad del vídeo. Los logs técnicos registran clase de excepción y código de salida de yt-dlp, nunca cookies ni credenciales.

### Límites y limpieza de trabajos

- Hasta dos trabajos activos registrados y un único calibrador/descargador ejecutándose a la vez.
- Descarga MP4 de hasta 2,5 GB, duración de vídeo hasta 3 horas y hasta 1 hora de tiempo de descarga.
- Antes de descargar se exige espacio libre para el máximo de descarga más 512 MiB. El tmpfs de Compose tiene 4 GiB.
- Se admite un único vídeo por URL; no se carga configuración local de `yt-dlp`, cookies, listas de reproducción ni URLs arbitrarias.
- El trabajo tiene un subdirectorio `job-{uuid}`. Antes de completar se comprueba su eliminación. Al arrancar, se marcan trabajos interrumpidos y se limpian únicamente subdirectorios temporales con UUID y temporales de escritura incompletos del directorio persistente de calibraciones.

Ejemplo sintético usando el fixture `sample-full-match` incluido para las pruebas:

```bash
curl 'http://127.0.0.1:8010/api/v1/videos/sample-full-match/locate?period=second&minute=58'
```

Respuesta representativa:

```json
{
  "video_id": "sample-full-match",
  "match_ref": "fixture-match-full",
  "request": {"period": "second", "minute": "58", "lead_seconds": 0, "event_kind": "other", "event_second": null},
  "status": "resolved",
  "result": {
    "video_second": 5460,
    "playback_video_second": 5460,
    "event_video_second": 5460,
    "event_clock_second_start": 3420,
    "event_clock_second_estimate": 3460,
    "lead_seconds": 0,
    "estimated": true,
    "method": "minute_tail",
    "segment_id": "S02",
    "temporal_segment_id": "T02",
    "football_period": "second",
    "extrapolated_seconds": 0,
    "reference_count": 12,
    "max_residual_seconds": 0.5,
    "confidence": {"ocr": "high", "period": "high", "location": "high"},
    "warnings": ["SofaScore solo aporta el minuto; se usa el segundo 40 como punto de reproducción."]
  }
}
```

Este ejemplo y las calibraciones de prueba son sintéticos; no representan retransmisiones reales. El segundo de reproducción se calcula con el offset artificial del fixture y no es un timestamp observado de una acción.

`45+2` debe codificarse en una URL como `45%2B2`; una petición sin codificar puede hacer que el signo `+` se interprete como espacio.

## Datos operativos y procedencia del motor

`data/videos.json` es la asociación explícita entre vídeo, partido y calibración. Para registrar otro vídeo se añade una entrada y se apunta a un JSON relativo dentro del directorio de datos. No se buscan archivos por el nombre del vídeo ni se asocia automáticamente un partido. El manifiesto y las calibraciones concretas de la instalación se conservan fuera de Git y deben incluirse en su almacenamiento privado al desplegar el servicio.

`V5.1_PROVENANCE.json` registra hashes de las fuentes del motor V5.1 copiadas a este servicio. No incluye hashes, nombres de vídeos, IDs de partidos ni resultados de calibraciones operativas.

## Pruebas

Con las dependencias de test instaladas:

```bash
python -m pytest -q
```

Las pruebas usan calibraciones sintéticas pequeñas y transporte HTTP ASGI. No extraen fotogramas, ejecutan OCR, descargan vídeos ni acceden a la API deportiva. Los recorridos de alta y baja inyectan descargador y calibrador falsos, y usan directorios temporales aislados; cubren referencias opacas, validación y persistencia de eventos, persistencia de la baja, limpieza, recuperación, primera/segunda parte, descuentos, ambigüedad y fuera de cobertura.

## Evolución e integración futura

- El servicio de desarrollo está integrado en `../deploy/dev/compose.yaml` bajo `video-service`, unido a `video-internal` (compartida con el dashboard) y a `video-egress` para los trabajos autorizados de vídeo. No publica un puerto al host ni comparte red con Sports Data. El dashboard usa `VIDEO_SERVICE_URL=http://video-service:8010` para la conexión interna.
- La pestaña Eventos resuelve el partido y sus comentarios mediante los adaptadores existentes del BFF, adapta los comentarios al contrato neutral y envía una referencia opaca. Los saltos pasan por `/api/v1/videos/{video_id}/locate` y solo una respuesta `resolved` mueve el reproductor.
- En desarrollo, el estado persistente usa el volumen `rja-dev-video-state` y el servicio arranca con el manifiesto de ejemplo vacío. En producción, Compose monta el catálogo operativo externo en solo lectura bajo `/app/data`; el volumen indicado por `RJ_PROD_VIDEO_STATE_VOLUME` conserva el estado y las calibraciones que crea el servicio. Ningún volumen de Video Service se comparte con Sports Data.
- El contrato neutral `video-sync.sports-events` v1 se valida y persiste junto al trabajo. El motor no requiere conexión a SQLite ni a ningún proveedor deportivo.
- El dashboard autentica y protege los cambios mediante su BFF de mismo origen. La API de Video Service es interna y no implementa autenticación propia; no debe publicarse directamente.
- La implementación no elimina vídeos remotos, no sube archivos locales y no usa IA. Los trabajos en cola que no han empezado se cancelan en el apagado; un proceso interrumpido se registra como fallido y sus temporales se limpian al reiniciar.
