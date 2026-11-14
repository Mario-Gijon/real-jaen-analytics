# Contrato interno backend–adquisición (protocolo unificado)

Vigente desde el 28-10-2026. Sustituye a la integración simulada v1 y al protocolo v2 del piloto, ambos en
[historico/](historico/README.md). Estado de la adquisición y ejecuciones reales: [adquisicion-sofascore.md](adquisicion-sofascore.md).
Despliegue: servicios `sports-data` y `acquisition` en `compose.dev.yaml` y `compose.prod.yaml`. Las imágenes mantienen esta frontera HTTP.

## Fronteras

- **Backend**: único dueño de la base deportiva, su journal, copias, candidatas, importación y publicación (el
  coordinador de `persistencia/actualizacion.py`). Calcula el inventario de su generación vigente y lo envía con la
  solicitud. No tiene scraper, navegador ni código de adquisición.
- **Servicio `acquisition`** (`acquisition/app.py`, `http://acquisition:8002`, Uvicorn con un worker y sin puerto al anfitrión): solo
  monta su perfil gestionado, su almacén de trabajos y el secreto. Nunca monta el volumen deportivo ni el journal.
  Posee el perfil (flock) toda su vida y abre una sesión exclusiva por trabajo.
- **Red**: backend y acquisition se hablan solo por `acquisition-control` (interna, sin salida). Solo acquisition
  está además en `acquisition-egress`. Limitaciones de esa salida: [adquisicion-sofascore.md](adquisicion-sofascore.md),
  «Salida de red y lo que se observa».
- **Autorización**: secreto de 32-256 caracteres ASCII en un archivo de solo lectura montado en ambos
  (`/run/secrets/acquisition-token`), cabecera `Authorization: Bearer`, comparación en tiempo constante, 401
  genérico. Solo `/health` va sin autorización. HTTP en red privada, sin cifrado en tránsito.

## Operaciones

| Método y ruta | Petición / respuesta |
|---|---|
| POST /internal/v1/acquisitions | JSON exacto `{request_id: UUID, recipe_id, inventory}` (≤ 32 MiB, claves duplicadas rechazadas, inventario con su huella verificada); 202 nuevo, 200 misma clave con la misma receta e inventario |
| GET /internal/v1/acquisitions/{id} | id, request_id, recipe_id, inventory_sha256, generation_id, state, phase, progress, error seguro y descriptor `{bytes, sha256}` al completar |
| GET /internal/v1/acquisitions/{id}/report | Resumen del trabajo: envíos, peticiones, sandbox, hosts de las páginas y muestra de conexiones; sin cuerpos ni cookies |
| POST /internal/v1/acquisitions/{id}/cancel | Cancelación cooperativa, idempotente |
| GET /internal/v1/acquisitions/{id}/bundle | Solo `completed`: bundle inmutable, `Content-Length` y `ETag` con su SHA-256 |

Recetas aprobadas:

- `sofascore-group2-2627-v1`: el Grupo 2 con cobertura parcial; formato `group-v1` y verificador `_verify_group`.
  Diseño, garantías y validación: [diseno-receta-grupo2.md](diseno-receta-grupo2.md).
- `sofascore-pilot-16599363-v1`: formato `pilot-v2`.

No se aceptan comandos, URLs, rutas, demoras ni parámetros del scraper. Un trabajo activo a la vez; `request_id` durable: la misma clave con otra receta o inventario da 409.
Estados: queued, running, cancelling, cancelled, completed, failed, interrupted. Al reiniciar, los no terminados
pasan a interrupted y no se reanudan; repetir su clave devuelve el mismo trabajo interrumpido.

Cada trabajo corre en un proceso propio (`acquisition/job.py`, el SDK guarda estado global por proceso) dentro de
la sesión exclusiva del perfil. Cancelar crea un aviso cooperativo; a los 30 s, o a los 480 s de trabajo, se mata
todo su grupo de procesos. Si el navegador no se cerró limpiamente, la sesión queda abierta y el servicio deja de
aceptar trabajos: el perfil exige la recuperación administrativa manual (`acquisition.admin recover-profile`).

La aplicación ASGI adapta HTTP a `Jobs`, `configuration()` y `Profile` de `acquisition/service.py`; el ciclo de vida
cierra los trabajos antes de liberar el bloqueo exclusivo del perfil. Las versiones directas de FastAPI y Uvicorn se
declaran en `requirements-acquisition.txt` y se verifican contra la imagen de navegador auditada; no se instalan
dependencias ni se accede a la red durante build.

Proveedores: `ACQ_PROVIDER=sofascore` con `ACQ_ENABLE_REAL=1` (Compose real) o `fixture-site` con
`ACQ_TEST_MODE=1`, solo en la imagen `test` (sitio local que sirve respuestas grabadas al mismo flujo del SDK y su
navegador). Sin uno de los dos, el servicio no arranca.

## Bundle y garantías

Formato en `persistencia/bundle.py` (compartido por productor y receptor). Los identificadores del formato
(`contract: pilot-v2`, receta `sofascore-pilot-16599363-v1`) se conservan del piloto que lo validó para que la
evidencia sellada siga siendo verificable.

1. **Inventario de la generación vigente**: `inventory()` copia del coordinador los eventos vigentes e históricos,
   las sustituciones y los originales, con su huella; el servicio lo rechaza si la huella no cuadra.
2. **Cobertura por recurso**: calendario, evento y secciones de cada evento vigente con `data` o `not_applicable`
   justificado por fase. Un vacío o un 404 nunca es ausencia confirmada; un parcial no se sella ni se publica.
3. **Procedencia**: cada respuesta original con su intento, `fetched_at` y huella; lo no consultado se reutiliza
   tal cual del inventario, sin refechar. En Grupo 2, IDs nuevos de jornadas consultadas se informan para revisión,
   pero no se consultan ni se publican en el calendario; una corrección que cambia la identidad de un partido se
   rechaza antes de publicar.
4. **Productor aprobado**: `producer_rules()` (huella del código de la receta, el transporte, el adaptador y el
   trabajo, la revisión del scraper y la política) debe estar en `productores-adquisicion.json`. Cambiar ese código
   exige añadir su huella tras revisarlo. La del productor del 28-10 (`e18eba33…`) solo vale para la generación de
   su bundle. Ese archivo está fuera de `persistencia/` para que aprobar un productor no altere la huella de reglas.
5. **Bundle inmutable verificado** antes del coordinador: descriptor, ETag, límites, nombres, tipos, base64, hashes,
   contexto (trabajo y solicitud), transporte (`sofascore-https` real; `loopback-fixture` solo en una instalación de
   prueba marcada) y generación.
6. **Generación esperada**: el receptor compara el inventario actual con el de la solicitud y entrega al coordinador
   `generacion_esperada`; si cambió, se rechaza sin publicar.

### Capacidad de `comments.jsonl` en Grupo 2

`group-v2` admite hasta **8 MiB** para `comments.jsonl`. `MAX_FILE` continúa en **4 MiB** para los demás archivos;
`MAX_TOTAL` (64 MiB), `MAX_WIRE` (96 MiB), `MAX_LINE` (2 MiB), tamaños declarados, SHA-256, JSONL y procedencia
siguen siendo obligatorios y no cambian.

Antes de cada consulta de comentarios, Acquisition comprueba el tamaño ya serializado y reserva una línea completa
de `MAX_LINE` más su salto de línea. Si la reserva no cabe, no registra otro intento y deja los partidos restantes
pendientes para la siguiente actualización. La respuesta de cada consulta ya iniciada queda guardada en el volumen
durable del trabajo y se incluye en el bundle si cabe.

La respuesta original va en Base64 dentro de una única línea JSONL. Si una respuesta individual supera `MAX_LINE`
después de codificar su evidencia, el contrato actual no puede transportarla: el cuerpo original permanece en el
almacenamiento persistente de Acquisition y el bundle no se sella. No se trunca ni se omite silenciosamente.

## Recepción en el backend e idempotencia

`Cliente.publicar(coordinador, request_id, receta)` (`persistencia/adquisicion.py`), llamado por el control de
actualizaciones del backend en modo control (`control.py`, el botón «Actualizar» de la interfaz, contrato en
`../../dashboard/docs/contrato-actualizaciones.md`) o por la orden `python -m persistencia adquirir` con la API
detenida. Ese control dispara la receta configurada (por defecto la del Grupo 2; `control.SCOPES`), con un único trabajo activo, claves de idempotencia y
cancelación cooperativa mientras se adquiere; su registro de trabajos vive en `<base>.actualizaciones/control/`. Bajo `<base>.actualizaciones/adquisiciones/<request_id>/`
anota cada paso antes de darlo: inventario, trabajo de adquisición, bundle descargado y cada intento de importación.

- Repetir la misma solicitud reutiliza su inventario, su trabajo y su bundle verificado: no vuelve a consultar al
  proveedor.
- Si el coordinador falla sin publicar (por ejemplo, tras confirmar la candidata), repetir la solicitud crea un
  nuevo intento de importación con el mismo bundle, solo si la generación sigue siendo la del inventario.
- Si la publicación ya se confirmó, repetir devuelve el mismo resultado sin otro trabajo.
- Una adquisición cancelada, fallida o interrumpida nunca inicia una importación.

## Pruebas

| Prueba | Dónde corre | Cubre |
|---|---|---|
| `tests/acquisition/test_protocol.py` (11) | imagen `test` | Autorización, parámetros e inventario inválidos, duplicados y conflictos, un trabajo activo, cancelación, fallos y bundles sin sellar, corrupción del sellado, reinicio sin reanudación, proveedor explícito, productor aprobado |
| `tests/acquisition/test_transport.py` (16) | imagen `test` | Presupuesto anotado antes del envío, deduplicación, reintentos y esperas, bloqueo persistente tras 403/429, redirecciones, cancelación, propietario exclusivo, journal lleno, evidencia, sin reconsultar tras reiniciar, 404/vacío/malformado nunca son ausencia; la receta no sella tras fallo de copia, corrección fuera del partido o recurso incompleto |
| `tests/acquisition/test_sdk_adapter.py` (8) | imagen `test`, sandbox exigido | Challenge con tráfico reducido, 403 sin challenge, challenge irresoluble, 429 en el navegador, ruta no permitida, perfil ocupado sin perfil temporal, TLS de navegador y curl |
| `docker/acquisition/test_integration.py` | pila `compose.test.yaml` | Recorrido completo con el servicio, el SDK, el navegador y el perfil gestionado, sobre una copia aislada (abajo) |
| `tests/test_adquisicion.py` | suite del anfitrión | Clave durable del coordinador, huellas aprobadas, productor no aprobado rechazado |

La pila de integración usa una red interna sin salida, volúmenes nuevos y un secreto temporal. El servicio
reproduce, por el SDK y su navegador, las respuestas originales del bundle real del 28-10. El backend trabaja
sobre una copia de `copias/real-jaen.20261027-083912-846469.piloto-16599363.sqlite`. Casos:

- servicio sin proveedor rechazado, perfil inicializado, montajes de acquisition limitados a perfil, trabajos,
  secreto y rutas grabadas, y autenticación en vivo;
- bundle real del 28-10 aceptado por el receptor definitivo en su propia generación, y rechazado como prueba y en
  la generación actual;
- el cliente real rechaza una instalación de prueba;
- fallo inyectado tras confirmar la candidata, sin publicar; reintento con el mismo trabajo y bundle que publica;
  duplicado idempotente; reinicio del coordinador idempotente; mantenimiento por CLI rechazado mientras el
  coordinador posee la base; ninguna petición nueva al proveedor en los reintentos;
- solicitud preparada en la generación anterior: se adquiere y verifica, pero no se publica;
- cinco bundles manipulados y un descriptor alterado, rechazados antes del coordinador;
- identificadores, originales y decisiones locales conservados, y todas las respuestas iguales a las del lector SQL
  independiente;
- SIGKILL del servicio durante un trabajo: el perfil exige recuperación, se recupera manualmente y el trabajo queda
  interrumpido, sin reanudarse ni publicarse;
- sitio que rechaza (403 sin challenge): el trabajo falla sin importar nada y el bloqueo persistente detiene el
  siguiente sin ningún envío;
- auditoría de los logs: el secreto no aparece.

## Lo retirado y qué lo cubre

| Retirado | Cubierto por |
|---|---|
| `persistencia/bundle_v2.py`, `adquisicion_v2.py` | `persistencia/bundle.py` (mismo verificador) y `persistencia/adquisicion.py` (receptor integrado en el cliente) |
| `acquisition/pilot_bundle.py`, `pilot_transport.py`, `sdk_transport.py` | `acquisition/recipe.py`, `acquisition/transport.py` |
| Pruebas de transporte y de receta de `docker/acquisition/test_pilot_v2.py` | `tests/acquisition/test_transport.py`, las mismas 16 |
| Recepción de `pilot_backend_check.py` (autenticación, 4 manipulaciones, fallo tras candidata, publicación, duplicado, generación obsoleta, IDs, rutas) | `test_protocol.py` e `integration_flow.py` |
| Recorrido `collect-sdk` y `scripts/test_pilot_v2.py` | Integración con el servicio y el proveedor `fixture-site` |
| `verify-real` y `scripts/prueba_real_sdk.py` | `integration_flow.py real-bundle`; la prueba real queda en el commit `69ac18f` |
| Proveedor simulado v1, `validar_bundle` de un archivo, `compose.simulated.yaml` | Proveedor `fixture-site`, `bundle.verify`, `compose.test.yaml` |
| `acquisition/real_bundle.py`, `sofascore_offline.py`, `sofascore-scope.json` y sus pruebas, `Dockerfile.pilot`, `scripts/build_pilot_image.py`, `plan_sofascore_evidence.py` | Sin equivalente: era un formato propuesto que no llegó a conectarse. Sus garantías de ámbito, cobertura y ausencia las aplica `bundle.verify` |

## Activación y compatibilidad

El coordinador exige que la huella de reglas de la base (`carga.referencia_del_codigo`: `api.py`, `queries.py`, `importar.py`,
`alembic.ini`, `requirements-sql.txt` y todo `persistencia/`) coincida con la del código; si no, pide mantenimiento
explícito (`reconstruir --confirmar` con la API detenida, que hace copia antes). Sobre la base activa hará falta **una
vez**, en la primera adopción, por dos motivos comprobados el 28-10:

- las bases cargadas antes del coordinador no registran huella (la copia inactiva del 24-10, código `3fa4147-dirty`,
  no tiene ninguna en `import_run`), así que la primera apertura siempre necesita ese mantenimiento;
- la huella ha cambiado desde entonces con cambios de reglas reales (`d4f5bbb`, conflictos de estadísticas en
  `api.py`; `d653241`, orden de convocatorias en `sincronizacion.py`) y con el código del protocolo en
  `persistencia/` (`bdf8fad` y esta integración).

Las actualizaciones posteriores no lo necesitan: importar evidencia no cambia el código, y el coordinador guarda en
su journal la huella y la generación confirmadas (en la pila de prueba hubo varias publicaciones y reinicios del
coordinador sin mantenimiento). Solo vuelve a hacer falta al desplegar código nuevo de `persistencia/`, `api.py`, `queries.py` o
`importar.py`. La lista de productores aprobados está fuera de `persistencia/` (`productores-adquisicion.json`)
para que aprobar una receta nueva no lo exija; una prueba de la suite lo comprueba. No se ha hecho sobre la base
activa.

Un bundle queda ligado a la generación de su inventario y nunca se adapta a otra: el verificador exige la misma
huella de inventario y generación, el receptor compara con la generación vigente antes de entregarlo y el
coordinador vuelve a comprobarla con `generacion_esperada`. Tras el mantenimiento, el bundle real del 28-10 ya no se
puede publicar; su aprobación de productor está limitada a su propia generación.

Fuera de este contrato: identidad y rol de operador del servidor del grupo (hoy, una clave local compartida), política
histórica y otros partidos, retención y cuotas de trabajos y bundles, TLS fuera de la red privada y rotación de
secretos. Queda también validar en el servidor del grupo el kernel, el LSM, los namespaces, el sistema de archivos
y los permisos del secreto.
