"""
The build number, from git: how many commits the checkout has and its short commit id, with a "+" when
tracked files have uncommitted changes -- "Build 294 (af8b2d1)". The server reports its own in "hello";
tools/sync_client_data.py writes the client's into client/data/build.json, so the main menu can show
both and warn when they differ.
"""
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_cache = None


def _git(*args):
    out = subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True, timeout=10)
    if out.returncode != 0:
        raise OSError(out.stderr.strip())
    return out.stdout.strip()


def build_info(refresh=False):
    """{build: commit count, commit: short id (+ when changed since), label: "Build 294 (af8b2d1)"};
    "unknown" when git can't say (no git, not a checkout)."""
    global _cache
    if _cache is not None and not refresh:
        return _cache
    try:
        count = int(_git('rev-list', '--count', 'HEAD'))
        commit = _git('rev-parse', '--short', 'HEAD')
        if _git('status', '--porcelain', '--untracked-files=no'):
            commit += '+'
        _cache = {'build': count, 'commit': commit, 'label': f'Build {count} ({commit})'}
    except (OSError, ValueError, subprocess.SubprocessError):
        _cache = {'build': None, 'commit': None, 'label': 'Build unknown'}
    return _cache
