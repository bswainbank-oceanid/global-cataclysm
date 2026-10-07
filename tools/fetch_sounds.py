"""
Builds the game's sounds from the Freesound recordings chosen for them: assets/sounds/sources.json names, for
each sound (the files the unit set's move_sound / attack_sound point at, and the interface's own, named ui_...),
a Freesound sound and how to cut it. This downloads each one's preview (cached in server_data/sound_audition/previews), takes the right
stretch of it, mixes it to mono, fades it, matches its loudness to the others and writes
assets/sounds/units/<name>.wav (assets/sounds/ui/<name>.wav for a ui_ sound) -- then assets/sounds/CREDITS.md, every source with its author and licence.

    python tools/fetch_sounds.py [--only armor_attack,...]

sources.json, per sound:
    {"freesound_id": 168707, "name": ..., "author": ..., "url": ..., "license": ...,   (from the search)
     "length": 1.5,          seconds kept (default: 1.6 for a move, 1.4 for an attack, 0.3 for a ui_ sound)
     "start": 2.0,           where to start (default: an attack from its onset; a move at its loudest stretch;
                             a ui_ sound from the recording's start)
     "gain_db": 0}           louder or softer than the rest
Sounds not in sources.json keep whatever file they have (tools/make_sounds.py's, say).
"""
import argparse
import json
import os
import sys
import urllib.request
import wave

import numpy as np
from scipy import signal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES = os.path.join(ROOT, 'assets', 'sounds', 'sources.json')
OUT = os.path.join(ROOT, 'assets', 'sounds', 'units')
UI_OUT = os.path.join(ROOT, 'assets', 'sounds', 'ui')
CREDITS = os.path.join(ROOT, 'assets', 'sounds', 'CREDITS.md')
CACHE = os.path.join(ROOT, 'server_data', 'sound_audition', 'previews')
RATE = 44100
TARGET_RMS = {'move': 0.13, 'attack': 0.16, 'ui': 0.10}
PEAK = 0.89


def preview(sound_id):
    """The cached preview of a Freesound sound (downloaded the first time; previews need no login)."""
    path = os.path.join(CACHE, f'{sound_id}.mp3')
    if not os.path.exists(path):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from sound_search import api_key  # (the same key the search uses)
        url = f'https://freesound.org/apiv2/sounds/{sound_id}/?fields=previews&token={api_key()}'
        with urllib.request.urlopen(url, timeout=30) as r:
            mp3 = json.load(r)['previews']['preview-hq-mp3']
        os.makedirs(CACHE, exist_ok=True)
        with urllib.request.urlopen(mp3, timeout=60) as r, open(path, 'wb') as f:
            f.write(r.read())
    return path


def load(path):
    import soundfile  # (decodes the MP3 previews: pip install soundfile)
    x, rate = soundfile.read(path, always_2d=True)
    x = x.mean(axis=1)
    if rate != RATE:
        g = np.gcd(rate, RATE)
        x = signal.resample_poly(x, RATE // g, rate // g)
    return x


def onset(x, threshold_db=-24):
    """Where the sound starts: the first sample within `threshold_db` of the peak, a little before it."""
    peak = np.max(np.abs(x)) or 1.0
    above = np.nonzero(np.abs(x) >= peak * 10 ** (threshold_db / 20))[0]
    return max(0, int(above[0]) - int(0.015 * RATE)) if len(above) else 0


def loudest(x, n):
    """The start of the loudest `n`-sample stretch (a move's engine at full song)."""
    if len(x) <= n:
        return 0
    energy = np.convolve(x ** 2, np.ones(int(0.05 * RATE)), 'same')
    window = np.convolve(energy, np.ones(n), 'valid')
    return int(np.argmax(window))


def cut(x, kind, spec):
    n = int(float(spec.get('length', {'move': 1.6, 'attack': 1.4, 'ui': 0.3}[kind])) * RATE)
    if 'start' in spec:
        i = int(float(spec['start']) * RATE)
    elif kind == 'ui':
        i = 0
    elif kind == 'attack':
        i = onset(x)
    else:
        i = loudest(x, n)
    y = x[i:i + n].copy()
    fin = int((0.05 if kind == 'move' and 'start' not in spec and i > 0 else 0.004) * RATE)
    fout = int({'move': 0.25, 'attack': 0.18, 'ui': 0.06}[kind] * RATE)
    if fin:
        y[:fin] *= np.linspace(0, 1, fin)
    fout = min(fout, len(y))
    y[-fout:] *= np.linspace(1, 0, fout)
    return y


def normalised(y, kind, gain_db=0.0):
    y = y - np.mean(y)
    rms = np.sqrt(np.mean(y ** 2)) or 1.0
    y = y * (TARGET_RMS[kind] / rms) * 10 ** (gain_db / 20)
    peak = np.max(np.abs(y)) or 1.0
    return y * (PEAK / peak) if peak > PEAK else y


def write(path, y):
    data = (np.clip(y, -1, 1) * 32767).astype('<i2')
    with wave.open(path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(data.tobytes())


def credits(sources):
    lines = ['# Sound credits', '',
             'The unit and interface sounds are cut from these recordings on [Freesound](https://freesound.org), all under',
             'Creative Commons 0 (public domain): no attribution is required, but they are credited here with thanks.',
             'Built by tools/fetch_sounds.py from assets/sounds/sources.json.', '',
             '| Sound | Recording | Author | Licence |', '|---|---|---|---|']
    for name, s in sources.items():
        licence = 'CC0' if 'zero' in str(s.get('license', '')) else str(s.get('license', ''))
        lines.append(f"| {name} | [{s.get('name', s['freesound_id'])}]({s.get('url', '')}) | {s.get('author', '')} | {licence} |")
    with open(CREDITS, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--only', default='')
    args = parser.parse_args(argv)
    sources = json.load(open(SOURCES, encoding='utf-8'))
    only = [n for n in args.only.split(',') if n]
    os.makedirs(OUT, exist_ok=True)
    for name, spec in sources.items():
        if only and name not in only:
            continue
        kind = 'ui' if name.startswith('ui_') else 'attack' if name.endswith('_attack') else 'move'
        y = normalised(cut(load(preview(spec['freesound_id'])), kind, spec), kind, float(spec.get('gain_db', 0)))
        folder = UI_OUT if kind == 'ui' else OUT
        os.makedirs(folder, exist_ok=True)
        write(os.path.join(folder, name + '.wav'), y)
        print(f"{name:22s} {len(y) / RATE:4.2f}s  <- {spec['freesound_id']} {str(spec.get('name', ''))[:40]}"
              .encode('ascii', 'replace').decode())
    credits(sources)
    return 0


if __name__ == '__main__':
    sys.exit(main())
