"""
Finds Freesound candidates for the unit sounds and builds a page to audition them: for each sound (a unit
type's move or attack, tools/make_sounds.py's names) it runs a few searches, CC0 only and of a sensible
length, ranks what they find by downloads and rating, keeps the best few, downloads their previews and
writes server_data/sound_audition/index.html -- open it in a browser, play them, and pick.

The Freesound API key is read from server_data/freesound_key.txt (the 40-character key; the rest of what
the credentials page shows may be in there too) or FREESOUND_API_KEY. server_data is git-ignored.

    python tools/sound_search.py [--per 4] [--only armor_attack,...] [--licence cc0|cc0+by]
"""
import argparse
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'server_data', 'sound_audition')
KEY_FILE = os.path.join(ROOT, 'server_data', 'freesound_key.txt')
API = 'https://freesound.org/apiv2/search/text/'
FIELDS = 'id,name,username,license,duration,avg_rating,num_ratings,num_downloads,previews,url,tags'

# sound name -> (what it should be, the searches, (shortest, longest) seconds to look at)
WANTED = {
    'infantry_move': ('Infantry moving: marching boots', ['marching soldiers', 'army march footsteps', 'boots marching gravel'], (0.8, 12)),
    'infantry_attack': ('Infantry attacking: rifle fire', ['rifle shot', 'rifle volley', 'm1 garand', 'gunshot rifle'], (0.2, 6)),
    'mech_infantry_move': ('Mechanized Infantry moving: an armored car / APC engine',
                           ['military vehicle engine', 'truck engine', 'diesel engine', 'jeep driving'], (0.8, 15)),
    'mech_infantry_attack': ('Mechanized Infantry attacking: a machine-gun burst', ['machine gun burst', 'machine gun'], (0.2, 6)),
    'armor_move': ('Armor moving: tank tracks and engine', ['tank tracks', 'tank engine', 'tank moving'], (0.8, 12)),
    'armor_attack': ('Armor attacking: a tank gun', ['tank firing', 'tank cannon', 'artillery shot'], (0.2, 6)),
    'fighter_move': ('Fighter moving: a jet flying past', ['jet flyby', 'fighter jet', 'jet plane passing'], (0.8, 15)),
    'fighter_attack': ('Fighter attacking: a missile launch', ['missile launch', 'rocket launch', 'missile fire', 'rocket whoosh'], (0.3, 8)),
    'bomber_move': ('Bomber moving: a heavy propeller plane', ['propeller aircraft', 'propeller plane', 'ww2 plane', 'piston engine airplane'], (0.8, 15)),
    'bomber_attack': ('Bomber attacking: several distant explosions (a bombing run)',
                      ['distant explosions', 'multiple explosions', 'bombing raid', 'distant bombing', 'carpet bombing'], (0.8, 15)),
    'carrier_move': ("Aircraft Carrier moving: a big ship's horn", ['ship horn', 'foghorn', 'big ship horn'], (0.8, 12)),
    'carrier_attack': ('Aircraft Carrier attacking: a jet launched off the deck', ['jet takeoff', 'jet engine', 'airplane take off', 'jet fly by'], (0.4, 15)),
    'submarine_move': ('Submarine moving: a sonar ping', ['sonar ping', 'sonar', 'submarine'], (0.4, 12)),
    'submarine_attack': ('Submarine attacking: a torpedo launch / underwater blast', ['torpedo launch', 'torpedo', 'underwater explosion'], (0.3, 8)),
    'cruiser_move': ("Cruiser moving: a warship's engine and wake", ['ship engine room', 'boat engine', 'ship engine', 'motorboat passing'], (0.8, 15)),
    'cruiser_attack': ('Cruiser attacking: a naval gun salvo', ['cannon fire', 'cannon shot', 'artillery', 'naval gun'], (0.2, 6)),
    'transport_move': ("Transport moving: a cargo ship's horn", ['boat horn', 'ship horn', 'ferry horn'], (0.5, 12)),
}

LICENCES = {'cc0': 'license:"Creative Commons 0"',
            'cc0+by': '(license:"Creative Commons 0" OR license:"Attribution")'}


def api_key():
    key = os.environ.get('FREESOUND_API_KEY', '').strip()
    if not key and os.path.exists(KEY_FILE):
        raw = open(KEY_FILE, encoding='utf-8', errors='replace').read()
        found = [t for t in re.findall(r'[A-Za-z0-9]+', raw) if len(t) == 40]
        key = found[0] if found else raw.strip()
    if not key:
        sys.exit(f'no Freesound API key: put it in {KEY_FILE} or FREESOUND_API_KEY')
    return key


def search(key, query, licence, shortest, longest, page_size=8):
    params = {'query': query, 'filter': f'{LICENCES[licence]} duration:[{shortest} TO {longest}]',
              'fields': FIELDS, 'page_size': page_size, 'sort': 'score', 'token': key}
    with urllib.request.urlopen(API + '?' + urllib.parse.urlencode(params), timeout=30) as r:
        return json.load(r)['results']


def score(s):
    """Popular and well rated first (a well-liked sound is usually a usable one)."""
    rating = s.get('avg_rating') or 0
    return (s.get('num_downloads') or 0) * (0.6 + 0.4 * rating / 5)


def candidates(key, name, licence, per):
    """The best few, taking each search's most relevant (well-liked first among equals) in turn, so every
    phrasing is represented."""
    _, queries, (shortest, longest) = WANTED[name]
    lists = []
    for q in queries:
        found = search(key, q, licence, shortest, longest)
        found = [s for s in found if (s.get('num_ratings') or 0) == 0 or (s.get('avg_rating') or 0) >= 2.5]
        lists.append([dict(s, found_by=q) for s in found])
    out, seen = [], set()
    while len(out) < per and any(lists):
        for found in lists:
            while found and found[0]['id'] in seen:
                found.pop(0)
            if found and len(out) < per:
                s = found.pop(0)
                seen.add(s['id'])
                out.append(s)
    return out


def download(url, path):
    if not os.path.exists(path):
        with urllib.request.urlopen(url, timeout=60) as r, open(path, 'wb') as f:
            f.write(r.read())


def page(results):
    rows = []
    for name, found in results.items():
        what = WANTED[name][0]
        cards = []
        for i, s in enumerate(found):
            letter = 'ABCDEFGH'[i]
            cards.append(f'''
      <div class="card">
        <div class="pick">{letter}</div>
        <div class="title"><a href="{html.escape(s['url'])}" target="_blank">{html.escape(s['name'])}</a></div>
        <div class="meta">by {html.escape(s['username'])} &middot; {s['duration']:.1f}s &middot; {s['num_downloads']:,} downloads
          &middot; rated {s.get('avg_rating') or 0:.1f} &middot; {'CC0' if 'zero' in s['license'] else 'CC-BY'}</div>
        <audio controls preload="none" src="previews/{s['id']}.mp3"></audio>
      </div>''')
        rows.append(f'''
  <section>
    <h2>{html.escape(name)}</h2>
    <p class="what">{html.escape(what)}</p>
    <div class="cards">{''.join(cards) or '<p class="none">No candidates found.</p>'}</div>
  </section>''')
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Unit Sound Audition</title>
<style>
  :root {{ --navy:#0B2433; --red:#C92A24; --paper:#E9E1D3; --cream:#F4EBDD; --gray:#6B747A; }}
  body {{ margin:0; background:var(--paper); color:var(--navy); font:15px/1.45 Inter, Helvetica, Arial, sans-serif; }}
  header {{ background:var(--navy); color:var(--cream); padding:18px 24px; }}
  header h1 {{ margin:0; font:900 28px "Barlow Condensed", "Arial Narrow", sans-serif; letter-spacing:.03em; text-transform:uppercase; }}
  header p {{ margin:6px 0 0; color:#A9B4B8; }}
  main {{ max-width:1100px; margin:0 auto; padding:12px 16px 40px; }}
  section {{ border-bottom:3px solid var(--navy); padding:14px 0; }}
  h2 {{ margin:0; font:800 20px "Barlow Condensed", "Arial Narrow", sans-serif; text-transform:uppercase; color:var(--red); }}
  .what {{ margin:2px 0 10px; color:var(--gray); }}
  .cards {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(250px, 1fr)); gap:10px; }}
  .card {{ background:#FFFDF8; border:2px solid var(--navy); border-radius:4px; padding:10px; position:relative; }}
  .pick {{ position:absolute; top:8px; right:10px; font:900 22px "Barlow Condensed", sans-serif; color:var(--red); }}
  .title {{ font-weight:600; padding-right:24px; overflow-wrap:anywhere; }}
  .title a {{ color:var(--navy); }}
  .meta {{ font-size:12px; color:var(--gray); margin:4px 0 8px; }}
  audio {{ width:100%; }}
</style></head><body>
<header><h1>Unit Sound Audition</h1>
<p>Play each candidate and tell Claude your pick per sound (e.g. "armor_attack B"), or "none" to search again.
Picks are trimmed to the best second or two and matched in loudness, so judge the character, not the length.</p></header>
<main>{''.join(rows)}
</main></body></html>
'''


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--per', type=int, default=6, help='candidates per sound')
    parser.add_argument('--only', default='', help='comma-separated sound names (default: all)')
    parser.add_argument('--licence', choices=sorted(LICENCES), default='cc0')
    args = parser.parse_args(argv)
    key = api_key()
    names = [n for n in args.only.split(',') if n] or list(WANTED)
    os.makedirs(os.path.join(OUT, 'previews'), exist_ok=True)
    data_path = os.path.join(OUT, 'candidates.json')
    results = json.load(open(data_path, encoding='utf-8')) if os.path.exists(data_path) else {}
    for name in names:
        found = candidates(key, name, args.licence, args.per)
        for s in found:
            download(s['previews']['preview-hq-mp3'], os.path.join(OUT, 'previews', f"{s['id']}.mp3"))
        results[name] = found
        line = f'{name:22s} {len(found)} candidates: ' + ', '.join(f"{s['id']} {s['name'][:24]!r}" for s in found)
        print(line.encode('ascii', 'replace').decode())  # (a Windows console can't show every title)
    results = {n: results[n] for n in WANTED if n in results}
    with open(data_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    with open(os.path.join(OUT, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(page(results))
    print('audition page:', os.path.join(OUT, 'index.html'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
