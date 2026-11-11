"""Test-only local content and fault injection. Never imported by acquisition."""
import asyncio
import errno
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import socket
import sys
import threading
from unittest.mock import patch
from acquisition import profile as module
from acquisition.profile import Profile, manifest, atomic_json
from acquisition.browser import managed_browser, validate_candidate

ROOT=Path('/profiles')
class Local(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_GET(self):
        data=b'<title>Offline profile fixture</title><p id="fixture">local only</p>'
        self.send_response(200); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)

async def browser(owner,mode):
    server=ThreadingHTTPServer(('127.0.0.1',0),Local)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    origin=f'http://127.0.0.1:{server.server_port}'
    try:
        async with managed_browser(owner) as browser:
            page=await browser.context.new_page();await page.goto(origin)
            assert await page.locator('#fixture').inner_text()=='local only'
            cookies=await browser.context.cookies(origin)
            previous=any(c['name']=='profile_fixture' and c['value']=='original-value' for c in cookies)
            if mode!='seed': assert previous,'Original cookie missing; no replacement allowed'
            if mode=='seed':
                await browser.context.add_cookies([{'name':'profile_fixture','value':'original-value','url':origin,'expires':2000000000}])
            report={'cookie_previous':previous,'sandbox':browser.sandbox,'generation':owner.read_state()['active']}
            if mode=='hold':
                stopped=asyncio.Event();asyncio.get_running_loop().add_signal_handler(signal.SIGTERM,stopped.set)
                atomic_json(ROOT/('ready-'+socket.gethostname()+'.json'),report);print('READY',flush=True);await stopped.wait()
            print(json.dumps(report),flush=True)
    finally:
        server.shutdown();server.server_close();thread.join()

def main():
    mode=sys.argv[1]
    with Profile(ROOT) as owner:
        if mode in ('seed','probe','hold'):
            (ROOT/('ready-'+socket.gethostname()+'.json')).unlink(missing_ok=True)
            asyncio.run(browser(owner,mode))
        elif mode.startswith('crash-'):
            real=module.atomic_json
            def write(path,value):
                if path==owner.state_path and mode=='crash-before': os.kill(os.getpid(),signal.SIGKILL)
                real(path,value)
                if path==owner.state_path and mode=='crash-after': os.kill(os.getpid(),signal.SIGKILL)
            with patch.object(module,'atomic_json',side_effect=write):
                owner.recover(lambda path:asyncio.run(validate_candidate(owner,path)),confirm_stopped=True,confirm_external=True)
        elif mode=='real-enospc':
            owner.initialize(confirm_new=True)
            source=owner.path(owner.read_state()['active']);(source/'data').write_bytes(b'x'*600000)
            before=manifest(source)
            # Bypass only the estimate: exercise actual filesystem ENOSPC on a private 1 MiB tmpfs.
            with patch.object(module,'SPACE_MARGIN',0),patch.object(module.shutil,'disk_usage',return_value=type('Usage',(),{'free':10**9})()):
                try: owner.recover(lambda path:None,confirm_stopped=True,confirm_external=True)
                except OSError as error: assert error.errno==errno.ENOSPC or 'Errno 28' in str(error)
                else: raise AssertionError('Expected actual ENOSPC')
            assert manifest(source)==before
            print('REAL_ENOSPC_SOURCE_INTACT',flush=True)
        else: raise ValueError(mode)

if __name__=='__main__':main()
