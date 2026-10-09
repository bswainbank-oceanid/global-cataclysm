"""
Makes the "Global Cataclysm 1972" shortcuts on the Windows desktop (and in the Start menu): they run
tools/launcher.py with pythonw (no console window), with the game's icon,
assets/logo/global-cataclysm.ico (written here from the poster icons if it's missing). The second,
"Global Cataclysm 1972 (Player 2)", keeps its own saved login (--profile=player2), so two players can
be logged in on this machine at once; its icon has a red "2" (global-cataclysm-player2.ico).

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
ICON_2 = os.path.join(ROOT, 'assets', 'logo', 'global-cataclysm-player2.ico')
FONT = os.path.join(ROOT, 'assets', 'fonts', 'BarlowCondensed-Black.ttf')
ICON_SIZES = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]  # (128: the Windows build's icon wants it)


def make_icon():
    """A Windows .ico (256, 128, 64, 48, 32 and 16 px) from the poster icons."""
    from PIL import Image
    big = Image.open(ICON_SOURCES[0]).convert('RGBA')
    big.save(ICON, sizes=ICON_SIZES)


def make_player2_icon():
    """The poster icon with a red "2" badge in its lower right corner (the style guide's signal red,
    a navy rim, the display face), big enough to read at taskbar size."""
    from PIL import Image, ImageDraw, ImageFont
    im = Image.open(ICON_SOURCES[0]).convert('RGBA').resize((256, 256), Image.LANCZOS)
    draw = ImageDraw.Draw(im)
    box = (138, 138, 252, 252)
    draw.ellipse(box, fill='#071923')
    draw.ellipse((box[0] + 7, box[1] + 7, box[2] - 7, box[3] - 7), fill='#C92A24')
    font = ImageFont.truetype(FONT, 104)
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    draw.text((cx, cy + 3), '2', font=font, fill='#FFFDF8', anchor='mm')
    im.save(ICON_2, sizes=ICON_SIZES)


def pythonw():
    """pythonw.exe beside the running interpreter (the real file, not the Windows Store alias)."""
    here = os.path.dirname(os.path.realpath(sys.executable))
    exe = os.path.join(here, 'pythonw.exe')
    return exe if os.path.exists(exe) else sys.executable


def shell_folder(name):
    out = subprocess.run(['powershell', '-NoProfile', '-Command', f"[Environment]::GetFolderPath('{name}')"],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def make_link(path, icon=ICON, extra='', description='Global Cataclysm: 1972'):
    script = os.path.join(ROOT, 'tools', 'launcher.py')
    arguments = f'\"{script}\"' + (f' {extra}' if extra else '')
    ps = (f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{path}'); "
          f"$s.TargetPath = '{pythonw()}'; $s.Arguments = '{arguments}'; "
          f"$s.WorkingDirectory = '{ROOT}'; $s.IconLocation = '{icon},0'; "
          f"$s.Description = '{description}'; $s.Save()")
    subprocess.run(['powershell', '-NoProfile', '-Command', ps], check=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-start-menu', action='store_true', help='only the desktop shortcut')
    args = parser.parse_args(argv)
    if not os.path.exists(ICON):
        make_icon()
    if not os.path.exists(ICON_2):
        make_player2_icon()
    places = [shell_folder('Desktop')]
    if not args.no_start_menu:
        places.append(shell_folder('Programs'))
    for folder in places:
        link = os.path.join(folder, NAME + '.lnk')
        make_link(link)
        print('made', link)
        link = os.path.join(folder, NAME + ' (Player 2).lnk')
        make_link(link, ICON_2, '--profile player2', 'Global Cataclysm: 1972 -- a second player on this machine')
        print('made', link)
    return 0


if __name__ == '__main__':
    sys.exit(main())
