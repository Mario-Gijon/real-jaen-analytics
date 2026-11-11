"""Logical digest of a SQLite base, read-only and immutable (no file is touched): revision, per-table row digests,
import runs and rule sets. Usage: python digest_base.py PATH"""
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

path = Path(sys.argv[1]).resolve()
c = sqlite3.connect(f'file:{path.as_posix()}?mode=ro&immutable=1', uri=True)
tables = [r[0] for r in c.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name")]
digest = {}
for t in tables:
    h = hashlib.sha256()
    n = 0
    for row in c.execute(f'select * from "{t}" order by 1'):
        h.update(json.dumps(row, default=lambda v: v.hex() if isinstance(v, bytes) else str(v)).encode())
        n += 1
    digest[t] = (n, h.hexdigest()[:16])
total = hashlib.sha256(json.dumps(digest, sort_keys=True).encode()).hexdigest()
cols = lambda t: [r[1] for r in c.execute(f'pragma table_info("{t}")')]
out = dict(path=str(path), revision=[r[0] for r in c.execute('select version_num from alembic_version')], logical=total,
           journal=c.execute('pragma journal_mode').fetchone()[0], tables=len(tables),
           import_runs=c.execute('select count(*) from import_run').fetchone()[0],
           rule_set=[dict(zip(cols('rule_set'), r)) for r in c.execute('select * from rule_set')][:3],
           matches=digest['match'][0], local_replacement=digest['local_replacement'], digests=digest)
print(json.dumps(out, default=str))
