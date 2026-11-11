"""Cooperative Linux profile ownership and explicit, copy-on-recovery generations."""
import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import uuid
from contextlib import contextmanager
SINGLETONS = ('SingletonLock', 'SingletonSocket', 'SingletonCookie')
SPACE_MARGIN = 32 * 1024 * 1024

class ProfileError(RuntimeError):
    code = 74

class Busy(ProfileError):
    code = 73

class RecoveryRequired(ProfileError):
    pass

def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

def atomic_json(path, value):
    tmp = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    try:
        with tmp.open('x', encoding='utf-8') as out:
            json.dump(value, out, ensure_ascii=False, indent=2)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
        sync_dir(path.parent)
    finally:
        tmp.unlink(missing_ok=True)

def manifest(root):
    result = {}
    if root.is_symlink() or not root.is_dir():
        raise RecoveryRequired('Perfil ausente o ruta insegura')
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            mode = path.lstat().st_mode
            key = path.relative_to(root).as_posix()
            if stat.S_ISLNK(mode):
                if key not in SINGLETONS:
                    raise RecoveryRequired('Enlace no admitido en el perfil')
                result[key] = {'link': os.readlink(path)}
            elif stat.S_ISREG(mode):
                with path.open('rb') as source:
                    result[key] = {'bytes': path.stat().st_size, 'sha256': hashlib.file_digest(source, 'sha256').hexdigest()}
            elif stat.S_ISDIR(mode):
                result[key] = {'directory': True}
            else:
                raise RecoveryRequired('Archivo especial no admitido en el perfil')
    return result

def sync_tree(root):
    for directory, dirs, files in os.walk(root, topdown=False, followlinks=False):
        for name in files:
            path = Path(directory) / name
            if not path.is_symlink():
                with path.open('rb') as source:
                    os.fsync(source.fileno())
        sync_dir(directory)

class Profile:

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.control = self.root / 'control'
        self.state_path = self.control / 'state.json'
        self.operation_path = self.control / 'maintenance.json'
        self.fd = None
        self.candidate = None

    def __enter__(self):
        import fcntl
        if self.fd is not None:
            raise ProfileError('El propietario ya está abierto')
        if not self.root.is_dir():
            raise RecoveryRequired('El almacenamiento debe prepararse explícitamente')
        self.control.mkdir(mode=448, exist_ok=True)
        if self.control.is_symlink():
            raise RecoveryRequired('Directorio de coordinación inseguro')
        fd = os.open(self.control / 'owner.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 384)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise RecoveryRequired('Archivo de coordinación inseguro')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise Busy('El perfil está en uso') from None
        except BaseException:
            os.close(fd)
            raise
        try:
            sync_dir(self.control)
        except BaseException:
            os.close(fd)
            raise
        self.fd = fd
        return self

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def locked(self):
        if self.fd is None:
            raise ProfileError('Se requiere propietario exclusivo')

    def read_state(self):
        self.locked()
        try:
            if self.state_path.is_symlink():
                raise ValueError()
            state = json.loads(self.state_path.read_text())
            if set(state) != {'schema', 'active', 'session'} or type(state['schema']) is not int or state['schema'] != 1 or (not isinstance(state['active'], str)):
                raise ValueError()
            if state['session'] is not None and (not isinstance(state['session'], str) or len(state['session']) != 32 or any((c not in '0123456789abcdef' for c in state['session']))):
                raise ValueError()
            self.path(state['active'])
            return state
        except (OSError, ValueError, KeyError, TypeError):
            raise RecoveryRequired('Perfil pendiente de recuperación: estado ausente o incoherente') from None

    def path(self, generation):
        if not generation or any((c not in '0123456789abcdef' for c in generation)) or len(generation) != 32:
            raise RecoveryRequired('Identificador de generación inválido')
        path = self.root / 'generations' / generation
        if path.parent.is_symlink() or path.is_symlink() or (not path.is_dir()):
            raise RecoveryRequired('Perfil pendiente de recuperación: generación ausente')
        return path

    def initialize(self, *, confirm_new=False):
        self.locked()
        if not confirm_new:
            raise ProfileError('Confirme explícitamente la creación de un perfil nuevo')
        if self.state_path.exists() or self.operation_path.exists():
            raise RecoveryRequired('No se puede reinicializar un perfil administrado')
        if set((p.name for p in self.root.iterdir())) != {'control'} or set((p.name for p in self.control.iterdir())) != {'owner.lock'}:
            raise RecoveryRequired('El destino contiene datos; no se creará un perfil vacío')
        directory = self.root / 'generations'
        directory.mkdir(mode=448)
        generation = uuid.uuid4().hex
        (directory / generation).mkdir(mode=448)
        sync_dir(directory / generation)
        sync_dir(directory)
        sync_dir(self.root)
        atomic_json(self.state_path, {'schema': 1, 'active': generation, 'session': None})
        return self.verify()

    def verify(self):
        state = self.read_state()
        if self.operation_path.exists() or self.operation_path.is_symlink() or state['session']:
            raise RecoveryRequired('Perfil pendiente de recuperación')
        files = manifest(self.path(state['active']))
        if any((name in files for name in SINGLETONS)):
            raise RecoveryRequired('Perfil pendiente de recuperación: singleton inesperado')
        return {'status': 'ready', 'generation': state['active'], 'files': len(files)}

    @contextmanager
    def session(self):
        self.verify()
        state = self.read_state()
        state['session'] = uuid.uuid4().hex
        atomic_json(self.state_path, state)
        path = self.path(state['active'])
        yield path
        sync_tree(path)
        if any((name in manifest(path) for name in SINGLETONS)):
            raise RecoveryRequired('Perfil pendiente de recuperación: cierre incompleto')
        state['session'] = None
        atomic_json(self.state_path, state)

    def allow_browser(self, path):
        self.locked()
        if path == self.candidate:
            return
        state = self.read_state()
        if not state['session'] or self.operation_path.exists() or path != self.path(state['active']):
            raise RecoveryRequired('El navegador requiere una sesión exclusiva preparada')

    def recover(self, validator, *, confirm_stopped=False, confirm_external=False):
        self.locked()
        if not confirm_stopped or not confirm_external:
            raise ProfileError('Confirme servicio detenido y ausencia de usuarios externos')
        state = self.read_state()
        source = self.path(state['active'])
        original = manifest(source)
        size = sum((entry.get('bytes', 0) for entry in original.values()))
        if shutil.disk_usage(self.root).free < size * 2 + SPACE_MARGIN:
            raise OSError(errno.ENOSPC, 'Espacio insuficiente para respaldo y candidata')
        token = uuid.uuid4().hex
        backups = self.root / 'backups'
        if backups.is_symlink():
            raise RecoveryRequired('Directorio de copias inseguro')
        backups.mkdir(mode=448, exist_ok=True)
        backup = backups / token
        candidate = self.root / 'generations' / token
        operation = {'schema': 1, 'source': state['active'], 'candidate': token, 'backup': token, 'stage': 'copying'}
        atomic_json(self.operation_path, operation)
        shutil.copytree(source, backup, symlinks=True)
        if manifest(backup) != original or manifest(source) != original:
            raise RecoveryRequired('No se pudo verificar la copia; fuente conservada')
        sync_tree(backup)
        atomic_json(backups / (token + '.json'), {'source': state['active'], 'files': original})
        sync_dir(self.root)
        shutil.copytree(backup, candidate, symlinks=True)
        if manifest(candidate) != original:
            raise RecoveryRequired('No se pudo verificar la candidata')
        for name in SINGLETONS:
            target = candidate / name
            if target.is_symlink():
                target.unlink()
            elif target.exists():
                raise RecoveryRequired('Tipo de singleton inesperado; copia y fuente conservadas')
        sync_tree(candidate)
        self.candidate = candidate
        try:
            validator(candidate)
        finally:
            self.candidate = None
        if manifest(source) != original or manifest(backup) != original:
            raise RecoveryRequired('Se detectaron cambios en fuente o respaldo')
        if any((name in manifest(candidate) for name in SINGLETONS)):
            raise RecoveryRequired('Cierre de candidata incompleto')
        sync_tree(candidate)
        sync_dir(candidate.parent)  # Directory entry must be durable before the active pointer.
        operation['stage'] = 'verified'
        atomic_json(self.operation_path, operation)
        atomic_json(self.state_path, {'schema': 1, 'active': token, 'session': None})
        self.operation_path.unlink()
        sync_dir(self.control)
        result = self.verify()
        result['backup'] = token
        return result
