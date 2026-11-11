# Ampliación del contrato /api/v1: actualización de datos

Redactado el 28-10-2026 antes de implementar; implementado y validado ese mismo día (resultados al final). Concreta,
sobre el servicio de adquisición definitivo (`../../api/docs/contrato-adquisicion-interno.md`), la propuesta
inicial del botón (retirada el 29-10-2026; recuperable en el historial de Git). Es la única excepción a la regla «`/api/v1` de solo
lectura»: las respuestas deportivas, el `DataSource` y sus rutas no cambian.

## Alcance real

El alcance lo declara el backend (`scope`) según la receta configurada (`RJ_UPDATE_RECIPE` en `control.py`). La
interfaz muestra sus textos tal cual (`label`, `description`, `actionLabel`):

- `group` (por defecto, `sofascore-group2-2627-v1`), botón «Actualizar Grupo 2». Consulta:
  - los resultados que faltan, sin importar su antigüedad;
  - los aplazados;
  - los cambios de calendario de las jornadas próximas y, por turnos, de las lejanas;
  - las correcciones de las últimas 72 h (regla 72 h / 6 h del SDK).

  Diseño y validación: `../../api/docs/diseno-receta-grupo2.md`. Una ejecución real el 29-10-2026,
  sobre una copia aislada y con los detalles de la jornada 9: `../../api/docs/prueba-real-grupo2.md`.
- `pilot` (`sofascore-pilot-16599363-v1`), botón «Actualizar piloto»: el calendario de la jornada 1 y un partido;
  se usa en las pruebas.

Lo que una actualización no consulta se conserva tal como está publicado.

El control de Grupo 2 ejecuta lotes sucesivos dentro de una sola solicitud del dashboard. Cada lote conserva los
límites de adquisición configurados; el control inicia otro únicamente si la publicación anterior avanzó y quedan
recursos elegibles aplazados por presupuesto. Un rechazo del proveedor, un transporte detenido, ausencia de progreso
o el máximo de lotes cierra la ejecución de forma segura. Cada lote se publica de forma verificada, por lo que los
datos ya publicados permanecen disponibles aunque la ejecución posterior se detenga.

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
- Una instalación puede restringir los detalles a unas jornadas (`detailRounds`, prueba real de una jornada): lo que
  el calendario detecte en otras queda en `restrictedMatchIds`, sin consultar y con sus datos anteriores; un recurso
  sin datos previos queda pendiente con el motivo `restricted`.
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

## Autorización

No hay una autenticación propia de las actualizaciones. La **sesión de acceso a la aplicación** permite consultar
y actualizar (`docker.md`, «Acceso»):
- la protege una contraseña compartida;
- dura 90 días;
- la verifican `src/proxy.ts` y, de nuevo, estas rutas.

Reglas:
- Sin sesión, las rutas de actualización responden `401 UNAUTHORIZED`, igual que las de datos.
- Toda operación que cambia estado exige además una cabecera `Origin` igual al origen de la petición (protección
  CSRF además de `SameSite`). Con un origen distinto, `403 FORBIDDEN`.
- La pantalla de actualización es `/operador`, sin enlace en la navegación: se llega desde el estado de la cabecera.
  La cabecera muestra el control a cualquier sesión, donde haya actualizaciones.

## Rutas públicas nuevas

Todas responden con `Cache-Control: no-store`.

| Método y ruta | Quién | Respuesta |
|---|---|---|
| GET /api/v1/updates/current | Sesión | `UpdateStatus`: alcance, última publicación, trabajos activo y último, y cobertura de la última publicación |
| POST /api/v1/updates | Sesión | Cuerpo vacío y cabecera `Idempotency-Key` (UUID). `202 {job, reused}`. La misma clave devuelve el mismo trabajo, aunque haya terminado. Con otro trabajo activo, devuelve ese con `reused: true` y no crea otro |
| GET /api/v1/updates/{id} | Sesión | `200 UpdateJob`; `404` si no existe |
| POST /api/v1/updates/{id}/cancel | Sesión | `202 UpdateJob`. Idempotente. `409 NOT_CANCELLABLE` si ya verifica, publica o terminó |
| GET /api/v1/session | Cualquiera | `{required, signedIn}`; `503` si se exige y no está configurado |
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
