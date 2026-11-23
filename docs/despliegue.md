# Despliegue

El repositorio tiene dos configuraciones Compose en la raíz. Ambas levantan Dashboard, Sports Data, Acquisition y Video Service. Acquisition no inicia trabajos al arrancar; las consultas deportivas y los trabajos de vídeo requieren una acción manual.

Los archivos privados `.env.dev` y `.env.prod` se suministran aparte. Los ejemplos versionados no contienen contraseñas, tokens ni datos reales. El anfitrión Docker necesita acceso de red durante las compilaciones para obtener imágenes base y dependencias.

Acquisition construye su runtime de Chromium desde el Dockerfile. La revisión aprobada del SDK de SofaScore está vendorizada en `acquisition/vendor/sofascore_scraper.tar.gz`; el Dockerfile verifica su hash, revisión y manifiesto de fuentes. No hace falta descargar o etiquetar por separado una imagen de runtime.

## Desarrollo

Mario entrega a Álvaro un paquete privado que se extrae en la raíz del repositorio clonado. Incluye `.env.dev`, secretos de desarrollo, catálogo/calibraciones de vídeo y estados SQLite para reproducir la instalación. No incluye vídeos originales, perfiles del navegador ni cookies. Las rutas de desarrollo son relativas al repositorio.

```sh
docker compose --env-file .env.dev -f compose.dev.yaml up -d --build
docker compose --env-file .env.dev -f compose.dev.yaml ps
```

El Dashboard se publica en `http://localhost:33201`. Sports Data y Video Service copian sus semillas privadas solo si sus volúmenes están vacíos. Un volumen con datos no se restaura encima. Acquisition crea un perfil nuevo, sin sesiones del navegador. La lectura inicial de Sports Data puede tardar varios minutos; Compose espera a que finalice antes de iniciar Dashboard. Sports Data, Acquisition y Video Service no publican puertos al anfitrión.

Para detener o reanudar los contenedores sin eliminar datos:

```sh
docker compose --env-file .env.dev -f compose.dev.yaml stop
docker compose --env-file .env.dev -f compose.dev.yaml start
```

Las carpetas y claves del paquete están excluidas de Git. Los secretos de desarrollo no se comparten con producción. No se consulta SofaScore, descarga vídeo ni ejecuta OCR al iniciar.

## Producción en SINBAD²

### Qué hace el despliegue

`.gitlab-ci.yml` contiene dos jobs con `environment: production` y `resource_group: sinbad2-production`:

- `deploy_to_sinbad2` se ejecuta automáticamente solo en pipelines de tipo `push` a la rama `main`.
- `bootstrap_sinbad2` es manual y solo aparece en pipelines iniciadas desde la interfaz web para la rama `main`. Está separado del despliegue normal.

No se fijan Runner tags. El Runner que GitLab asigne debe tener `bash`, `git`, `ssh`, `ssh-keygen`, `tar`, `mktemp` e `install`. El job transfiere el código y los archivos privados por SSH; las compilaciones y Docker Compose se ejecutan en el host remoto. Los archivos secretos no se copian al repositorio, a las imágenes Docker ni a artifacts del job. `.env.prod.example` es un ejemplo público sin credenciales.

Cada ejecución crea una release de código identificada por commit, pipeline y job bajo `~/deploy_real-jaen-analytics/releases/`. La extracción inicial se prepara en un directorio temporal y se renombra al terminar. Configuración y datos persisten fuera de cada release:

```text
~/deploy_real-jaen-analytics/
├── releases/<commit>-<pipeline>-<job>/
└── shared/
    ├── config/versions/<release>/.env.prod
    ├── config/current -> versions/<release>
    ├── secrets/versions/<release>/
    ├── secrets/current -> versions/<release>
    ├── imports/initial-backup/       # respaldo verificado para el primer arranque
    ├── video-catalog/                # catálogo operativo, independiente del código
    ├── incoming/                     # staging privado temporal
    └── deploy-state/last-good        # última release saludable
```

El sistema crea estas carpetas bajo el usuario SSH. Las carpetas raíz y `shared` quedan en modo `0700`. `.env.prod` queda en `0600`; los archivos de secretos versionados en `0444`, dentro de directorios de acceso restringido. Se montan como solo lectura y cada contenedor recibe únicamente los secretos que necesita: Dashboard usa acceso, actualización, sesión y control; Sports Data usa control y adquisición; Acquisition usa adquisición; Video Service no recibe secretos. Dentro de los contenedores los procesos son `node` o UID/GID `10001`.

Las redes y los cuatro volúmenes mantienen los nombres del ejemplo `.env.prod.example`. El despliegue normal se detiene antes de compilar si falta algún volumen o si solo existe una parte. También verifica la base deportiva, los trabajos de Acquisition, la base SQLite de Video Service, los permisos de escritura de los procesos y la lectura de secretos/catálogo. Si Acquisition está detenido, el preflight obtiene el bloqueo del perfil y ejecuta `verify-profile`, incluida su comprobación completa. Si está levantado y saludable, no intenta abrir el mismo perfil: usa la salud del servicio y la verificación de integridad que hizo su propietario al arrancar, junto con las comprobaciones estructurales de solo lectura. El bloqueo exclusivo del perfil se conserva.

El preflight no modifica los servicios activos ni los enlaces `current`. Valida Compose usando las rutas inmutables de la configuración y secretos candidatos, construye imágenes cuando cambia el SHA y verifica los datos antes de promover esos enlaces. Si cambian `ENV_PROD` o los secretos aunque el SHA sea el mismo, recrea explícitamente los servicios que consumen los valores modificados para que los procesos los vuelvan a leer. Una repetición sin cambios valida la salud y actualiza el registro de pipeline sin volver a levantar servicios. Los archivos de secretos se montan desde la versión validada.

Cuando una actualización vaya a recrear Acquisition, el despliegue desconecta temporalmente Sports Data de la red interna que admite trabajos y crea una barrera de mantenimiento en el volumen de trabajos. Espera a que la cola esté vacía durante cinco comprobaciones consecutivas; cada trabajo ya aceptado puede terminar mientras Acquisition sigue levantado. No cancela trabajos. Si la cola no se vacía en 25 minutos o no puede comprobarse, cancela la actualización, retira su barrera y restablece la conexión de Sports Data. Después recrea y valida Acquisition antes de volver a admitir trabajos. El servicio también rechaza nuevas solicitudes mientras exista la barrera.

Un catálogo de vídeo ya existente se valida tal como está: el despliegue no cambia sus permisos ni su propietario. Solo si el directorio está vacío crea `videos.json`, con permisos de solo lectura. Si falta el manifiesto en un catálogo que ya contiene datos, se detiene sin modificarlo.

El `up` usa imágenes etiquetadas por SHA del commit. La etiqueta local `:prod` y `last-good` solo se actualizan después de superar las comprobaciones de salud. Si no se puede guardar `last-good`, el despliegue restaura los enlaces, las imágenes y los servicios anteriores usando los mismos volúmenes. Si esa recuperación también falla, mantiene `current` apuntando a la versión candidata y conserva cerrada la entrada de trabajos cuando corresponde, para no anunciar una configuración anterior mientras pudiera seguir activa la versión nueva. Una interrupción forzada puede dejar la red o la barrera cerradas; inspecciona servicios y el marcador `deployment-maintenance` antes de reabrirlas. Nunca elimina ni restaura volúmenes en una actualización normal.

El `resource_group` evita despliegues simultáneos. Como el orden de jobs que esperan por un resource group depende de la configuración de GitLab, el host compara el ID de pipeline con la última release saludable y omite una ejecución antigua antes de cambiar la configuración o los servicios.

### Variables en GitLab

Configúralas en el proyecto que recibe esta pipeline, en **Settings → CI/CD → Variables**. Para cada variable sensible, selecciona tipo **File**, marca **Protected** y limita el ámbito a **production**. Desactiva la expansión de referencias de variables. Usa visibilidad **Masked** o **Masked and hidden** cuando GitLab acepte el formato; las claves SSH multilínea pueden no cumplir las restricciones de enmascarado. El script no imprime valores de variables ni contenido de archivos.

| Clave | Tipo | Contenido esperado |
|---|---|---|
| `ENV_PROD` | File | Contenido completo de `.env.prod`, basado en `.env.prod.example`. Mantén exactamente los nombres del proyecto, redes y volúmenes y las cuatro rutas `${RJA_DEPLOY_ROOT:?Set by the deployment job}/shared/...`. No incluyas contraseñas ni tokens. |
| `SSH_PRIVATE_KEY` | File | Clave privada dedicada al despliegue, sin frase de paso, autorizada en `REMOTE_USER`. |
| `SSH_KNOWN_HOSTS` | File | Entrada `known_hosts` para `SSH_HOST`, obtenida y verificada por administración fuera de la pipeline. No uses `ssh-keyscan` durante el job. |
| `SSH_HOST` | Variable | Nombre DNS o IPv4 del host SSH; se usa el puerto 22. |
| `REMOTE_USER` | Variable | Cuenta de despliegue con escritura en su `$HOME` y acceso directo a Docker. |
| `APP_ACCESS_PASSWORD` | File | Contraseña de acceso, al menos 12 caracteres imprimibles ASCII. |
| `APP_UPDATE_PASSWORD` | File | Contraseña administrativa, al menos 9 caracteres imprimibles ASCII y distinta de la anterior. |
| `APP_SESSION_SECRET` | File | Secreto de sesión de al menos 32 caracteres imprimibles ASCII. |
| `APP_CONTROL_TOKEN` | File | Token interno de al menos 32 caracteres imprimibles ASCII. |
| `APP_ACQUISITION_TOKEN` | File | Token de Acquisition de al menos 32 caracteres imprimibles ASCII y distinto del token de control. |

Los cinco archivos `APP_*` deben contener una sola línea de ASCII imprimible, de hasta 256 caracteres, sin espacios ni controles; se permite un salto de línea final. GitLab entrega una variable File al proceso como la ruta de un archivo temporal. El script comprueba que esa ruta sea un archivo File no vacío, valida tamaños y contenido, instala los archivos bajo `shared/secrets/` y cambia el enlace `current` mediante renombrado atómico. Cuando un valor rota, Compose recrea los servicios que lo consumen incluso si el commit no ha cambiado. El job no usa esas credenciales como argumentos, variables de entorno de los contenedores ni contenido de build.

Pasos para añadir cada entrada:

1. Abre **Settings → CI/CD → Variables → Add variable**.
2. Escribe la clave exacta de la tabla y pega el contenido esperado en **Value**. En **Type**, selecciona **File** salvo para `SSH_HOST` y `REMOTE_USER`, que son variables normales.
3. Marca **Protect variable** después de confirmar que `main` está protegida. Selecciona **production** en **Environment scope**.
4. Desactiva **Expand variable reference** y guarda. Para entradas de una sola línea, activa **Masked** si GitLab acepta el formato.
5. Repite para las diez claves. No subas los archivos ni copies sus contenidos a `.gitlab-ci.yml`, artifacts o imágenes.

La pantalla y algunas opciones pueden variar según la versión del GitLab autohospedado. La semántica de File, el ámbito de entorno y la protección se describe en la [documentación de variables CI/CD de GitLab](https://docs.gitlab.com/ci/variables/) y la [guía de claves SSH para jobs](https://docs.gitlab.com/ci/jobs/ssh_keys/).

### Primer despliegue: bootstrap manual

El bootstrap crea automáticamente las rutas bajo el home SSH. Si el directorio del respaldo aún no existe, una primera ejecución manual de `bootstrap_sinbad2` crea la estructura y falla con el mensaje de respaldo ausente, antes de compilar o crear volúmenes. Administración puede entonces copiar un respaldo verificado a:

```text
~/deploy_real-jaen-analytics/shared/imports/initial-backup/
```

El directorio debe contener `real-jaen.sqlite`, `actualizaciones.tar`, `acquisition-jobs.tar`, `video-service-state.tar`, `manifest.json` y `SHA256SUMS`. `procedencia.tar` es opcional. El respaldo debe proceder de `dashboard/scripts/respaldo-instalacion.sh` y corresponder con los datos y el esquema que se van a desplegar. El catálogo operativo de vídeo se suministra aparte en `shared/video-catalog/`; no forma parte del respaldo conjunto. Si aún no hay catálogo, el sistema crea un manifiesto vacío de vídeo solo cuando ese directorio está completamente vacío.

Después de proteger `main` y configurar las diez variables, inicia en GitLab una pipeline **Run pipeline** para la rama `main` y ejecuta el job manual `bootstrap_sinbad2`. Si el respaldo aún no está, el job fallará de forma segura y dejará creadas las carpetas necesarias. Tras copiar el respaldo, inicia otra pipeline web en `main` y ejecuta nuevamente el job. Ese job:

1. Se niega si ya existe cualquiera de los cuatro volúmenes destino.
2. Comprueba la presencia y `SHA256SUMS` del respaldo antes de crear volúmenes.
3. Compila las imágenes del commit y ejecuta `restaurar-instalacion.sh` para crear y poblar los cuatro volúmenes nuevos.
4. Valida las bases, permisos, secretos, catálogo y salud antes de guardar `last-good`.

Si falta el respaldo o la suma no coincide, no crea volúmenes. Si la restauración falla después de crear alguno, no elimina ninguno: queda un estado que administración debe inspeccionar antes de cualquier reintento. No vuelvas a ejecutar el bootstrap. Si los cuatro volúmenes existen y su contenido es válido, una actualización normal puede continuar; si la restauración dejó solo algunos volúmenes o datos incompletos, administración debe reparar esa instalación de forma explícita. Nunca se restaura automáticamente sobre un volumen existente. La restauración usa un contenedor de Docker como UID 0 con `CAP_CHOWN` para asignar propiedad a UID `10001`; no requiere `sudo` en la sesión SSH, pero el daemon Docker debe permitir esa operación.

No hagas un despliegue normal a `main` hasta tener el respaldo en su ubicación. Si la instalación no está inicializada, ese job falla con un mensaje claro y no crea volúmenes vacíos. Una vez bootstrap haya terminado correctamente, los siguientes pushes a `main` usan el job automático `deploy_to_sinbad2`.

### Actualizaciones, errores y datos

Las actualizaciones normales nunca restauran datos ni ejecutan `docker compose down -v`. Antes de cambiar servicios, el job exige los cuatro volúmenes existentes y válidos. Un fallo en cualquiera de los preflights deja los contenedores activos como estaban. Un fallo de compilación también conserva los servicios activos. Si el `up`, la salud o el guardado de `last-good` falla y hay una release saludable previa, el job recupera el código, imágenes, secretos y configuración anteriores y vuelve a iniciar esa release; mantiene los volúmenes y no ejecuta rollback de base de datos.

Iniciar la versión anterior puede seguir siendo incompatible con una migración o cambio de esquema que ya haya ocurrido. Por eso el mensaje de recuperación indica expresamente que la compatibilidad de los datos depende de los cambios de esquema: no se promete una reversión segura de datos. Sin una release saludable previa, el job informa del fallo y no intenta borrar ni restaurar volúmenes; puede haber servicios parcialmente iniciados, que deben inspeccionarse antes de reintentar.

Las releases, versiones de configuración y secretos antiguos no se podan automáticamente. Vigila el almacenamiento del host y acuerda una limpieza manual que conserve al menos la release `last-good` y cualquier versión requerida para diagnóstico. Las copias de seguridad deben gestionarse separadas del flujo de despliegue; la pipeline no las descarga, publica ni genera.

### Requisitos de infraestructura que faltan por confirmar

- **GitLab y Runner:** el proyecto autohospedado debe recibir los commits publicados en GitHub (por ejemplo, mediante un espejo autorizado); `main` debe estar protegida y el Runner seleccionado debe tener las herramientas anteriores y poder acceder a las variables protegidas del entorno `production`. No se declaran tags porque se desconoce la configuración disponible.
- **SSH:** salida de red desde Runner al host por TCP 22, clave pública autorizada para la cuenta y entrada de host verificada en `SSH_KNOWN_HOSTS`.
- **Docker:** Docker Engine y Compose v2.17 o posterior en el host, permisos para el daemon y capacidad de ejecutar builds, redes, volúmenes y contenedores UID `0` con `CAP_CHOWN` durante el bootstrap. El acceso a un daemon rootful equivale prácticamente a privilegios root; debe aprobarlo administración. Un daemon rootless solo sirve si su mapeo de UID permite esos cambios de propiedad y los bind mounts requeridos. No se presupone `sudo`.
- **Red del host:** acceso durante la compilación a registros e índices de dependencias; el Dashboard publica el puerto TCP 8120. Firewall, DNS, proxy inverso y TLS institucional deben configurarse aparte.
- **Datos:** respaldo con checksum verificado y compatible; catálogo de vídeo operativo y sus permisos de lectura para UID `10001` si ya existe.

No puedo verificar desde este entorno la versión/configuración del GitLab privado, el proyecto efectivo, el espejo desde GitHub, la protección de `main`, las variables, el Runner, la conectividad SSH, los permisos Docker, el espacio disponible, los datos existentes ni la exposición de 8120. No se ha conectado con GitLab privado ni con SINBAD² y no se ha ejecutado ningún despliegue real. La validación final del YAML con la versión del GitLab autohospedado y CI Lint deberá hacerse cuando el cambio se publique allí.

## Operaciones de Docker en producción

Para inspeccionar la release saludable desde el servidor, usa la carpeta indicada por `release_id` en `shared/deploy-state/last-good`; `.env.prod` se encuentra en el enlace estable `shared/config/current/.env.prod`. Carga el root de despliegue y el SHA de la última release antes de invocar Compose:

```sh
export RJA_DEPLOY_ROOT="$HOME/deploy_real-jaen-analytics"
RJ_IMAGE_TAG=$(sed -n 's/^commit_sha=//p' "$RJA_DEPLOY_ROOT/shared/deploy-state/last-good")
export RJ_IMAGE_TAG
cd "$RJA_DEPLOY_ROOT/releases/$(sed -n 's/^release_id=//p' "$RJA_DEPLOY_ROOT/shared/deploy-state/last-good")"
docker compose --project-directory "$PWD" \
  --env-file "$RJA_DEPLOY_ROOT/shared/config/current/.env.prod" \
  -f compose.prod.yaml ps
docker compose --project-directory "$PWD" \
  --env-file "$RJA_DEPLOY_ROOT/shared/config/current/.env.prod" \
  -f compose.prod.yaml logs --tail=100 dashboard
```

El `RJ_IMAGE_TAG` usado en operación manual debe ser el `commit_sha` registrado en `shared/deploy-state/last-good`; no confíes en `:prod` para identificar una release concreta. No publiques los puertos internos de Sports Data, Acquisition o Video Service. Mantén fijos proyecto, redes y volúmenes y no ejecutes `docker compose down -v`.

## Respaldos y restauración excepcional

Desde la raíz del repositorio, crea cada respaldo en una ubicación nueva. Por defecto, los scripts usan `.env.prod` y `compose.prod.yaml`; para desarrollo indica ambos explícitamente.

```sh
ENV_FILE=.env.prod sh dashboard/scripts/respaldo-instalacion.sh /ruta/nueva/para/respaldo
COMPOSE_FILE=compose.dev.yaml ENV_FILE=.env.dev \
  sh dashboard/scripts/respaldo-instalacion.sh /ruta/nueva/para/respaldo-dev
```

El respaldo comprueba que no haya una actualización activa, detiene temporalmente los cuatro servicios para copiar una base consistente y luego los vuelve a arrancar. Incluye la SQLite deportiva, el diario de coordinación, los trabajos de Acquisition y el estado/calibraciones de Video Service. Genera `SHA256SUMS`; no incluye secretos, cookies, perfil del navegador ni catálogo externo de vídeos.

En SINBAD², ejecútalo desde una release para que Compose use el mismo SHA de imágenes que la instalación activa y entrega la ruta privada de configuración explícitamente:

```sh
export RJA_DEPLOY_ROOT="$HOME/deploy_real-jaen-analytics"
RJ_IMAGE_TAG=$(sed -n 's/^commit_sha=//p' "$RJA_DEPLOY_ROOT/shared/deploy-state/last-good")
export RJ_IMAGE_TAG
release_id=$(sed -n 's/^release_id=//p' "$RJA_DEPLOY_ROOT/shared/deploy-state/last-good")
cd "$RJA_DEPLOY_ROOT/releases/$release_id"
ENV_FILE="$RJA_DEPLOY_ROOT/shared/config/current/.env.prod" \
  sh dashboard/scripts/respaldo-instalacion.sh \
  "$RJA_DEPLOY_ROOT/shared/imports/backups/$(date +%Y%m%d-%H%M%S)"
```

La restauración es únicamente para volúmenes nuevos. Comprueba sumas, integridad y generación, restaura la SQLite y estados persistentes, inicializa un perfil nuevo y deja los servicios detenidos. Nunca la ejecutes sobre volúmenes existentes ni como parte de una actualización normal.
