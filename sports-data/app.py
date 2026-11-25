"""FastAPI application for sports-data queries and optional update control.

Run with ``uvicorn app:app``. By default this reads ``datos/``. Set
``SPORTS_DATA_STORAGE=sql`` and ``SPORTS_DATA_DB`` to read one SQLite generation;
the cooperative lock is kept until the ASGI app shuts down. Development can
temporarily set ``SPORTS_DATA_STORAGE=postgresql`` to serve a cached generation
refreshed from the shared PostgreSQL publication state on demand.
That mode also stores and serves player photos from the shared PostgreSQL database;
the JSON and SQLite modes retain their legacy local photo store.
The separate
read-only enrichment endpoint is registered only with
``SPORTS_DATA_ENRICHMENT_ENABLED=1``. Outside control mode it reads the explicitly
configured ``SPORTS_DATA_ENRICHMENT_DB``; control mode reads a retained snapshot
of the coordinator generation captured for each request.
"""
from contextlib import ExitStack, asynccontextmanager
import hmac
import json
import os
from pathlib import Path
import re
import sys
import uuid
from urllib.parse import urlsplit

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

import api as data_loader
from fotos import router as player_photos
from fotos import serving as photo_serving
from fotos.store import default_root as photo_root
from queries import consultar


ROOT = Path(__file__).resolve().parent
UPDATE_PREFIX = '/internal/v1/updates'
router = APIRouter()


class APIJSONResponse(JSONResponse):
    """JSON response format used by the sports-data REST contract."""

    media_type = 'application/json; charset=utf-8'


def _load_data(resources):
    storage = os.environ.get('SPORTS_DATA_STORAGE', 'json')
    scope = Path(os.environ.get('SPORTS_DATA_SCOPE', str(ROOT / 'ambito.json')))

    if storage == 'json':
        folder = Path(os.environ.get('SPORTS_DATA_DIR', str(ROOT / 'datos')))
        replacements = os.environ.get('SPORTS_DATA_REPLACEMENTS')
        return data_loader.cargar(folder, scope, Path(replacements) if replacements else None)

    if storage == 'sql':
        database = os.environ.get('SPORTS_DATA_DB')
        if not database:
            raise ValueError('SPORTS_DATA_DB es obligatorio con SPORTS_DATA_STORAGE=sql')

        from persistencia.bloqueo import bloquear
        from persistencia.lectura import BaseNoUtilizable, cargar_desde_base

        database_path = Path(database)
        if not database_path.is_file():
            raise BaseNoUtilizable(f'No existe la base {database_path} (el modo SQL nunca crea una)')
        resources.enter_context(bloquear(database_path))
        return cargar_desde_base(database_path, scope)

    raise ValueError(f'SPORTS_DATA_STORAGE debe ser json o sql, no {storage!r}')


def _load_postgresql(resources):
    """Start the shared generation coordinator for PostgreSQL query-only mode."""
    from persistencia.actualizacion import CoordinadorPostgreSQL
    from persistencia.postgresql import crear_motor_postgresql

    motor = crear_motor_postgresql()
    resources.callback(motor.dispose)
    scope = Path(os.environ.get('SPORTS_DATA_SCOPE', str(ROOT / 'ambito.json')))
    workdir = os.environ.get('SPORTS_DATA_WORKDIR')
    if not workdir:
        raise ValueError('SPORTS_DATA_WORKDIR es obligatorio con SPORTS_DATA_STORAGE=postgresql')
    coordinator = CoordinadorPostgreSQL(motor, scope, workdir)
    resources.enter_context(coordinator)
    return coordinator, motor


def _load_comments(database):
    from persistencia.comentarios import load_current_comments
    return load_current_comments(database)


def _control_enabled():
    value = os.environ.get('SPORTS_DATA_CONTROL', '0')
    if value not in ('0', '1'):
        raise ValueError('SPORTS_DATA_CONTROL debe ser 0 o 1')
    return value == '1'


def require_complete_restore(marker=None):
    """Refuse service startup while an administrator has an unfinished installation restore."""
    marker = Path(marker or os.environ.get('RJ_RESTORE_MARKER_FILE', '/imports/.real-jaen-restore-incomplete'))
    if marker.exists() or marker.is_symlink():
        raise RuntimeError(f'Restauración incompleta detectada ({marker}); no se inicia Sports Data.')


def _enrichment_enabled():
    value = os.environ.get('SPORTS_DATA_ENRICHMENT_ENABLED', '0')
    if value not in ('0', '1'):
        raise ValueError('SPORTS_DATA_ENRICHMENT_ENABLED debe ser 0 o 1')
    return value == '1'


def _load_control(resources):
    """Start one update coordinator for the explicitly selected SQLite or PostgreSQL backend."""
    from multiprocessing import parent_process

    if parent_process() is not None:
        raise ValueError('El modo control de sports-data no admite procesos worker')
    workers = (os.environ.get('SPORTS_DATA_WORKERS'), os.environ.get('WEB_CONCURRENCY'))
    if any(value is not None and value != '1' for value in workers):
        raise ValueError('El modo control de sports-data solo admite un worker')
    storage = os.environ.get('SPORTS_DATA_STORAGE', 'json')
    if storage not in ('sql', 'postgresql'):
        raise ValueError('El modo control requiere SPORTS_DATA_STORAGE=sql o postgresql')
    scope = Path(os.environ.get('SPORTS_DATA_SCOPE', str(ROOT / 'ambito.json')))

    from control import Actualizaciones, RECIPE, restriccion, secret
    from persistencia.actualizacion import Coordinador, CoordinadorPostgreSQL
    from persistencia.adquisicion import Cliente

    control_secret_file = os.environ.get('RJ_CONTROL_SECRET_FILE')
    if not control_secret_file:
        raise ValueError('El modo control necesita RJ_CONTROL_SECRET_FILE')
    token = secret(control_secret_file)
    mode = 'prueba' if os.environ.get('RJ_ACQUISITION_TEST') == '1' else 'real'
    client = Cliente(
        os.environ.get('RJ_ACQUISITION_URL', 'http://acquisition:8002'),
        os.environ.get('RJ_ACQUISITION_SECRET_FILE', '/run/secrets/acquisition-token'),
        modo=mode,
        restriccion=restriccion(os.environ),
    )
    if storage == 'postgresql':
        workdir = os.environ.get('SPORTS_DATA_WORKDIR')
        if not workdir:
            raise ValueError('SPORTS_DATA_WORKDIR es obligatorio en control PostgreSQL')
        from persistencia.postgresql import crear_motor_postgresql

        motor = crear_motor_postgresql()
        resources.callback(motor.dispose)
        coordinator = CoordinadorPostgreSQL(motor, scope, workdir)
    else:
        database = os.environ.get('SPORTS_DATA_DB')
        if not database:
            raise ValueError('SPORTS_DATA_DB es obligatorio en modo control SQLite')
        coordinator = (Coordinador(database, scope, enrichment_snapshots=True)
                       if _enrichment_enabled() else Coordinador(database, scope))
    resources.enter_context(coordinator)
    updates = Actualizaciones(coordinator, client, recipe=os.environ.get('RJ_UPDATE_RECIPE', RECIPE))
    # ExitStack closes the update worker before releasing the coordinator's base lock.
    resources.callback(updates.close)
    return coordinator, updates, token


def _data_response(request: Request):
    query = request.scope.get('query_string', b'')
    # raw_path preserves percent escapes, which matters for invalid IDs.
    raw_path = request.scope.get('raw_path')
    path = raw_path.decode('latin-1') if raw_path is not None else request.url.path
    if query:
        status, body = 400, {'error': 'Esta versión no admite filtros de consulta.'}
    elif (getattr(request.app.state, 'control_mode', False)
          or getattr(request.app.state, 'postgresql_coordinator', False)):
        try:
            generation = request.app.state.coordinator.capturar()
        except Exception as error:
            import sys
            print(f'[control] generation capture: {type(error).__name__}', file=sys.stderr, flush=True)
            status, body = 503, {'error': 'Datos no disponibles'}
        else:
            # Capture once for the full request. A later publication only affects the next request.
            status, body = generation.consultar(path)
    else:
        status, body = consultar(request.app.state.data, path)

    body = photo_serving.with_photo_urls(path, status, body, _photo_lookup(request))

    return APIJSONResponse(
        content=body,
        status_code=status,
        headers={'Cache-Control': 'no-store'},
    )


def _photo_lookup(request: Request):
    """Keep sporting responses usable if the separate photo catalog is unavailable."""
    def lookup(player_ids):
        try:
            return request.app.state.photo_reader.urls(player_ids)
        except photo_serving.PhotoStoreFault as error:
            print(f'[fotos] {error}', file=sys.stderr, flush=True)
            return {}
    return lookup


def _comments_response(request: Request, public_match_id: str):
    from persistencia.comentarios import match_comments_response

    query = request.scope.get('query_string', b'')
    if query:
        status, body = 400, {'error': 'Esta versión no admite filtros de consulta.'}
    elif (getattr(request.app.state, 'control_mode', False)
          or getattr(request.app.state, 'postgresql_coordinator', False)):
        try:
            generation = request.app.state.coordinator.capturar()
        except Exception as error:
            print(f'[control] capture comments: {type(error).__name__}', file=sys.stderr, flush=True)
            status, body = 503, {'error': 'Datos no disponibles'}
        else:
            # Match and comments come from one captured published generation.
            status, body = generation.consultar_comentarios(public_match_id)
    else:
        status, body = match_comments_response(request.app.state.data, request.app.state.comments, public_match_id)
    return APIJSONResponse(content=body, status_code=status, headers={'Cache-Control': 'no-store'})


updates_router = APIRouter()


def _updates_response(request: Request):
    from control import NotCancellable, Unavailable

    path = request.scope.get('raw_path')
    path = path.decode('latin-1') if path is not None else request.url.path
    route = urlsplit(path).path
    method = request.method
    prefix = UPDATE_PREFIX
    if method == 'POST' and not route.startswith(prefix):
        return APIJSONResponse(content={'error': 'Ruta inexistente'}, status_code=404,
                               headers={'Cache-Control': 'no-store'})

    provided = request.headers.get('Authorization', '').encode('utf-8')
    if not hmac.compare_digest(provided, b'Bearer ' + request.app.state.control_token):
        status, body = 401, {'error': 'unauthorized'}
    else:
        updates = request.app.state.updates
        try:
            if route == prefix and method == 'POST':
                item, reused = updates.solicitar(request.headers.get('Idempotency-Key', ''))
                status, body = (200 if reused else 202), {'job': updates.public(item), 'reused': reused}
            elif route == prefix + '/current' and method == 'GET':
                status, body = 200, updates.actual()
            else:
                match = re.fullmatch(prefix + r'/([0-9a-f-]{36})(/cancel)?', route)
                if not match or str(uuid.UUID(match[1])) != match[1]:
                    status, body = 404, {'error': 'not_found'}
                elif method == 'POST' and match[2]:
                    status, body = 202, updates.public(updates.cancelar(match[1]))
                elif method == 'GET' and not match[2]:
                    status, body = 200, updates.public(updates.get(match[1]))
                else:
                    status, body = 404, {'error': 'not_found'}
        except KeyError:
            status, body = 404, {'error': 'not_found'}
        except (ValueError, TypeError):
            status, body = 400, {'error': 'invalid_request'}
        except NotCancellable:
            status, body = 409, {'error': 'not_cancellable'}
        except Unavailable:
            status, body = 409, {'error': 'update_unavailable'}
        except Exception as e:
            print(f'[control] {type(e).__name__}', file=sys.stderr, flush=True)
            status, body = 503, {'error': 'unavailable'}
    return APIJSONResponse(content=body, status_code=status, headers={'Cache-Control': 'no-store'})


@updates_router.api_route('/internal/{path:path}', methods=['GET', 'POST'], include_in_schema=False)
def internal_updates(request: Request):
    return _updates_response(request)


@updates_router.post('/', include_in_schema=False)
@updates_router.post('/{path:path}', include_in_schema=False)
def unsupported_control_post(request: Request):
    return _updates_response(request)


@router.get('/', include_in_schema=False)
def root(request: Request):
    return _data_response(request)


@router.get('/health')
def health(request: Request):
    return _data_response(request)


@router.get('/api/v1/scope')
def scope(request: Request):
    return _data_response(request)


@router.get('/api/v1/teams/{ident}')
def team(request: Request, ident: str):
    return _data_response(request)


@router.get('/api/v1/teams/{ident}/matches')
def team_matches(request: Request, ident: str):
    return _data_response(request)


@router.get('/api/v1/teams/{ident}/players')
def team_players(request: Request, ident: str):
    return _data_response(request)


@router.get('/api/v1/teams/{ident}/performance')
def team_performance(request: Request, ident: str):
    return _data_response(request)


@router.get('/api/v1/teams/{ident}/player-stats')
def team_player_stats(request: Request, ident: str):
    return _data_response(request)


@router.get('/api/v1/players/{ident}')
def player(request: Request, ident: str):
    return _data_response(request)


@router.get('/api/v1/players/{ident}/matches')
def player_matches(request: Request, ident: str):
    return _data_response(request)


@router.get('/api/v1/matches/{ident}')
def match(request: Request, ident: str):
    return _data_response(request)


@router.get('/api/v1/matches/{ident}/comments')
def match_comments(request: Request, ident: str):
    return _comments_response(request, ident)


@router.get('/api/v1/matches/{ident}/{section}')
def match_section(request: Request, ident: str, section: str):
    return _data_response(request)


@router.get('/{path:path}', include_in_schema=False)
def fallback(request: Request, path: str):
    # Unknown paths and trailing slashes retain the REST error contract.
    return _data_response(request)


def create_app(data=None, *, control_mode=None) -> FastAPI:
    """Build an app, optionally with a preloaded dataset for isolated tests."""
    storage = os.environ.get('SPORTS_DATA_STORAGE', 'json')
    control_mode = _control_enabled() if control_mode is None else control_mode
    enrichment_enabled = _enrichment_enabled()
    if control_mode and data is not None:
        raise ValueError('El modo control necesita una generación del coordinador, no datos precargados')

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        require_complete_restore()
        with ExitStack() as resources:
            application.state.control_mode = control_mode
            application.state.storage = storage
            application.state.postgresql_engine = None
            application.state.postgresql_coordinator = False
            application.state.photo_root = None if storage == 'postgresql' else photo_root()
            if control_mode:
                (application.state.coordinator,
                 application.state.updates,
                 application.state.control_token) = _load_control(resources)
                if getattr(application.state.coordinator, 'es_postgresql', False):
                    application.state.postgresql_engine = application.state.coordinator.motor
                    application.state.postgresql_coordinator = True
            else:
                if data is not None:
                    application.state.data = data
                    application.state.comments = {}
                    if storage == 'postgresql':
                        from persistencia.postgresql import crear_motor_postgresql

                        application.state.postgresql_engine = crear_motor_postgresql()
                        resources.callback(application.state.postgresql_engine.dispose)
                elif storage == 'postgresql':
                    (application.state.coordinator,
                     application.state.postgresql_engine) = _load_postgresql(resources)
                    application.state.postgresql_coordinator = True
                else:
                    application.state.data = _load_data(resources)
                    database = os.environ.get('SPORTS_DATA_DB') if storage == 'sql' else None
                    application.state.comments = _load_comments(Path(database)) if database else {}
            if storage == 'postgresql':
                from fotos.postgresql import PostgreSQLPhotoReader, PostgreSQLPhotoStore

                if application.state.postgresql_engine is None:
                    raise RuntimeError('El modo PostgreSQL necesita el motor compartido para las fotografías')
                application.state.photo_store = PostgreSQLPhotoStore(application.state.postgresql_engine)
                application.state.photo_reader = PostgreSQLPhotoReader(
                    application.state.postgresql_engine, photo_serving.source_priority())
            else:
                application.state.photo_reader = photo_serving.PhotoReader(
                    application.state.photo_root, photo_serving.source_priority())
                application.state.photo_store = None
            application.state.photo_import_token = player_photos.import_secret(control_mode)
            yield

    application = FastAPI(title='Sports Data API', lifespan=lifespan)
    application.include_router(player_photos.router)
    if control_mode:
        application.include_router(updates_router)
    if enrichment_enabled:
        # In control mode the route captures the coordinator's current generation
        # snapshot; otherwise it uses the explicit isolated database path.
        from enrichment.router import router as enrichment_router

        enrichment_database = os.environ.get('SPORTS_DATA_ENRICHMENT_DB')
        application.state.enrichment_database_path = (
            Path(enrichment_database)
            if enrichment_database and not control_mode and storage != 'postgresql' else None
        )
        application.include_router(enrichment_router)
    application.include_router(router)
    # The old server accepts trailing slashes without redirecting; the fallback
    # route then lets ``consultar`` decide their exact result.
    application.router.redirect_slashes = False
    return application


app = create_app()
