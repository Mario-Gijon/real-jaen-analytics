# Fotografías de jugadores en Sports Data

Sports Data mantiene las fotografías fuera de `real-jaen.sqlite` y de las generaciones deportivas. El catálogo vive
por defecto en `/state/player-photos/`, dentro del volumen `sports-state` ya existente:

```text
/state/player-photos/
  fotos.sqlite
  blobs/<sha256>.webp
  tmp/
```

`SPORTS_DATA_PHOTOS_DIR` permite indicar otra ruta de almacén si el entorno lo necesita. La lectura y el acceso de
inventario no crean el catálogo: si no hay fotografías, la API responde con `photo_url: null`. Una importación aceptada
inicializa el índice. Ninguna de estas operaciones abre ni modifica la base deportiva, ni cambia la huella del
coordinador.

## Consulta y servicio

Las respuestas correctas de `GET /api/v1/players/{id}`, `GET /api/v1/teams/{id}/players` y
`GET /api/v1/teams/{id}/player-stats` incluyen `photo_url` en cada jugador. El valor es una URL relativa de esta API o
`null`. Los registros se asocian exclusivamente por el ID numérico exacto del jugador; IDs que Sports Data no conoce
se registran como `unknown_player` y no se sirven.

`GET /api/v1/players/{id}/photo/{sha256}` sirve solo el WebP vigente de un jugador que también existe en la generación
deportiva actual. La ruta usa el hash íntegro del contenido como versión, rechaza filtros y rutas arbitrarias, comprueba
el hash del archivo antes de servirlo y añade `nosniff`. Una versión reemplazada responde 404. La consulta de un jugador
sigue funcionando aunque el catálogo falte o no se pueda leer; en ese caso se registra el problema y se devuelve una
URL nula.

## Interfaz interna de importación

Las rutas siguientes usan `Authorization: Bearer <token>` con el secreto ya configurado en
`RJ_ACQUISITION_SECRET_FILE` (en modo control, `/run/secrets/acquisition-token`). Sin ese secreto, la interfaz no está
disponible. Deben quedar solo en la red interna de servicios.

- `GET /internal/v1/player-photos/inventory?source_id=<id>` devuelve los hashes activos de esa fuente y la huella del
  inventario. Leer un catálogo inexistente devuelve un inventario vacío y no crea archivos.
- `POST /internal/v1/player-photos/import` recibe el cuerpo `photos-v1` como JSON, con `Content-Type: application/json`
  y las cabeceras `X-Photo-Bundle-Bytes`, `X-Photo-Bundle-SHA256`, `X-Acquisition-Job-ID` y
  `X-Acquisition-Request-ID`. Sports Data calcula de nuevo el inventario y exige que el paquete lo coincida. El cuerpo
  está limitado a 96 MiB.

El paquete se verifica contra `productores-fotos.json`, el contrato `photos-v1`, el hash, el tamaño y las dimensiones
del contenedor y la cabecera WebP, y el contexto de trabajo antes de almacenar contenido. La fase actual no decodifica
el flujo comprimido completo. Los importes son idempotentes por solicitud; una
reutilización con contenido distinto se rechaza. La procedencia declarada se guarda en el catálogo. La aprobación de
una huella de reglas del productor es una autorización técnica del protocolo; por sí sola no demuestra derechos de
redistribución. Antes de importar una colección hay que comprobar por separado que su procedencia permite el uso
previsto. Esta integración no incluye fotografías originales.

La imagen acepta únicamente WebP de hasta 4 MiB por archivo y dimensiones máximas de 4096 × 4096. Los blobs se nombran
por SHA-256; la API nunca deriva una ruta de archivo de un parámetro de petición. `SPORTS_DATA_PHOTO_SOURCES` determina
el orden de fuentes servidas (por defecto, solo `df11`).

## Importación manual desde Acquisition

Acquisition incluye un productor propio y no importa código de Sports Data. La colección se prepara fuera del
repositorio con `manifest.json` y `photos/`, y se monta en modo de solo lectura en `/collections`. Para desarrollo, el
montaje es opcional y se activa con `compose.photos.yaml`:

```sh
export RJ_PHOTO_COLLECTIONS_DIR=/ruta/externa/a/colecciones-autorizadas
docker compose --env-file .env.dev -f compose.dev.yaml -f compose.photos.yaml \
  run --rm --no-deps --entrypoint python acquisition -B -m acquisition.admin import-photos \
  --collection df11/1 --confirm-usage-rights
```

La misma operación en una release de producción requiere añadir `compose.photos.yaml` a la configuración de Compose,
definir `RJ_PHOTO_COLLECTIONS_DIR` y usar el `.env.prod` de esa release. El comando `run --rm --no-deps` crea un
contenedor temporal y no reinicia Acquisition ni inicia una actualización deportiva. No ejecutes `up` para habilitar
la colección durante esta operación.

`--collection` selecciona un subdirectorio relativo bajo `/collections`; solo admite segmentos seguros. La confirmación
`--confirm-usage-rights` es obligatoria y significa que quien ejecuta el comando ha comprobado los permisos de uso y
redistribución de todas las imágenes. No se inicia ninguna importación al arrancar Acquisition ni al pulsar Actualizar.
El importador consulta el inventario actual, prepara y verifica un paquete photos-v1 y lo entrega a Sports Data con el
secreto interno de Acquisition. Los hashes iguales se omiten; Sports Data vuelve a validar el paquete y clasifica cada
identificador exacto como nuevo, cambiado, sin cambios, inválido o desconocido.

El montaje y el productor no son necesarios para iniciar servicios ni para actualizar datos deportivos. Dashboard
todavía no adapta `photo_url` para mostrar estas fotografías en su BFF.
