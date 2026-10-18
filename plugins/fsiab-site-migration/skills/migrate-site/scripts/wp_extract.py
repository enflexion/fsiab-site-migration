"""WordPress / WooCommerce raw cache -> neutral source.json.

Usage: wp_extract.py WORKDIR     (reads WORKDIR/raw from wp_fetch.py)

source.json schema (shared by every platform extractor, consumed by build.py):
  platform, site_url, site_name
  categories: [{slug, name, url, description_html, seo_title, seo_description, product_count, parent_slug}]
  products:   [{id, slug, name, url, body_html, short_html, seo_title, seo_description, in_stock,
                tags, categories:[slug], images:[{src, alt}],
                tiers:[{label, price, sku, in_stock}], unpriced_options:[label], single_price_options:str|None}]
  pages:      [{id, slug, title, url, html, seo_title, seo_description, noindex}]
  posts:      [{id, slug, title, url, html, date, author, categories:[name], image, seo_title, seo_description}]
"""
import sys, os, json, re, html
from urllib.parse import urlparse

WORK = sys.argv[1]
raw = lambda n: json.load(open(os.path.join(WORK, "raw", n)))
u = lambda s: re.sub(r"\s+", " ", html.unescape(s or "")).strip()
site = raw("site.json")


_seo_path = os.path.join(WORK, "raw", "seo_html.json")
seo_html = json.load(open(_seo_path)) if os.path.exists(_seo_path) else {}   # wp_seo_fetch.py (Rank Math etc.)


def yo(obj):
    y = (obj or {}).get("yoast_head_json")
    if y:
        return y
    h = seo_html.get((obj or {}).get("link") or "") or {}
    if not h or "error" in h:
        return {}
    return {"title": h.get("title"), "description": h.get("description"),
            "robots": {"index": "noindex" if "noindex" in (h.get("robots") or "") else "index"},
            "og_image": [{"url": h["og_image"]}] if h.get("og_image") else None}


cat_by_id = {c["id"]: c for c in raw("wp_product_cat.json")}
categories = [{
    "slug": c["slug"], "name": u(c["name"]), "url": c["link"],
    "description_html": c.get("description") or "",
    "seo_title": u(yo(c).get("title")), "seo_description": u(yo(c).get("description")),
    "product_count": c.get("count", 0),
    "parent_slug": cat_by_id[c["parent"]]["slug"] if c.get("parent") in cat_by_id else None,
} for c in raw("wp_product_cat.json")]

wpp = {p["id"]: p for p in raw("wp_products.json")}
variations = raw("variations.json")
products = []
for p in raw("store_products.json"):
    y = yo(wpp.get(p["id"]))
    div = 10 ** int(p["prices"].get("currency_minor_unit", 2))
    tiers, unpriced, single_opts = [], [], None
    if p["type"] == "variable":
        for v in variations.get(str(p["id"])) or []:
            label = u(v.get("variation") or "")
            if label.count(":") == 1:
                label = label.split(":", 1)[1].strip()
            tiers.append({"label": label or "Option", "price": f"{int(v['prices']['price']) / div:.2f}",
                          "sku": v.get("sku") or "", "in_stock": v.get("is_in_stock", True)})
        listed = [u(t["name"]) for a in p["attributes"] if a.get("has_variations") for t in a["terms"]]
        unpriced = [x for x in listed if x not in {t["label"] for t in tiers}]
    else:
        tiers.append({"label": None, "price": f"{int(p['prices']['price']) / div:.2f}", "sku": p.get("sku") or "", "in_stock": p.get("is_in_stock", True)})
        if p["attributes"]:
            single_opts = "; ".join(a["name"] + ": " + ", ".join(u(t["name"]) for t in a["terms"]) for a in p["attributes"])
    products.append({
        "id": p["id"], "slug": p["slug"], "name": u(p["name"]), "url": p["permalink"],
        "body_html": p.get("description") or "", "short_html": p.get("short_description") or "",
        "seo_title": u(y.get("title")), "seo_description": u(y.get("description")),
        "in_stock": bool(p.get("is_in_stock")), "tags": [u(t["name"]) for t in p.get("tags", [])],
        # Store API can omit categories (hidden ones); wp/v2 product_cat has the full membership
        "categories": list(dict.fromkeys([c["slug"] for c in p["categories"]] +
                                         [cat_by_id[i]["slug"] for i in (wpp.get(p["id"]) or {}).get("product_cat", []) if i in cat_by_id])),
        "images": [{"src": i["src"], "alt": u(i.get("alt"))} for i in p["images"]],
        "tiers": tiers, "unpriced_options": unpriced, "single_price_options": single_opts,
    })

pages = [{
    "id": p["id"], "slug": p["slug"], "title": u(p["title"]["rendered"]), "url": p["link"],
    "html": p["content"]["rendered"], "seo_title": u(yo(p).get("title")), "seo_description": u(yo(p).get("description")),
    "noindex": (yo(p).get("robots") or {}).get("index") == "noindex",
} for p in raw("pages.json")]
for f in sorted(os.listdir(os.path.join(WORK, "raw"))):          # custom post types from wp_cpt_fetch.py
    if f.startswith("cpt_") and f.endswith(".json"):
        pages += [{
            "id": p["id"], "slug": p["slug"], "title": u(p["title"]["rendered"]), "url": p["link"],
            "html": (p.get("content") or {}).get("rendered") or "", "seo_title": u(yo(p).get("title")), "seo_description": u(yo(p).get("description")),
            "noindex": (yo(p).get("robots") or {}).get("index") == "noindex", "post_type": p.get("type") or f[4:-5],
        } for p in raw(f)]

blogcats = {c["id"]: u(c["name"]) for c in raw("categories.json")}
posts = [{
    "id": p["id"], "slug": p["slug"], "title": u(p["title"]["rendered"]), "url": p["link"],
    "html": p["content"]["rendered"], "date": p["date"][:10], "author": "",
    "categories": [blogcats[c] for c in p.get("categories", []) if c in blogcats],
    "image": (yo(p).get("og_image") or [{}])[0].get("url") if yo(p).get("og_image") else None,
    "seo_title": u(yo(p).get("title")), "seo_description": u(yo(p).get("description")),
} for p in raw("posts.json")]

src = {"platform": "wordpress", "site_url": site.get("url") or site.get("home"), "site_name": u(site.get("name")),
       "categories": categories, "products": products, "pages": pages, "posts": posts}
json.dump(src, open(os.path.join(WORK, "source.json"), "w"), ensure_ascii=False, indent=1)
print(f"source.json: {len(categories)} categories, {len(products)} products, {len(pages)} pages, {len(posts)} posts")
