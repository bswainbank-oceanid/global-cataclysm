"""
Starts Global Cataclysm: 1972 the way a player does -- what the desktop shortcut runs (made by
tools/make_shortcut.py, with pythonw, so no console window opens):

  1. copies the game data into the client (tools/sync_client_data.py),
  2. starts the multi-player server in the background if nothing is listening on its port yet
     (it keeps running when the game window closes: games carry on, and the next launch uses it;
     its log is server_data/server.log),
  3. opens the game window (Godot, connected to that server).

Godot is found from the GODOT environment variable, or else Godot 4's WinGet install. Problems are
shown in a message box (there's no console to print to).

    pythonw tools/launcher.py [--port 8765] [--db PATH]
"""
import argparse
import glob
import os
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PORT = 8765
WINGET_GODOT = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Microsoft', 'WinGet', 'Packages',
                            'GodotEngine.GodotEngine_*', 'Godot_v4*_win64.exe')
DETACHED = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW


def message(text, title='Global Cataclysm: 1972'):
    """A message box (pythonw has no console)."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)
    except Exception:
        print(text, file=sys.stderr)


def listening(port):
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(('localhost', port)) == 0


def find_godot():
    exe = os.environ.get('GODOT')
    if exe and os.path.exists(exe):
        return exe
    found = sorted(p for p in glob.glob(WINGET_GODOT) if not p.endswith('_console.exe'))
    return found[-1] if found else None


def python_for_server():
    """The interpreter to run the server with: this one (pythonw when launched from the shortcut)."""
    return sys.executable


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=DEFAULT_PORT)
    parser.add_argument('--db', default=None, help="the server's player database (default: the server's own)")
    args = parser.parse_args(argv)

    godot = find_godot()
    if godot is None:
        message("Godot 4 wasn't found. Install it (winget install GodotEngine.GodotEngine) or set the GODOT "
                "environment variable to its .exe.")
        return 1

    sync = subprocess.run([sys.executable, os.path.join(ROOT, 'tools', 'sync_client_data.py')], cwd=ROOT,
                          capture_output=True, text=True, creationflags=0x08000000)
    if sync.returncode != 0:
        message('Copying the game data into the client failed:\n\n' + (sync.stderr or sync.stdout)[-1500:])
        return 1

    if not listening(args.port):
        os.makedirs(os.path.join(ROOT, 'server_data'), exist_ok=True)
        log = open(os.path.join(ROOT, 'server_data', 'server.log'), 'a', encoding='utf-8')
        command = [python_for_server(), '-m', 'server.app', '--port', str(args.port)] + (['--db', args.db] if args.db else [])
        subprocess.Popen(command, cwd=ROOT,
                         stdout=log, stderr=log, stdin=subprocess.DEVNULL, creationflags=DETACHED, close_fds=True)
        for _ in range(60):  # up to 15 seconds for it to come up (it loads every live game first)
            if listening(args.port):
                break
            time.sleep(0.25)
        else:
            message("The game server didn't start. Its log is server_data/server.log.")
            return 1

    subprocess.Popen([godot, '--path', os.path.join(ROOT, 'client'), '--', f'--server=ws://localhost:{args.port}'],
                     cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=DETACHED, close_fds=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
