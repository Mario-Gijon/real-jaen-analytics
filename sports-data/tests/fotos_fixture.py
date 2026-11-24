"""Synthetic photos-v1 bundles for the photo tests: valid WebP headers, controlled ids, no network or Acquisition."""
import copy
import uuid

from fotos import bundle

KNOWN = (3007, 82474, 83687, 88859)
ENCODER = 'df11-opaque-crop-v1 + libwebp q90 m6 picture yuva420p'
# A test-only producer approval: never written to the real productores-fotos.json.
RULES = bundle.sha(b'test photo producer')
APPROVED = {RULES: dict(rules_sha256=RULES, contract='photos-v1', recipe_id='player-photos-local-v1',
                        productor='prueba', uso='pruebas')}


def webp(width=215, height=268, payload=b''):
    """A simple (VP8) WebP whose container and key-frame header are valid; the image data are arbitrary."""
    frame = b'\x10\x95\x00' + b'\x9d\x01\x2a' + width.to_bytes(2, 'little') + height.to_bytes(2, 'little') + payload
    if len(frame) % 2:
        frame += b'\x00'
    chunk = b'VP8 ' + len(frame).to_bytes(4, 'little') + frame
    return b'RIFF' + (4 + len(chunk)).to_bytes(4, 'little') + b'WEBP' + chunk


def photo(player_id, data=None, *, transfer='included', reason=None, provenance=None):
    """One entry spec: (entry dict, bytes or None)."""
    if transfer == 'rejected':
        return dict(sofascore_id=str(player_id), transfer='rejected', sha256=None, bytes=None, media_type=None,
                    width=None, height=None, file=None, provenance=provenance or {}, reason=reason or 'PNG no válido'), None
    data = data if data is not None else webp(payload=f'player-{player_id}'.encode())
    digest = bundle.sha(data)
    width, height = int.from_bytes(data[26:28], 'little') & 0x3FFF, int.from_bytes(data[28:30], 'little') & 0x3FFF
    entry = dict(sofascore_id=str(player_id), transfer=transfer, sha256=digest, bytes=len(data),
                 media_type='image/webp', width=width, height=height,
                 file=bundle.file_name(digest, 'image/webp') if transfer == 'included' else None,
                 provenance=provenance if provenance is not None else
                 {'fm_id': str(67000000 + player_id), 'source_file': f'{67000000 + player_id}.png',
                  'processing': ENCODER},
                 reason=None)
    return entry, (data if transfer == 'included' else None)


class Bundle:
    """A bundle under construction. `manifest` and `files` can be tampered with before `body()`."""

    def __init__(self, specs, *, source='df11', version='1', inventory=None, request_id=None, status=None):
        self.job_id, self.request_id = str(uuid.uuid4()), request_id or str(uuid.uuid4())
        entries = [entry for entry, _ in specs]
        self.files = {bundle.file_name(e['sha256'], e['media_type']): data for e, data in specs if data is not None}
        rejected = any(e['transfer'] == 'rejected' for e in entries)
        self.manifest = dict(
            schema=1, contract='photos-v1', job_id=self.job_id, request_id=self.request_id,
            operation_id=str(uuid.uuid4()), recipe_id='player-photos-local-v1', rules_sha256=RULES,
            source=dict(id=source, label=None),
            collection=dict(id=source, version=version, format='rja-player-photo-collection', format_version=1,
                            manifest_sha256=bundle.sha(f'{source}-{version}'.encode())),
            processing=dict(tool='real-jaen-player-photos', processing_ids=[ENCODER]),
            inventory_sha256=inventory['sha256'] if inventory else None,
            started_at='2026-11-18T10:00:00+00:00', finished_at='2026-11-18T10:00:05+00:00',
            result=dict(status=status or ('partial' if rejected else 'complete'),
                        issues=[{'sofascore_id': e['sofascore_id'], 'reason': e['reason']}
                                for e in entries if e['transfer'] == 'rejected']),
            entry_count=len(entries), entries=entries)
        self.inventory = inventory

    def body(self):
        return bundle.build(self.manifest, self.files)

    def verify(self, **kwargs):
        body, descriptor = self.body()
        kwargs.setdefault('inventory', self.inventory)
        kwargs.setdefault('approved', APPROVED)
        return bundle.verify(body, descriptor, context=(self.job_id, self.request_id), **kwargs)

    def copy(self):
        return copy.deepcopy(self)


class FakeAcquisition:
    """The acquisition service as the photo updater sees it through Cliente: durable request keys (same key with
    another inventory is a 409), job states, sealed bundles built from the inventory it received.

    `collection` maps SofaScore id (int) to bytes, or to None for a photo the producer rejects. `mode` changes the
    next job: ok, not_configured, unavailable, interrupted, failed, download_error, tampered, wrong_inventory."""

    def __init__(self, collection, *, source='df11'):
        self.collection, self.source, self.mode = dict(collection), source, 'ok'
        self.jobs, self.by_request, self.posts, self.downloads = {}, {}, [], []
        self.during_wait = None  # callable run while "acquiring", to simulate concurrent changes or a crash

    def _json(self, method, suffix='', data=None, timeout=10):
        if method != 'POST' or suffix:
            raise AssertionError('only the job creation POST is used')
        self.posts.append(data['request_id'])
        if self.mode == 'unavailable':
            raise RuntimeError('Adquisicion no disponible')
        bundle.check_inventory(data['inventory'])
        known = self.by_request.get(data['request_id'])
        if known is not None:
            if self.jobs[known]['inventory_sha256'] != data['inventory']['sha256']:
                raise RuntimeError('Respuesta interna HTTP 409')
            return dict(self.jobs[known]['state'])
        ident = str(uuid.uuid4())
        self.by_request[data['request_id']] = ident
        self.jobs[ident] = dict(inventory_sha256=data['inventory']['sha256'], mode=self.mode,
                                state=dict(id=ident, state='running', error=None, bundle=None))
        self._seal(ident, data)
        return dict(id=ident, state='queued', error=None, bundle=None)

    def _seal(self, ident, data):
        job = self.jobs[ident]
        mode = job['mode']
        if mode in ('not_configured', 'failed', 'interrupted'):
            job['state'] = dict(id=ident, state='interrupted' if mode == 'interrupted' else 'failed', bundle=None,
                                error={'not_configured': 'collection_not_configured', 'failed': 'collection_unavailable',
                                       'interrupted': 'process_interrupted'}[mode])
            return
        held = bundle.check_inventory(data['inventory'])
        specs = []
        for player, payload in sorted(self.collection.items()):
            if payload is None:
                specs.append(photo(player, transfer='rejected', reason='el hash no coincide con el manifiesto'))
            elif held.get(str(player)) == bundle.sha(payload):
                specs.append(photo(player, payload, transfer='omitted_unchanged'))
            else:
                specs.append(photo(player, payload))
        b = Bundle(specs, source=self.source, inventory=data['inventory'], request_id=data['request_id'])
        b.job_id = b.manifest['job_id'] = ident
        b.manifest['operation_id'] = data['operation_id']
        if mode == 'wrong_inventory':
            b.manifest['inventory_sha256'] = '0' * 64
        body, descriptor = b.body()
        if mode == 'tampered':
            body = body.replace(b'"df11"', b'"dfXX"', 1)
        job['body'], job['bundle_files'] = body, len(b.files)
        job['state'] = dict(id=ident, state='completed', error=None, bundle=descriptor)

    def esperar(self, ident, timeout=600, avance=None):
        if self.during_wait is not None:
            hook, self.during_wait = self.during_wait, None
            hook()
        return dict(self.jobs[ident]['state'])

    def descargar(self, ident, descriptor):
        self.downloads.append(ident)
        if self.jobs[ident]['mode'] == 'download_error':
            self.jobs[ident]['mode'] = 'ok'
            raise RuntimeError('Descarga distinta del descriptor')
        return self.jobs[ident]['body']

    def files_sent(self, request_id):
        return self.jobs[self.by_request[request_id]]['bundle_files']
