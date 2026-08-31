/* Shopify storefront search proxy (Netlify Functions v2).
 *
 * Why this exists: every one of these stores runs on Shopify, and Shopify serves
 * `/products.json` with Access-Control-Allow-Origin — but its SEARCH endpoints
 * (`/search/suggest.json`, `/search.json`, `/search?view=json`) send no CORS header
 * at all, so the binder page cannot call them from the browser. products.json has no
 * text search and 401 Games alone lists 53,809 Pokemon products, so mirroring the
 * catalogue is not an option either.
 *
 * It also does the SECOND hop. suggest.json never returns variants (verified — the
 * array is always []), and variants are where the printing lives: four of the five
 * stores encode it in the variant title ("Lightly Played Reverse Holofoil"), so
 * product-level `available` cannot tell a reverse holo from the normal print. Doing
 * that hop here instead of in the browser keeps the client at one request per
 * (card, store), which matters on a phone.
 *
 * The host allowlist is not optional: without it this is an open proxy pointed at
 * anything on the internet.
 */
const ALLOWED = new Set([
  'store.401games.ca',
  'levelupgames.ca',
  'exorgames.com',
  'chimeragamingonline.com',
  'obsidiangames.ca',
  'facetofacegames.com',
  'hobbiesville.com',
  'houseofcards.ca',
  'deckoutgaming.ca'
]);

const MAX_DEEP = 3;          // products per store we'll pull variants for
const UPSTREAM_TIMEOUT = 8000;

/* This function runs on Netlify's US infrastructure, and four of the nine shops use
 * Shopify Markets regional pricing — so they geolocated the function and answered in
 * USD, which the binder then displayed as CAD. House of Cards' Slowbro 117/091 came
 * back as $20.00 when the shop's own page says $27.20. The mark-ups differ per store
 * (1.20x, 1.35x, 1.39x observed), so no exchange rate can undo it after the fact;
 * the country has to be pinned on the request. ?country=CA overrides the geolocation
 * and is ignored by shops that do not use Markets. */
const COUNTRY = 'country=CA';

const CORS = {
  'access-control-allow-origin': '*',
  'access-control-allow-methods': 'GET, OPTIONS',
  'content-type': 'application/json',
  'cache-control': 'public, max-age=900'   // 15 min at the edge; the client caches 24h
};

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), { status, headers: CORS });

const UA = { 'user-agent': 'master-set-binder/1.0 (personal collection tracker)' };

async function grab(url) {
  const r = await fetch(url, { signal: AbortSignal.timeout(UPSTREAM_TIMEOUT), headers: UA });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

/* Cheap pre-filter so we only spend a second request on plausible products. This is
 * cost control, NOT the authoritative match — the client still matches properly.
 *
 * The total is optional: Deck Out Gaming writes "Slowpoke (81)" with no total at all,
 * so insisting on "81/123" here meant none of its products were ever deep-fetched and
 * it could only ever return coarse product-level stock. A few extra candidates are
 * cheap — MAX_DEEP caps the cost and the client throws out the wrong ones. */
const numRe = (num, tot) => {
  const n = String(num).replace(/^0+/, ''), t = String(tot || '').replace(/^0+/, '');
  const bare = `(?:^|[^A-Za-z\\d/])0*${n}(?![\\d])`;
  return t ? new RegExp(`${bare}|(?:^|[^\\d/])0*${n}\\s*/\\s*0*${t}(?![\\d/])`)
           : new RegExp(bare);
};

export default async (req) => {
  if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers: CORS });

  const p = new URL(req.url).searchParams;
  const host = p.get('host') || '';
  const q = (p.get('q') || '').trim();
  const num = p.get('num') || '';
  const tot = p.get('tot') || '';

  if (!ALLOWED.has(host)) return json({ error: 'host not allowed' }, 400);
  if (!q) return json({ error: 'missing q' }, 400);

  /* unavailable_products=show matters: 401 Games' storefront search hides sold-out
     products by default, which silently swallowed its separate "- Reverse Holo"
     listings — the very ones worth knowing about. The other four are unaffected. */
  const searchUrl = `https://${host}/search/suggest.json?q=${encodeURIComponent(q)}` +
                    `&resources%5Btype%5D=product&resources%5Blimit%5D=10` +
                    `&resources%5Boptions%5D%5Bunavailable_products%5D=show` +
                    `&${COUNTRY}`;

  let products;
  try {
    const d = await grab(searchUrl);
    products = d?.resources?.results?.products || [];
  } catch (e) {
    const timeout = e?.name === 'TimeoutError' || e?.name === 'AbortError';
    return json({ error: timeout ? 'store timeout' : String(e?.message || e) }, 502);
  }

  const slim = products.map((x) => ({
    title: x.title,
    available: !!x.available,
    price: x.price != null ? Number(x.price) : null,
    url: 'https://' + host + String(x.url || '').split('?')[0],
    handle: String(x.url || '').split('/products/')[1]?.split('?')[0] || '',
    variants: null                       // null = not fetched, [] = fetched and empty
  }));

  // second hop: variant-level stock for the products that could be this card
  if (num) {
    const re = numRe(num, tot);
    const deep = slim.filter((s) => re.test(s.title) && s.handle).slice(0, MAX_DEEP);
    await Promise.all(deep.map(async (s) => {
      try {
        const d = await grab(`https://${host}/products/${s.handle}.js?${COUNTRY}`);
        s.variants = (d.variants || []).map((v) => ({
          title: String(v.title || ''),
          available: !!v.available,
          price: v.price != null ? Number(v.price) / 100 : null,   // .js prices are cents
          qty: typeof v.inventory_quantity === 'number' ? v.inventory_quantity : null
        }));
      } catch { s.variants = null; }     // leave null; the client falls back to product level
    }));
  }

  return json({ host, q, currency: 'CAD', products: slim });
};

export const config = { path: '/api/stock' };
