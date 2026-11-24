"""Explicit, one-shot import of a prepared local photo collection to Sports Data."""
from contextlib import closing
import json
import os
from pathlib import Path
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid

from acquisition import photo_job, photo_recipe
from acquisition.photo_protocol import bundle

SPORTS_DATA_URL = 'http://sports-data:8002'
MAX_RESPONSE = 8 * 1024 * 1024


class PhotoImportError(RuntimeError):
    pass


def _secret(path: str | Path) -> bytes:
    try:
        value = Path(path).read_bytes().strip()
    except OSError:
        raise PhotoImportError('No se pudo leer el secreto interno de Acquisition.') from None
    if not 32 <= len(value) <= 256 or not value.isascii() or any(c <= 32 for c in value):
        raise PhotoImportError('El secreto interno de Acquisition no es válido.')
    return value


def _json_request(url: str, token: bytes, *, body: bytes | None = None, headers=None, opener=None):
    opener = urllib.request.urlopen if opener is None else opener
    request_headers = {'Authorization': 'Bearer ' + token.decode('ascii'), 'Accept': 'application/json'}
    request_headers.update(headers or {})
    if body is not None:
        request_headers['Content-Type'] = 'application/json'
    request = urllib.request.Request(url, data=body, headers=request_headers)
    try:
        with closing(opener(request, timeout=60)) as response:
            status = response.status
            raw = response.read(MAX_RESPONSE + 1)
    except urllib.error.HTTPError as error:
        raise PhotoImportError(f'Sports Data rechazó la solicitud (HTTP {error.code}).') from None
    except (OSError, urllib.error.URLError, TimeoutError):
        raise PhotoImportError('Sports Data no está disponible para importar las fotografías.') from None
    if len(raw) > MAX_RESPONSE:
        raise PhotoImportError('La respuesta de Sports Data supera el tamaño admitido.')
    if not 200 <= status < 300:
        raise PhotoImportError(f'Sports Data respondió con HTTP {status}.')
    try:
        value = bundle.parse(raw)
    except bundle.Rejected:
        raise PhotoImportError('Sports Data devolvió una respuesta JSON no válida.') from None
    if not isinstance(value, dict):
        raise PhotoImportError('Sports Data devolvió una respuesta JSON no válida.')
    return value


def import_collection(selector: str, *, confirm_usage_rights: bool, collections_root='/collections',
                      secret_file='/run/secrets/acquisition-token', sports_data_url=SPORTS_DATA_URL, opener=None):
    """Read a prepared collection, build and send one photos-v1 bundle; never starts a sporting acquisition job."""
    if not confirm_usage_rights:
        raise PhotoImportError('Confirma los permisos de uso con --confirm-usage-rights antes de importar.')

    try:
        collection_path = photo_recipe.locate(collections_root, selector)
        collection = photo_recipe.read_collection(collection_path)
    except photo_recipe.PhotoRecipeError as error:
        raise PhotoImportError(f'Colección no importable ({error.code}): {error}.') from None

    token = _secret(secret_file)
    source_id = collection.manifest['source']['id']
    inventory_url = sports_data_url.rstrip('/') + '/internal/v1/player-photos/inventory?' + \
        urllib.parse.urlencode({'source_id': source_id})
    inventory = _json_request(inventory_url, token, opener=opener)
    try:
        bundle.check_inventory(inventory)
    except bundle.Rejected:
        raise PhotoImportError('El inventario recibido de Sports Data no es válido.') from None
    if inventory.get('source_id') != source_id:
        raise PhotoImportError('El inventario pertenece a otra fuente de fotografías.')

    spec = dict(job_id=str(uuid.uuid4()), request_id=str(uuid.uuid4()), operation_id=str(uuid.uuid4()),
                recipe_id=photo_recipe.PHOTO_RECIPE)
    with tempfile.TemporaryDirectory(prefix='rja-photo-import-') as temporary:
        folder = Path(temporary)
        (folder / 'job.json').write_text(json.dumps(spec), encoding='utf-8')
        (folder / 'inventory.json').write_bytes(bundle.encode(inventory))
        result = photo_job.run(folder, {'ACQ_COLLECTIONS_ROOT': str(collections_root),
                                        'ACQ_PHOTO_COLLECTION': selector})
        try:
            report = bundle.parse((folder / 'report.json').read_bytes())
        except (OSError, bundle.Rejected):
            raise PhotoImportError('Acquisition no pudo preparar el paquete de fotografías.') from None
        if result != 0 or not isinstance(report, dict) or report.get('descriptor') is None:
            code = report.get('error_code') if isinstance(report, dict) else None
            raise PhotoImportError(f'La colección no se pudo preparar{f" ({code})" if code else ""}.')
        try:
            body = (folder / 'bundle.json').read_bytes()
            descriptor = report['descriptor']
        except (OSError, KeyError):
            raise PhotoImportError('Acquisition no pudo sellar el paquete de fotografías.') from None

    rules = photo_recipe.photo_producer_rules()
    approved = {rules: dict(rules_sha256=rules, contract=bundle.CONTRACT,
                            recipe_id=photo_recipe.PHOTO_RECIPE, productor='self-check', uso='self-check')}
    try:
        bundle.verify(body, descriptor, context=(spec['job_id'], spec['request_id']), approved=approved,
                      inventory=inventory)
    except bundle.Rejected:
        raise PhotoImportError('El paquete preparado por Acquisition no superó su propia verificación.') from None

    upload_headers = {
        'X-Photo-Bundle-Bytes': str(descriptor['bytes']),
        'X-Photo-Bundle-SHA256': descriptor['sha256'],
        'X-Acquisition-Job-ID': spec['job_id'],
        'X-Acquisition-Request-ID': spec['request_id'],
    }
    imported = _json_request(sports_data_url.rstrip('/') + '/internal/v1/player-photos/import', token,
                             body=body, headers=upload_headers, opener=opener)
    return dict(status='imported', request_id=spec['request_id'], producer_rules_sha256=rules,
                prepared=report.get('summary'), imported=imported)
