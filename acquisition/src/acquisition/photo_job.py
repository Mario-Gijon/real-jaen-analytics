"""Process of one player-photos-local-v1 job: `python -m acquisition.photo_job <job folder>`.

Started by service.subprocess_runner like acquisition.job, and leaves the same evidence: a read-only bundle.json whose
descriptor is in report.json (Jobs.run only completes a job when both agree). It opens no browser and no profile, and
needs no network: it reads the collection selected by ACQ_PHOTO_COLLECTION under ACQ_COLLECTIONS_ROOT (/collections).
On failure report.json carries a stable `error_code` (photo_recipe.ERRORS) that the service passes on as the job error.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from acquisition.photo_protocol import bundle
from acquisition import photo_recipe


def _write(path: Path, data: bytes, *, read_only: bool = False) -> None:
    with path.open('xb') as out:
        out.write(data)
        out.flush()
        os.fsync(out.fileno())
    if read_only:
        path.chmod(0o400)


def run(folder, environ=None) -> int:
    folder = Path(folder)
    environ = os.environ if environ is None else environ
    spec = json.loads((folder / 'job.json').read_text(encoding='utf-8'))
    started = photo_recipe.iso_now()
    root = environ.get('ACQ_COLLECTIONS_ROOT', photo_recipe.COLLECTIONS_ROOT)
    selector = environ.get('ACQ_PHOTO_COLLECTION', '')
    report = dict(provider='local-collection', recipe=spec['recipe_id'], started_at=started, finished_at=None,
                  collection_selector=selector or None, descriptor=None, summary=None, error=None, error_code=None,
                  cancelled=False, stopped=None, sends=[], requests=[])
    code = 1
    try:
        if spec['recipe_id'] != photo_recipe.PHOTO_RECIPE:
            raise ValueError('Receta no fotográfica en photo_job')
        inventory = bundle.parse((folder / 'inventory.json').read_bytes())
        collection = photo_recipe.read_collection(photo_recipe.locate(root, selector))
        body, descriptor, summary = photo_recipe.build(collection, inventory, spec, started_at=started)
        if (folder / 'cancel').exists():
            report.update(cancelled=True, error='cancelled')
        else:
            _write(folder / 'bundle.json', body, read_only=True)
            report.update(descriptor=descriptor, summary=summary)
            code = 0
    except photo_recipe.PhotoRecipeError as error:
        report.update(error=str(error)[:400], error_code=error.code)
    except Exception as error:  # an unexpected fault is reported, never sealed
        report.update(error=f'{type(error).__name__}: {error}'[:400])
    report['finished_at'] = photo_recipe.iso_now()
    _write(folder / 'report.json', bundle.encode(report))
    return code


if __name__ == '__main__':
    sys.exit(run(sys.argv[1]))
