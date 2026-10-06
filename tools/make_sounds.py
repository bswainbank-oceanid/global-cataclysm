"""
Generates the unit sound effects -- a move and an attack sound for each unit type (a Transport never rolls,
so it has no attack sound) -- as short 16-bit mono WAV files in assets/sounds/units, synthesised from
shaped noise, tones, sweeps and echoes (numpy/scipy; no recordings, so no licences). They are
placeholders in a printed-poster spirit, not cinema: any file can be replaced by a real recording under
the same name. Each unit type names its sounds in the unit set (move_sound / attack_sound).

    python tools/make_sounds.py [--only infantry_move,...] [--seed 7]
"""
import argparse
import os
import sys
import wave

import numpy as np
from scipy import signal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'assets', 'sounds', 'units')
RATE = 44100
TARGET_RMS = 0.16   # every sound comes out about as loud as the others
PEAK = 0.89         # ...and never clips (-1 dBFS)
rng = np.random.default_rng(7)


# ---- building blocks -----------------------------------------------------------------------------

def t(dur):
    return np.arange(int(dur * RATE)) / RATE


def noise(dur):
    return rng.uniform(-1, 1, int(dur * RATE))


def silence(dur):
    return np.zeros(int(dur * RATE))


def env(n, attack=0.005, release=0.1, hold=None):
    """A linear attack, an optional hold, then an exponential release, over `n` samples."""
    a = max(1, int(attack * RATE))
    out = np.ones(n)
    out[:a] = np.linspace(0, 1, a)
    start = a + int((hold or 0) * RATE)
    if start < n:
        k = np.arange(n - start) / RATE
        out[start:] = np.exp(-k / max(release, 1e-4))
    return out


def fade(x, fin=0.02, fout=0.08):
    x = x.copy()
    i, o = int(fin * RATE), int(fout * RATE)
    if i:
        x[:i] *= np.linspace(0, 1, i)
    if o:
        x[-o:] *= np.linspace(1, 0, o)
    return x


def band(x, lo, hi, order=3):
    sos = signal.butter(order, [lo, hi], btype='band', fs=RATE, output='sos')
    return signal.sosfilt(sos, x)


def low(x, hz, order=3):
    return signal.sosfilt(signal.butter(order, hz, btype='low', fs=RATE, output='sos'), x)


def high(x, hz, order=3):
    return signal.sosfilt(signal.butter(order, hz, btype='high', fs=RATE, output='sos'), x)


def saw(freq, dur):
    """A sawtooth whose frequency may sweep (`freq` a number or an array as long as the sound)."""
    f = np.broadcast_to(np.asarray(freq, dtype=float), t(dur).shape)
    phase = np.cumsum(f) / RATE
    return 2 * (phase % 1.0) - 1


def sine(freq, dur):
    f = np.broadcast_to(np.asarray(freq, dtype=float), t(dur).shape)
    return np.sin(2 * np.pi * np.cumsum(f) / RATE)


def place(total, parts):
    """Mixes `parts` [(start seconds, samples), ...] into one sound of `total` seconds."""
    out = np.zeros(int(total * RATE))
    for start, x in parts:
        i = max(0, int(start * RATE))
        j = min(len(out), i + len(x))
        out[i:j] += x[:j - i]
    return out


def echo(x, delay=0.12, decay=0.35, repeats=3):
    out = np.concatenate([x, np.zeros(int(delay * RATE * repeats))])
    for k in range(1, repeats + 1):
        i = int(delay * RATE * k)
        out[i:i + len(x)] += x * decay ** k
    return out


def boom(dur=0.9, low_hz=55, crack=0.6):
    """A heavy gun or explosion: a sharp crack over a falling sub-bass thump and a long rumble."""
    n = int(dur * RATE)
    body = low(noise(dur), 300) * env(n, 0.002, dur / 4)
    thump = sine(np.linspace(low_hz * 2.2, low_hz * 0.6, n), dur) * env(n, 0.001, dur / 5)
    snap = band(noise(dur), 1200, 5000) * env(n, 0.0005, 0.012) * crack * 0.4
    return body * 1.2 + thump * 1.4 + snap


def crack(dur=0.18, lo=900, hi=5000, tail=0.03):
    """One rifle or machine-gun shot."""
    n = int(dur * RATE)
    return band(noise(dur), lo, hi) * env(n, 0.0005, tail) + low(noise(dur), 400) * env(n, 0.001, tail * 2) * 0.6


def thud(dur=0.16, hz=160):
    """A boot on the ground."""
    n = int(dur * RATE)
    return low(noise(dur), hz) * env(n, 0.002, 0.035) + band(noise(dur), 1500, 4000) * env(n, 0.001, 0.012) * 0.25


# ---- the sounds ----------------------------------------------------------------------------------

def infantry_move():
    steps = [(i * 0.24 + rng.uniform(-0.01, 0.01), thud(hz=rng.uniform(140, 190)) * (1 if i % 2 else 0.8))
             for i in range(5)]
    return place(1.25, steps)


def infantry_attack():
    shots = [(i * 0.075 + rng.uniform(0, 0.02), crack(0.3, 700, 4500, 0.05) * rng.uniform(0.7, 1)) for i in range(4)]
    return echo(place(0.65, shots), 0.11, 0.25, 2)


def mech_move():
    d = 1.1
    rpm = 62 + 6 * np.sin(2 * np.pi * 1.3 * t(d))
    engine = low(saw(rpm, d) + 0.5 * saw(rpm * 2.01, d), 600)
    road = low(noise(d), 250) * 0.5
    return fade(engine * 0.7 + road, 0.12, 0.25)


def mech_attack():
    burst = [(i * 0.065, crack(0.12, 1200, 6000, 0.018) * rng.uniform(0.75, 1)) for i in range(9)]
    return echo(place(0.75, burst), 0.09, 0.2, 2)


def armor_move():
    d = 1.2
    diesel = low(saw(38 + 3 * np.sin(2 * np.pi * 0.8 * t(d)), d), 300) * 0.9
    clanks = place(d, [(i / 11 + rng.uniform(0, 0.01), band(noise(0.05), 2500, 6000) * env(int(0.05 * RATE), 0.0005, 0.008))
                       for i in range(13)])
    return fade(diesel + clanks * 0.35 + low(noise(d), 180) * 0.4, 0.1, 0.3)


def armor_attack():
    return echo(boom(1.1, 48, 0.9), 0.17, 0.3, 2)


def fighter_move():
    d = 1.3
    n = int(d * RATE)
    centre = np.linspace(3200, 900, n)  # the doppler fall as it screams past
    roar = np.zeros(n)
    x = noise(d)
    for k in range(0, n, 2048):  # the band follows the pass in short slices
        c = centre[min(k + 1024, n - 1)]
        roar[k:k + 2048] = band(x[k:k + 2048 + 512], c * 0.6, min(c * 1.6, RATE / 2 - 100))[:len(roar[k:k + 2048])]
    whine = sine(np.linspace(1700, 650, n), d) * 0.15
    swell = np.interp(t(d), [0, 0.55, 0.75, d], [0.05, 1, 0.8, 0])
    return (roar + whine) * swell


def fighter_attack():
    burst = [(i * 0.042, crack(0.09, 1800, 8000, 0.012)) for i in range(12)]
    return place(0.75, burst) + high(noise(0.75), 3000) * env(int(0.75 * RATE), 0.01, 0.2) * 0.15


def bomber_move():
    d = 1.4
    prop = 0.55 + 0.45 * np.sign(np.sin(2 * np.pi * 21 * t(d)))  # the propellers' beat
    engine = low(saw(86, d) + 0.6 * saw(129.5, d), 700) * prop
    return fade(engine * 0.8 + low(noise(d), 400) * 0.3, 0.25, 0.4)


def bomber_attack():
    whistle = sine(np.linspace(2100, 450, int(0.75 * RATE)), 0.75) * np.linspace(0.15, 0.6, int(0.75 * RATE))
    return place(1.8, [(0, whistle), (0.72, boom(1.05, 42, 1.0))])


def carrier_move():
    d = 1.3
    horn = low(saw(98, d) + 0.8 * saw(123.5, d), 900)
    return fade(horn * env(int(d * RATE), 0.08, 0.5, hold=0.6), 0.05, 0.25)


def carrier_attack():
    d = 1.1
    n = int(d * RATE)
    hiss = band(noise(d), 1800, 7000) * np.interp(t(d), [0, 0.15, 0.45, d], [0, 1, 0.3, 0]) * 0.6  # the steam catapult
    jet = band(noise(d), 300, 2500) * np.interp(t(d), [0, 0.3, 0.6, d], [0, 0.2, 1, 0])
    clunk = place(d, [(0.33, low(noise(0.2), 200) * env(int(0.2 * RATE), 0.001, 0.05) * 2.0)])
    return hiss * 0.6 + jet + clunk


def sub_move():
    ping = sine(1150, 0.9) * env(int(0.9 * RATE), 0.002, 0.18)
    return echo(ping, 0.32, 0.4, 3)


def sub_attack():
    d = 1.2
    n = int(d * RATE)
    thunk = low(noise(0.3), 160) * env(int(0.3 * RATE), 0.001, 0.06) * 2
    bubbles = low(noise(d), 900) * (0.5 + 0.5 * np.abs(np.sin(2 * np.pi * 9 * t(d) + rng.uniform(0, 3, n) * 0.2)))
    run = bubbles * np.interp(t(d), [0, 0.1, 0.6, d], [0, 1, 0.6, 0])
    return place(d, [(0, thunk), (0.05, run * 0.8)])


def cruiser_move():
    d = 1.2
    engine = low(saw(52, d) + 0.5 * saw(78.3, d), 400)
    wash = low(noise(d), 1200) * (0.6 + 0.4 * np.sin(2 * np.pi * 0.9 * t(d)))
    return fade(engine * 0.6 + wash * 0.7, 0.15, 0.35)


def cruiser_attack():
    return place(1.7, [(0, boom(1.0, 60, 0.8)), (0.32, boom(1.0, 55, 0.7) * 0.8), (0.6, boom(1.0, 65, 0.7) * 0.7)])


def transport_move():
    d = 0.9
    horn = low(saw(78, d) + 0.6 * saw(117, d), 700)
    return fade(horn * env(int(d * RATE), 0.06, 0.35, hold=0.35), 0.04, 0.2)


SOUNDS = {
    'infantry_move': infantry_move, 'infantry_attack': infantry_attack,
    'mech_infantry_move': mech_move, 'mech_infantry_attack': mech_attack,
    'armor_move': armor_move, 'armor_attack': armor_attack,
    'fighter_move': fighter_move, 'fighter_attack': fighter_attack,
    'bomber_move': bomber_move, 'bomber_attack': bomber_attack,
    'carrier_move': carrier_move, 'carrier_attack': carrier_attack,
    'submarine_move': sub_move, 'submarine_attack': sub_attack,
    'cruiser_move': cruiser_move, 'cruiser_attack': cruiser_attack,
    'transport_move': transport_move,
}


def normalised(x):
    x = x - np.mean(x)
    rms = np.sqrt(np.mean(x ** 2)) or 1.0
    x = x * (TARGET_RMS / rms)
    peak = np.max(np.abs(x)) or 1.0
    if peak > PEAK:
        x = x * (PEAK / peak)
    return fade(x, 0.002, 0.03)


def write(name, x):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, name + '.wav')
    data = (np.clip(x, -1, 1) * 32767).astype('<i2')
    with wave.open(path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(data.tobytes())
    return path


def main(argv=None):
    global rng
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--only', default='', help='comma-separated names (default: all)')
    parser.add_argument('--seed', type=int, default=7)
    args = parser.parse_args(argv)
    names = [n for n in args.only.split(',') if n] or list(SOUNDS)
    for name in names:
        rng = np.random.default_rng(args.seed + sorted(SOUNDS).index(name))  # (each sound its own, repeatable)
        x = normalised(SOUNDS[name]())
        path = write(name, x)
        print(f'{name:22s} {len(x) / RATE:4.2f}s  {os.path.relpath(path, ROOT)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
