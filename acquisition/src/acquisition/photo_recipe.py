"""Recipe player-photos-local-v1: a prepared photo collection, mounted read-only, sealed into a photos-v1 bundle.

No browser, no profile session, no network, no FFmpeg and no DF11 megapack: the collection already contains the final
WebP files and the manifest exported by Real Jaén Player Photos (`rja-player-photo-collection` v1):

  <ACQ_COLLECTIONS_ROOT>/<ACQ_PHOTO_COLLECTION>/manifest.json + photos/<file>

The collection is chosen by configuration only (ACQ_PHOTO_COLLECTION, e.g. `df11/1`), never by the request. It must be
a relative path of safe segments that resolves inside the collections root.

Every declared file is checked (size, SHA-256, real WebP content and dimensions). A photo that fails is sent as
`rejected` with its reason (the result is then `partial`); a malformed manifest fails the whole job. Photos whose
SHA-256 is already in the inventory sent by Sports Data travel as `omitted_unchanged`, without bytes.

photo_producer_rules() is the fingerprint Sports Data must have approved in productores-fotos.json.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path, PurePosixPath
import re

from acquisition.photo_protocol import bundle, images

PHOTO_RECIPE = 'player-photos-local-v1'
COLLECTIONS_ROOT = '/collections'
COLLECTION_FORMAT = 'rja-player-photo-collection'
COLLECTION_FORMAT_VERSION = 1
MAX_MANIFEST = 16 * 1024 * 1024
SEGMENT = r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}'
SELECTOR = re.compile(rf'^{SEGMENT}(?:/{SEGMENT}){{0,3}}$')
TOP_FIELDS = {'format', 'format_version', 'collection', 'source', 'processing', 'photo_count', 'photos'}
PHOTO_FIELDS = {'sofascore_id', 'file', 'sha256', 'bytes', 'format', 'media_type', 'width', 'height', 'provenance'}
# Stable error codes reported to Sports Data through the job's error field.
ERRORS = ('collection_not_configured', 'collection_unavailable', 'collection_invalid', 'inventory_invalid',
          'source_mismatch', 'bundle_too_large')
# Files that make up this producer, relative to this module (image layout: /opt/app/acquisition and /opt/app/fotos).
PRODUCER_FILES = ('__init__.py', 'admin.py', 'photo_recipe.py', 'photo_job.py', 'photo_import.py',
                  'photo_protocol/__init__.py', 'photo_protocol/bundle.py', 'photo_protocol/images.py')


class PhotoRecipeError(Exception):
    def __init__(self, code: str, message: str):
        if code not in ERRORS:
            raise ValueError(code)
        super().__init__(message)
        self.code = code


def photo_producer_rules() -> str:
    sources = {name: hashlib.sha256((Path(__file__).parent / name).resolve().read_bytes()).hexdigest()
               for name in PRODUCER_FILES}
    return bundle.sha(bundle.encode(dict(sources=sources, contract=bundle.CONTRACT, recipe=PHOTO_RECIPE,
                                         policy=dict(collections_root=COLLECTIONS_ROOT, max_photo=bundle.MAX_PHOTO,
                                                     max_total=bundle.MAX_TOTAL))))


# ─── Collection ──────────────────────────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Photo:
    sofascore_id: str
    sha256: str | None
    data: bytes | None
    media_type: str | None
    width: int | None
    height: int | None
    provenance: dict
    reason: str | None  # set when the declared file cannot be used


@dataclass(frozen=True)
class Collection:
    path: Path
    manifest: dict
    manifest_sha256: str
    photos: tuple[Photo, ...]


def locate(root: Path | str, selector: str | None) -> Path:
    if not selector:
        raise PhotoRecipeError('collection_not_configured', 'No hay ninguna colección de fotografías configurada')
    if not SELECTOR.match(selector):
        raise PhotoRecipeError('collection_invalid', 'La colección configurada no es una ruta relativa segura')
    base = Path(root)
    if not base.is_dir():
        raise PhotoRecipeError('collection_unavailable', 'No está montado el directorio de colecciones')
    resolved_root = base.resolve()
    path = (base / selector).resolve()
    if path != resolved_root and resolved_root not in path.parents:
        raise PhotoRecipeError('collection_invalid', 'La colección configurada sale del directorio de colecciones')
    if not path.is_dir():
        raise PhotoRecipeError('collection_unavailable', 'La colección configurada no existe')
    return path


def _safe_file(text) -> bool:
    if not isinstance(text, str) or '\\' in text or ':' in text or '\x00' in text:
        return False
    parts = text.split('/')
    return (len(parts) == 2 and parts[0] == 'photos' and parts[1] not in ('', '.', '..')
            and not PurePosixPath(text).is_absolute())


def _invalid(message: str):
    return PhotoRecipeError('collection_invalid', message)


def read_collection(path: Path) -> Collection:
    """The collection with every declared photo either verified (bytes kept) or rejected with its reason."""
    path = Path(path)
    manifest_path = path / 'manifest.json'
    try:
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise PhotoRecipeError('collection_unavailable', 'La colección no tiene manifest.json')
        if manifest_path.stat().st_size > MAX_MANIFEST:
            raise _invalid('manifest.json supera el tamaño admitido')
        raw = manifest_path.read_bytes()
    except OSError:
        raise PhotoRecipeError('collection_unavailable', 'No se pudo leer manifest.json') from None
    try:
        m = bundle.parse(raw)
    except bundle.Rejected as error:
        raise _invalid(f'manifest.json no válido: {error}') from None
    if not isinstance(m, dict) or set(m) != TOP_FIELDS:
        raise _invalid('manifest.json no tiene los campos del formato')
    if (m['format'], m['format_version']) != (COLLECTION_FORMAT, COLLECTION_FORMAT_VERSION) \
            or type(m['format_version']) is not int:
        raise _invalid('Formato de colección no admitido')
    collection, source = m['collection'], m['source']
    if (not isinstance(collection, dict) or set(collection) != {'id', 'version'}
            or not all(isinstance(collection[k], str) and bundle.LABEL.match(collection[k]) for k in collection)):
        raise _invalid('collection no válido')
    if (not isinstance(source, dict) or set(source) != {'id', 'label'} or not isinstance(source['id'], str)
            or not bundle.LABEL.match(source['id'])
            or not (source['label'] is None or (isinstance(source['label'], str) and source['label'].strip()
                                                and len(source['label']) <= bundle.MAX_TEXT))):
        raise _invalid('source no válido')
    if not isinstance(m['processing'], dict) or len(bundle.encode(m['processing'])) > bundle.MAX_PROVENANCE:
        raise _invalid('processing no válido')
    if (not isinstance(m['photos'], list) or type(m['photo_count']) is not int
            or m['photo_count'] != len(m['photos']) or len(m['photos']) > bundle.MAX_ENTRIES):
        raise _invalid('photo_count no coincide con las fotografías')

    photos, ids, files = [], set(), set()
    root = path.resolve()
    for n, e in enumerate(m['photos']):
        if not isinstance(e, dict) or set(e) != PHOTO_FIELDS:
            raise _invalid(f'photos[{n}] no tiene los campos del formato')
        sid = e['sofascore_id']
        if not isinstance(sid, str) or not bundle.PLAYER_ID.match(sid):
            raise _invalid(f'photos[{n}]: identificador SofaScore no válido')
        if sid in ids:
            raise _invalid(f'identificador SofaScore repetido: {sid}')
        if not _safe_file(e['file']) or e['file'] in files:
            raise _invalid(f'{sid}: ruta no segura o repetida')
        ids.add(sid)
        files.add(e['file'])
        provenance = e['provenance']
        if not isinstance(provenance, dict) or len(bundle.encode(provenance)) > bundle.MAX_PROVENANCE:
            raise _invalid(f'{sid}: provenance no válido')
        photos.append(_check_photo(root, e, provenance))
    return Collection(path=path, manifest=m, manifest_sha256=bundle.sha(raw), photos=tuple(photos))


def _check_photo(root: Path, e: dict, provenance: dict) -> Photo:
    sid = e['sofascore_id']

    def rejected(reason):
        return Photo(sid, None, None, None, None, None, provenance, reason)
    media_type = e['media_type']
    if media_type not in images.FORMATS or e['format'] != images.FORMATS[media_type][0]:
        return rejected('formato de imagen no admitido')
    if (type(e['width']) is not int or type(e['height']) is not int
            or not 0 < e['width'] <= images.MAX_SIDE or not 0 < e['height'] <= images.MAX_SIDE):
        return rejected('dimensiones declaradas no válidas')
    if not isinstance(e['sha256'], str) or not bundle.SHA256.match(e['sha256']) \
            or type(e['bytes']) is not int or not 0 < e['bytes'] <= bundle.MAX_PHOTO:
        return rejected('hash o tamaño declarados no válidos')
    path = root / PurePosixPath(e['file'])
    try:
        if path.is_symlink() or not path.is_file() or path.resolve().parent != root / 'photos':
            return rejected('el archivo declarado no existe')
        if path.stat().st_size > bundle.MAX_PHOTO:
            return rejected('el archivo supera el tamaño admitido')
        data = path.read_bytes()
    except OSError:
        return rejected('no se pudo leer el archivo')
    if len(data) != e['bytes']:
        return rejected('el tamaño no coincide con el manifiesto')
    if hashlib.sha256(data).hexdigest() != e['sha256']:
        return rejected('el hash no coincide con el manifiesto')
    try:
        width, height = images.inspect(data, media_type)
    except images.ImageError as error:
        return rejected(f'imagen no válida: {error}')
    if (width, height) != (e['width'], e['height']):
        return rejected('las dimensiones no coinciden con el manifiesto')
    return Photo(sid, e['sha256'], data, media_type, width, height, provenance, None)


# ─── Bundle ──────────────────────────────────────────────────────────────────────────────────────────────────────

def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def build(collection: Collection, inventory: dict, spec: dict, *, started_at: str,
          clock=iso_now) -> tuple[bytes, dict, dict]:
    """(body, descriptor, summary) of the photos-v1 bundle for one job. Re-verified with the receiver's own verifier
    before it is returned, so the job never seals a bundle Sports Data would reject for its structure."""
    try:
        held = bundle.check_inventory(inventory)
    except bundle.Rejected as error:
        raise PhotoRecipeError('inventory_invalid', f'Inventario no válido: {error}') from None
    source = collection.manifest['source']
    if inventory['source_id'] != source['id']:
        raise PhotoRecipeError('source_mismatch', f"La colección es de la fuente {source['id']} y el inventario de "
                                                   f"{inventory['source_id']}")
    entries, blobs, issues, total = [], {}, [], 0
    counts = dict(included=0, omitted_unchanged=0, rejected=0)
    for photo in sorted(collection.photos, key=lambda p: int(p.sofascore_id)):
        if photo.reason is not None:
            entries.append(dict(sofascore_id=photo.sofascore_id, transfer='rejected', sha256=None, bytes=None,
                                media_type=None, width=None, height=None, file=None, provenance=photo.provenance,
                                reason=photo.reason))
            issues.append(dict(sofascore_id=photo.sofascore_id, reason=photo.reason))
            counts['rejected'] += 1
            continue
        unchanged = held.get(photo.sofascore_id) == photo.sha256
        name = bundle.file_name(photo.sha256, photo.media_type)
        entries.append(dict(sofascore_id=photo.sofascore_id,
                            transfer='omitted_unchanged' if unchanged else 'included',
                            sha256=photo.sha256, bytes=len(photo.data), media_type=photo.media_type,
                            width=photo.width, height=photo.height, file=None if unchanged else name,
                            provenance=photo.provenance, reason=None))
        if unchanged:
            counts['omitted_unchanged'] += 1
        else:
            counts['included'] += 1
            if name not in blobs:
                blobs[name] = photo.data
                total += len(photo.data)
    if total > bundle.MAX_TOTAL:
        raise PhotoRecipeError('bundle_too_large', 'Las fotografías nuevas superan el tamaño máximo de un paquete')
    rules = photo_producer_rules()
    m = collection.manifest
    manifest = dict(
        schema=bundle.SCHEMA, contract=bundle.CONTRACT, job_id=spec['job_id'], request_id=spec['request_id'],
        operation_id=spec['operation_id'], recipe_id=PHOTO_RECIPE, rules_sha256=rules,
        source=dict(id=source['id'], label=source['label']),
        collection=dict(id=m['collection']['id'], version=m['collection']['version'], format=m['format'],
                        format_version=m['format_version'], manifest_sha256=collection.manifest_sha256),
        processing=m['processing'], inventory_sha256=inventory['sha256'],
        started_at=started_at, finished_at=clock(),
        result=dict(status='partial' if issues else 'complete', issues=issues),
        entry_count=len(entries), entries=entries)
    body, descriptor = bundle.build(manifest, blobs)
    if descriptor['bytes'] > bundle.MAX_WIRE:
        raise PhotoRecipeError('bundle_too_large', 'El paquete supera el tamaño máximo de transferencia')
    self_approval = {rules: dict(rules_sha256=rules, contract=bundle.CONTRACT, recipe_id=PHOTO_RECIPE,
                                 productor='self-check', uso='self-check')}
    bundle.verify(body, descriptor, context=(spec['job_id'], spec['request_id']), approved=self_approval,
                  inventory=inventory)
    summary = dict(collection=dict(id=m['collection']['id'], version=m['collection']['version'],
                                   manifest_sha256=collection.manifest_sha256),
                   source_id=source['id'], photos=len(entries), counts=counts, bytes_included=total,
                   result=manifest['result']['status'])
    return body, descriptor, summary
