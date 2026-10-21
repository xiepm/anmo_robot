"""Loopback-only static SPA host for the prebuilt native Windows UI."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import signal
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
ROUTES = {'/', '/survey', '/plan', '/position', '/scan', '/confirm', '/run', '/abnormal', '/finish', '/records'}


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if urlsplit(self.path).path == '/healthz':
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'ok\n')
            return
        if urlsplit(self.path).path in ROUTES:
            self.path = '/index.html'
        super().do_GET()

    def end_headers(self):
        self.send_header('Cache-Control', 'no-cache')
        super().end_headers()


def main():
    dist = ROOT / 'web/dist-native'
    if not (dist / 'index.html').is_file():
        raise SystemExit('Missing web/dist-native. Run windows/Install-Native.cmd first.')
    server = ThreadingHTTPServer(('127.0.0.1', 5173), partial(Handler, directory=str(dist)))
    server.daemon_threads = True
    def stop(*_):
        raise KeyboardInterrupt
    for name in ('SIGINT', 'SIGTERM', 'SIGBREAK'):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), stop)
    try:
        server.serve_forever(poll_interval=.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
