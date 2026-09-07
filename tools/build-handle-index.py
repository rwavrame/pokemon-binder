#!/usr/bin/env python3
"""Build the store product-handle index baked into master-set-binder.html.

Why this exists: Shopify's SEARCH endpoints send no CORS header, but
/products/<handle>.js does. So the binder can read live variant-level stock
straight from the browser with no server at all — provided it already knows each
card's product handle at each store. Search is therefore needed exactly once, and
that is this script's job. It runs here, never on a host.

It downloads each store's Pokemon catalogue through the public /products.json
feed and matches titles to the guide's cards using the same rules the page uses
at match time (number + name tokens, zero-padded numbers, sibling annotations).

Usage:  python3 tools/build-handle-index.py            # writes tools/handles.json
        python3 tools/build-handle-index.py --inject   # also embeds it in the HTML
"""
import json, re, sys, subprocess, time, collections, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = ROOT / 'master-set-binder.html'
OUT  = ROOT / 'tools' / 'handles.json'

def store_list():
    """The shops to sweep, read from the STORES array in the HTML.

    Kept here as a copy once, with a comment saying the ids had to match. That is the
    same trap that would have skipped Pitch Black: a store added to one list and not
    the other is never swept, and nothing reports it -- the run just quietly does not
    look there. One source of truth instead.
    """
    src = HTML.read_text(encoding='utf-8')
    i = src.index('const STORES=[')
    j = src.index('];', i)
    found = re.findall(r"id:'([^']+)'\s*,\s*name:'[^']*'\s*,\s*host:'([^']+)'", src[i:j])
    # Count the entries independently and insist the two agree. A name written with
    # double quotes (an apostrophe forces it) slips straight past the pattern above,
    # and the store is then never swept -- silently, which is the whole failure mode
    # this function exists to prevent.
    declared = len(re.findall(r"\{\s*id:", src[i:j]))
    if not found or len(found) != declared:
        sys.exit(f'parsed {len(found)} of {declared} STORES entries -- check the HTML '
                 f'uses single-quoted name: values')
    return found


STORES = store_list()

# ---------- fetch -----------------------------------------------------------
# Shopify rate-limits these feeds hard. A first run without pacing got HTTP 429 from
# six of the nine stores and produced 20% coverage; the zeros looked structural but
# were self-inflicted. Pace every request and back off properly on 429.
PACE = 0.6          # seconds between requests to the same store
CACHE = pathlib.Path(__file__).resolve().parent / '.catalogue-cache'

def get(url, tries=6):
    for a in range(tries):
        r = subprocess.run(['curl', '-sS', '-g', '-m', '45', '-w', '\n%{http_code}',
                            '-A', 'Mozilla/5.0', url], capture_output=True, text=True)
        body, _, code = r.stdout.rpartition('\n')
        if code.strip() == '429':
            wait = 10 * (a + 1)
            print(f'      429 — backing off {wait}s', flush=True)
            time.sleep(wait)
            continue
        try:
            return json.loads(body)
        except Exception:
            time.sleep(2 + 2 * a)
    return None

SKIP_COLL = ('japan', 'korean', 'chinese', 'sealed', 'graded', 'playmat', 'plush',
             'booster', 'box', 'bundle', 'tin', 'etb', 'preorder', 'pre-order',
             'supplies', 'sleeve', 'binder', 'deck-box')

def all_collections(host):
    """collections.json caps at 250 per page but DOES paginate, so walk it. Without
    this the big stores' real singles collections are invisible and the heuristic
    lands on things like graded-pokemon or korean-pokemon."""
    out, page = [], 1
    while page <= 20:
        d = get(f'https://{host}/collections.json?limit=250&page={page}')
        cs = (d or {}).get('collections', [])
        if not cs:
            break
        out += cs
        if len(cs) < 250:
            break
        page += 1
        time.sleep(PACE)
    return out

def pokemon_collections(host):
    """Every collection that plausibly holds English Pokemon singles. Unioning these
    is what gets past Shopify's hard 100-page (25,000 product) cap on any one feed."""
    picked = []
    for c in all_collections(host):
        nm = (c['handle'] + ' ' + c.get('title', '')).lower()
        if 'pok' not in nm:
            continue
        if any(w in nm for w in SKIP_COLL):
            continue
        picked.append((c['handle'], c.get('products_count') or 0))
    picked.sort(key=lambda x: -x[1])
    return picked[:40]

def pokemon_collection(host):
    d = get(f'https://{host}/collections.json?limit=250')
    cols = (d or {}).get('collections', [])
    def pick(pred):
        best = None
        for c in cols:
            nm = (c['handle'] + ' ' + c['title']).lower()
            if 'japan' in nm or 'sealed' in nm or 'preorder' in nm:
                continue
            if not pred(nm):
                continue
            cnt = c.get('products_count') or 0
            if not best or cnt > best[1]:
                best = (c['handle'], cnt)
        return best
    return (pick(lambda n: 'pok' in n and 'single' in n)
            or pick(lambda n: 'pok' in n and ('all' in n or 'card' in n))
            or pick(lambda n: 'pok' in n))

# collections.json itself caps at 250, so a few stores' real singles collection is
# invisible and the name heuristic lands on "graded-pokemon" or "korean-pokemon".
# For those, the whole catalogue is small enough to just walk.
SET_TOKENS = {}   # set name -> distinctive tokens; filled by guide_cards()
# Words that appear in set names but carry no identifying power in a product title.
# "Pokemon GO" reduces to nothing here, which is the point: its only real token is
# "pokemon", and treating that as a set signature made every title naming the word
# look like it named a contradicting set.
SET_STOP = {'pokemon', 'promo', 'promos', 'the', 'and', 'set', 'series', 'tcg',
            'collection', 'deck', 'box', 'edition'}


def set_sig(name):
    """A set name reduced to the tokens that actually identify it."""
    return {x for x in toks(name) if len(x) > 2 and x not in SET_STOP}

FORCE_FULL = {'exorgames.com', 'hobbiesville.com', 'deckoutgaming.ca'}

def feed(base, label, seen, out, page_cap=100):
    """Page one products.json feed into `out`, skipping handles already seen."""
    page, added = 1, 0
    while page <= page_cap:
        d = get(f'{base}?limit=250&page={page}')
        if d is None:
            print(f'      {label}: page {page} failed, keeping what we have', flush=True)
            break
        ps = d.get('products') or []
        if not ps:
            break
        for p in ps:
            if p['handle'] not in seen:
                seen.add(p['handle'])
                out.append((p['title'], p['handle']))
                added += 1
        if len(ps) < 250:
            break
        page += 1
        time.sleep(PACE)
    return added

def catalogue(host):
    """Every product we might care about, as (title, handle).

    Union of the whole catalogue and every plausible Pokemon collection. Any single
    feed is capped by Shopify at 100 pages / 25,000 products, which silently truncated
    the big stores on an earlier run; unioning several feeds routes around that."""
    CACHE.mkdir(exist_ok=True)
    cf = CACHE / (host.replace('.', '_') + '.json')
    if cf.exists():
        print('    (from cache)', flush=True)
        return [tuple(x) for x in json.loads(cf.read_text())]
    seen, out = set(), []
    n = feed(f'https://{host}/products.json', 'full catalogue', seen, out)
    print(f'    full catalogue: {n}', flush=True)
    for handle, cnt in pokemon_collections(host):
        n = feed(f'https://{host}/collections/{handle}/products.json', handle, seen, out)
        if n:
            print(f'    + {handle}: {n} new', flush=True)
    cf.write_text(json.dumps(out))
    return out

# ---------- matching (mirrors the rules in master-set-binder.html) -----------
def search_name(n):
    n = re.sub(r'\s*—.*$', '', str(n))
    n = re.sub(r'\s*\([^)]*\)', '', n)
    return n.strip() or str(n)

def ann_toks(n):
    out = set()
    for seg in re.findall(r'\(([^)]*)\)', str(n)):
        out |= toks(seg)
    return out

def toks(s):
    return {t for t in re.split(r'[^a-z0-9]+', str(s).lower().replace('é', 'e')) if t}

# Dark Fox writes "Mareep - GG34/null" when it has no total. Without the null branch the
# trailing guard rejects the whole token -- the slash is still there -- and GG34 becomes
# invisible, losing every Galarian Gallery and Trainer Gallery card at that shop.
NUMTOK = re.compile(r'(?:^|[^A-Za-z0-9/])([A-Za-z]{0,4}\d{1,4})(?:\s*/\s*([A-Za-z]{0,4}\d{1,4}|[Nn]ull|N/?A))?(?![A-Za-z0-9/])')

def canon_num(x):
    s = str(x or '').strip()
    if s.lower() in ('null', 'na', 'n/a'):
        return None                      # a stated non-total is the same as none stated
    m = re.match(r'^([A-Za-z]*)0*(\d+)$', s)
    return (m.group(1).upper(), m.group(2)) if m else None

def matches(card, title, sib_ann):
    want = canon_num(card['num'])
    if not want:
        return False
    want_t = canon_num(card.get('tot'))
    lettered = bool(want[0])
    tt = toks(title)

    ann = ann_toks(card['n'])
    if sib_ann:
        hits_sib = bool(tt & sib_ann)
        hits_mine = bool(tt & ann)
        if hits_sib and not hits_mine:
            return False

    # Face to Face writes the card id into the title — "Feebas - 49/106 - Common
    # [ex9-49] [Non-Holo]" — so the tokens are (49,106), (ex9,None) and (49,None).
    # Checking the total only when a token happened to carry one let that bare 49
    # match ANY Feebas numbered 49, which pointed three different Feebas cards at
    # the same cheap product. If the title states a total anywhere, it must agree.
    numtoks = [(canon_num(a), canon_num(b)) for a, b in
               ((m.group(1), m.group(2)) for m in NUMTOK.finditer(title))]
    saw_total = any(b for a, b in numtoks if a == want)
    num_ok = False
    for a, b in numtoks:
        if a != want:
            continue
        if not lettered and want_t:
            if b:
                if b[1] != want_t[1]:
                    continue
            elif saw_total:
                continue            # a bare number, when the title does state one
        num_ok = True
        break
    if not num_ok:
        return False
    # A store that names a DIFFERENT set has told us this is not our card, whatever the
    # number says. Dark Fox lists "Feebas (Delta Species) - 49/106 [Dragon Frontiers]" --
    # that card is really 49/101, and their typo'd total is the Emerald Feebas's number.
    # The number agreed, a total was stated, so the corroboration check below was skipped
    # and an Emerald pocket got a Dragon Frontiers listing.
    # Compatible means one name contains the other, so "Crown Zenith" still matches a
    # "Crown Zenith Galarian Gallery" card and a bare "Promo" names no set at all.
    # Promos are exempt: a promo listing routinely names the main set it accompanies,
    # e.g. "Slowbro (083) [Staff] [Mega Evolution Promo]" for a card whose set is
    # "MEP Black Star Promos". Their numbers are distinctive enough to stand alone.
    mine_set = set_sig(card.get('set', ''))
    if not (tt & {'promo', 'promos'}) and not (mine_set and mine_set <= tt):
        # The card's own set is not named, so a different one being named is a
        # contradiction. Ignore a set that is present only because the card is called
        # that -- "Detective Pikachu - SM170" is an SM Black Star Promo, not a card from
        # the Detective Pikachu set, and must not reject itself on its own name.
        name_toks = toks(card['n'])
        named = [SET_TOKENS[s] for s in SET_TOKENS
                 if SET_TOKENS[s] and SET_TOKENS[s] <= tt
                 and not SET_TOKENS[s] <= name_toks]
        if named and not any(s <= mine_set or mine_set <= s for s in named):
            return False

    # A title that never states a total (Deck Out writes "Slowpoke (81)") is matched on
    # the number alone, so make the set corroborate it or 81 matches 81 from any set.
    # Lettered numbers are exempt: SM109 or GG34 identifies a card on its own, the way a
    # bare 81 never can. Demanding corroboration from them cost 305 real listings across
    # 121 cards -- promos, mostly, where the guide says "SM Black Star Promos" and every
    # shop writes "Promo", so the words never intersect and a correct match was binned.
    if want_t and not saw_total and not lettered:
        st = {t for t in toks(card.get('set', '')) if len(t) > 2}
        if st and not (st & tt):
            return False

    mine = toks(search_name(card['n']))
    if mine <= tt:
        return True
    head = re.split(r'[\(\[]|\s-\s|\d', title)[0]
    h = toks(head)
    return bool(h) and h <= mine

# ---------- guide cards -----------------------------------------------------
def guide_cards():
    src = HTML.read_text(encoding='utf-8')
    i = src.index('const DATA = {'); j = src.index('};', i)
    D = json.loads(src[i + len('const DATA = '):j + 1])
    # Every artist/rarity/expansion set is appended to DATA.species after the fact.
    # Discovered rather than listed, so a new one cannot be silently left out of the
    # sweep -- which is exactly how Pitch Black would have been missed.
    for key in re.findall(r'const ([A-Z0-9_]+_SPECIES) = ', src):
        k = src.index(f'const {key} = ') + len(f'const {key} = ')
        e = src.index(f';\nObject.assign(DATA.species, {key})', k)
        D['species'].update(json.loads(src[k:e]))
    # Every set the guide knows, as significant tokens, for the contradiction check in
    # matches(). Built from the guide itself so it needs no maintenance.
    global SET_TOKENS
    SET_TOKENS = {}
    for s in D['species'].values():
        for c in s['cards']:
            nm = c.get('set', '')
            if nm and nm not in SET_TOKENS:
                SET_TOKENS[nm] = set_sig(nm)

    cards, seen = [], set()
    for s in D['species'].values():
        for c in s['cards']:
            real = c.get('srcId') or c['id']
            if real in seen or c.get('custom'):
                continue
            seen.add(real)
            cards.append({'key': real, 'n': c['n'], 'num': c['num'], 'set': c.get('set', ''),
                          'tot': c.get('tot'), 'sid': c.get('sid')})
    for g, lst in D['cameos'].items():
        for c in lst:
            real = c.get('srcId') or c['id']
            if real in seen:
                continue
            seen.add(real)
            cards.append({'key': real, 'n': c['n'], 'num': c['num'], 'set': c.get('set', ''),
                          'tot': c.get('tot'), 'sid': c.get('sid')})
    # annotation siblings: cards sharing a set+number, where the annotation matters
    bysn = collections.defaultdict(list)
    for c in cards:
        bysn[(c['sid'], str(c['num']))].append(c)
    for c in cards:
        sibs = [x for x in bysn[(c['sid'], str(c['num']))] if x['n'] != c['n']]
        c['sib_ann'] = set().union(*[ann_toks(x['n']) for x in sibs]) - ann_toks(c['n']) if sibs else set()
    return cards

# ---------- main ------------------------------------------------------------
def main():
    cards = guide_cards()
    print(f'guide cards to index: {len(cards)}')
    index = collections.defaultdict(list)
    for sid, host in STORES:
        print(f'\n[{sid}] {host}', flush=True)
        t0 = time.time()
        cat = catalogue(host)
        print(f'    {len(cat)} products in {time.time()-t0:.0f}s', flush=True)
        hits = 0
        for title, handle in cat:
            tl = title.lower()
            for c in cards:
                if c['n'].split()[0].lower()[:6] not in tl:
                    continue
                if matches(c, title, c['sib_ann']):
                    index[c['key']].append([sid, handle])
                    hits += 1
        print(f'    matched {hits} products', flush=True)
    covered = sum(1 for c in cards if index.get(c['key']))
    print(f'\nCOVERAGE: {covered}/{len(cards)} cards have >=1 handle '
          f'({100*covered/len(cards):.0f}%)')
    OUT.write_text(json.dumps(index, separators=(',', ':')), encoding='utf-8')
    print(f'wrote {OUT}  ({OUT.stat().st_size/1024:.0f} KB)')

if __name__ == '__main__':
    main()
