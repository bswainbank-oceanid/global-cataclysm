"""
Runs the Godot client headlessly-ish to save a screenshot, so UI can be
checked without a human at the keyboard (see client/scripts/dbg.gd for the
arguments it understands). Syncs client data first.

Run: python tools/client_shot.py OUT.png [--cam x,y,zoom] [--hover ID]
         [--select ID] [--wheel X,Y,STEPS]
         [--drag X1,Y1,X2,Y2] [--click X,Y]
         [--server [URL]] [--autoplay N] [--steps N] [--state FILE.json] [--badges flag|strip|category] [--wait FRAMES] [--size WxH]
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def find_godot():
    exe = shutil.which('godot_console') or shutil.which('godot')
    if exe:
        return exe
    pkgs = Path.home() / 'AppData' / 'Local' / 'Microsoft' / 'WinGet' / 'Packages'
    hits = sorted(pkgs.glob('GodotEngine.GodotEngine_*/Godot_v*_console.exe'))
    if hits:
        return str(hits[-1])
    sys.exit('Godot not found -- install it (winget install GodotEngine.GodotEngine)')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('out')
    ap.add_argument('--cam')
    ap.add_argument('--hover')
    ap.add_argument('--select')
    ap.add_argument('--wheel')
    ap.add_argument('--drag')
    ap.add_argument('--click')
    ap.add_argument('--state')
    ap.add_argument('--server', nargs='?', const='true')
    ap.add_argument('--autoplay')
    ap.add_argument('--steps')
    ap.add_argument('--badges')
    ap.add_argument('--wait', default='8')
    ap.add_argument('--size', default='1600x900')
    a = ap.parse_args()

    subprocess.run([sys.executable, str(ROOT / 'tools' / 'sync_client_data.py')], check=True)
    user_args = [f'--shot={Path(a.out).resolve()}', f'--wait={a.wait}']
    for key in ('cam', 'hover', 'select', 'wheel', 'drag', 'click', 'state', 'badges', 'server', 'autoplay', 'steps'):
        if getattr(a, key):
            val = getattr(a, key)
            user_args.append(f'--{key}={Path(val).resolve() if key == "state" else val}')
    godot = find_godot()
    subprocess.run([godot, '--headless', '--path', str(ROOT / 'client'), '--import'], capture_output=True, timeout=180)
    cmd = [godot, '--path', str(ROOT / 'client'), '--resolution', a.size, '--position', '-2400,100', '--'] + user_args
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    print(r.stdout[-1500:])
    if r.stderr.strip():
        print('STDERR:', r.stderr[-1500:])
    sys.exit(r.returncode)


if __name__ == '__main__':
    main()
