"""Linux checks on the actual mounted storage; only private temporary databases and child processes.
Run with the service stopped: python -m scripts.check_container --base /state/real-jaen.sqlite
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from unittest.mock import patch

from persistencia import sincronizacion, sqlite
from persistencia.bloqueo import BaseEnUso, archivo_de_bloqueo, bloquear

ROOT = Path(__file__).resolve().parent.parent


def spawn(code, *args):
    return subprocess.Popen([sys.executable, '-B', '-u', '-c', code, *map(str, args)], cwd=ROOT,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def stop(child, normal=False, graceful=False):
    if child.poll() is None:
        if graceful:
            child.terminate()
        elif normal:
            child.stdin.write('stop\n')
            child.stdin.flush()
        else:
            child.kill()
    out, err = child.communicate(timeout=15)
    if normal and child.returncode:
        raise RuntimeError(err or out)


def api(base, scope):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, SPORTS_DATA_CONTROL='0', SPORTS_DATA_STORAGE='sql',
               SPORTS_DATA_DB=str(base), SPORTS_DATA_SCOPE=str(scope))
    p = subprocess.Popen([sys.executable, '-B', '-m', 'uvicorn', 'app:app', '--host', '127.0.0.1',
                          '--port', str(port), '--log-level', 'warning'], cwd=ROOT, env=env,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=1) as response:
                    assert response.status == 200
                return p
            except OSError:
                if p.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError('La API temporal no arrancó')
                time.sleep(0.1)
    except BaseException:
        stop(p)
        raise


def check(source, scope):
    if sys.platform != 'linux':
        raise RuntimeError('Esta comprobación debe ejecutarse en Linux, dentro del contenedor')
    if os.geteuid() == 0:
        raise RuntimeError('Ejecute como el usuario normal del backend, no como root')
    source = Path(source).resolve()
    # TemporaryDirectory is intentionally on the persistent filesystem, not /tmp or an image layer.
    with tempfile.TemporaryDirectory(prefix='.storage-check-', dir=source.parent) as directory:
        work = Path(directory)
        base = work / 'real-jaen.sqlite'
        sqlite.copia_consistente(source, base)  # Rejects if our real API still owns source.
        initial = sqlite.describir(base)['huella']
        alias = work / 'alias.sqlite'
        alias.symlink_to(base)
        child = api(base, scope)
        try:
            for path in (base, alias, work / 'sub' / '..' / base.name):
                (work / 'sub').mkdir(exist_ok=True)
                try:
                    with bloquear(path):
                        raise AssertionError('No se excluyó al otro proceso')
                except BaseEnUso:
                    pass
            operations = [
                ['importar', '--exportacion', '2841=/imports/no-existe.jsonl'],
                ['reconstruir', '--confirmar'], ['migrar'],
                ['sustituciones', '--archivo', '/imports/no-existe.json', '--ambito', str(scope)],
                ['restaurar', '--copia', '/imports/no-existe.sqlite', '--confirmar'],
            ]
            for operation in operations:
                result = subprocess.run([sys.executable, '-B', '-m', 'persistencia', *operation, '--base', str(base)],
                                        cwd=ROOT, capture_output=True, text=True, timeout=30)
                assert result.returncode == 1 and 'Base en uso' in result.stderr, result.stderr
                assert not (work / 'copias').exists(), 'Una operación rechazada creó copias'
        finally:
            stop(child, graceful=True)
        assert archivo_de_bloqueo(base).exists(), 'Se borró el archivo de coordinación'
        assert sincronizacion.verificar(base)['ok']
        child = api(base, scope)
        stop(child)  # SIGKILL: the kernel, not cleanup code, must release the lock.
        assert sincronizacion.verificar(base)['ok']

        # Real WAL transaction, killed before COMMIT. All logical rows must survive unchanged.
        child = spawn("""from persistencia.bloqueo import bloquear
from persistencia.sqlite import crear_motor
import sys
with bloquear(sys.argv[1]):
    engine = crear_motor(sys.argv[1])
    with engine.begin() as c:
        c.exec_driver_sql("UPDATE team_name SET name = 'UNCOMMITTED STORAGE CHECK'")
        print('READY', flush=True)
        sys.stdin.readline()
""", base)
        try:
            assert child.stdout.readline().strip() == 'READY'
        finally:
            stop(child)
        assert sqlite.describir(base)['huella'] == initial, 'Cambios parciales tras la caída'
        assert sincronizacion.verificar(base)['ok']
        backup = sincronizacion.copiar(base)
        sincronizacion.importar_evidencia(base)  # Successful no-evidence synchronization; no invented records.
        sincronizacion.reconstruir(base, confirmar=True)
        assert not sincronizacion.migrar(base)['migrada']
        before_failure = sqlite.describir(base)['huella']
        with patch.object(sqlite.os, 'replace', side_effect=OSError('storage-check injected failure')):
            try:
                sincronizacion.restaurar(backup['ruta'], base)
            except OSError:
                pass
            else:
                raise AssertionError('No se produjo el fallo simulado')
        assert sqlite.describir(base)['huella'] == before_failure
        sincronizacion.restaurar(backup['ruta'], base)
        assert sqlite.describir(base)['huella'] == initial
        assert sincronizacion.verificar(base)['ok']
        mounts = Path('/proc/mounts').read_text().splitlines()
        mounted = [line for line in mounts if line.split()[1] == str(source.parent)]
        return {'ok': True, 'platform': sys.platform, 'uid': os.geteuid(), 'sqlite': sqlite.sqlite3.sqlite_version,
                'storage': str(source.parent), 'mount': mounted, 'checks': ['permissions', 'process-lock', 'symlinks',
                'api-rejection', 'normal-exit', 'SIGKILL', 'WAL-rollback', 'maintenance', 'restore-failure']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--ambito', type=Path, default=ROOT / 'ambito.json')
    args = parser.parse_args()
    print(json.dumps(check(args.base, args.ambito), ensure_ascii=False))


if __name__ == '__main__':
    main()
