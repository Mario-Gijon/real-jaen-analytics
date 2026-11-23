# Auditoría técnica CI/CD para SINBAD²

**Fecha:** 23 de noviembre de 2026
**Alcance:** auditoría del despliegue actual, cambio de disparador y propuesta. No se implementó un despliegue nuevo, no se contactó con SINBAD² y no se manipularon datos de producción.

## Resumen

El job existente transfiere una copia archivada del commit por SSH a una carpeta de release del servidor y allí construye e inicia los cuatro servicios con Docker Compose. El trabajo ya no debe ejecutarse para `master`: ahora se limita a **pushes de rama a `main`**. La condición también descarta eventos de merge request, tags, schedules, ejecuciones manuales y pipelines downstream.

El flujo actual sirve para actualizar una instalación previamente preparada. **No puede completar una primera instalación por sí solo.** Espera que ya existan `.env.prod`, cinco archivos secretos, los directorios externos de secretos/importaciones/catálogo y cuatro volúmenes Docker restaurados. En particular, Sports Data se niega a crear una base SQL vacía y Acquisition exige un perfil inicializado. Si faltan los volúmenes, el preflight se detiene; si existen pero no contienen estado válido, Compose puede dejar un arranque parcial. Esa negativa protege los datos reales: no se debe reemplazar por una inicialización automática con datos vacíos.

Una variable de tipo File puede suministrar `.env.prod` y los secretos en GitLab, y la pipeline puede crear rutas que pertenezcan al usuario SSH. Aún hacen falta permisos de despliegue del daemon Docker, conectividad SSH desde un Runner adecuado, un respaldo deportivo verificado para la primera carga y una decisión sobre el catálogo externo de vídeo. Las rutas de ejemplo `/etc/...` y `/srv/...` normalmente requieren preparación o permisos de administrador; pueden trasladarse a directorios persistentes bajo el `$HOME` del usuario de despliegue.

## Estado actual

### Pipeline y conexión SSH

- `.gitlab-ci.yml` solo define `deploy_to_sinbad2`, con `resource_group: sinbad2-production`. Antes del cambio escuchaba la rama `master`; la regla actual exige simultáneamente `CI_PIPELINE_SOURCE == "push"` y `CI_COMMIT_BRANCH == "main"`, seguida de `when: never` para los demás casos.
- El Runner necesita `git`, `ssh`, `ssh-agent`, `ssh-add`, `ssh-keygen` y `tar`, además de una shell con `pipefail` compatible. No necesita Docker: el cliente Docker y Docker Compose se invocan en el host remoto por SSH. El job no declara imagen ni `tags`, así que depende del Runner que GitLab seleccione y de sus herramientas instaladas.
- `SSH_PRIVATE_KEY` y `SSH_KNOWN_HOSTS` admiten tanto variables File (su valor en el proceso es la ruta al archivo temporal) como texto. El job copia ambos a `~/.ssh`, limita permisos, valida la clave y carga la privada en `ssh-agent`. El servidor se verifica con `StrictHostKeyChecking=yes` y `GlobalKnownHostsFile=/dev/null`; no se confía en una clave obtenida dinámicamente durante el despliegue.
- La conexión usa `REMOTE_USER@SSH_HOST` y asume SSH por el puerto predeterminado. La clave no debe necesitar frase de paso porque `ssh-add` se ejecuta sin mecanismo para introducirla. La prueba local de `ssh-keygen -lf` sobre un `known_hosts` sintético pasó; no verificó el host real.
- El commit se transmite como `git archive` por la salida estándar de SSH. El Runner no usa `scp` ni `rsync`. El host remoto extrae el archivo en `~/deploy_real-jaen-analytics/releases/<commit>-<pipeline>-<job>`; la configuración privada queda fuera de esa carpeta. El archivo incluye solo lo versionado, incluido el SDK de adquisición vendorizado.

### Releases y despliegues sucesivos

- Cada pipeline deja una carpeta de release distinta y ejecuta Compose con esa carpeta como directorio del proyecto. Las imágenes se construyen en SINBAD²; tienen etiquetas locales fijas `:prod`, no etiquetas por release.
- La configuración de Compose conserva nombres estables para el proyecto, seis redes y los cuatro volúmenes. Los datos persisten entre releases porque esos volúmenes tienen nombres explícitos y están fuera de las carpetas de código.
- Antes de actualizar, el job comprueba el archivo `.env.prod`, el puerto y la dirección de escucha, la sintaxis de Compose y la existencia nominal de los cuatro volúmenes. Luego ejecuta `docker compose up -d --build --wait --wait-timeout 300` y exige estado `running` y `healthy` para cada servicio.
- No ejecuta `down`, `down -v`, restauraciones ni eliminación de volúmenes. No hay un enlace `current`, limpieza de releases, conservación de imágenes por SHA ni rollback automático. Una carpeta anterior queda disponible, pero no basta para restaurar la versión anterior porque las etiquetas de imagen `:prod` se reutilizan.

### Compose, configuración y datos

`compose.prod.yaml` define los cuatro servicios y publica únicamente Dashboard en `0.0.0.0:8120`. Las APIs deportivas, de adquisición y vídeo quedan en redes privadas. Sports Data y vídeo tienen red de salida solo donde se necesita; Acquisition requiere salida para SofaScore y Video Service para YouTube. Compose puede crear redes automáticamente, pero no configura el cortafuegos, proxy inverso, DNS, TLS ni la exposición del puerto en la red de la Universidad.

Las imágenes se construyen desde el checkout remoto; no se descargan imágenes propias publicadas en un registry. Los Dockerfiles fijan por digest las bases de Node 22.22.2, Python 3.14.4 y Deno 2.9.8. Dashboard instala con `npm ci` y el lockfile; Sports Data usa requisitos Python fijados; Acquisition verifica hash y revisión del SDK SofaScore vendorizado, instala Chromium/Playwright y valida dependencias; Video Service instala FFmpeg, Tesseract y requisitos Python con algunos rangos de versión. Los `.dockerignore` evitan enviar SQLite, `.env`, estados, vídeos originales, tests o `node_modules` a los contextos pertinentes; el catálogo operativo de vídeo también se excluye de la imagen. La build remota necesita red hacia los registros base, apt y los índices de paquetes. Los pins no hacen completamente reproducible el contenido de apt ni los requisitos de Video Service con rangos.

Las comprobaciones de salud actuales son:

| Servicio | Prueba | Cuándo la espera el despliegue |
|---|---|---|
| Dashboard | Healthcheck de la imagen: solicita `/api/v1/session` y `sports-data/health`; exige modo `real` y que el endpoint de sesión indique autenticación requerida | Compose `--wait`; luego el job comprueba estado y salud Docker |
| Sports Data | Healthcheck de la imagen: `GET http://127.0.0.1:8002/health` | Compose `--wait`; también es dependencia saludable de Dashboard |
| Acquisition | Compose consulta `GET http://127.0.0.1:8002/health` cada 10 s, con 20 s de arranque y 3 reintentos | Compose `--wait`; luego el job comprueba estado y salud Docker |
| Video Service | Compose consulta `GET http://127.0.0.1:8010/health` cada 10 s, con 20 s de arranque y 3 reintentos | Compose `--wait`; también es dependencia saludable de Dashboard |

El job espera hasta 300 segundos y luego repite la comprobación de `State.Status=running` y `Health.Status=healthy` para los cuatro contenedores. No hay un `curl` desde fuera del host, ni una comprobación del proxy/firewall/URL institucional.

Las rutas externas obligatorias de producción son:

| Ajuste de `.env.prod` | Uso | Qué debe haber en el servidor |
|---|---|---|
| `RJ_PROD_SECRET_DIR` | Archivos secretos montados en Dashboard, Sports Data y Acquisition | Directorio y archivos descritos abajo, legibles por los UID de contenedor correspondientes |
| `RJ_IMPORT_DIR` | Respaldo inicial y ficheros de importación, montados solo lectura en Sports Data | Directorio existente; el respaldo verificado debe quedar dentro para restaurarlo |
| `RJ_VIDEO_CATALOG_DIR` | Catálogo operativo montado solo lectura en Video Service | Directorio existente con el manifiesto JSON indicado; calibraciones referenciadas si las hay |

Los bind mounts usan `create_host_path: false`: Compose no crea esas rutas. `.env.prod.example` apunta a `/etc/real-jaen-analytics/secrets` y `/srv/real-jaen-analytics/...`. El job actual no crea esas rutas ni instala archivos en ellas. Cambiar `.env.prod` a ubicaciones persistentes bajo el home del usuario permite que una pipeline las cree con `mkdir -p`, siempre que el usuario sea dueño de esos directorios.

Los secretos que consume la aplicación son cinco archivos, no claves de entorno con el secreto en claro:

| Archivo | Consumidores | Restricción observada |
|---|---|---|
| `access-password` | Dashboard | Al menos 12 caracteres ASCII imprimibles |
| `update-password` | Dashboard | Al menos 9 caracteres; distinta de la contraseña de acceso |
| `session/session-secret` | Dashboard | Al menos 32 caracteres ASCII imprimibles |
| `control-token` | Dashboard y Sports Data | Al menos 32 bytes ASCII, sin espacios ni controles |
| `acquisition-token` | Sports Data y Acquisition | Al menos 32 bytes ASCII, sin espacios ni controles |

Cada contenedor monta solo los archivos que necesita y en modo solo lectura. Dashboard corre como usuario `node`; los otros tres servicios usan UID/GID `10001`. Al instalar los archivos se debe dar lectura a esos procesos sin hacerlos legibles por cuentas ajenas del host. Un `chmod 0600` propiedad del usuario SSH suele impedir que los contenedores los lean; hay que probar el esquema de permisos/ACL/grupos elegido. El acceso al Docker daemon también permite inspeccionar volúmenes y montar rutas del host, por lo que el usuario SSH con acceso a un daemon rootful tiene privilegios prácticamente equivalentes a root en ese servidor.

### Primer arranque y volúmenes

Los cuatro volúmenes persistentes son `RJ_PROD_SPORTS_STATE_VOLUME`, `RJ_PROD_ACQUISITION_PROFILES_VOLUME`, `RJ_PROD_ACQUISITION_JOBS_VOLUME` y `RJ_PROD_VIDEO_STATE_VOLUME`. El job actual exige que todos existan antes de ejecutar Compose, pero no verifica su contenido ni su integridad.

- **Sports Data:** la aplicación SQL falla si falta `/state/real-jaen.sqlite`; no crea una base vacía. La base inicial debe corresponder a la revisión del código. `restaurar-instalacion.sh` comprueba sumas, integridad, revisión y generación antes de instalarla.
- **Acquisition:** verifica un perfil administrado existente. El Compose de producción no inicializa un perfil vacío; el script de restauración crea uno nuevo explícitamente y también restaura los trabajos persistentes.
- **Video Service:** inicializa su SQLite y su subdirectorio de calibraciones en el volumen de estado. Sin embargo, Compose monta un catálogo externo sobre `/app/data`; el `videos.json` de ejemplo que incorpora la imagen queda oculto y el catálogo montado debe contener un manifiesto válido. Las calibraciones y asociaciones operativas externas no están en el respaldo conjunto.
- **Persistencia de desarrollo:** `compose.dev.yaml` sí incluye lógica de siembra controlada desde `.rja-private/seed` y creación de perfil cuando está vacío. Esa lógica no está en `compose.prod.yaml` y no es un mecanismo de bootstrap de producción.

El respaldo incluye la SQLite deportiva, su diario de actualizaciones, los trabajos de Acquisition y el estado/calibraciones de Video Service; no incluye secretos, perfil/cookies del navegador ni catálogo externo. Restaurar exige que los cuatro volúmenes no existan, fija permisos con contenedores que ejecutan como UID 0 y `CAP_CHOWN`, instala los datos y deja los servicios detenidos. No requiere que el usuario SSH use `sudo`, pero sí que el daemon Docker permita esas operaciones. Si la restauración falla después de crear algunos volúmenes, deja volúmenes parciales y una repetición segura se negará al encontrarlos; se necesita recuperación deliberada, nunca borrarlos automáticamente.

El script de respaldo detiene los cuatro servicios para producir una copia consistente y vuelve a arrancarlos con un `trap`. El `trap` se instala después del primer `docker compose stop`; si esa detención falla parcialmente, el script podría salir antes de instalarlo y dejar servicios detenidos.

## Problemas y gravedad

| Gravedad | Hallazgo | Efecto |
|---|---|---|
| Alta | El despliegue no inicializa datos ni crea configuración externa; el primer despliegue falla o queda parcialmente iniciado sin una restauración válida. | Impide una instalación nueva y exige preparar la configuración y la carga deportiva por separado. |
| Alta | El usuario SSH necesita acceso al Docker daemon remoto y capacidad de escribir en releases y datos externos. | Sin permiso al daemon no hay despliegue; si es rootful, ese permiso es casi equivalente a root. No se debe asumir `sudo`. |
| Alta | El `up` y las comprobaciones no forman una transacción; Compose puede recrear algunos servicios antes de fallar. | Un fallo de build deja el servicio anterior corriendo; un fallo durante el arranque/salud puede dejar una mezcla de contenedores viejos y nuevos. No hay rollback automático. Los volúmenes no se borran, pero no existe una recuperación de versión automática. |
| Alta | La pipeline actual solo comprueba que existan los cuatro volúmenes. | Un volumen vacío o incoherente pasa esa condición; Sports Data/Acquisition luego fallarán al iniciar. No se valida la base antes de reemplazar contenedores. |
| Media | `resource_group` evita ejecuciones simultáneas dentro del proyecto, pero no garantiza por defecto el orden de pipelines en espera. | Dos pushes cercanos pueden desplegar commits fuera de orden; GitLab usa `unordered` como modo predeterminado. |
| Media | `.env.prod` y las rutas `/etc` y `/srv` se administran manualmente hoy. | Una cuenta SSH sin privilegios no puede crear esas rutas en un host limpio. El archivo no se transfiere desde GitLab. |
| Media | Las etiquetas Docker `:prod` se sobrescriben y las carpetas de release no se depuran. | Se acumulan releases y no se conservan imágenes direccionables para rollback. |
| Media | La build ocurre en el host remoto y necesita acceso a registros de imágenes, apt, PyPI, npm y otras fuentes de paquetes. | Un firewall sin salida o recursos insuficientes bloquean despliegues. Chromium, OCR y el tmpfs de vídeo aumentan consumo de memoria/disco. |
| Media | El job no fija `tags` ni declara `environment: production`. | No se fuerza un Runner específico/protegido y no se pueden limitar cómodamente variables al entorno `production`. |
| Media | No se valida desde fuera el puerto 8120, el proxy o el certificado. | Los healthchecks prueban los procesos dentro de los contenedores, no que la URL pública/institucional funcione. |
| Baja | Versiones de dependencias de Video Service incluyen rangos y las instalaciones apt de ffmpeg/Tesseract no están fijadas por snapshot. | Una reconstrucción futura puede resolver paquetes distintos aunque las imágenes base estén ancladas por digest. |

## Requisitos previos reales

### Configuración de GitLab y Runner

- Proteger `main` y permitir cambios de despliegue solo mediante el proceso de revisión acordado. Configurar las variables sensibles como protegidas y limitar su ámbito al entorno de producción.
- Tener un Runner de despliegue confiable, protegido y preferiblemente dedicado al proyecto, con un tag de despliegue. La instancia debe permitir el job y sus variables protegidas. No hace falta que ese Runner tenga Docker si se conserva la compilación remota.
- En el entorno de trabajo del Runner deben existir `git`, una shell compatible con `set -o pipefail`, `ssh`, `ssh-agent`, `ssh-add`, `ssh-keygen` y `tar`. Si se usa un executor Docker/Kubernetes, esos comandos deben estar en la imagen efectiva del job; el `.gitlab-ci.yml` no instala paquetes.
- Permitir desde el Runner la conexión SSH a `SSH_HOST` y almacenar una clave pública de host obtenida/verificada por administración. No es posible validar esa ruta desde este entorno.

### Servidor SINBAD²

- Una cuenta SSH con escritura en `~/deploy_real-jaen-analytics`, shell funcional y acceso al Docker CLI/daemon, sin necesidad inherente de `sudo`. El host requiere Docker Engine y Docker Compose v2.17 o posterior por `build.additional_contexts`; también debe soportar `docker compose up --wait`.
- Herramientas remotas `tar` y `sed`, además de `mkdir`, `sh` y los comandos Docker. El daemon/builder debe admitir los contextos adicionales de BuildKit requeridos por Acquisition.
- El daemon debe poder crear contenedores, redes y volúmenes y construir imágenes. Con daemon rootful, conceder acceso Docker equivale prácticamente a dar root; debe aprobarlo el administrador. En modo rootless hay que comprobar el mapeo de UID/permisos y el soporte de bind mounts, seccomp y capacidades.
- Salida de red durante las builds y capacidad adecuada de disco, RAM y CPU. Acquisition instala Chromium; Video Service instala FFmpeg/Tesseract y reserva un tmpfs de hasta 4 GiB para descargas temporales.
- El puerto TCP 8120 disponible y la decisión institucional para publicarlo: firewall, proxy inverso, DNS y TLS si corresponden. No forman parte de estos archivos.
- Si se conservan las rutas de ejemplo `/etc` y `/srv`, administración debe crearlas y asignar permisos antes del despliegue. Para evitarlo, configurar rutas persistentes bajo el home SSH, accesibles a Docker y a los contenedores según los permisos descritos.
- Para la primera instalación: respaldo conjunto verificado que corresponda a la revisión del código, catálogo externo de vídeo que se quiera conservar, cinco secretos válidos y directorio de importaciones existente. Un respaldo de tamaño real no debería pasar como variable de entorno; conviene transportarlo una vez por un canal de datos/artefactos con acceso restringido.

## Propuesta de arquitectura simplificada

Mantener el Runner como orquestador SSH y hacer que la instalación de datos/configuración y el arranque sean pasos explícitos del lado remoto. Así el Runner no necesita Docker-in-Docker ni acceso al Docker de SINBAD². El repositorio se archiva una vez por commit y las compilaciones ocurren en el servidor que ejecutará las imágenes.

1. Declarar el job para `push` a `main`, añadir `environment: production` y un tag de Runner dedicado/protegido. Configurar el recurso GitLab para desplegar commits de manera ordenada o descartar commits obsoletos.
2. Guardar `.env.prod` en `Settings → CI/CD → Variables` como variable File (por ejemplo `ENV_PROD`). Instalarlo fuera de `releases`, con escritura temporal, permisos adecuados y renombrado atómico. No imprimir su contenido ni adjuntarlo como artefacto.
3. Guardar cada archivo secreto como variable File independiente. La pipeline crea directorios que sean propiedad del usuario bajo una ruta compartida persistente y los escribe sin mostrar el contenido. El modo/grupo/ACL final debe permitir lectura a `node` y UID `10001` solo en los contenedores que montan cada archivo.
4. Antes de `up`, validar `.env.prod`, las rutas externas, los permisos de lectura, el catálogo, los cuatro nombres de volumen y la base deportiva con las herramientas de solo lectura de la aplicación. Si falta el estado deportivo o un perfil válido, detenerse antes de cambiar contenedores.
5. Compilar con etiquetas por commit/release y ejecutar un despliegue con verificación de salud. Si la salud falla, volver a la release e imágenes anteriores sin tocar los volúmenes. Retener un número definido de releases e imágenes tras una publicación saludable; esa limpieza debe excluir siempre las cuatro bases de datos persistentes.
6. Mantener el respaldo/restauración fuera del camino de despliegues normales. No incluir `down -v`, inicialización vacía ni restauración sobre volúmenes existentes.

### Variables propuestas en GitLab

Configurar en el proyecto que realmente aloja esta pipeline, protegidas y limitadas al entorno `production` cuando GitLab lo permita:

| Clave sugerida | Tipo | Contenido/uso |
|---|---|---|
| `SSH_PRIVATE_KEY` | File | Clave privada exclusiva del deploy; sin frase de paso para el flujo SSH actual |
| `SSH_KNOWN_HOSTS` | File | Líneas `known_hosts` de SINBAD² verificadas fuera de la pipeline |
| `SSH_HOST` | Variable | Host o IP SSH alcanzable desde el Runner |
| `REMOTE_USER` | Variable | Cuenta de despliegue en SINBAD² |
| `ENV_PROD` | File | Contenido completo de `.env.prod`, sin contraseñas ni tokens de aplicación |
| `APP_ACCESS_PASSWORD` | File | Valor de `access-password` |
| `APP_UPDATE_PASSWORD` | File | Valor de `update-password` |
| `APP_SESSION_SECRET` | File | Valor de `session/session-secret` |
| `APP_CONTROL_TOKEN` | File | Copia para los montajes de Dashboard y Sports Data |
| `APP_ACQUISITION_TOKEN` | File | Copia para Sports Data y Acquisition |

Los nombres `SSH_PRIVATE_KEY` y `SSH_KNOWN_HOSTS` coinciden con el flujo ya existente, que acepta la ruta temporal que GitLab asigna a una variable File. Los valores de host y usuario no son secretos, pero conviene protegerlos junto con el job. En la pantalla, la opción File hace que la variable de entorno contenga una ruta al archivo temporal, no su contenido. Un respaldo SQLite grande debe estar en almacenamiento de artefactos con permisos controlados o copiarse una sola vez al host, no guardarse como variable File.

Referencias oficiales: [variables de tipo File y configuración de CI/CD](https://docs.gitlab.com/ci/variables/), [reglas de jobs y fuentes de pipeline](https://docs.gitlab.com/ci/jobs/job_rules/), [uso de claves SSH](https://docs.gitlab.com/ci/jobs/ssh_keys/), [Runner y executors](https://docs.gitlab.com/runner/executors/) y [orden de resource groups](https://docs.gitlab.com/ci/resource_groups/).

## Estrategia segura para el primer despliegue

1. Acordar con administración la cuenta SSH, permiso de Docker, ruta persistente y exposición de 8120. Si el usuario no debe tener acceso al Docker rootful, no se puede conservar este diseño sin un servicio/agent de despliegue con permisos delegados.
2. Cargar las variables protegidas File en GitLab y ajustar `.env.prod` para que las rutas de secretos, importaciones y catálogo estén dentro de una ruta escribible por la cuenta de despliegue, o pedir a administración que prepare y asigne las rutas actuales.
3. Suministrar una copia de respaldo consistente que corresponda al esquema/código y validar sus `SHA256SUMS`; aportar por separado el catálogo de vídeo. La pipeline puede crear las carpetas vacías que el usuario posee, pero no puede inventar los datos deportivos ni el catálogo real.
4. Inicializar exactamente los cuatro volúmenes nuevos con `restaurar-instalacion.sh` y su guarda de “solo volúmenes inexistentes”. El script no arranca servicios. No crear primero volúmenes vacíos en la pipeline: eso impediría una restauración segura posterior.
5. Validar estado y permisos y arrancar Compose. Confirmar las cuatro saludes y el acceso a la URL institucional. Si algún volumen ya existe, detener el bootstrap y requerir diagnóstico; nunca borrarlo ni reutilizarlo automáticamente.

Como evolución, la operación de bootstrap podría ser un job separado y manual que solo continúe cuando **los cuatro volúmenes estén ausentes** y encuentre el respaldo verificado esperado. El job ordinario de push a `main` solo actualiza código y exige volúmenes inicializados. No conviene aceptar una bandera que permita restaurar sobre una instalación activa.

## Estrategia para actualizaciones posteriores

- Mantener los nombres de proyecto, red y volúmenes estables. Crear configuración/secretos bajo `shared`, fuera de las carpetas de release; actualizar esos archivos de forma atómica y con permisos comprobables.
- Transferir el commit a una release nueva. Validar configuración, catálogo y base sin escrituras. Construir imágenes con etiquetas únicas por commit antes de sustituir servicios.
- Ejecutar `up -d --wait` y comprobar los cuatro estados. No ejecutar respaldos/restauraciones como parte del `push`.
- Ante error antes del arranque, dejar la release previa en servicio. Si falla el chequeo de salud tras recrear servicios, restaurar Compose e imágenes de la última release saludable sin modificar volúmenes; informar al job. No intentar “reparar” una base vacía ni borrar datos.
- Retener releases e imágenes anteriores para rollback y podar solo releases sin uso. Un cambio de esquema futuro requiere un paso de migración separado, respaldo previo y plan de reversión compatible; actualmente la aplicación no migra la base automáticamente durante `up`.

## Plan de implementación

1. Confirmar en la instancia GitLab la versión, el proyecto que recibe `main`, Runner asignado/protegido y si existe espejo GitHub → GitLab. La configuración de GitHub no dispara por sí sola un pipeline GitLab salvo que ese repositorio reciba el commit.
2. Administrar Runner protegido/tagged, branch protection, `environment: production`, ámbito de variables y `resource_group` con orden de despliegue apropiado.
3. Decidir rutas bajo `$HOME` o pedir preparación administrativa de `/etc` y `/srv`; validar usuarios/UID de contenedor, ACL/grupos y acceso al daemon sin asumir `sudo`.
4. Definir e instalar File variables para entorno y secretos; crear un procedimiento de rotación sin logs ni artifacts con credenciales.
5. Definir transporte seguro y verificación del respaldo/catálogo para bootstrap. Probar la restauración solo con volúmenes de ensayo vacíos, nunca contra SINBAD² ni datos reales en esta fase.
6. Implementar preflight de solo lectura, etiquetas inmutables, promoción, salud, rollback, retención de releases y límites de concurrencia/commits obsoletos.
7. Validar CI con GitLab CI Lint y un Runner de prueba compatible. Hacer el primer despliegue real solo cuando servidor, restauración y acceso institucional estén aprobados.

## Verificaciones realizadas y pendientes

### Pasadas localmente

- Revisé `.gitlab-ci.yml`, Compose de desarrollo/producción, ejemplos de entorno, guía de despliegue, scripts completos de respaldo/restauración, Dockerfiles, manifests de dependencias, README, referencias de secretos, datos, salud y persistencia.
- `docker compose version`: 5.6.1 en este entorno. `docker compose --env-file .env.prod.example -f compose.prod.yaml config -q`: pasó. La configuración equivalente de desarrollo también pasó. Esto valida el parseo/configuración local con ejemplos; no verifica que las rutas existan en SINBAD².
- `sh -n dashboard/scripts/respaldo-instalacion.sh dashboard/scripts/restaurar-instalacion.sh`: pasó. No ejecuté esos scripts.
- Prueba de `ssh-keygen -lf` con un `known_hosts` sintético: pasó.
- No ejecuté suites de pruebas de aplicación ni builds de imágenes; el cambio se limita al disparador CI y a esta auditoría.
- El árbol de trabajo estaba limpio en `main`, sincronizado con `origin/main`; no había `.env.prod` local. Se revisaron los remotos antes de trabajar y el cambio se limitará al remoto GitHub `origin`.

### No verificables sin GitLab o SINBAD²

- GitLab CI Lint en la instancia autohospedada, versión/configuración del GitLab, proyecto de despliegue efectivo, existencia de espejo desde GitHub, protección real de `main`, settings de las variables, Runner seleccionado, su shell y sus ejecutables.
- Llegada SSH desde el Runner, usuario/clave/host key reales, permisos remotos, disponibilidad de Docker/Compose compatible, conectividad de build, capacidad del host, nombres/contenidos/permisos de volúmenes, `.env.prod`, secretos, archivos de importación, catálogo, puerto y URL externa.
- Builds de imágenes, arranque de contenedores, restauración real, salud en host, rollback y disponibilidad institucional. No se hizo SSH, contacto con SINBAD², conexión a GitLab privado ni despliegue real.
