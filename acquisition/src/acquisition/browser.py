"""Sandboxed offline browser; one explicit profile path, no alternate-profile fallback."""
import asyncio
from contextlib import asynccontextmanager
import os
from pathlib import Path
import socket
from urllib.parse import urlsplit
from .profile import ProfileError

def require_offline():
    if {name for _, name in socket.if_nameindex()} != {'lo'}:
        raise ProfileError('Se requiere contenedor con red deshabilitada (network_mode: none)')

def processes():
    found = {}
    for directory in Path('/proc').glob('[0-9]*'):
        try:
            values = dict(line.split(':', 1) for line in (directory / 'status').read_text().splitlines() if ':' in line)
            if int(values['Uid'].split()[0]) == os.getuid() and int(directory.name) != os.getpid():
                found[int(directory.name)] = (values, (directory / 'cmdline').read_bytes().split(b'\0'))
        except (OSError, ValueError, KeyError):
            pass
    return found

def verify_sandbox(before, path):
    """Acredita por /proc el sandbox de un Chromium abierto sobre `path` después de `before` (PIDs previos): un único
    proceso principal con esa ruta y renderers con NoNewPrivs, seccomp en modo filtro, sin capacidades y con un
    namespace PID y un filtro seccomp propios."""
    relevant = [entry for pid, entry in processes().items() if pid not in before]
    browser = [v for v, args in relevant if any(a == ('--user-data-dir=' + str(path)).encode() for a in args) and not any(a.startswith(b'--type=') for a in args)]
    renderers = [v for v, args in relevant if b' --type=renderer ' in b' ' + b' '.join(args) + b' ']
    if len(browser) != 1 or not renderers:
        raise ProfileError('No se ha acreditado la ruta o los procesos del sandbox')
    for values in renderers:
        if values['NoNewPrivs'].strip() != '1' or values['Seccomp'].strip() != '2' or int(values['CapEff'], 16) != 0:
            raise ProfileError('Protecciones de renderer no acreditadas')
        if len(values['NSpid'].split()) <= len(browser[0]['NSpid'].split()) or int(values['Seccomp_filters']) <= int(browser[0]['Seccomp_filters']):
            raise ProfileError('Namespaces o filtro adicional no acreditados')
    return {'namespaces': True, 'seccomp': True, 'proc_verified': True}

class SandboxBrowser:
    def __init__(self, owner, path):
        self.owner, self.path = owner, path
        self.context = self.engine = None
        self.sandbox = None

    async def __aenter__(self):
        require_offline()
        self.owner.allow_browser(self.path)
        self.before = set(processes())
        from patchright.async_api import async_playwright
        try:
            self.engine = await async_playwright().start()
            self.context = await self.engine.chromium.launch_persistent_context(
                str(self.path), executable_path=self.engine.chromium.executable_path,
                headless=True, chromium_sandbox=True, ignore_https_errors=False,
                permissions=[], args=['--disable-background-networking'],
                ignore_default_args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage',
                                     '--disable-ipc-flooding-protection', '--disable-client-side-phishing-detection'])
            async def offline(route):
                url = urlsplit(route.request.url)
                if url.scheme in ('about', 'chrome', 'data') or (url.scheme == 'http' and url.hostname == '127.0.0.1'):
                    await route.continue_()
                else:
                    await route.abort()
            await self.context.route('**/*', offline)
            page = await self.context.new_page()
            await page.goto('chrome://sandbox')
            status = await page.locator('body').inner_text()
            for expected in ('Layer 1 Sandbox\tNamespace', 'PID namespaces\tYes', 'Network namespaces\tYes', 'Seccomp-BPF sandbox\tYes'):
                if expected not in status:
                    raise ProfileError('No se ha acreditado el sandbox del navegador')
            self.sandbox = verify_sandbox(self.before, self.path)
            await page.close()
            return self
        except BaseException:
            await self.close()
            raise

    async def close(self):
        try:
            if self.context:
                await asyncio.wait_for(self.context.close(), 15)
        finally:
            if self.engine:
                await asyncio.wait_for(self.engine.stop(), 15)
        for _ in range(50):
            if not (set(processes()) - self.before):
                return
            await asyncio.sleep(.1)
        raise ProfileError('Cierre de procesos incompleto; perfil pendiente de recuperación')

    async def __aexit__(self, *exc):
        await self.close()

@asynccontextmanager
async def managed_browser(owner):
    with owner.session() as path:
        async with SandboxBrowser(owner, path) as browser:
            yield browser

async def validate_candidate(owner, path):
    async with SandboxBrowser(owner, path):
        pass
