"""
Serves the web build (exports/web/, tools/build_client.py --web) on this PC for testing, telling browsers to
check for a newer copy of every file each time -- as deploy/Caddyfile does on the real server. (Python's
plain http.server doesn't, and browsers then keep playing an old build after a rebuild.)

    python tools/serve_web.py [--port 8800]
    then open http://localhost:8800/?server=ws://localhost:8765/ws
"""
import argparse
import functools
import http.server
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, 'exports', 'web')


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Cache-Control', 'no-cache')
        super().end_headers()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--port', type=int, default=8800)
    parser.add_argument('--directory', default=WEB)
    args = parser.parse_args(argv)
    handler = functools.partial(NoCacheHandler, directory=args.directory)
    with http.server.ThreadingHTTPServer(('127.0.0.1', args.port), handler) as server:
        print(f'serving {args.directory} at http://localhost:{args.port}/')
        server.serve_forever()
    return 0


if __name__ == '__main__':
    sys.exit(main())
