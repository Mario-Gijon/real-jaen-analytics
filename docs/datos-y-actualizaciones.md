# Datos y actualizaciones

## Origen de los datos deportivos

Sports Data sirve la información que ya está publicada en su SQLite. En una instalación inicial, la base se carga desde un respaldo verificado; después puede actualizarse mediante la receta controlada de SofaScore. Abrir el Dashboard o consultar partidos, equipos y jugadores no inicia una adquisición.

Los comentarios narrativos y las estadísticas forman parte de los recursos deportivos persistidos. La procedencia y la cobertura se conservan junto a las generaciones publicadas para distinguir un dato ausente de una consulta pendiente o fallida.

## Actualización manual de SofaScore

La actualización se inicia desde el control del Dashboard por una sesión con permiso de actualización. El control solo está disponible cuando Sports Data confirma que Acquisition está disponible con un proveedor y modo compatibles. Que el contenedor esté saludable, por sí solo, no garantiza que SofaScore acepte o complete una consulta.

La receta habitual es `sofascore-group2-2627-v1` (Grupo 2). Una operación:

- revisa de forma rutinaria una ventana móvil de hasta tres jornadas recientes;
- también puede tratar resultados que faltan, aplazamientos, partidos recién finalizados, cambios relevantes, calendario y comentarios pendientes fuera de esa revisión rutinaria;
- permite hasta tres recuperaciones históricas selectivas;
- limita a **50 envíos físicos** a SofaScore el conjunto de lotes de una pulsación. El límite cuenta las peticiones reales del transporte, incluidas las del navegador, y el presupuesto se conserva entre reinicios.

No hay actualizaciones programadas ni reintentos automáticos que inicien otra adquisición. Una nueva operación requiere otra acción manual.

## Publicación y conservación

Acquisition trabaja sobre el inventario que le entrega Sports Data y produce un paquete sellado. Sports Data verifica el paquete, la procedencia, la generación y la cobertura antes de publicarlo.

Una actualización puede publicar recursos válidos aunque otros queden pendientes. En ese caso, los partidos y recursos afectados conservan sus datos anteriores, incluidos comentarios y estadísticas, y el Dashboard informa de lo nuevo, lo conservado y lo pendiente. Un rechazo del proveedor o un paquete no verificable no se publica; la generación válida anterior permanece servida. Un pendiente no se interpreta como un resultado vacío, cero o ausencia confirmada.

Sports Data es el único propietario de `real-jaen.sqlite`. Su volumen persistente contiene la generación publicada y el diario de coordinación de actualizaciones. Reconstruir una imagen o actualizar el código no importa datos ni reemplaza la base.

## Vídeos y sincronización OCR

Video Service utiliza un catálogo operativo explícito para asociar cada `video_id` con una referencia opaca de partido y, cuando existe, una calibración. El catálogo se monta desde `RJ_VIDEO_CATALOG_DIR` en solo lectura; el estado y las calibraciones generadas por el servicio tienen almacenamiento persistente propio.

Un trabajo de vídeo se inicia expresamente desde la interfaz. Para una URL pública admitida de YouTube, el servicio descarga el archivo temporalmente, ejecuta OCR sobre el reloj de la retransmisión y guarda la calibración si supera sus validaciones. El Dashboard puede usar comentarios narrativos como contexto temporal, pero Video Service no consulta SofaScore ni SQLite deportiva por su cuenta. Las consultas de reproducción solo resuelven posiciones con cobertura suficiente y conservan la incertidumbre cuando no la hay.

Este flujo no es una adquisición de datos deportivos: actualizar SofaScore no descarga vídeos ni ejecuta OCR. El OCR tampoco produce un análisis táctico automático ni una interpretación mediante LLM.

## Respaldos y restauración

Los scripts descritos en [despliegue](despliegue.md) separan expresamente estas operaciones de un despliegue de código:

- `respaldo-instalacion.sh` crea una copia consistente de la SQLite y de sus registros de actualización, además del historial de trabajos de Acquisition y el estado/calibraciones persistentes de Video Service. Verifica integridad y generación y genera `SHA256SUMS`.
- El respaldo no contiene secretos ni cookies/perfil del navegador. La restauración inicializa un perfil nuevo.
- El catálogo externo de vídeos (`RJ_VIDEO_CATALOG_DIR`) no forma parte del archivo de respaldo y debe respaldarse por separado para conservar sus referencias y calibraciones existentes.
- `restaurar-instalacion.sh` valida las sumas y la generación, se niega a utilizar volúmenes ya existentes y no arranca servicios. Se reserva para la primera carga o una recuperación explícita; no se ejecuta en cada publicación de código.
