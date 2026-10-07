"""
Builds the client players download: a Windows program that connects to `--server-url` on its own (no
--server needed), zipped as exports/GlobalCataclysm-Build<N>.zip -- or with --web the browser version,
the files of exports/web/ (served at https://<domain>/play/), which connects to the server it was loaded
from (docs/DEPLOY.md).

    python tools/build_client.py --server-url wss://play.example.com/ws
    python tools/build_client.py --web
    python tools/build_client.py --server-url wss://play.example.com --godot PATH\\Godot_v4.7.2-stable_win64_console.exe

It syncs the client's data first (tools/sync_client_data.py), writes the server's address into the build
(client/data/server.json, read by net_client.gd's release_url; removed again afterwards, so the development
copy keeps connecting only with --server), and exports with the "Windows Desktop" preset
(client/export_presets.cfg). Godot's export templates must be installed for the Godot version used:
Editor -> Manage Export Templates -> Download and Install (they land in
%APPDATA%\\Godot\\export_templates\\<version>).
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT = os.path.join(ROOT, 'client')
RELEASE = os.path.join(CLIENT, 'data', 'server.json')
OUT = os.path.join(ROOT, 'exports')
PRESET = 'Windows Desktop'
WEB_PRESET = 'Web'
EXE = 'GlobalCataclysm.exe'
GODOT_GLOB = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Microsoft', 'WinGet', 'Packages', 'GodotEngine.GodotEngine*',
                          'Godot_v*_win64_console.exe')


def find_godot():
    found = sorted(glob.glob(GODOT_GLOB))
    return found[-1] if found else None


def templates_missing(godot):
    """The export templates' folder for this Godot, if it isn't there (else None)."""
    version = subprocess.run([godot, '--version'], capture_output=True, text=True).stdout.strip()
    # '4.7.2.stable.official.abc123' -> '4.7.2.stable'
    parts = version.split('.')
    name = '.'.join(parts[:parts.index('stable') + 1]) if 'stable' in parts else '.'.join(parts[:4])
    folder = os.path.join(os.environ.get('APPDATA', ''), 'Godot', 'export_templates', name)
    return None if all(os.path.exists(os.path.join(folder, f)) for f in ('windows_release_x86_64.exe', 'web_nothreads_release.zip')) else folder


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--server-url', help='the server players connect to, e.g. wss://play.example.com/ws '
                                             '(the Windows build; the web build uses the server it is loaded from)')
    parser.add_argument('--web', action='store_true', help='build the browser version (exports/web/) instead')
    parser.add_argument('--godot', default=None, help='the Godot console executable (default: the WinGet install)')
    args = parser.parse_args(argv)
    if not args.web and not args.server_url:
        parser.error('--server-url is needed (or --web)')
    if args.server_url and not args.server_url.startswith(('wss://', 'ws://')):
        parser.error('--server-url must start with wss:// (or ws:// for testing)')
    godot = args.godot or find_godot()
    if not godot or not os.path.exists(godot):
        parser.error("can't find Godot: pass --godot")
    missing = templates_missing(godot)
    if missing:
        print(f"Godot's export templates aren't installed (expected in {missing}).\n"
              "Install them from the Godot editor: Editor -> Manage Export Templates -> Download and Install.")
        return 1

    sys.path.insert(0, ROOT)
    from server.build import build_info
    subprocess.run([sys.executable, os.path.join(ROOT, 'tools', 'sync_client_data.py')], check=True)
    build = build_info(refresh=True)
    if build['commit'].endswith('+'):
        print(f"note: {build['label']} has uncommitted changes")
    if args.web:
        return build_web(godot, build)
    os.makedirs(os.path.join(OUT, 'client'), exist_ok=True)
    exe = os.path.join(OUT, 'client', EXE)
    try:
        with open(RELEASE, 'w', encoding='utf-8') as f:
            json.dump({'url': args.server_url}, f)
        subprocess.run([godot, '--headless', '--path', CLIENT, '--import'], check=True)
        subprocess.run([godot, '--headless', '--path', CLIENT, '--export-release', PRESET, exe], check=True)
    finally:
        if os.path.exists(RELEASE):
            os.remove(RELEASE)
    if not os.path.exists(exe):
        print('the export made no program: see the messages above')
        return 1
    archive = os.path.join(OUT, f"GlobalCataclysm-Build{build['build']}.zip")
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        z.write(exe, EXE)
    print(f"built {archive} ({os.path.getsize(archive) // 1024 // 1024} MB): {build['label']}, server {args.server_url}")
    return 0


def build_web(godot, build):
    """The browser version, in exports/web/ (emptied first): index.html and the engine and game files."""
    web = os.path.join(OUT, 'web')
    os.makedirs(web, exist_ok=True)
    for old in os.listdir(web):
        os.remove(os.path.join(web, old))
    if os.path.exists(RELEASE):  # (no built-in server: the page connects to its own)
        os.remove(RELEASE)
    subprocess.run([godot, '--headless', '--path', CLIENT, '--import'], check=True)
    subprocess.run([godot, '--headless', '--path', CLIENT, '--export-release', WEB_PRESET,
                    os.path.join(web, 'index.html')], check=True)
    if not os.path.exists(os.path.join(web, 'index.html')):
        print('the export made no page: see the messages above')
        return 1
    size = sum(os.path.getsize(os.path.join(web, f)) for f in os.listdir(web))
    print(f"built {web} ({size // 1024 // 1024} MB, {len(os.listdir(web))} files): {build['label']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
