# Master Set Binder

`index.html` is the whole thing — a single self-contained page. Open it locally or
serve this folder; GitHub Pages publishes it straight from `/docs`.

Stock checking talks directly to the shops' Shopify storefronts from your browser
(`/products/<handle>.js`, which is CORS-open). There is no server and no API key, so
there is nothing to run out of.

The product handles it needs are baked into the page. To refresh them after adding
cards, or when a shop reorganises:

    python3 tools/build-handle-index.py     # ~10-20 min, writes tools/handles.json
    python3 tools/inject-handles.py         # embeds it and updates docs/
