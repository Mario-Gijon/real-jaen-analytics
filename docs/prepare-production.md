# Preparar la primera instalación PostgreSQL en SINBAD²

Guía breve para administración. **No despliega aplicaciones ni sustituye GitLab CI/CD.** Para comandos, preflight y recuperación detallados, consulte [postgresql-self-hosted.md](postgresql-self-hosted.md) y [despliegue.md](despliegue.md).

## Acordar antes de empezar

- La raíz que usan los scripts remotos es exactamente `$HOME/deploy_real-jaen-analytics`. La cuenta de despliegue debe ser propietaria, tener acceso directo al daemon Docker y no necesitar `sudo`. No se presupone otra ruta ni un grupo privilegiado.
- Hace falta disponer del checkout exacto del commit candidato como `releases/bootstrap-<SHA>` para usar los Compose, inicializar el perfil y restaurar. La pipeline entrega código al servidor solo después de un push a `main`; acordar con Francisco el canal autorizado para preparar ese checkout antes del merge. Este paso solo prepara la infraestructura, no ejecuta una release de aplicaciones.
- `scripts/prepare-postgresql-app-secrets.sh` requiere que la cuenta ya tenga un grupo primario privado de un solo usuario. Administración debe provisionarlo y confirmar su GID; no lo adivine.
- El destino GitLab acordado es [despliegues-autorizados/real-jaen-analytics](https://serezade.ujaen.es:8030/despliegues-autorizados/real-jaen-analytics). Los alias GitLab configurados en el checkout local apuntan a `mgijon/real-jaen-analytics` y `despliegues-autorizados/app-real-jaen`; confirmar con Francisco cuál es el proyecto canónico antes de preparar el MR.

## Preparación administrada

1. Use PostgreSQL 18 y Docker Compose 2.24.4 o posterior. Prepare el árbol privado bajo la raíz acordada, `.env.prod` y `.env.postgresql.prod` como archivos `0600`, y `shared/config/current` como enlace a `shared/config/versions/bootstrap`. No cree `last-good`: debe faltar en la primera instalación.
2. Confirme que aún no existen los recursos con los nombres PROD. Prepare los cinco volúmenes: `rja-sinbad2-postgresql-data`, `rja-sinbad2-sports-operational-state`, `rja-sinbad2-video-operational-state`, `rja-sinbad2-acquisition-profiles` y `rja-sinbad2-acquisition-jobs`. La guía técnica contiene los nombres, etiquetas y comprobaciones de propiedad exactos.
3. Administración provisiona el grupo privado como grupo primario de la cuenta. Después genere credenciales PostgreSQL nuevas con `scripts/create-postgresql-secrets.sh "$RJA_DEPLOY_ROOT/shared/postgresql/secrets"` y ejecute `scripts/prepare-postgresql-app-secrets.sh "$RJA_DEPLOY_ROOT/shared/postgresql/secrets"`. Ponga el GID que imprime en `.env.postgresql.prod`; solo las dos URLs runtime quedan en `0640`. Mantenga las credenciales admin/owner y URLs de migración en `0600`.
4. Genere cinco secretos de aplicación nuevos en el servidor. Los mismos valores se cargarán más tarde en GitLab; no se guardan en el repositorio ni en el backup. Mantenga `shared/secrets/current` como enlace administrado a `versions/`.
5. Construya las imágenes de bootstrap desde el checkout acordado, cree los cuatro volúmenes operativos vacíos con UID:GID `10001:10001` e inicie **solo PostgreSQL** con los tres Compose de producción. No inicie servicios de aplicación.
6. Verifique `SHA256SUMS` del backup v3, cópielo a una ruta privada fuera de `shared/imports` y restaure con el script oficial desde el checkout bootstrap:

   ```sh
   export RJA_DEPLOY_ROOT="$HOME/deploy_real-jaen-analytics"
   export RJ_IMAGE_TAG=606fb03ca74ae45af09c77fb1ff0149ba5548a42
   cd "$RJA_DEPLOY_ROOT/releases/bootstrap-$RJ_IMAGE_TAG"
   export ENV_FILES="$RJA_DEPLOY_ROOT/shared/config/current/.env.prod:$RJA_DEPLOY_ROOT/shared/config/current/.env.postgresql.prod"
   export COMPOSE_FILES="compose.prod.yaml:compose.postgresql.yaml:compose.postgresql.prod.yaml"
   scripts/restore-postgresql.sh "/ruta/privada/backup-v3"
   ```

   Si falla, conserve el marcador de restauración y los volúmenes parciales; no los limpie ni los reutilice. Inicialice y verifique un perfil Chromium nuevo y ejecute las comprobaciones previas al merge indicadas en la guía completa. No haga adquisiciones ni scraping.

Se usan los scripts existentes en lugar de un wrapper: la preparación depende de decisiones administrativas explícitas (grupo/GID, fuente de checkout, destinos vacíos y sincronización de secretos con GitLab). Un orquestador duplicaría esas comprobaciones y podría ocultar una puerta de seguridad.

## GitLab y despliegue de aplicaciones

Antes del MR, configure como variables **File**, protegidas, con ámbito `production` y expansión desactivada: `ENV_PROD`, `ENV_POSTGRESQL_PROD`, `APP_ACCESS_PASSWORD`, `APP_UPDATE_PASSWORD`, `APP_SESSION_SECRET`, `APP_CONTROL_TOKEN` y `APP_ACQUISITION_TOKEN`. Configure además `SSH_HOST`, `REMOTE_USER`, `SSH_PRIVATE_KEY` y `SSH_KNOWN_HOSTS`, usando una host key comprobada fuera de banda. `RJ_APP_SECRET_GID` debe tener ya el valor numérico confirmado; el marcador del paquete no sirve para desplegar.

El Dashboard escucha solo en `127.0.0.1:8090`. Apache publica HTTPS en `https://sinbad2.ujaen.es/real-jaen`, conserva `/real-jaen` (`basePath`) y reenvía al puerto local 8090.

Los desarrolladores preparan `feat/integrate-player-events`; administración revisa y aprueba su Merge Request hacia `main`. `.gitlab-ci.yml` ejecuta `scripts/deploy-sinbad2.sh deploy` únicamente al producirse un push a `main`; ese script transfiere el commit y llama a `scripts/deploy-sinbad2-remote.sh`. **Todos los despliegues de aplicaciones se hacen por ese CI/CD.** No ejecutar manualmente los scripts de despliegue ni fusionar antes de completar y verificar la preparación. El job conserva los cinco volúmenes y no inicia ni restaura PostgreSQL.

## PostgreSQL DEV futuro

Solicitar por separado a administración una instancia DEV independiente de PROD, con recursos y credenciales propios, conectividad privada expresamente autorizada desde los ordenadores de desarrollo y TLS verificado. No se presupone que esta instancia esté aprobada o preparada.
