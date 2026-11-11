"""Internal acquisition service logic: Compose service `acquisition`, http://acquisition:8002, no host port.

The backend sends the inventory of its current generation with each request; this service never mounts the sporting
volume, its journal or its copies. One active job, durable request keys, cooperative cancellation, interruption on
restart without automatic resume, sealed immutable bundles. Each job runs the fixed recipe in its own process
(acquisition/job.py) inside the exclusive session of the managed profile, which this process owns for its whole life.

Providers (ACQ_PROVIDER): 'sofascore' requires ACQ_ENABLE_REAL=1; 'fixture-site' requires ACQ_TEST_MODE=1, the test
instruments of the test image and ACQ_FIXTURE_ROUTES. Without one of them the service refuses to start.
"""
from contextlib import closing, nullcontext
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import signal
import threading
import time
import uuid
from .profile import ProfileError, atomic_json, sync_dir

PREFIX = '/internal/v1/acquisitions'
RECIPES = {'sofascore-pilot-16599363-v1', 'sofascore-group2-2627-v1'}
ACTIVE = {'queued', 'running', 'cancelling'}
MAX_REQUEST = 32 * 1024 * 1024  # inventory of the current generation (about 12 MB with 380 events)
# Hard limit of the job process per recipe; the recipes themselves have 300 s and 900 s.
JOB_LIMITS = {'sofascore-pilot-16599363-v1': 480, 'sofascore-group2-2627-v1': 1200}
CANCEL_GRACE = 30


def now(): return datetime.now(timezone.utc).isoformat()
def encoded(value): return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
def secret(path):
    value = Path(path).read_bytes().strip()
    if not 32 <= len(value) <= 256 or not value.isascii() or any(c <= 32 for c in value):
        raise ValueError('Invalid internal secret configuration')
    return value


class Conflict(Exception): pass


def subprocess_runner(folder, spec, cancelled):
    """Job process in its own session/process group; cooperative cancel first, then the whole group is killed."""
    env = {k: v for k, v in os.environ.items() if k != 'ACQ_SECRET_FILE'}
    with (folder / 'job.log').open('ab') as log:
        process = subprocess.Popen([sys.executable, '-B', '-m', 'acquisition.job', str(folder)], env=env,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    started, asked = time.monotonic(), None
    while process.poll() is None:
        elapsed = time.monotonic() - started
        if cancelled() and asked is None:
            (folder / 'cancel').touch()
            asked = elapsed
        if elapsed > JOB_LIMITS[spec['recipe_id']] or (asked is not None and elapsed - asked > CANCEL_GRACE):
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            return 'killed'
        time.sleep(.1)
    return process.returncode


class Jobs:
    def __init__(self, root, *, provider, owner=None, runner=subprocess_runner, extra=None):
        import fcntl
        if owner is None and runner is subprocess_runner:
            raise ValueError('A real job process requires the profile owner')
        self.root = Path(root)
        self.root.mkdir(exist_ok=True)
        self.lockfile = (self.root / 'owner.lock').open('a+b')
        fcntl.flock(self.lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.provider, self.owner, self.runner, self.extra = provider, owner, runner, dict(extra or {})
        self.guard = threading.RLock()
        self.stop = threading.Event()
        self.blocked = None
        with closing(self.db()) as c, c:
            c.execute('CREATE TABLE IF NOT EXISTS job(id TEXT PRIMARY KEY,request TEXT UNIQUE,state TEXT,data TEXT)')
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_active ON job((1)) WHERE state IN ('queued','running','cancelling')")
            for ident, data in c.execute("SELECT id,data FROM job WHERE state IN ('queued','running','cancelling')").fetchall():
                item = json.loads(data)
                item.update(state='interrupted', phase='interrupted', error='process_interrupted')
                c.execute('UPDATE job SET state=?,data=? WHERE id=?', ('interrupted', json.dumps(item), ident))
        self.threads = []

    def db(self):
        c = sqlite3.connect(self.root / 'jobs.sqlite', timeout=10)
        c.execute('PRAGMA synchronous=FULL')
        return c

    def get(self, ident):
        with closing(self.db()) as c:
            row = c.execute('SELECT data FROM job WHERE id=?', (ident,)).fetchone()
        if not row: raise KeyError(ident)
        return json.loads(row[0])

    def save(self, item):
        with closing(self.db()) as c, c:
            c.execute('UPDATE job SET state=?,data=? WHERE id=?', (item['state'], json.dumps(item), item['id']))

    def submit(self, request, recipe, inventory):
        from persistencia.bundle import check_inventory, encode
        check_inventory(inventory)
        digest = inventory['sha256']
        with self.guard, closing(self.db()) as c, c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT data FROM job WHERE request=?', (request,)).fetchone()
            if row:
                item = json.loads(row[0])
                if (item['recipe_id'], item['inventory_sha256']) != (recipe, digest): raise Conflict()
                return item, False
            if self.blocked or c.execute("SELECT 1 FROM job WHERE state IN ('queued','running','cancelling')").fetchone():
                raise Conflict()
            item = dict(id=str(uuid.uuid4()), request_id=request, recipe_id=recipe, inventory_sha256=digest,
                        generation_id=inventory['generation_id'], provider=self.provider, state='queued', phase='queued',
                        progress=0, error=None, started_at=now())
            folder = self.root / 'work' / item['id']
            folder.mkdir(parents=True)
            with (folder / 'inventory.json').open('xb') as out:
                out.write(encode(inventory)); out.flush(); os.fsync(out.fileno())
            sync_dir(folder)
            c.execute('INSERT INTO job VALUES(?,?,?,?)', (item['id'], request, item['state'], json.dumps(item)))
        worker = threading.Thread(target=self.run, args=(item['id'],), daemon=True)
        self.threads.append(worker)
        worker.start()
        return item, True

    def cancel(self, ident):
        with self.guard:
            item = self.get(ident)
            if item['state'] in ACTIVE:
                item.update(state='cancelling', phase='cancelling')
                self.save(item)
            return item

    def update(self, ident, **fields):
        with self.guard:
            item = self.get(ident)
            if item['state'] != 'cancelling':
                item.update(fields)
                self.save(item)
            return item

    def cancelled(self, ident):
        return self.stop.is_set() or self.get(ident)['state'] == 'cancelling'

    def run(self, ident):
        folder = self.root / 'work' / ident
        try:
            item = self.update(ident, state='running', phase='collecting', progress=1)
            spec = dict(job_id=ident, request_id=item['request_id'], recipe_id=item['recipe_id'], provider=self.provider,
                        inventory_sha256=item['inventory_sha256'], started_at=item['started_at'],
                        transport_root=str(self.root / 'transport'), **self.extra)
            session = self.owner.session() if self.owner is not None else nullcontext(None)
            with session as profile:
                if self.owner is not None:
                    self.owner.allow_browser(profile)
                spec['profile'] = str(profile)
                atomic_json(folder / 'job.json', spec)
                code = self.runner(folder, spec, lambda: self.cancelled(ident))
            report = json.loads((folder / 'report.json').read_bytes()) if (folder / 'report.json').exists() else {}
            with self.guard:
                item = self.get(ident)
                if item['state'] == 'cancelling' or self.stop.is_set():
                    raise InterruptedError('cancelled')
                if code != 0:
                    raise RuntimeError('provider_failed')
                body = (folder / 'bundle.json').read_bytes()
                descriptor = dict(bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
                if report.get('descriptor') != descriptor or (folder / 'bundle.json').stat().st_mode & 0o222:
                    raise RuntimeError('bundle_not_sealed')
                item.update(state='completed', phase='finished', progress=100, bundle=descriptor)
                self.save(item)  # Durable completion only after the sealed file and its descriptor agree.
        except Exception as error:
            if isinstance(error, ProfileError):
                self.blocked = 'profile_recovery_required'  # manual recovery (acquisition.admin), never automatic
            with self.guard:
                item = self.get(ident)
                if item['state'] == 'cancelling':
                    state, reason = 'cancelled', None
                elif self.stop.is_set():
                    state, reason = 'interrupted', 'service_stopping'
                else:
                    state = 'failed'
                    reason = self.blocked or (str(error) if str(error) == 'bundle_not_sealed' else 'provider_failed')
                item.update(state=state, phase='finished', error=reason)
                self.save(item)

    def report(self, ident):
        self.get(ident)
        path = self.root / 'work' / ident / 'report.json'
        if not path.exists(): raise Conflict()
        value = json.loads(path.read_bytes())
        # Summary only: physical sends, requests, sandbox, page hosts and sampled connections. No bodies or cookies.
        summary = {k: value.get(k) for k in ('provider', 'started_at', 'finished_at', 'error', 'cancelled', 'stopped',
                                             'limits', 'sends', 'requests', 'sandbox', 'launches', 'connections')}
        summary['browser'] = {k: (value.get('browser') or {}).get(k) for k in ('total', 'by_host', 'api')}
        return summary

    def bundle(self, ident):
        item = self.get(ident)
        if item['state'] != 'completed': raise Conflict()
        body = (self.root / 'work' / ident / 'bundle.json').read_bytes()
        if len(body) != item['bundle']['bytes'] or hashlib.sha256(body).hexdigest() != item['bundle']['sha256']:
            raise RuntimeError('Bundle integrity failed')
        return body, item['bundle']['sha256']

    def close(self):
        self.stop.set()
        for thread in self.threads: thread.join(CANCEL_GRACE + 5)
        self.lockfile.close()


def reductions(environ):
    """Limits of this installation for the group recipe, which can only lower the production maxima:
    ACQ_MAX_ATTEMPTS (logical attempts of the transport), ACQ_MAX_SENDS (physical sends of the adapter) and
    ACQ_DETAIL_ROUNDS (comma-separated rounds whose matches may be consulted; listings are not restricted). Anything
    else refuses to start."""
    from acquisition import group_plan
    out = {}
    for name, key, ceiling in (('ACQ_MAX_ATTEMPTS', 'max_attempts', group_plan.MAX_ATTEMPTS),
                               ('ACQ_MAX_SENDS', 'max_sends', group_plan.MAX_SENDS)):
        raw = environ.get(name)
        if raw:
            if not raw.isdigit() or not 1 <= int(raw) <= ceiling:
                raise SystemExit(f'{name} must be an integer from 1 to {ceiling}: it can only lower the production limit')
            out[key] = int(raw)
    if out.get('max_attempts', 0) > out.get('max_sends', group_plan.MAX_SENDS):
        raise SystemExit('ACQ_MAX_ATTEMPTS cannot exceed the physical sends')
    raw = environ.get('ACQ_DETAIL_ROUNDS')
    if raw:
        parts = raw.split(',')
        if not all(p.strip().isdigit() and 1 <= int(p) <= 38 for p in parts):
            raise SystemExit('ACQ_DETAIL_ROUNDS must list rounds from 1 to 38')
        out['detail_rounds'] = sorted({int(p) for p in parts})
    return out


def configuration(environ):
    provider = environ.get('ACQ_PROVIDER')
    if provider == 'sofascore' and environ.get('ACQ_ENABLE_REAL') == '1' and environ.get('ACQ_TEST_MODE') != '1':
        return provider, reductions(environ)
    if provider == 'fixture-site' and environ.get('ACQ_TEST_MODE') == '1' and environ.get('ACQ_ENABLE_REAL') != '1':
        import importlib.util
        routes = environ.get('ACQ_FIXTURE_ROUTES')
        if importlib.util.find_spec('sdk_fixture') is None or not routes or not Path(routes).is_file():
            raise SystemExit('Test provider requires the test image and its recorded routes')
        extra = dict(fixture_routes=routes, fixture_scenario=environ.get('ACQ_FIXTURE_SCENARIO', 'challenge'))
        # Simulated time and a smaller budget, to test the group recipe's plan; only with the local provider.
        if environ.get('ACQ_TEST_NOW'):
            extra['test_now'] = datetime.fromisoformat(environ['ACQ_TEST_NOW']).timestamp()
        if environ.get('ACQ_TEST_PLANNED_ATTEMPTS'):
            extra['test_planned_attempts'] = int(environ['ACQ_TEST_PLANNED_ATTEMPTS'])
        return provider, dict(extra, **reductions(environ))
    raise SystemExit('No acquisition provider enabled: set ACQ_PROVIDER=sofascore with ACQ_ENABLE_REAL=1, '
                     'or the isolated test provider')
