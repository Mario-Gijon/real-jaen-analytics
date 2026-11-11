"""Initialize an absent container database from a completed SQLite backup, never from a live WAL database.
Run explicitly: python -m scripts.initialize_snapshot /imports/snapshot.sqlite /state/real-jaen.sqlite
"""
import argparse
from contextlib import closing
import json
import shutil
import tempfile
from pathlib import Path

from persistencia import sincronizacion, sqlite
from persistencia.bloqueo import bloquear


def initialize(snapshot, target):
    snapshot, target = Path(snapshot).resolve(), Path(target).resolve()
    with bloquear(target):
        if any(p.exists() for p in (target, *(target.with_name(target.name + s) for s in sqlite.ACOMPANANTES))):
            raise ValueError('El destino o sus diarios ya existen; use restaurar, nunca sobrescriba al inicializar')
        if any(snapshot.with_name(snapshot.name + s).exists() for s in sqlite.ACOMPANANTES):
            raise ValueError('Se necesita una copia SQLite finalizada sin WAL/SHM/journal, no una base en uso')
        expected = sqlite.comprobar_copia(snapshot)
        with closing(sqlite._lectura(snapshot)) as c:
            mode = c.execute('PRAGMA journal_mode').fetchone()[0]
        if mode != 'delete' or expected['revision'] != sincronizacion.revision_del_codigo():
            raise ValueError('La copia debe usar journal DELETE y la revisión del código; migre solo una copia aparte')
        # The input is a verified, complete backup on a read-only mount. Only our private staged file is writable.
        with tempfile.TemporaryDirectory(prefix='.initialize-', dir=target.parent) as name:
            incoming, ready = Path(name) / 'incoming.sqlite', Path(name) / 'ready.sqlite'
            shutil.copyfile(snapshot, incoming)
            sqlite.comprobar_copia(incoming, expected)
            sqlite.copia_consistente(incoming, ready)
            result = sincronizacion.verificar(ready)
            if not result['ok']:
                raise ValueError(f'La copia no corresponde al código: {result["problemas"]}')
            sqlite.comprobar_copia(ready, expected)
            ready.replace(target)
        return {'base': str(target), 'revision': expected['revision'], 'huella': expected['huella']}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('snapshot', type=Path)
    p.add_argument('target', type=Path)
    args = p.parse_args()
    try:
        result = initialize(args.snapshot, args.target)
    except (RuntimeError, ValueError, OSError) as e:
        p.exit(1, f'Rechazado: {e}\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
