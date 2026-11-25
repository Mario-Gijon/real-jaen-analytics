"""HTTP boundary for player photos, separate from the sporting query and persistence modules."""
import hmac
import os
import sqlite3
import sys

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from fotos import bundle, producers, serving
from fotos.players import PlayersFromQuery
from fotos.store import ImportConflict, InventoryConflict, PhotoStore, StoreError
from queries import consultar

PREFIX = '/internal/v1/player-photos'
router = APIRouter()


def import_secret(control_mode):
    """Use the already-mounted Acquisition bearer secret; no separate credential is introduced in this phase."""
    path = os.environ.get('RJ_ACQUISITION_SECRET_FILE')
    if not path and control_mode:
        path = '/run/secrets/acquisition-token'
    if not path:
        return None
    from control import secret
    return secret(path)


def _error(status, message):
    return JSONResponse(content={'error': message}, status_code=status, headers={'Cache-Control': 'no-store'})


def _authorized(request):
    token = request.app.state.photo_import_token
    if token is None:
        return _error(404, 'Ruta no disponible')
    provided = request.headers.get('Authorization', '').encode('utf-8')
    if not hmac.compare_digest(provided, b'Bearer ' + token):
        return _error(401, 'unauthorized')
    return None


def _photo_response(request: Request, ident: str, version: str):
    if request.scope.get('query_string', b''):
        return _error(400, 'Esta versión no admite filtros de consulta.')
    if not serving.PLAYER_ID.fullmatch(ident):
        return _error(400, 'Identificador inválido')
    if not serving.VERSION.fullmatch(version):
        return _error(404, 'Fotografía no encontrada')

    player_path = f'/api/v1/players/{ident}'
    if (getattr(request.app.state, 'control_mode', False)
            or getattr(request.app.state, 'postgresql_coordinator', False)):
        try:
            generation = request.app.state.coordinator.capturar()
        except Exception as error:
            print(f'[control] capture player photo: {error}', file=sys.stderr, flush=True)
            return _error(503, 'Datos no disponibles')
        status, _ = generation.consultar(player_path)
    else:
        status, _ = consultar(request.app.state.data, player_path)
    if status != 200:
        return _error(404, 'Fotografía no encontrada')

    try:
        found = request.app.state.photo_reader.serve(int(ident), version)
    except serving.PhotoStoreFault as error:
        print(f'[fotos] {error}', file=sys.stderr, flush=True)
        return _error(500, 'La fotografía no está disponible por un error del almacén.')
    if found is None:
        return _error(404, 'Fotografía no encontrada')
    data, selected = found
    return Response(content=data, media_type=selected.media_type, headers={
        'Cache-Control': 'private, max-age=31536000, immutable', 'ETag': f'"{selected.sha256}"',
        'X-Content-Type-Options': 'nosniff'})


async def _read_body(request: Request):
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > bundle.MAX_WIRE:
            return None
        chunks.append(chunk)
    return b''.join(chunks)


@router.get(PREFIX + '/inventory')
def inventory(request: Request, source_id: str = ''):
    denied = _authorized(request)
    if denied is not None:
        return denied
    if not bundle.LABEL.fullmatch(source_id):
        return _error(400, 'Identificador de fuente inválido')
    try:
        value = request.app.state.photo_reader.inventory(source_id)
    except serving.PhotoStoreFault as error:
        print(f'[fotos] {error}', file=sys.stderr, flush=True)
        return _error(503, 'El catálogo de fotografías no está disponible.')
    return JSONResponse(content=value, headers={'Cache-Control': 'no-store'})


@router.post(PREFIX + '/import')
async def import_bundle(request: Request):
    denied = _authorized(request)
    if denied is not None:
        return denied
    if not request.headers.get('content-type', '').lower().startswith('application/json'):
        return _error(415, 'Se requiere application/json')
    raw = await _read_body(request)
    if raw is None:
        return _error(413, 'Paquete de fotografías demasiado grande')

    try:
        declared_text = request.headers.get('X-Photo-Bundle-Bytes', '')
        if not declared_text.isdigit() or len(declared_text) > 10:
            raise bundle.Rejected('Invalid descriptor')
        descriptor = {'bytes': int(declared_text), 'sha256': request.headers.get('X-Photo-Bundle-SHA256', '')}
        job_id = request.headers.get('X-Acquisition-Job-ID', '')
        request_id = request.headers.get('X-Acquisition-Request-ID', '')
        if descriptor['bytes'] != len(raw) or descriptor['sha256'] != bundle.sha(raw):
            raise bundle.Rejected('Bundle descriptor mismatch')
        envelope = bundle.parse(raw)
        manifest = envelope.get('manifest') if isinstance(envelope, dict) else None
        if (not isinstance(manifest, dict) or manifest.get('job_id') != job_id
                or manifest.get('request_id') != request_id):
            raise bundle.Rejected('Wrong job context')
        previous = request.app.state.photo_reader.imported_bundle(request_id)
        if previous is not None:
            if previous[0] != descriptor['sha256']:
                return _error(409, 'La solicitud ya se importó con otro paquete.')
            return JSONResponse(content=dict(previous[1], reused=True), headers={'Cache-Control': 'no-store'})
        source = manifest.get('source')
        if (not isinstance(source, dict) or not isinstance(source.get('id'), str)
                or not bundle.LABEL.fullmatch(source['id'])):
            raise bundle.Rejected('Invalid source')
        current_inventory = request.app.state.photo_reader.inventory(source['id'])
        verified = bundle.verify(raw, descriptor, context=(job_id, request_id),
                                 approved=producers.load(), inventory=current_inventory)
    except producers.RegistryError as error:
        print(f'[fotos] {error}', file=sys.stderr, flush=True)
        return _error(503, 'No se pudo verificar el registro de productores.')
    except serving.PhotoStoreFault as error:
        print(f'[fotos] {error}', file=sys.stderr, flush=True)
        return _error(503, 'El catálogo de fotografías no está disponible.')
    except (ValueError, TypeError, bundle.Rejected) as error:
        print(f'[fotos] paquete rechazado: {type(error).__name__}: {error}', file=sys.stderr, flush=True)
        return _error(422, 'Paquete de fotografías rechazado')

    if (getattr(request.app.state, 'control_mode', False)
            or getattr(request.app.state, 'postgresql_coordinator', False)):
        try:
            generation = request.app.state.coordinator.capturar()
        except Exception as error:
            print(f'[control] capture photo import: {error}', file=sys.stderr, flush=True)
            return _error(503, 'Datos no disponibles')
        consult = generation.consultar
    else:
        data = request.app.state.data
        consult = lambda path: consultar(data, path)

    try:
        photo_store = (request.app.state.photo_store if request.app.state.storage == 'postgresql'
                       else PhotoStore(request.app.state.photo_root))
        summary = photo_store.import_bundle(verified, PlayersFromQuery(consult))
    except ImportConflict:
        return _error(409, 'La solicitud ya se importó con otro paquete.')
    except InventoryConflict:
        return _error(409, 'El inventario cambió durante la importación; vuelve a generarla.')
    except (OSError, StoreError, sqlite3.Error) as error:
        print(f'[fotos] importación fallida: {type(error).__name__}', file=sys.stderr, flush=True)
        return _error(503, 'No se pudo guardar el paquete de fotografías.')
    except Exception as error:
        print(f'[fotos] importación fallida: {type(error).__name__}', file=sys.stderr, flush=True)
        return _error(503, 'No se pudo guardar el paquete de fotografías.')
    return JSONResponse(content=summary, headers={'Cache-Control': 'no-store'})


@router.get('/api/v1/players/{ident}/photo/{version}')
def player_photo(request: Request, ident: str, version: str):
    return _photo_response(request, ident, version)
