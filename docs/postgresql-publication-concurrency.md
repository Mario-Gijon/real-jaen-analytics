# Publicación PostgreSQL y seguimiento de trabajos

Este documento registra el diseño del correctivo 08C.1-FIX para Sports Data con PostgreSQL. No cambia reglas
deportivas, recetas, límites de adquisición ni algoritmos de enriquecimiento.

## Orden y propiedad de la publicación

Cada publicación adquiere primero el bloqueo asesor transaccional de escritores deportivos
`pg_advisory_xact_lock(1380075856, 4)`. Después comprueba el trabajo compartido sin bloquear su fila. La importación,
el enriquecimiento, la comprobación de coherencia y la preparación de la generación ocurren mientras la fila del
trabajo sigue disponible para heartbeats y lecturas HTTP. La transacción conserva el bloqueo asesor y la fila de
`sports_data_publication_state` hasta confirmar los datos deportivos, comentarios, enriquecimientos, publicación y
nueva generación atómicamente.

Al final se establece un `lock_timeout` de dos segundos, se bloquea brevemente la fila del trabajo y se revalidan
propietario, fencing token original, estado, fase, cancelación, ID de publicación y vigencia del lease mediante
`clock_timestamp()` de PostgreSQL. Solo después se registran publicación y generación, se confirma el trabajo y se
realiza el `COMMIT`. La pérdida de propiedad o un lease vencido revierten la transacción completa. El propietario
antiguo nunca adopta el token de un sucesor.

La recuperación usa el mismo orden de bloqueo asesor, pero intenta adquirirlo sin esperar. Usa un `lock_timeout` de
250 ms, un `statement_timeout` de dos segundos, `FOR UPDATE SKIP LOCKED` y consulta el registro durable de
publicaciones antes de clasificar un trabajo. Un trabajo `publishing` con resultado aún indeterminado conserva su
cupo activo. La recuperación periódica corre desde el reconciliador aunque no haya navegador; las consultas GET no
recuperan ni reasignan trabajos.

## Conexiones y lecturas

Cada proceso Sports Data tiene un pool principal limitado a cinco conexiones (`pool_size=4`, un overflow,
`pool_timeout=2s`) para publicación, consultas, refresco de generación y reconciliación. Un pool reservado de una
conexión (`pool_timeout=1s`) atiende heartbeats, para un máximo de **seis conexiones por proceso**. El heartbeat usa
timeouts de lock y sentencia de dos y cinco segundos, respectivamente. El proceso de control de Compose se ejecuta
con un worker, por lo que el máximo local se mantiene en seis conexiones para Sports Data.

Las lecturas de API conservan la generación inmutable en memoria. PostgreSQL no expone escrituras sin confirmar;
hasta el `COMMIT`, otras instalaciones siguen leyendo la generación anterior. Cada petición conserva la generación
capturada al empezar. El refresco entre instalaciones sigue respetando
`SPORTS_DATA_GENERATION_CHECK_INTERVAL_SECONDS`; un polling de estado ya no lo fuerza en cada petición.

## Dashboard

`UpdatesProvider` mantiene un único lector de estado para la barra superior y la vista de operador. La barra comprueba
por separado la sesión antes de mostrar controles; `/operador` puede seguir mostrando el estado sin depender de esa
sesión. Las lecturas de estado no se solapan; los seguimientos de trabajo usan una petición cada vez y reintentos con
backoff acotado hasta 30 segundos. Un fallo conserva el último estado confirmado y lo marca como antiguo. Una
respuesta válida posterior limpia el error. Una respuesta tardía no puede sobrescribir una respuesta más reciente.
El timeout del cliente HTTP sigue siendo de diez segundos; una lectura antigua impide iniciar otra actualización.

## Medición y coste de `no_changes`

El coordinador registra con reloj monotónico la duración y el resultado de estas fases, junto a un UUID de
publicación: `input_prepare`, `owner_initial_check`, `sports_import`, `comments_import`, `enrichment`, `coherence`,
`generation_build`, `owner_final_check`, `publication_record`, `generation_record`, `shared_job_confirmation`,
`local_journal`, `commit` y el total. No registra documentos, rutas, sentencias SQL, secretos ni mensajes de error.

Una publicación `no_changes` sigue ejecutando el plan incremental e integridad de la importación; recalcula y compara
cada enriquecimiento seleccionado para detectar conflictos de la versión de reglas; verifica completitud y
coherencia; y reconstruye/precalienta la generación completa. La segunda publicación real observada vio 15.632
enriquecimientos sin cambios y duró 7 min 2 s en total, con unos 6 min 13 s en publicación. Esos resultados
preceden a la instrumentación por fase.

El fixture aislado en PostgreSQL 18 mantuvo `sports_import` pausado 3,4 s, más que el lease de 3 s, mientras los
heartbeats lo renovaban. El coordinador completó en 3,475 s; las fases registradas fueron `sports_import` 3400,1 ms,
`comments_import` 0,0 ms, `enrichment` 0,3 ms, `coherence` 0,2 ms, `generation_build` 0,0 ms y `commit` 2,6 ms.
Durante la pausa, 38 lecturas directas de trabajo y generación respondieron en p95 56,3 ms y máximo 107,2 ms; la
muestra incluye cinco consultas concurrentes con 50 ms de trabajo SQL simulado cada una. Todas observaron la
generación anterior. Tras el commit, el registro de publicación, la nueva generación y la referencia del trabajo
estaban confirmados conjuntamente. El importador, enriquecedor y constructor de generación se simularon en este
fixture: los tiempos solo validan coordinación y locks, y no predicen una publicación real ni la latencia HTTP.
El coordinador cierra la conexión PostgreSQL antes de escribir su journal local; si el proceso cae entre ambos pasos,
el registro durable de publicación permite reconciliarlo al reiniciar.

No se omiten comprobaciones ni se guarda en caché su resultado porque eso podría ocultar un cambio de reglas o una
incoherencia. La instrumentación queda lista para medir una futura publicación; este hito no inicia una actualización
real.
