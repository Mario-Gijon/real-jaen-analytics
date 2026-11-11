"""Sitio de prueba en 127.0.0.1 con el comportamiento de SofaScore que importa al flujo del SDK.

Las rutas API sirven los cuerpos que se le pasan (en las pruebas, los descargados el 27-10-2026); el sitio solo
imita el acceso: sin la cookie sofa_captcha la API responde 403 con «challenge», captcha.html la entrega y redirige al
`redirectUrl` del mismo origen, y /robots.txt es la página ligera. Registra cada petición HTTP recibida y cada
handshake TLS rechazado, para contrastar lo que el cliente dice haber enviado con lo que llegó.

Escenarios: challenge, directo (sin challenge), prohibido (403 sin challenge), irresoluble (el captcha no entrega la
cookie) y limite-navegador (la primera petición API con cookie va bien; las siguientes, 429).
"""
import json
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

API = '/api/v1'
TOKEN = "fixture-token-only"
ROBOTS = b'User-agent: *\nDisallow:\n'
# A route whose body is this marker answers 500 (a server error of the provider).
SERVER_ERROR = b'__500__'
# This one answers 500 to the browser (with the cookie) and then refuses without a challenge (plain 403) the retry
# without the cookie: a refusal after a server error, which must stop everything.
SERVER_ERROR_THEN_FORBIDDEN = b'__500_403__'


def load_downloaded(folder):
    """Cuerpos de las cinco rutas del piloto desde la evidencia de una descarga real (round_1.json y raw JSONL)."""
    from pathlib import Path
    folder = Path(folder)
    rnd = json.loads((folder / 'round_1.json').read_text(encoding='utf-8'))
    routes = {rnd['path']: json.dumps(rnd['payload'], ensure_ascii=False).encode('utf-8')}
    for line in next(folder.glob('raw-*.jsonl')).read_text(encoding='utf-8').splitlines():
        row = json.loads(line)
        body = {'event': row['payload']} if row['key'] == 'event' else row['payload']
        path = f"/event/{row['event_id']}" + ('' if row['key'] == 'event' else '/' + row['key'])
        routes[path] = json.dumps(body, ensure_ascii=False).encode('utf-8')
    return routes


def routes_from_bundle(body):
    """Cuerpos originales de un bundle sellado (provenance.jsonl): la última respuesta correcta de cada ruta, byte a
    byte como la recibió el productor."""
    import base64
    envelope = json.loads(body)
    rows = [json.loads(line) for line in base64.b64decode(envelope['files']['provenance.jsonl']).splitlines()]
    return {row['path']: base64.b64decode(row['body_b64']) for row in sorted(rows, key=lambda r: r['number'])}


def save_routes(routes, path):
    import base64
    from pathlib import Path
    Path(path).write_text(json.dumps({k: base64.b64encode(v).decode() for k, v in sorted(routes.items())}), encoding='utf-8')


def routes_from_file(path):
    import base64
    from pathlib import Path
    return {k: base64.b64decode(v) for k, v in json.loads(Path(path).read_text(encoding='utf-8')).items()}


class FixtureSite:
    def __init__(self, routes, *, scenario='challenge', cert=None):
        self.routes, self.scenario = dict(routes), scenario
        self.hits, self.tls_rejected = [], []
        self.lock = threading.Lock()
        self.with_cookie = 0
        site = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args):
                pass

            def send(self, status, body=b'', ctype='application/json', headers=()):
                self.send_response(status)
                self.send_header('Content-Type', ctype)
                self.send_header('Content-Length', str(len(body)))
                for k, v in headers:
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)
                return status

            def do_GET(self):
                url = urlsplit(self.path)
                cookie = 'sofa_captcha=' in (self.headers.get('Cookie') or '')
                status = self.route(url, cookie)
                with site.lock:
                    site.hits.append(dict(path=url.path, cookie=cookie, status=status))

            def route(self, url, cookie):
                s = site.scenario
                if url.path == '/robots.txt':
                    return self.send(200, ROBOTS, 'text/plain')
                if url.path == '/captcha.html':
                    target = parse_qs(url.query).get('redirectUrl', ['/robots.txt'])[0]
                    if urlsplit(target).netloc not in ('', self.headers.get('Host')):
                        target = '/robots.txt'
                    page = ('<!doctype html><title>captcha</title><script>setTimeout(() => location.href = '
                            + json.dumps(target) + ', 200)</script>').encode()
                    cookies = () if s == 'irresoluble' else (('Set-Cookie', f'sofa_captcha={TOKEN}; Path=/'),)
                    return self.send(200, page, 'text/html', cookies)
                if not url.path.startswith(API):
                    return self.send(404, b'{}')
                if s == 'prohibido':
                    return self.send(403, b'{"error":{"code":403,"reason":"Forbidden"}}')
                if site.routes.get(url.path[len(API):]) == SERVER_ERROR_THEN_FORBIDDEN:
                    if cookie:
                        return self.send(500, b'{"error":{"code":500}}')
                    return self.send(403, b'{"error":{"code":403,"reason":"Forbidden"}}')
                if s != 'directo' and not cookie:
                    return self.send(403, b'{"error":{"code":403,"reason":"challenge"}}')
                if s == 'limite-navegador' and cookie:
                    with site.lock:
                        site.with_cookie += 1
                        n = site.with_cookie
                    if n > 1:
                        return self.send(429, b'{"error":{"code":429}}', headers=(('Retry-After', '120'),))
                body = site.routes.get(url.path[len(API):])
                if body == SERVER_ERROR:
                    return self.send(500, b'{"error":{"code":500}}')
                return self.send(200, body) if body is not None else self.send(404, b'{"error":{"code":404}}')

        class Server(ThreadingHTTPServer):
            def get_request(self):
                try:
                    return super().get_request()
                except OSError as e:
                    with site.lock:
                        site.tls_rejected.append(getattr(e, 'reason', None) or type(e).__name__)
                    raise

        self.http = Server(('127.0.0.1', 0), Handler)
        if cert:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(*cert)
            self.http.socket = context.wrap_socket(self.http.socket, server_side=True)
        scheme = 'https' if cert else 'http'
        self.origin = f'{scheme}://127.0.0.1:{self.http.server_port}'
        self.api = self.origin + API
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()

    def api_hits(self):
        with self.lock:
            return [h for h in self.hits if h['path'].startswith(API)]
