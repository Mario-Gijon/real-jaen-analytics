# Decisiones canónicas de identidad de jugadores

Esta herramienta administra asociaciones explícitas entre IDs SofaScore en PostgreSQL. No deduce equivalencias por
nombre y no cambia la interfaz de Dashboard. Todas las órdenes usan `SPORTS_DATA_MIGRATION_DATABASE_URL`, el rol
administrativo de Sports Data y los destinos permitidos por `SPORTS_DATA_MIGRATION_ALLOWED_HOSTS`; no aceptan una URL
por argumento.

## Previsualizar y aplicar

Antes de aplicar, revisa una propuesta con:

```sh
python -m persistencia identidad previsualizar --canonico 901 --alias 902
```

El ID canónico debe ser un ID SofaScore `origin` activo. Cada alias debe identificar una entidad distinta. La
previsualización no escribe. Rechaza ciclos, asociaciones inválidas y cualquier par de IDs que coincida en una
convocatoria vigente del mismo partido.

La aplicación requiere responsable, motivo, evidencia y el ámbito de publicación:

```sh
python -m persistencia identidad aplicar \
  --canonico 901 --alias 902 \
  --responsable "Nombre del administrador" \
  --motivo "Evidencia documental contrastada" \
  --evidencia "Referencia a la fuente y conclusión revisada" \
  --ambito ambito.json
```

La orden adquiere el bloqueo asesor de escritura deportiva y el contador compartido de trabajos, y se rechaza si hay
Acquisition activo. Reasigna los IDs externos al jugador canónico, registra la decisión y su instantánea de reversión,
proyecta las convocatorias y referencias derivadas al mismo jugador y publica una generación mediante pg0002. Los
payloads, observaciones, IDs externos de origen, estadísticas y participantes narrativos conservan sus valores fuente.
Las fichas prefieren los atributos observados para el ID canónico; un atributo ausente solo se completa desde alias
si sus valores coinciden. Los desacuerdos originales y su procedencia permanecen en las observaciones.
Las consultas de comentarios enriquecidos aceptan el ID canónico o cualquiera de sus alias sin repetir comentarios;
los participantes enriquecidos siguen exponiendo el ID fuente. El catálogo de fotografías se consulta a través de
cualquiera de los IDs asociados, sin cambiar la fuente ni la procedencia de la imagen.

## Consultar y revertir

```sh
python -m persistencia identidad listar
python -m persistencia identidad revertir \
  --decision-id 17 \
  --responsable "Nombre del administrador" \
  --motivo "La evidencia fue reconsiderada" \
  --evidencia "Referencia a la revisión" \
  --ambito ambito.json
```

La reversión añade una decisión `revert`; no borra el historial. Solo procede si las filas afectadas conservan el
estado posterior exacto y no existen decisiones posteriores activas ni nuevas convocatorias u observaciones
personales de atributo para los IDs alias. Ambas órdenes de escritura preparan la instantánea servida y actualizan el
número durable de generación en la misma transacción; una instancia activa la recibe en su siguiente comprobación
normal.

No se necesita una migración de esquema. La compatibilidad y sincronización se comprueban antes y después de cada
operación. No aplicar decisiones sobre jugadores reales hasta revisar individualmente la evidencia y la previsualización.
