"""FastAPI transport for the internal acquisition service."""
from contextlib import asynccontextmanager
import hmac
import os
import re
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import Response
from starlette.requests import ClientDisconnect

from .profile import Profile
from .service import Conflict, Jobs, MAX_REQUEST, PREFIX, RECIPES, configuration, encoded, secret


def _response(status, value, *, raw=None, etag=None):
    body = raw if raw is not None else encoded(value)
    headers = {'ETag': f'"{etag}"'} if etag else None
    return Response(body, status_code=status, media_type='application/json', headers=headers)


def _target(request):
    """Return the original request target, including its query string, as the old handler matched it."""
    path = request.scope.get('raw_path') or request.url.path.encode('ascii')
    query = request.scope.get('query_string', b'')
    return (path + (b'?' + query if query else b'')).decode('latin-1')


async def _body(request, length):
    if not 0 < length <= MAX_REQUEST:
        raise ValueError()
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > length or size > MAX_REQUEST:
            raise ValueError()
        chunks.append(chunk)
    if size != length:
        raise ValueError()
    return b''.join(chunks)


def create_app(*, jobs=None, token=None):
    """Build the service app. `jobs` and `token` are only injected by offline contract tests."""
    injected = jobs is not None or token is not None
    if injected and (jobs is None or not isinstance(token, bytes)):
        raise ValueError('Jobs and a byte token must be injected together')

    @asynccontextmanager
    async def lifespan(app):
        if injected:
            app.state.jobs, app.state.token = jobs, token
            try:
                yield
            finally:
                jobs.close()
            return

        provider, extra = configuration(os.environ)
        internal_token = secret(os.environ['ACQ_SECRET_FILE'])
        # Keep the profile owner for the process lifetime and release Jobs before leaving its lock.
        with Profile(os.environ.get('ACQ_PROFILE_ROOT', '/profiles')) as owner:
            owner.verify()
            current_jobs = Jobs(os.environ.get('ACQ_JOBS_ROOT', '/acquisition-jobs'),
                                provider=provider, owner=owner, extra=extra)
            app.state.jobs, app.state.token = current_jobs, internal_token
            try:
                yield
            finally:
                current_jobs.close()

    app = FastAPI(title='Internal acquisition service', docs_url=None, redoc_url=None, openapi_url=None,
                  lifespan=lifespan)

    @app.api_route('/{path:path}', methods=['GET', 'POST'])
    async def dispatch(request: Request, path: str):
        target = _target(request)
        post = request.method == 'POST'
        try:
            if not post and target == '/health':
                return _response(200, {'status': 'ok'})

            provided = request.headers.get('authorization', '').encode('utf-8')
            if not hmac.compare_digest(provided, b'Bearer ' + request.app.state.token):
                return _response(401, {'error': 'unauthorized'})

            if post and target == PREFIX:
                from persistencia.bundle import parse
                length = int(request.headers.get('content-length', '0'))
                data = parse(await _body(request, length))
                if (not isinstance(data, dict) or set(data) != {'request_id', 'recipe_id', 'inventory'}
                        or not all(isinstance(data[k], str) for k in ('request_id', 'recipe_id'))
                        or not isinstance(data['inventory'], dict) or data['recipe_id'] not in RECIPES
                        or str(uuid.UUID(data['request_id'])) != data['request_id']):
                    raise ValueError()
                item, new = request.app.state.jobs.submit(data['request_id'], data['recipe_id'], data['inventory'])
                return _response(202 if new else 200, item)

            match = re.fullmatch(PREFIX + r'/([0-9a-f-]{36})(/cancel|/bundle|/report)?', target)
            if not match or str(uuid.UUID(match[1])) != match[1]:
                return _response(404, {'error': 'not_found'})
            ident, action = match.groups()
            service_jobs = request.app.state.jobs
            if post and action == '/cancel':
                return _response(200, service_jobs.cancel(ident))
            if not post and action == '/bundle':
                body, digest = service_jobs.bundle(ident)
                return _response(200, None, raw=body, etag=digest)
            if not post and action == '/report':
                return _response(200, service_jobs.report(ident))
            if not post and action is None:
                return _response(200, service_jobs.get(ident))
            return _response(404, {'error': 'not_found'})
        except Conflict:
            return _response(409, {'error': 'conflict'})
        except KeyError:
            return _response(404, {'error': 'not_found'})
        except (ValueError, TypeError, ClientDisconnect):
            return _response(400, {'error': 'invalid_request'})
        except Exception:
            return _response(503, {'error': 'unavailable'})

    return app


app = create_app()
