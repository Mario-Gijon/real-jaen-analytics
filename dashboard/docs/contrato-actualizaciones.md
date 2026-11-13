# Ampliación del contrato /api/v1: actualización de datos

Redactado el 28-10-2026 antes de implementar; implementado y validado ese mismo día (resultados al final). Concreta,
sobre el servicio de adquisición definitivo (`../../api/docs/contrato-adquisicion-interno.md`), la propuesta
inicial del botón (retirada el 29-10-2026; recuperable en el historial de Git). Es la única excepción a la regla «`/api/v1` de solo
lectura»: las respuestas deportivas, el `DataSource` y sus rutas no cambian.

## Alcance real

El alcance lo declara el backend (`scope`) según la receta configurada (`RJ_UPDATE_RECIPE` en `control.py`). La
interfaz muestra sus textos tal cual (`label`, `description`, `actionLabel`):

- `group` (por defecto, `sofascore-group2-2627-v1`), botón «Actualizar Grupo 2». Consulta:
  - revisiones rutinarias dentro de las tres últimas jornadas con un partido finalizado, calculadas con el inventario
    publicado y los calendarios ya consultados; si se finaliza una jornada posterior, la ventana avanza;
  - los resultados que faltan, sin importar su antigüedad;
  - los aplazados recuperados, los partidos recién finalizados y los cambios relevantes, aunque sean de jornadas antiguas;
  - los cambios de calendario de las jornadas próximas y, por turnos, de las lejanas;
  - los comentarios pendientes y hasta tres recuperaciones selectivas de partidos históricos;
  - las correcciones recientes que indique la regla 72 h / 6 h del SDK.

  Una pulsación permite como máximo 50 envíos físicos a SofaScore, compartidos por todos sus lotes. El contador se
  reserva antes de cada petición y vive junto al estado persistente de Acquisition; si no se puede verificar, se
  detiene sin reiniciarlo. No hay consultas automáticas en segundo plano.

  Diseño y validación: `../../api/docs/diseno-receta-grupo2.md`. Una ejecución real el 29-10-2026,
  sobre una copia aislada y con los detalles de la jornada 9: `../../api/docs/prueba-real-grupo2.md`.
- `pilot` (`sofascore-pilot-16599363-v1`), botón «Actualizar piloto»: el calendario de la jornada 1 y un partido;
  se usa en las pruebas.

Lo que una actualización no consulta se conserva tal como está publicado.

El control de Grupo 2 ejecuta lotes sucesivos dentro de una sola solicitud del dashboard. El presupuesto físico
restante se comparte entre lotes y no se reinicia al reiniciar servicios. El control inicia otro lote únicamente si
la publicación anterior avanzó, quedan recursos elegibles aplazados por presupuesto y el informe confirma margen
seguro. Cada lote se publica de forma verificada, por lo que los datos ya publicados permanecen disponibles aunque
la ejecución posterior se detenga.

### Publicación con cobertura parcial

Una actualización del grupo publica aunque algunos recursos queden sin datos. El resultado
(`UpdateJob.result.coverage`) distingue:

- **datos nuevos** (`newResources`);
- **datos anteriores conservados** (`keptResources`);
- **pendientes** por partido (`pending`): el recurso, el motivo y si se sigue sirviendo la versión anterior (`kept`).

Además lista los partidos con la revisión aplazada por presupuesto (`deferredMatchIds`, con sus datos conservados) y
los partidos nuevos con su posible sustitución (`newMatches`, decisión manual), con sus nombres (`matchNames`).

- Si quedan pendientes, la interfaz dice **«Actualización completada con pendientes»** y los enumera con su motivo.
- Las revisiones aplazadas se enumeran por partido: se siguen sirviendo sus datos anteriores.
- Los partidos fuera de la ventana rutinaria se identifican en `restrictedMatchIds` cuando no concurre una excepción;
  se conservan sus datos anteriores. Los cambios relevantes y recién finalizados no quedan bloqueados por la ventana.
- `UpdateScope` y `coverage` muestran la ventana aplicada, su jornada de referencia, el máximo de 50 envíos y el uso
  acumulado de la pulsación. Los datos nuevos, los conservados, el presupuesto aplazado y los errores del proveedor se
  presentan por separado.
- Si la generación publicada no cambia y no quedan recursos pendientes ni aplazados, el estado dice **«Revisión
  completada sin cambios»**. Una generación sin cambios se informa por separado de un paquete reutilizado.
- Si una actualización posterior falla o se cancela, `lastCoverage` mantiene visibles los pendientes y aplazados de
  la última publicación válida, incluidos los lotes ya publicados durante esa ejecución.
- Un pendiente nunca es ausencia ni cero.
- Un rechazo del proveedor no publica nada.

## Flujo y fronteras

UI → cliente tipado (`src/client/api.ts`) → `/api/v1/updates/*` de Next → cliente de control del servidor
(`src/server/updates/`) → API interna del backend (`/internal/v1/updates/*`) → control de actualizaciones →
cliente de adquisición → servicio `acquisition` y coordinador.

- **Next** comprueba la sesión de acceso, el origen y el modo, y llama al backend con el **secreto interno de
  control** (`RJ_CONTROL_SECRET_FILE`), que solo lee el servidor de Next. Nunca llega al navegador ni aparece en
  respuestas o logs. Es distinto del secreto entre backend y adquisición, que Next no conoce.
- **Backend**: en modo control (`control.py`), el mismo proceso que sirve la API es el dueño del coordinador. Cada
  GET deportivo captura la generación vigente al entrar, y una publicación cambia la referencia sin cortar las
  peticiones en curso. Un único trabajo activo, con un registro durable de trabajos y claves de idempotencia en
  `<base>.actualizaciones/control/`. Sin modo control, `api.py` sigue igual que hasta ahora.
- **Demo** (`DATA_MODE=demo`) o falta de configuración: la interfaz no muestra el control y las operaciones
  responden `409 UPDATE_UNAVAILABLE` sin llamar al backend.
- **Acquisition apagado o modo incompatible**: Sports Data comprueba `/health` y la identidad del proveedor sin
  consultar a SofaScore. `available` se calcula en cada consulta; la interfaz lo vuelve a consultar cada 15 s.
  Antes de guardar un trabajo, Sports Data repite la comprobación y responde `409 update_unavailable` si falla,
  sin crear una fila de trabajo. Si el servicio cae después de esa comprobación, la operación queda registrada y
  termina de forma segura, conservando la generación anterior.

## Autorización

No hay usuarios individuales ni roles configurables. La **sesión de acceso a la aplicación** distingue dos
capacidades compartidas:
- la contraseña de lectura permite consultar la aplicación;
- la contraseña de actualización permite consultar y operar actualizaciones;
- `RJ_ACCESS_PASSWORD_FILE` y `RJ_UPDATE_PASSWORD_FILE` apuntan a archivos privados externos al repositorio. La
  contraseña administrativa es opcional; si falta, coincide con la normal o no es válida, las actualizaciones
  quedan deshabilitadas;
- `RJ_SESSION_SECRET_FILE` firma la cookie durante 90 días. La firma está ligada a la capacidad y a la contraseña
  que la concedió. Cambiar esa contraseña revoca sus sesiones; rotar el secreto de sesión revoca todas;
- las cookies `v1` anteriores se invalidan al pasar al formato `v2` para impedir que una sesión antigua conserve
  permiso de actualización;
- `src/proxy.ts` y las rutas API verifican la capacidad; una `canUpdate` enviada por el navegador no concede acceso.

Reglas:
- Sin sesión, todas las rutas de actualización responden `401 UNAUTHORIZED`.
- Una sesión de lectura recibe `403 FORBIDDEN` en esas rutas. No se reenvían solicitudes de lectura a Sports Data.
- Una sesión de lectura que abre `/operador` vuelve al inicio. La cabecera no monta el control ni consulta el estado
  de actualización; solo hace una consulta de sesión para saber si puede mostrarlo.
- Toda operación que cambia estado exige además una cabecera `Origin` igual al origen de la petición (protección
  CSRF además de `SameSite`). Con un origen distinto, `403 FORBIDDEN`.
- La pantalla de actualización es `/operador`, sin enlace en la navegación: se llega desde el estado de la cabecera.
  La cabecera muestra el control únicamente a una sesión con permiso de actualización.

## Rutas públicas nuevas

Todas responden con `Cache-Control: no-store`.

| Método y ruta | Quién | Respuesta |
|---|---|---|
| GET /api/v1/updates/current | Sesión administrativa | `UpdateStatus`: disponibilidad dinámica de Acquisition, alcance, última publicación, trabajos activo y último, y cobertura de la última publicación |
| POST /api/v1/updates | Sesión administrativa | Cuerpo vacío y cabecera `Idempotency-Key` (UUID). `202 {job, reused}`. La misma clave devuelve el mismo trabajo, aunque haya terminado. Con otro trabajo activo, devuelve ese con `reused: true` y no crea otro |
| GET /api/v1/updates/{id} | Sesión administrativa | `200 UpdateJob`; `404` si no existe |
| POST /api/v1/updates/{id}/cancel | Sesión administrativa | `202 UpdateJob`. Idempotente. `409 NOT_CANCELLABLE` si ya verifica, publica o terminó |
| GET /api/v1/session | Cualquiera | `{required, signedIn, canUpdate}`; `canUpdate` solo refleja la capacidad validada por el servidor; `503` si se exige y no está configurado |
| POST /api/v1/session | Cualquiera | `{"password": "…"}`: `204` y cookie de 90 días; `401` si no es válida; `429` si hay demasiados intentos |
| DELETE /api/v1/session | Cualquiera | `204` y cookie borrada |

```ts
type UpdateScope = {
  kind: "pilot" | "group";
  label: string;            // «Actualización del Grupo 2 (Primera Federación 26/27)»
  description: string;      // qué consulta y qué no
  actionLabel: string;      // «Actualizar Grupo 2» / «Actualizar piloto»
  round: number | null;     // solo el piloto
  matchIds: string[];       // solo el piloto: ids públicos
};
type UpdateJob = {
  id: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled" | "interrupted";
  phase: "queued" | "acquiring" | "verifying" | "publishing" | "finished";
  cancellable: boolean;     // solo mientras se adquiere
  published: boolean;       // true solo si el coordinador confirmó la generación nueva
  requestedAt: string;      // UTC ISO-8601
  finishedAt: string | null;
  pollAfterSeconds: number;
  error: { code: UpdateErrorCode; message: string; retryable: boolean } | null;
  result: { publishedAt: string; reusedBundle: boolean; coverage: UpdateCoverage | null } | null;  // coverage: solo el grupo
};
type UpdateStatus = {
  available: boolean;
  scope: UpdateScope | null;
  lastPublished: { at: string } | null;    // última generación publicada por este flujo; null si nunca
  activeJob: UpdateJob | null;
  lastJob: UpdateJob | null;
  lastCoverage: UpdateCoverage | null;     // cobertura de esa última publicación (grupo)
};
```

Los códigos de error del trabajo son `ACQUISITION_FAILED` (el proveedor rechazó o falló; no se reintenta solo),
`ACQUISITION_INTERRUPTED`, `CANCELLED`, `BUNDLE_REJECTED`, `GENERATION_CHANGED`, `PUBLICATION_FAILED`,
`ACQUISITION_UNAVAILABLE` e `INTERRUPTED` (reinicio del backend durante el trabajo). En todos se sigue sirviendo la
última generación válida. Los mensajes son seguros: sin rutas, trazas, URLs internas ni secretos.

`ApiErrorCode` se amplía con `UNAUTHORIZED` (401), `FORBIDDEN` (403), `UPDATE_UNAVAILABLE` (409, modo o
configuración), `CONFLICT` (409, por ejemplo `NOT_CANCELLABLE` o una recuperación pendiente) y `TOO_MANY_REQUESTS`
(429). El código `UPSTREAM_UNAVAILABLE` (503) indica que el backend no responde.

## API interna del backend (no pública)

Solo en modo control, sin puerto al anfitrión y con `Authorization: Bearer <secreto de control>`, comparado en
tiempo constante. Las mismas operaciones con prefijo `/internal/v1/updates`. Sin el secreto configurado, el modo
control no arranca. Las rutas deportivas y `/health` no cambian.

## Comportamiento

- **Un único trabajo activo.** Una segunda pulsación, otra pestaña u otra persona reciben el mismo trabajo. La
  interfaz deshabilita el botón mientras espera respuesta y reutiliza la misma clave de idempotencia hasta obtener
  el trabajo.
- **Progreso por fase**, sin porcentajes inventados. En `acquiring` se puede cancelar: el backend pide la
  cancelación cooperativa al servicio de adquisición. En `verifying` y `publishing` no se interrumpe.
- **Resultado.** `completed` solo cuando el coordinador confirmó la generación. Cualquier fallo deja publicada la
  generación anterior.
- **Reintento sin volver a consultar.** Si el último trabajo falló al publicar, con el bundle ya verificado y la
  generación sin cambios, el siguiente trabajo reutiliza esa solicitud (`result.reusedBundle: true`).
- **Reinicio del backend.** Un trabajo activo se concilia con el coordinador: si su publicación quedó confirmada, se
  marca `completed`; si no, `interrupted`. Nunca se reanuda solo.
- **Tras publicar**, la interfaz ofrece recargar los datos; Next olvida su caché de nombres de equipo al observar
  una publicación.

## Validación

- Backend: `tests/test_control.py` (9) con el coordinador real sobre la base de fixtures: idempotencia, un único
  trabajo activo, cancelación al adquirir y antes de verificar, rechazo del proveedor, bundle rechazado, fallo al
  publicar con reintento sobre la misma solicitud, conciliación tras reinicio y autorización HTTP.
- Frontend: rutas (`tests/api/updates-route.test.ts`), interfaz (`tests/ui/UpdateControl.test.tsx`), regla de
  arquitectura con esta excepción exacta y e2e simuladas (`e2e/updates.spec.ts`, 1440 y 390 px).
- Recorrido completo (`../../api/docker/acquisition/test_updates.py` y `scripts/validate-updates-stack.mjs`).
  Usa una pila temporal con el proveedor local `fixture-site`, una copia aislada de una copia inactiva, el backend en
  modo control y Next de producción. Comprueba:
  - cancelación mientras se consulta, sin cambios en los datos;
  - tres pulsaciones que producen un único inicio, seguido hasta la publicación;
  - SIGKILL del backend durante la consulta: interrumpido, con la misma generación y los mismos datos;
  - rechazo del proveedor: fallido, con los datos anteriores;
  - reinicio limpio con el estado conservado;
  - ningún secreto en las respuestas que recibe el navegador ni en los logs, y cookie `HttpOnly` y `SameSite=Strict`.

  Sin consultar SofaScore ni tocar la base activa.

Limitaciones de la autorización local para un servidor: [docker.md](docker.md), «Actualización desde la interfaz».
