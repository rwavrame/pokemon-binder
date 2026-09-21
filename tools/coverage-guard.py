#!/usr/bin/env python3
"""Refuse to publish a sweep that looks like a partial fetch.

Shops add and remove singles constantly, so per-store counts drift either way. A
COLLAPSE is different: it means a feed broke and the sweep captured a fraction of the
store. Everything Games sat at 11 handles for weeks exactly that way. Compare the new
index against the committed one and exit non-zero if anything looks collapsed.
"""
import json, sys, pathlib, collections, subprocess

DROP = 0.20          # a store may lose a fifth of its handles before we call it broken
HERE = pathlib.Path(__file__).resolve().parent


def per_store(idx):
    c = collections.Counter()
    for _cid, lst in idx.items():
        for sid, _h in lst:
            c[sid] += 1
    return c


def main():
    new = json.loads((HERE / 'handles.json').read_text())
    old_raw = subprocess.run(['git', 'show', 'HEAD:tools/handles.json'],
                             capture_output=True, text=True, cwd=HERE.parent)
    if old_raw.returncode != 0:
        print('No committed index to compare against; accepting this one.')
        return
    old = json.loads(old_raw.stdout)

    n, o = per_store(new), per_store(old)
    print(f"{'store':<7}{'new':>8}{'old':>8}{'change':>9}")
    problems = []
    for sid in sorted(set(n) | set(o), key=lambda s: -o.get(s, 0)):
        nv, ov = n.get(sid, 0), o.get(sid, 0)
        pct = (nv - ov) / ov * 100 if ov else 0.0
        flag = ''
        if ov and nv < ov * (1 - DROP):
            flag = '  <-- COLLAPSED'
            problems.append(f'{sid}: {ov} -> {nv} ({pct:.0f}%)')
        print(f'{sid:<7}{nv:>8}{ov:>8}{pct:>8.0f}%{flag}')

    if len(new) < len(old):
        problems.append(f'cards with a handle fell: {len(old)} -> {len(new)}')
    print(f'\ncards with >=1 handle: {len(new)} (was {len(old)})')

    if problems:
        print('\nRefusing to publish:')
        for p in problems:
            print('  -', p)
        sys.exit(1)
    print('Coverage guard passed.')


if __name__ == '__main__':
    main()
