#!/usr/bin/env python3
"""Embed tools/handles.json into master-set-binder.html as `const HANDLES = {...}`.

Kept separate from the discovery sweep so the index can be rebuilt and re-injected
independently, and so a failed sweep can never half-write the guide.
"""
import json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = ROOT / 'master-set-binder.html'
IDX  = ROOT / 'tools' / 'handles.json'

def main():
    idx = json.loads(IDX.read_text(encoding='utf-8'))
    src = HTML.read_text(encoding='utf-8')
    m = re.search(r'const HANDLES = \{.*?\};\n', src, re.S)
    if not m:
        sys.exit('could not find the HANDLES declaration in the HTML')
    blob = 'const HANDLES = ' + json.dumps(idx, separators=(',', ':')) + ';\n'
    src = src[:m.start()] + blob + src[m.end():]
    HTML.write_text(src, encoding='utf-8')
    cards = len(idx)
    handles = sum(len(v) for v in idx.values())
    print(f'injected {handles} handles for {cards} cards '
          f'({len(blob)/1024:.0f} KB); HTML now {HTML.stat().st_size/1024:.0f} KB')
    for copy in (ROOT / 'docs' / 'index.html',
                 ROOT / 'docs' / 'master-set-binder.html'):
        copy.parent.mkdir(exist_ok=True)
        copy.write_text(src, encoding='utf-8')
    print('staged both docs/ copies (GitHub Pages serves from /docs)')

if __name__ == '__main__':
    main()
