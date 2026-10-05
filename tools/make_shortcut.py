"""
Makes the "Global Cataclysm 1972" shortcut on the Windows desktop (and in the Start menu): it runs
tools/launcher.py with pythonw (no console window), with the game's icon,
assets/logo/global-cataclysm.ico (written here from the poster icons if it's missing).

    python tools/make_shortcut.py [--no-start-menu]
"""
import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICON = os.path.join(ROOT, 'assets', 'logo', 'global-cataclysm.ico')
ICON_SOURCES = [os.path.join(ROOT, 'assets', 'logo', f'Vintage Global Defense Poster Icon {n}x{n}.png')
                for n in (256, 64, 32)]
NAME = 'Global Cataclysm 1972'


def make_icon():
    """A Windows .ico (256, 64, 48, 32 and 16 px) from the poster icons."""
    from PIL import Image
    big = Image.open(ICON_SOURCES[0]).convert('RGBA')
    big.save(ICON, sizes=[(256, 256), (64, 64), (48, 48), (32, 32), (16, 16)])


def pythonw():
    """pythonw.exe beside the running interpreter (the real file, not the Windows Store alias)."""
    here = os.path.dirname(os.path.realpath(sys.executable))
    exe = os.path.join(here, 'pythonw.exe')
    return exe if os.path.exists(exe) else sys.executable


def shell_folder(name):
    out = subprocess.run(['powershell', '-NoProfile', '-Command', f"[Environment]::GetFolderPath('{name}')"],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def make_link(path):
    script = os.path.join(ROOT, 'tools', 'launcher.py')
    ps = (f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{path}'); "
          f"$s.TargetPath = '{pythonw()}'; $s.Arguments = '\"{script}\"'; "
          f"$s.WorkingDirectory = '{ROOT}'; $s.IconLocation = '{ICON},0'; "
          f"$s.Description = 'Global Cataclysm: 1972'; $s.Save()")
    subprocess.run(['powershell', '-NoProfile', '-Command', ps], check=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-start-menu', action='store_true', help='only the desktop shortcut')
    args = parser.parse_args(argv)
    if not os.path.exists(ICON):
        make_icon()
    places = [shell_folder('Desktop')]
    if not args.no_start_menu:
        places.append(shell_folder('Programs'))
    for folder in places:
        link = os.path.join(folder, NAME + '.lnk')
        make_link(link)
        print('made', link)
    return 0


if __name__ == '__main__':
    sys.exit(main())
