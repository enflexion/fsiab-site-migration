"""Fetch a WordPress / WooCommerce site's public data into a raw cache folder.

Usage: wp_fetch.py https://site.com.au OUTDIR
Writes OUTDIR/raw/*.json. Public endpoints only, no credentials. Throttled.
"""
import sys, os, json, time, urllib.request, urllib.error

SITE = sys.argv[1].rstrip("/")
OUT = os.path.join(sys.argv[2], "raw")
os.makedirs(OUT, exist_ok=True)
UA = "Mozilla/5.0 (Macintosh; FSIAB site migration)"
PAUSE = 0.8


def get(url, want_headers=False):
    for attempt in range(5):
        time.sleep(PAUSE)
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read()
                return (body, dict(r.headers)) if want_headers else body
        except urllib.error.HTTPError as e:
            if e.code in (429, 503):
                time.sleep(10 * (attempt + 1)); continue
            if e.code in (400, 401, 403, 404):
                return (None, dict(e.headers)) if want_headers else None
            raise
        except urllib.error.URLError:
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"gave up on {url}")


def paged(path, per_page=100, extra=""):
    items, page = [], 1
    while True:
        sep = "&" if "?" in path else "?"
        url = f"{SITE}/wp-json/{path}{sep}per_page={per_page}&page={page}{extra}"
        body, hdr = get(url, want_headers=True)
        if body is None:
            break
        batch = json.loads(body)
        if not isinstance(batch, list) or not batch:
            break
        items.extend(batch)
        total_pages = int(hdr.get("X-WP-TotalPages") or hdr.get("x-wp-totalpages") or 0)
        if total_pages and page >= total_pages:
            break
        if not total_pages and len(batch) < per_page:
            break
        page += 1
    return items


def save(name, data):
    with open(os.path.join(OUT, name), "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"  {name}: {len(data) if isinstance(data, list) else 'ok'}")


print(f"Fetching {SITE}")
root = json.loads(get(f"{SITE}/wp-json/"))
save("site.json", {k: root.get(k) for k in ("name", "description", "url", "home", "namespaces")})

save("pages.json", paged("wp/v2/pages", extra="&status=publish"))
save("posts.json", paged("wp/v2/posts", extra="&status=publish"))
save("wp_products.json", paged("wp/v2/product"))            # carries yoast_head_json
save("wp_product_cat.json", paged("wp/v2/product_cat"))     # carries yoast_head_json, parent, description
save("categories.json", paged("wp/v2/categories"))          # blog categories
# prices, images, categories, attributes. Paging can drift (duplicates one product, skips another),
# so dedupe by id and fetch anything wp/v2 lists but the Store API pages missed.
_sp = list({p["id"]: p for p in paged("wc/store/v1/products")}.values())
_have = {p["id"] for p in _sp}
for _w in json.load(open(os.path.join(OUT, "wp_products.json"))):
    if _w["id"] not in _have:
        _b = get(f"{SITE}/wp-json/wc/store/v1/products/{_w['id']}")
        if _b:
            _sp.append(json.loads(_b))
save("store_products.json", _sp)
save("store_product_cats.json", json.loads(get(f"{SITE}/wp-json/wc/store/v1/products/categories?per_page=100") or b"[]"))

# variations for variable products
variations = {}
for p in json.load(open(os.path.join(OUT, "store_products.json"))):
    if p.get("type") == "variable" and p.get("variations"):
        vs = []
        for v in p["variations"]:
            body = get(f"{SITE}/wp-json/wc/store/v1/products/{v['id']}")
            if body:
                vs.append(json.loads(body))
        variations[str(p["id"])] = vs
save("variations.json", variations)

# product add-ons (public v2 endpoint may or may not answer without auth)
addons = {}
for p in json.load(open(os.path.join(OUT, "store_products.json"))):
    body = get(f"{SITE}/wp-json/wc-product-add-ons/v2/product-add-ons/{p['id']}")
    if body:
        try:
            addons[str(p["id"])] = json.loads(body)
        except Exception:
            pass
save("addons.json", addons)

# menus via the public navigation block if present (often not public)
print("done")
