"""
Serves the web build (exports/web/, tools/build_client.py --web) on this PC for testing, telling browsers to
check for a newer copy of every file each time -- as deploy/Caddyfile does on the real server. (Python's
plain http.server doesn't, and browsers then keep playing an old build after a rebuild.)

    python tools/serve_web.py [--port 8800] [--lan]
    then open http://localhost:8800/?server=ws://localhost:8765/ws

--lan opens it to other devices on this network (a tablet or phone on the same Wi-Fi), and prints the address
to open there. The game server must be open to them too: python -m server.app --host 0.0.0.0. (Over plain http
on a network address, a browser may hold back the game's audio; the real server's https doesn't.)
"""
import argparse
import functools
import http.server
import os
import socket
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, 'exports', 'web')


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Cache-Control', 'no-cache')
        super().end_headers()


def lan_address():
    """This PC's address on the local network (the one its default route leaves by)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(('192.0.2.1', 9))  # (no packet is sent: a UDP connect only picks the route)
            return s.getsockname()[0]
        except OSError:
            return '127.0.0.1'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--port', type=int, default=8800)
    parser.add_argument('--directory', default=WEB)
    parser.add_argument('--lan', action='store_true', help='open to other devices on this network')
    parser.add_argument('--game-port', type=int, default=8765, help="the game server's port, for the printed address")
    args = parser.parse_args(argv)
    handler = functools.partial(NoCacheHandler, directory=args.directory)
    with http.server.ThreadingHTTPServer(('0.0.0.0' if args.lan else '127.0.0.1', args.port), handler) as server:
        print(f'serving {args.directory} at http://localhost:{args.port}/', flush=True)
        if args.lan:
            ip = lan_address()
            print(f'on this network: http://{ip}:{args.port}/?server=ws://{ip}:{args.game_port}/ws', flush=True)
        server.serve_forever()
    return 0


if __name__ == '__main__':
    sys.exit(main())
