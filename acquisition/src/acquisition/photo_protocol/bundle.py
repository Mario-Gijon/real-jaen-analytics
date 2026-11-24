"""photos-v1: transfer bundle of player photos from Acquisition to Sports Data (approved in phase 4B).

This is not the collection manifest that Real Jaén Player Photos exports (`rja-player-photo-collection`). A bundle is
what one acquisition job delivers: the collection it read, the job context, one entry per photo with its transfer
status and, as files, only the bytes Sports Data does not already hold. See docs/contrato-fotos-v1.md.

Envelope, like the sporting bundles: one JSON body `{"manifest": {...}, "files": {"<sha256>.<ext>": "<base64>"}}`
with an outer descriptor `{bytes, sha256}` checked before anything is parsed. Entry transfer statuses:

  included           the bytes travel in `files` under `<sha256>.<ext>`
  omitted_unchanged  no bytes: the producer saw the same SHA-256 in the inventory Sports Data sent
  rejected           the producer could not use the source photo (`reason`); never replaces a stored photo

The producer's statuses are not trusted for the outcome: the store classifies every entry again against its index.
Standard library only; nothing here imports persistencia/.
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import re
import uuid

from . import images

SCHEMA = 1
CONTRACT = 'photos-v1'
# Recipe of Acquisition that produces photos-v1 from a prepared local collection (acquisition/photo_recipe.py).
RECIPES = frozenset({'player-photos-local-v1'})
COLLECTION_FORMATS = {'rja-player-photo-collection': frozenset({1})}

MAX_WIRE = 96 * 1024 * 1024      # same ceiling as the sporting bundles' wire size
MAX_TOTAL = 64 * 1024 * 1024     # decoded image bytes in one bundle
MAX_PHOTO = 4 * 1024 * 1024      # one image; the sporting MAX_FILE
MAX_ENTRIES = 20000
MAX_PROVENANCE = 4096            # canonical JSON bytes of one entry's provenance
MAX_TEXT = 300

LABEL = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')
PLAYER_ID = re.compile(r'^[1-9][0-9]{0,11}$')
SHA256 = re.compile(r'^[0-9a-f]{64}$')
TRANSFERS = ('included', 'omitted_unchanged', 'rejected')

MANIFEST_FIELDS = frozenset({'schema', 'contract', 'job_id', 'request_id', 'operation_id', 'recipe_id', 'rules_sha256',
                             'source', 'collection', 'processing', 'inventory_sha256', 'started_at', 'finished_at',
                             'result', 'entry_count', 'entries'})
ENTRY_FIELDS = frozenset({'sofascore_id', 'transfer', 'sha256', 'bytes', 'media_type', 'width', 'height', 'file',
                          'provenance', 'reason'})


class Rejected(ValueError):
    """The bundle as a whole is not acceptable. Nothing from it may be stored."""


def encode(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Rejected(f'Duplicate JSON key: {key}')
        result[key] = value
    return result


def parse(raw: bytes):
    def constant(value):
        raise Rejected(f'Non-finite JSON value: {value}')
    try:
        return json.loads(raw, object_pairs_hook=_unique, parse_constant=constant)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise Rejected('Malformed JSON') from None


def file_name(sha256: str, media_type: str) -> str:
    return f'{sha256}.{images.FORMATS[media_type][1]}'


# ─── Inventory: what Sports Data already holds, sent with the request ─────────────────────────────────────────────

def inventory(source_id: str, active: dict[int, str]) -> dict:
    """The active photos of one source, `{player_id: sha256}`, with a fingerprint the bundle must echo.
    The producer omits the bytes of a photo whose SHA-256 is already here."""
    photos = [{'sofascore_id': str(player), 'sha256': digest} for player, digest in sorted(active.items())]
    body = {'contract': CONTRACT + '-inventory', 'source_id': source_id, 'photos': photos}
    return dict(body, sha256=sha(encode(body)))


def check_inventory(value) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {'contract', 'source_id', 'photos', 'sha256'}:
        raise Rejected('Invalid inventory')
    body = {k: value[k] for k in ('contract', 'source_id', 'photos')}
    if value['contract'] != CONTRACT + '-inventory' or value['sha256'] != sha(encode(body)):
        raise Rejected('Inventory fingerprint mismatch')
    photos = {}
    for row in value['photos']:
        if (not isinstance(row, dict) or set(row) != {'sofascore_id', 'sha256'} or not _player_id(row['sofascore_id'])
                or not _sha256(row['sha256']) or row['sofascore_id'] in photos):
            raise Rejected('Invalid inventory entry')
        photos[row['sofascore_id']] = row['sha256']
    return photos


# ─── Building (tests now; the future producer can reuse it) ───────────────────────────────────────────────────────

def build(manifest: dict, blobs: dict[str, bytes]) -> tuple[bytes, dict]:
    """Body and descriptor of a bundle. `blobs` maps file name to bytes; only names used by included entries."""
    body = encode({'manifest': manifest,
                   'files': {name: base64.b64encode(data).decode('ascii') for name, data in sorted(blobs.items())}})
    return body, {'bytes': len(body), 'sha256': sha(body)}


# ─── Verification ────────────────────────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Entry:
    player_id: int
    transfer: str
    sha256: str | None
    media_type: str | None
    bytes: int | None
    width: int | None
    height: int | None
    provenance: dict
    reason: str | None


@dataclass(frozen=True)
class VerifiedBundle:
    sha256: str
    manifest: dict
    entries: tuple[Entry, ...]
    blobs: dict[str, bytes] = field(repr=False)  # sha256 -> bytes, already checked

    @property
    def request_id(self) -> str:
        return self.manifest['request_id']

    @property
    def source_id(self) -> str:
        return self.manifest['source']['id']


def _player_id(value) -> bool:
    return isinstance(value, str) and bool(PLAYER_ID.match(value))


def _sha256(value) -> bool:
    return isinstance(value, str) and bool(SHA256.match(value))


def _uuid(value) -> bool:
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _instant(value) -> datetime:
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise Rejected('Invalid instant') from None
    if moment.tzinfo is None:
        raise Rejected('Instant without time zone')
    return moment


def _text(value, *, optional=False) -> bool:
    return (optional and value is None) or (isinstance(value, str) and 0 < len(value) <= MAX_TEXT and value.strip() == value)


def _int(value, low=0, high=None) -> bool:
    return type(value) is int and value >= low and (high is None or value <= high)


def verify(body: bytes, descriptor: dict, *, context: tuple[str, str], approved: dict[str, dict],
           inventory: dict | None = None) -> VerifiedBundle:
    """Verify a whole bundle before anything is stored. `context` is (job_id, request_id) of the acquisition job the
    bundle was downloaded for; `approved` is the photo producer registry (fotos.producers.load()); `inventory`, if
    given, is the one sent with the request. Raises Rejected."""
    if (not isinstance(descriptor, dict) or set(descriptor) != {'bytes', 'sha256'} or not _int(descriptor['bytes'])
            or not _sha256(descriptor['sha256'])):
        raise Rejected('Invalid descriptor')
    if len(body) > MAX_WIRE or len(body) != descriptor['bytes'] or sha(body) != descriptor['sha256']:
        raise Rejected('Bundle descriptor mismatch')
    envelope = parse(body)
    if not isinstance(envelope, dict) or set(envelope) != {'manifest', 'files'}:
        raise Rejected('Unknown envelope')
    m, files = envelope['manifest'], envelope['files']
    if not isinstance(m, dict) or set(m) != MANIFEST_FIELDS or not isinstance(files, dict):
        raise Rejected('Invalid manifest structure')
    if type(m['schema']) is not int or m['schema'] != SCHEMA or m['contract'] != CONTRACT:
        raise Rejected('Incompatible contract')
    if not all(_uuid(m[k]) for k in ('job_id', 'request_id', 'operation_id')):
        raise Rejected('Invalid identifiers')
    if (m['job_id'], m['request_id']) != tuple(context):
        raise Rejected('Wrong job context')
    if m['recipe_id'] not in RECIPES:
        raise Rejected('Unknown recipe')
    # Producer approval: the fingerprint must be listed in the photo registry for this very contract and recipe.
    if not _sha256(m['rules_sha256']):
        raise Rejected('Missing producer rules')
    entry = approved.get(m['rules_sha256']) if isinstance(approved, dict) else None
    if entry is None:
        raise Rejected('Unapproved producer rules')
    if entry.get('contract') != CONTRACT or entry.get('recipe_id') != m['recipe_id']:
        raise Rejected('Producer approved for another contract or recipe')

    source, collection = m['source'], m['collection']
    if (not isinstance(source, dict) or set(source) != {'id', 'label'} or not isinstance(source['id'], str)
            or not LABEL.match(source['id']) or not _text(source['label'], optional=True)):
        raise Rejected('Invalid source')
    if (not isinstance(collection, dict) or set(collection) != {'id', 'version', 'format', 'format_version',
                                                                 'manifest_sha256'}
            or not all(isinstance(collection[k], str) and LABEL.match(collection[k]) for k in ('id', 'version'))
            or collection['format'] not in COLLECTION_FORMATS
            or type(collection['format_version']) is not int
            or collection['format_version'] not in COLLECTION_FORMATS[collection['format']]
            or not _sha256(collection['manifest_sha256'])):
        raise Rejected('Invalid collection')
    if not isinstance(m['processing'], dict) or len(encode(m['processing'])) > MAX_PROVENANCE:
        raise Rejected('Invalid processing')
    if _instant(m['finished_at']) < _instant(m['started_at']):
        raise Rejected('Invalid clock')

    expected_inventory = None
    if inventory is not None:
        expected_inventory = check_inventory(inventory)
        if inventory['source_id'] != source['id']:
            raise Rejected('Inventory of another source')
        if m['inventory_sha256'] != inventory['sha256']:
            raise Rejected('Inventory mismatch')
    elif not (m['inventory_sha256'] is None or _sha256(m['inventory_sha256'])):
        raise Rejected('Invalid inventory fingerprint')

    entries_raw = m['entries']
    if not isinstance(entries_raw, list) or len(entries_raw) > MAX_ENTRIES or m['entry_count'] != len(entries_raw) \
            or type(m['entry_count']) is not int:
        raise Rejected('Entry count mismatch')

    entries, seen, wanted = [], set(), {}
    for n, e in enumerate(entries_raw):
        if not isinstance(e, dict) or set(e) != ENTRY_FIELDS:
            raise Rejected(f'Invalid entry {n}')
        if not _player_id(e['sofascore_id']):
            raise Rejected(f'Invalid SofaScore id in entry {n}')
        if e['sofascore_id'] in seen:
            raise Rejected(f'Duplicate SofaScore id {e["sofascore_id"]}')
        seen.add(e['sofascore_id'])
        if e['transfer'] not in TRANSFERS:
            raise Rejected(f'Unknown transfer status for {e["sofascore_id"]}')
        if not isinstance(e['provenance'], dict) or len(encode(e['provenance'])) > MAX_PROVENANCE:
            raise Rejected(f'Invalid provenance for {e["sofascore_id"]}')
        if e['transfer'] == 'rejected':
            if (not _text(e['reason']) or any(e[k] is not None for k in ('sha256', 'bytes', 'media_type', 'width',
                                                                          'height', 'file'))):
                raise Rejected(f'Invalid rejected entry {e["sofascore_id"]}')
        else:
            if (e['reason'] is not None or not _sha256(e['sha256']) or e['media_type'] not in images.FORMATS
                    or not _int(e['bytes'], 1, MAX_PHOTO) or not _int(e['width'], 1, images.MAX_SIDE)
                    or not _int(e['height'], 1, images.MAX_SIDE)):
                raise Rejected(f'Invalid photo entry {e["sofascore_id"]}')
            if e['transfer'] == 'included':
                if e['file'] != file_name(e['sha256'], e['media_type']):
                    raise Rejected(f'Unsafe or unexpected file name for {e["sofascore_id"]}')
                previous = wanted.setdefault(e['file'], e)
                if (previous['bytes'], previous['media_type']) != (e['bytes'], e['media_type']):
                    raise Rejected(f'Inconsistent entries for {e["file"]}')
            else:  # omitted_unchanged
                if e['file'] is not None:
                    raise Rejected(f'Omitted entry with a file: {e["sofascore_id"]}')
                if expected_inventory is not None and expected_inventory.get(e['sofascore_id']) != e['sha256']:
                    raise Rejected(f'Omitted photo not in the inventory: {e["sofascore_id"]}')
        entries.append(Entry(player_id=int(e['sofascore_id']), transfer=e['transfer'], sha256=e['sha256'],
                             media_type=e['media_type'], bytes=e['bytes'], width=e['width'], height=e['height'],
                             provenance=e['provenance'], reason=e['reason']))

    result = m['result']
    if (not isinstance(result, dict) or set(result) != {'status', 'issues'} or result['status'] not in
            ('complete', 'partial') or not isinstance(result['issues'], list)):
        raise Rejected('Invalid result')
    rejected = sum(e.transfer == 'rejected' for e in entries)
    if (result['status'] == 'complete') != (rejected == 0 and not result['issues']):
        raise Rejected('A partial collection cannot be reported as complete')
    if len(encode(result['issues'])) > MAX_PROVENANCE * 4:
        raise Rejected('Too many issues')

    if set(files) != set(wanted):
        raise Rejected('Files do not match the included entries')
    blobs, total = {}, 0
    for name, entry in wanted.items():
        encoded = files[name]
        if not isinstance(encoded, str) or len(encoded) > (MAX_PHOTO * 4) // 3 + 4:
            raise Rejected(f'Invalid file {name}')
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise Rejected(f'Invalid base64 in {name}') from None
        total += len(data)
        if total > MAX_TOTAL:
            raise Rejected('Bundle exceeds the decoded size limit')
        if len(data) != entry['bytes'] or sha(data) != entry['sha256']:
            raise Rejected(f'Size or hash mismatch in {name}')
        try:
            dimensions = images.inspect(data, entry['media_type'])
        except images.ImageError as error:
            raise Rejected(f'Invalid image {name}: {error}') from None
        if dimensions != (entry['width'], entry['height']):
            raise Rejected(f'Declared dimensions differ in {name}')
        blobs[entry['sha256']] = data
    for e in entries:  # every entry that shares a blob declares the same dimensions as the real image
        if e.transfer == 'included' and (e.width, e.height) != images.inspect(blobs[e.sha256], e.media_type):
            raise Rejected(f'Declared dimensions differ for {e.player_id}')
    return VerifiedBundle(sha256=descriptor['sha256'], manifest=m, entries=tuple(entries), blobs=blobs)
