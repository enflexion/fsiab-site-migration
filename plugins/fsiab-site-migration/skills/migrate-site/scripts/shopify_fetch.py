"""Fetch a Shopify storefront's public data into WORKDIR/raw, then write WORKDIR/source.json.

Usage: shopify_fetch.py https://store.com.au WORKDIR [--no-html]
Public storefront endpoints only, no admin API, no credentials. Throttled to about one request
every 2.5 seconds because Shopify returns 429s at roughly 60 quick requests.

What comes from where:
  products, variants, images, tags     /products.json
  collections (+ description)          /collections.json
  collection membership                /collections/{handle}/products.json
  SEO title + meta description         HTML <head> of each product, collection and page
  pages                                sitemap_pages*.xml, then each page's HTML (.rte block)
  blog posts                           sitemap_blogs*.xml / /blogs/{blog}.atom (listed, not yet built)
"""
import sys, os, json, re, time, html, urllib.request, urllib.error
from html.parser import HTMLParser
from urllib.parse import urlparse
from variants import tiers_from_variants

SITE = sys.argv[1].rstrip("/")
WORK = sys.argv[2]
NO_HTML = "--no-html" in sys.argv
RAW = os.path.join(WORK, "raw")
os.makedirs(os.path.join(RAW, "html"), exist_ok=True)
UA = "Mozilla/5.0 (Macintosh; FSIAB site migration)"
PAUSE = 2.5
_last = [0.0]


def get(url, binary=False):
    cache = os.path.join(RAW, "html", re.sub(r"[^A-Za-z0-9._-]", "_", urlparse(url).path.strip("/") or "home") + (("_" + re.sub(r"[^A-Za-z0-9]", "_", urlparse(url).query)) if urlparse(url).query else "") + ".cache")
    if os.path.exists(cache):
        return open(cache, "rb").read().decode("utf-8", "replace")
    for attempt in range(6):
        wait = PAUSE - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read()
                open(cache, "wb").write(body)
                return body.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 429:
                ra = int(e.headers.get("Retry-After") or 20)
                print(f"  429, waiting {ra}s")
                time.sleep(max(ra, 20))
                continue
            if e.code in (401, 403, 404):
                return None
            if e.code >= 500:
                time.sleep(5 * (attempt + 1)); continue
            raise
        except urllib.error.URLError:
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"gave up on {url}")


def getjson(url):
    t = get(url)
    return json.loads(t) if t else None


def paged(path, key):
    out, page = [], 1
    while True:
        j = getjson(f"{SITE}{path}{'&' if '?' in path else '?'}limit=250&page={page}")
        items = (j or {}).get(key) or []
        out.extend(items)
        if len(items) < 250:
            return out
        page += 1


def head_meta(doc):
    if not doc:
        return "", ""
    t = re.search(r"<title[^>]*>(.*?)</title>", doc, re.S | re.I)
    d = re.search(r'<meta[^>]+name=["\']description["\'][^>]*content=["\']([^"\']*)', doc, re.I) or \
        re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]*name=["\']description', doc, re.I)
    clean = lambda s: re.sub(r"\s+", " ", html.unescape(s or "")).strip()
    return clean(t.group(1)) if t else "", clean(d.group(1)) if d else ""


class _Block(HTMLParser):
    """Capture the inner HTML of the first element whose class list contains `cls`."""
    def __init__(self, cls, by_id=False):
        super().__init__(convert_charrefs=False)
        self.cls, self.by_id, self.depth, self.buf, self.done = cls, by_id, 0, [], False

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        a = dict(attrs)
        hit = (a.get("id") == self.cls) if self.by_id else (self.cls in (a.get("class") or "").split())
        if self.depth == 0 and hit:
            self.depth = 1
            return
        if self.depth:
            self.depth += 0 if tag in ("br", "img", "hr", "input", "meta", "link", "source") else 1
            self.buf.append(self.get_starttag_text())

    def handle_endtag(self, tag):
        if self.done or not self.depth:
            return
        if tag in ("br", "img", "hr", "input", "meta", "link", "source"):
            return
        self.depth -= 1
        if self.depth == 0:
            self.done = True
            return
        self.buf.append(f"</{tag}>")

    def handle_startendtag(self, tag, attrs):
        if self.depth and not self.done:
            self.buf.append(self.get_starttag_text())

    def handle_data(self, d):
        if self.depth and not self.done:
            self.buf.append(d)

    def handle_entityref(self, n):
        if self.depth and not self.done:
            self.buf.append(f"&{n};")

    def handle_charref(self, n):
        if self.depth and not self.done:
            self.buf.append(f"&#{n};")


# Theme sections that are widgets, not page content. Matched against the section wrapper id,
# e.g. id="shopify-section-template--2175__featured_blog_Ab12Cd".
DROP_SECTIONS = re.compile(r"shopify-section-.*(featured[_-]blog|featured[_-]collection|featured[_-]product|collection[_-]list|"
                           r"newsletter|email[_-]signup|contact[_-]form|__form|popup|cookie|announcement|header|footer|"
                           r"recently[_-]viewed|product[_-]recommendations|__apps?[_-]|instagram|reviews?)", re.I)


class _Strip(HTMLParser):
    """Rebuild HTML without the subtrees of dropped theme sections. Records what was dropped."""
    VOID = {"br", "img", "hr", "input", "meta", "link", "source", "area", "base", "col", "embed", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.out, self.skip, self.skip_tag, self.dropped = [], 0, None, []

    # Only the dropped element's own tag type is counted (section wrappers are <div>s, which
    # themes always close), so one unclosed <li> or <p> elsewhere cannot swallow the page.
    def handle_starttag(self, tag, attrs):
        if self.skip:
            if tag == self.skip_tag:
                self.skip += 1
            return
        i = dict(attrs).get("id") or ""
        if tag not in self.VOID and DROP_SECTIONS.search(i):
            self.skip, self.skip_tag = 1, tag
            self.dropped.append(re.sub(r"^shopify-section-(template--\d+__|sections--\d+__)?", "", i))
            return
        self.out.append(self.get_starttag_text())

    def handle_startendtag(self, tag, attrs):
        if not self.skip:
            self.out.append(self.get_starttag_text())

    def handle_endtag(self, tag):
        if self.skip:
            if tag == self.skip_tag:
                self.skip -= 1
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, d):
        if not self.skip:
            self.out.append(d)

    def handle_entityref(self, n):
        if not self.skip:
            self.out.append(f"&{n};")

    def handle_charref(self, n):
        if not self.skip:
            self.out.append(f"&#{n};")


def strip_sections(doc):
    p = _Strip()
    p.feed(doc or "")
    return "".join(p.out), p.dropped


def block(doc, cls, by_id=False):
    p = _Block(cls, by_id)
    p.feed(doc or "")
    return "".join(p.buf).strip()


def sitemap_urls(kind):
    idx = get(f"{SITE}/sitemap.xml") or ""
    urls = []
    for sm in re.findall(r"<loc>([^<]*sitemap_" + kind + r"[^<]*)</loc>", idx):
        sm = html.unescape(sm)
        urls += [html.unescape(u) for u in re.findall(r"<loc>([^<]+)</loc>", get(sm) or "")]
    return urls


print(f"Fetching {SITE}")
home = get(SITE + "/") or ""
site_name = head_meta(home)[0]
m = re.search(r'<meta property="og:site_name" content="([^"]+)"', home)
if m:
    site_name = html.unescape(m.group(1))

products = paged("/products.json", "products")
print(f"  products: {len(products)}")
collections = paged("/collections.json", "collections")
print(f"  collections: {len(collections)}")
membership = {}
for c in collections:
    items = paged(f"/collections/{c['handle']}/products.json", "products")
    membership[c["handle"]] = [p["handle"] for p in items]
print(f"  collection memberships fetched")

seo = {}
if not NO_HTML:
    for i, p in enumerate(products, 1):
        seo[f"/products/{p['handle']}"] = head_meta(get(f"{SITE}/products/{p['handle']}"))
        if i % 20 == 0:
            print(f"  product pages {i}/{len(products)}")
    for c in collections:
        seo[f"/collections/{c['handle']}"] = head_meta(get(f"{SITE}/collections/{c['handle']}"))

page_urls = [u for u in sitemap_urls("pages") if "/pages/" in u]
pages = []
for u in page_urls:
    doc = get(u)
    if not doc:
        continue
    t, d = head_meta(doc)
    # Online Store 2.0 themes build a page from stacked sections (banner, image-with-text,
    # rich text...), so the page's own .rte block is often just a stock blurb. Take the whole
    # main content area, drop the widget sections inside it; the cleaner strips the layout.
    body = block(doc, "MainContent", by_id=True) or block(doc, "main-content", by_id=True) or block(doc, "main-page") or block(doc, "rte")
    body, dropped = strip_sections(body)
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", body or doc, re.S)
    noindex = bool(re.search(r'<meta[^>]+name=["\']robots["\'][^>]+noindex', doc, re.I))
    pages.append({"url": u, "handle": urlparse(u).path.rstrip("/").split("/")[-1], "title_tag": t, "meta_description": d,
                  "h1": re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", h1.group(1)))).strip() if h1 else "",
                  "html": body, "noindex": noindex, "dropped_sections": dropped})
print(f"  pages: {len(pages)}")
blog_urls = sitemap_urls("blogs")
articles = []
for u_ in blog_urls:
    parts = urlparse(u_).path.strip("/").split("/")
    if len(parts) != 3 or parts[0] != "blogs":
        continue  # the blog index itself, not an article
    doc = get(u_)
    if not doc:
        continue
    t, d = head_meta(doc)
    body = block(doc, "article-template__content") or block(doc, "article__content") or block(doc, "rte")
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", doc, re.S)
    date = re.search(r'<time[^>]*datetime="([0-9-]{10})', doc)
    img = re.search(r'<meta property="og:image" content="([^"]+)"', doc)
    author = re.search(r'"author"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"', doc)
    tags = re.findall(r'href="/blogs/[^/]+/tagged/[^"]+"[^>]*>([^<]+)<', doc)
    articles.append({"url": u_, "blog": parts[1], "handle": parts[2], "title_tag": t, "meta_description": d,
                     "h1": re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", h1.group(1)))).strip() if h1 else "",
                     "html": body, "date": date.group(1) if date else "",
                     "image": html.unescape(img.group(1)) if img else "", "author": author.group(1) if author else "",
                     "tags": [html.unescape(x).strip() for x in tags]})
print(f"  blog articles: {len(articles)}")

# Shopify policies live outside the pages sitemap
policies = []
for handle in ("terms-of-service", "privacy-policy", "refund-policy", "shipping-policy", "contact-information", "legal-notice"):
    doc = get(f"{SITE}/policies/{handle}")
    if not doc:
        continue
    body = block(doc, "shopify-policy__body") or block(doc, "rte")
    if not re.sub(r"<[^>]+>|\s", "", body or ""):
        continue
    t = block(doc, "shopify-policy__title")
    t = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", t))).strip() or handle.replace("-", " ").title()
    policies.append({"handle": handle, "url": f"{SITE}/policies/{handle}", "title": t, "html": body})
print(f"  policies: {[p_['handle'] for p_ in policies]}")

for name, data in (("products.json", products), ("collections.json", collections), ("membership.json", membership),
                   ("seo.json", seo), ("pages.json", pages), ("blog_urls.json", blog_urls),
                   ("articles.json", articles), ("policies.json", policies),
                   ("site.json", {"url": SITE, "name": site_name})):
    json.dump(data, open(os.path.join(RAW, name), "w"), ensure_ascii=False, indent=1)

# ------------------------------------------------------------------ -> source.json
u = lambda s: re.sub(r"\s+", " ", html.unescape(s or "")).strip()
in_coll = {}
for h, hs in membership.items():
    for ph in hs:
        in_coll.setdefault(ph, []).append(h)

COLOUR_WORDS = re.compile(r"\b(white|whites|pink|pinks|red|reds|green|greens|cream|creams|yellow|yellows|orange|oranges|purple|purples|"
                          r"pastel|pastels|bright|brights|burgundy|burgandy|blush|lime|mauve|mauves|peach|blue|black|gold|"
                          r"neutral|natural|rust|sage|lilac|lavender|coral|ivory|tonal|colourful|colorful|muted)\b", re.I)
src_products = []
for p in products:
    variants = p.get("variants") or []
    all_opts = [o["name"] for o in p.get("options") or [] if o.get("name") != "Title"]
    # A colour option becomes FSIAB colour options (the `colours` table, with the variant's
    # photo), not price tiers. Everything else goes through the tier rule.
    colour_i = next((i for i, n in enumerate(all_opts) if re.fullmatch(r"colou?rs?", n.strip(), re.I)), None)
    if colour_i is None:
        # options whose values are mostly colour/palette names ("Whites & Greens", "Pastels")
        for i, n in enumerate(all_opts):
            if re.search(r"size|budget|denomination|amount|wine|stem|quantity|qty", n, re.I):
                continue
            vals = [x for x in (p.get("options") or [])[[o["name"] for o in p.get("options") or []].index(n)]["values"]]
            hits = sum(1 for x in vals if COLOUR_WORDS.search(x))
            if len(vals) >= 2 and hits / len(vals) >= 0.6:
                colour_i = i
                break
    colours, notes = [], []
    if colour_i is not None and len(all_opts) >= 1:
        img_by_id = {i["id"]: i["src"].split("?")[0] for i in p.get("images") or []}
        seen = {}
        for v in variants:
            cv = u(v.get(f"option{colour_i + 1}") or "")
            if not cv or cv in seen:
                continue
            fi = v.get("featured_image") or {}
            src_ = (fi.get("src") or "").split("?")[0] or img_by_id.get(v.get("image_id"), "")
            seen[cv] = {"colour": cv, "image": src_, "sku": (v.get("sku") or "").strip()}
        colours = list(seen.values())
        by_colour = {}
        for v in variants:
            by_colour.setdefault(u(v.get(f"option{colour_i + 1}") or ""), set()).add(v["price"])
    opts = [n for i, n in enumerate(all_opts) if i != colour_i]
    idx = [i for i in range(len(all_opts)) if i != colour_i]
    vs = [{"values": [u(v.get(f"option{i + 1}") or "") for i in idx], "price": f"{float(v['price']):.2f}",
           "sku": (v.get("sku") or "").strip() if colour_i is None else "", "in_stock": bool(v.get("available", True))} for v in variants]
    if colour_i is not None and not opts:
        # colour was the only option: one price (lowest), colours carry the choice
        prices = sorted({float(v["price"]) for v in variants})
        vs = [{"values": [], "price": f"{prices[0]:.2f}", "sku": "", "in_stock": any(v.get("available", True) for v in variants)}]
        if len(prices) > 1:
            notes.append(f"colours had different prices (${prices[0]:.2f} to ${prices[-1]:.2f}); ${prices[0]:.2f} used. CHECK THIS PRODUCT")
    elif colour_i is not None:
        # de-duplicate the remaining combinations; colour price differences are reported
        merged = {}
        for v in vs:
            k = tuple(v["values"])
            if k in merged:
                merged[k]["prices"].add(v["price"])
                merged[k]["in_stock"] = merged[k]["in_stock"] or v["in_stock"]
            else:
                merged[k] = {"values": v["values"], "prices": {v["price"]}, "in_stock": v["in_stock"]}
        vs = []
        for k, m in merged.items():
            lo = min(m["prices"], key=float)
            if len(m["prices"]) > 1:
                notes.append(f"'{' / '.join(k)}' costs more in some colours ({', '.join(sorted(m['prices'], key=float))}); ${lo} used. CHECK THIS PRODUCT")
            vs.append({"values": m["values"], "price": lo, "sku": "", "in_stock": m["in_stock"]})
    if not opts or all((v.get("title") or "") == "Default Title" for v in variants):
        vs = vs[:1]
    tiers, tier_notes = tiers_from_variants(opts, vs)
    notes += tier_notes
    if colours:
        notes.append(f"{len(colours)} colour option(s) imported as FSIAB colours: " + ", ".join(c["colour"] for c in colours))
    cmp_at = [float(v["compare_at_price"]) for v in variants if v.get("compare_at_price") and float(v["compare_at_price"]) > float(v["price"])]
    if cmp_at:
        notes.append("on sale on the old site (compare-at price set); imported at the sale price")
    st, sd = seo.get(f"/products/{p['handle']}", ("", ""))
    imgs = sorted(p.get("images") or [], key=lambda i: i.get("position", 0))
    src_products.append({
        "id": p["id"], "slug": p["handle"], "name": u(p["title"]), "url": f"{SITE}/products/{p['handle']}",
        "body_html": p.get("body_html") or "", "short_html": "",
        "seo_title": st, "seo_description": sd,
        "in_stock": any(v.get("available", True) for v in variants) if variants else True,
        "tags": [t.strip() for t in (p.get("tags") if isinstance(p.get("tags"), list) else (p.get("tags") or "").split(",")) if t.strip()],
        "categories": in_coll.get(p["handle"], []),
        "images": [{"src": i["src"].split("?")[0], "alt": u(i.get("alt"))} for i in imgs],
        "tiers": tiers, "unpriced_options": [], "single_price_options": None, "notes": notes, "colours": colours,
        "vendor": p.get("vendor"), "product_type": p.get("product_type"),
    })

src_categories = []
for c in collections:
    st, sd = seo.get(f"/collections/{c['handle']}", ("", ""))
    desc_html = c.get("description") or c.get("body_html") or ""
    # Many themes never show the collection description (it is only used for meta). Import it
    # as category content only if its text actually appeared on the old collection page.
    page = get(f"{SITE}/collections/{c['handle']}") if not NO_HTML else ""
    main = block(page or "", "MainContent", by_id=True) or (page or "")
    probe = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", desc_html))).strip()[:60]
    main_txt = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", main)))
    visible = bool(probe) and probe in main_txt
    src_categories.append({"slug": c["handle"], "name": u(c["title"]), "url": f"{SITE}/collections/{c['handle']}",
                           "description_html": desc_html, "description_visible": visible,
                           "seo_title": st, "seo_description": sd, "product_count": len(membership.get(c["handle"], [])),
                           "parent_slug": None})

def no_suffix(t):
    for sep in (" – ", " - ", " | ", " — "):
        if site_name and t.endswith(sep + site_name):
            return t[: -len(sep + site_name)]
    return t


src_pages = [{"id": pg["handle"], "slug": pg["handle"], "title": pg["h1"] or no_suffix(pg["title_tag"]), "url": pg["url"],
              "html": pg["html"], "seo_title": pg["title_tag"], "seo_description": pg["meta_description"], "noindex": pg["noindex"]}
             for pg in pages]

src_pages += [{"id": "policy-" + p_["handle"], "slug": "policy-" + p_["handle"], "title": p_["title"], "url": p_["url"],
               "html": p_["html"], "seo_title": p_["title"], "seo_description": "", "noindex": False, "policy": p_["handle"]}
              for p_ in policies]
src_posts = [{"id": a["handle"], "slug": a["handle"], "title": a["h1"] or no_suffix(a["title_tag"]), "url": a["url"],
              "html": a["html"], "date": a["date"], "author": a["author"], "categories": a["tags"] or ([] if a["blog"] == a["handle"] else [a["blog"].replace("-", " ").title()]),
              "image": a["image"] if not a["image"].startswith("//") else "https:" + a["image"],
              "seo_title": a["title_tag"], "seo_description": a["meta_description"]} for a in articles]
json.dump({"platform": "shopify", "site_url": SITE, "site_name": site_name, "categories": src_categories,
           "products": src_products, "pages": src_pages, "posts": src_posts},
          open(os.path.join(WORK, "source.json"), "w"), ensure_ascii=False, indent=1)
print(f"source.json: {len(src_categories)} collections, {len(src_products)} products, {len(src_pages)} pages (incl. {len(policies)} policies), {len(src_posts)} posts")
