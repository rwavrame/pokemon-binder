#!/usr/bin/env python3
"""Fail fast if a store will not talk to us from this machine.

The sweep has only ever run from a home IP. On a cloud runner a shop may answer a
bot-check page instead of JSON, and a sweep that quietly indexes nothing is worse than
no sweep at all -- it would overwrite a good index with an empty one. So prove every
store is reachable BEFORE spending hours fetching, and make the job fail loudly if not.
"""
import json, re, subprocess, sys, pathlib, time

HTML = pathlib.Path(__file__).resolve().parent.parent / 'master-set-binder.html'


def store_list():
    src = HTML.read_text(encoding='utf-8')
    i = src.index('const STORES=[')
    j = src.index('];', i)
    blk = src[i:j]
    out = re.findall(r"id:'([^']+)'\s*,\s*name:\s*(?:'[^']*'|\"[^\"]*\")\s*,\s*host:'([^']+)'", blk)
    declared = len(re.findall(r'\{\s*id:', blk))
    if len(out) != declared:
        sys.exit(f'FAIL: parsed {len(out)} stores but {declared} are declared')
    return out


def probe(host):
    """One cheap request. We only care that it is real JSON with products in it."""
    r = subprocess.run(['curl', '-sS', '-m', '45', '-A', 'Mozilla/5.0',
                        f'https://{host}/products.json?limit=1'],
                       capture_output=True, text=True)
    try:
        d = json.loads(r.stdout)
    except Exception:
        return False, (r.stdout[:60] or r.stderr[:60]).replace('\n', ' ')
    if not isinstance(d.get('products'), list):
        return False, 'no products key'
    return True, f"{len(d['products'])} product(s)"


def main():
    bad = []
    for sid, host in store_list():
        ok, note = probe(host)
        print(f"  {'ok  ' if ok else 'FAIL'}  {sid:<6}{host:<32}{note}", flush=True)
        if not ok:
            bad.append(f'{sid} ({host}): {note}')
        time.sleep(0.5)
    if bad:
        print('\nUnreachable from this runner:')
        for b in bad:
            print('  -', b)
        sys.exit('Refusing to sweep: a partial sweep would overwrite a good index.')
    print('\nAll stores reachable.')


if __name__ == '__main__':
    main()
