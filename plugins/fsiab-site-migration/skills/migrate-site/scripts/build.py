"""Build a migration manifest from WORKDIR/source.json + WORKDIR/decisions.json + the target site.

Usage: build.py WORKDIR TARGET_KEY [--assume-clean]
  --assume-clean  ignore target records this import already created (listed in WORKDIR/state.json),
                  so a manifest can be planned before rolling a previous run back.
Platform neutral: every extractor writes the same source.json (see wp_extract.py for the schema).
Writes WORKDIR/manifest.json and prints a summary. Reads the target, never writes to it.
"""
import sys, os, json, re, html
from urllib.parse import urlparse
from htmlclean import clean, text_of, first_sentences
from fsiab import Fsiab

WORK, KEY = sys.argv[1], sys.argv[2]
ASSUME_CLEAN = "--assume-clean" in sys.argv
SRCD = json.load(open(os.path.join(WORK, "source.json")))
D = json.load(open(os.path.join(WORK, "decisions.json")))
SRC = D.get("source_url", SRCD["site_url"]).rstrip("/")
HOST = urlparse(SRC).netloc.lower().replace("www.", "")
SRC_HOSTS = {HOST, "www." + HOST} | {h.lower() for h in D.get("extra_source_hosts", [])}
SITE = D.get("site_name") or SRCD.get("site_name") or HOST
LABEL1 = D.get("single_price_label", "Standard")
BLOG_DIR = D.get("blog_directory", "blog")
VOUCHER_URL = D.get("voucher_url", "/cgi-bin/cart/voucher.cgi")
warnings, manual = [], []

GIFT_VOUCHER = re.compile(r"\bgift\s*(voucher|card|certificate)s?\b", re.I)
WORKSHOP = re.compile(r"\bworkshops?\b", re.I)
# Specific drink words count anywhere in the product text; loose words ("spirits",
# "bubbles", "cocktail") only in the name, type or tags, so "lift their spirits", a
# "Free Spirit" rose or "you can add bubbles" do not mark a product as alcohol.
ALCOHOL = re.compile(r"\b(wine|wines|prosecco|processco|champagne|moet|moët|chandon|beer|beers|cider|gin|vodka|whisk(?:e)?y|"
                     r"tequila|liqueur|bourbon|shiraz|chardonnay|pinot|sauvignon|riesling|merlot|cabernet|ros[eé] wine)\b", re.I)
ALCOHOL_NAME_ONLY = re.compile(r"\b(spirits|bubbles|cocktails?|margarita|rum|sparkling)\b", re.I)


def fsiab_slug(s):  # products_api / pages_api _make_slug
    s = s.lower()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^a-z0-9\-]", "", s)
    return re.sub(r"-{2,}", "-", s).strip("-")


# Built-in FSIAB routes that sit above the generated page/product rules in every site's
# .htaccess (2026-09-29: a migrated page at /gallery was shadowed by gallery.cgi and looped).
RESERVED = {"gallery", "galleries", "galleryshow", "tags", "blog", "authors", "flowers", "occasions",
            "addons", "under-60", "60-80", "80-120", "over-120", "cgi-bin", "images", "sites", "cart",
            "checkout", "search", "api", "admin", "login", "account"}


def safe_slug(slug, kind):
    return (f"our-{slug}" if kind == "page" else f"{slug}-flowers") if slug in RESERVED else slug


def safe_cat_name(n):
    """Category names build the category URL and route (cat.cgi looks them up by name), so they
    hold letters, digits and spaces only: & -> and, apostrophes dropped, anything else -> space.
    The florist's exact wording goes in the heading (headingone) and SEO title instead."""
    n = html.unescape(n).replace("&", " and ")
    n = re.sub(r"['\u2018\u2019`]", "", n)
    n = re.sub(r"[^A-Za-z0-9 ]+", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def norm(path):
    p = (path or "/").split("?")[0].split("#")[0]
    p = re.sub(r"/+", "/", p).rstrip("/").lower()
    return p or "/"


def strip_site_suffix(title):
    for name in {SITE, SRCD.get("site_name") or ""}:
        if name:
            t = re.sub(r"\s*[-–—|:]\s*" + re.escape(name) + r"\s*$", "", title, flags=re.I)
            if t != title:
                return t.strip()
    return title


def strip_leading_heading(body, *names):
    """Drop a first heading that just repeats the category/page name (the template prints it)."""
    m = re.match(r"\s*<h[1-6][^>]*>(.*?)</h[1-6]>\s*", body, re.S)
    if m and text_of(m.group(1)).strip().lower() in {n.strip().lower() for n in names if n}:
        return body[m.end():]
    return body


def old_host(url):
    return urlparse(url).netloc.lower() in SRC_HOSTS or "cdn.shopify.com" in url or url.startswith("//" + HOST)


# ------------------------------------------------------------------ target (read only)
api = Fsiab(KEY, pause=0.3)
mine = {"categories": set(), "products": set(), "pages": set()}
if ASSUME_CLEAN and os.path.exists(os.path.join(WORK, "state.json")):
    st0 = json.load(open(os.path.join(WORK, "state.json")))
    mine["categories"] = set(st0.get("categories", {}))
    mine["products"] = {v.get("url") for v in st0.get("products", {}).values()}
    mine["pages"] = {int(v["pageid"]) for v in st0.get("pages", {}).values() if v.get("action") == "create"}
t_cats = {c["name"]: c for c in api.get("/categories")["categories"] if c["name"] not in mine["categories"]}
t_prods = {p["url"]: p for p in api.get("/products")["products"] if p.get("url") and p["url"] not in mine["products"]}
t_pages = {(p.get("pagefilename") or ""): p for p in api.get("/pages", status="all")["pages"] if int(p["pageid"]) not in mine["pages"]}
dynamic_sids = {int(s["sid"]) for s in api.get("/pages/sections")["sections"] if (s.get("dynamic_mode") or "") == "dynamic"}
T_SID = D.get("page_sid", 2)


def target_page_kind(slug):
    """'placeholder' (template page whose content is just its title), 'designer' (dynamic
    section layout, never overwrite), 'content' (real content), or None if absent."""
    tp = t_pages.get(slug)
    if not tp:
        return None, None
    full = api.get(f"/pages/{tp['pageid']}")
    if int(full.get("sid") or 0) in dynamic_sids:
        return "designer", full
    txt = text_of(full.get("pagecontent") or "")
    if not txt or txt.lower() == (full.get("pagetitle") or "").strip().lower():
        return "placeholder", full
    return "content", full


# where a source page, policy or legal page lands when the target already has one for it
PAGE_SYNONYMS = {
    "terms-of-service": ["terms", "terms-and-conditions", "terms-conditions"], "terms-conditions": ["terms", "terms-and-conditions"],
    "terms-and-conditions": ["terms", "terms-conditions"], "terms": ["terms"],
    "privacy-policy": ["privacy", "privacy-policy"], "privacy": ["privacy"],
    "refund-policy": ["refund", "refunds", "returns"], "refunds": ["refund"], "returns": ["refund"],
    "shipping-policy": ["delivery", "shipping"], "delivery": ["delivery"], "flower-delivery": ["delivery"],
    "wedding": ["weddings", "wedding"], "weddings": ["weddings", "wedding"],
    "about": ["about", "about-us"], "about-us": ["about-us", "about"], "contact": ["contact", "contact-us"], "contact-us": ["contact"],
}

# ------------------------------------------------------------------ categories
cat_decisions = D.get("categories", {})
landing_of = {slug: d["landing_page"] for slug, d in cat_decisions.items() if isinstance(d, dict) and d.get("landing_page")}
landing_pages = {v: k for k, v in landing_of.items()}
page_by_slug = {pg["slug"]: pg for pg in SRCD["pages"]}
prod_by_slug = {p["slug"]: p for p in SRCD["products"]}

categories, cat_name_for_slug, redirects, cat_kind = [], {}, [], {}
extra_members = {}  # product slug -> [category name] from landing pages
for c in SRCD["categories"]:
    d = cat_decisions.get(c["slug"])
    old = urlparse(c["url"]).path
    if d is None:
        if c.get("product_count"):
            warnings.append(f"category '{c['name']}' ({c['product_count']} products) has no decision; skipped")
        categories.append({"ref": c["slug"], "action": "skip", "name": c["name"], "reason": "no decision", "source_url": c["url"]})
        continue
    if "skip" in d:
        categories.append({"ref": c["slug"], "action": "skip", "name": c["name"], "reason": d["skip"], "source_url": c["url"]})
        if "redirect_to" in d:
            redirects.append({"old": old, "to": {"path": d["redirect_to"]}})
        elif "redirect_to_category" in d:
            redirects.append({"old": old, "to": {"type": "category", "name": d["redirect_to_category"]}})
        elif "redirect_to_product" in d:
            redirects.append({"old": old, "to": {"type": "product", "ref": d["redirect_to_product"]}})
        if WORKSHOP.search(c["name"]):
            manual.append(f"Workshops: the old '{c['name']}' category belongs in the FSIAB Workshops feature. Set it up there.")
        continue
    # names are carried over exactly unless a decision deliberately overrides them
    display_name = d.get("override_name") or c["name"]
    name = d.get("map_to_existing") or safe_cat_name(display_name)
    if not d.get("map_to_existing") and name != display_name:
        warnings.append(f"category '{display_name}' is named '{name}' (names hold letters, digits and spaces only; the heading keeps the original)")
    if "map_to_existing" in d and name not in t_cats:
        sys.exit(f"decision maps '{c['slug']}' to '{name}' which does not exist on the target")
    cat_name_for_slug[c["slug"]] = name
    redirects.append({"old": old, "to": {"type": "category", "name": name}})

    # SEO: the florist's own title and description, exactly. A landing page (the page that
    # actually ranked for this category on WordPress sites) wins over the archive.
    lp = page_by_slug.get(d.get("landing_page")) if d.get("landing_page") else None
    seo_title = d.get("override_title") or (lp and lp["seo_title"]) or c.get("seo_title") or ""
    seo_desc = d.get("override_description") or (lp and lp["seo_description"]) or c.get("seo_description") or ""
    body = ""
    if lp:
        body, rep_ = clean(lp["html"])
        for s in re.findall(r"/product/([a-z0-9-]+)/", lp["html"]) + re.findall(r"/products/([a-z0-9-]+)", lp["html"]):
            if s in prod_by_slug:
                extra_members.setdefault(s, [])
                if name not in extra_members[s]:
                    extra_members[s].append(name)
        redirects.append({"old": urlparse(lp["url"]).path, "to": {"type": "category", "name": name}})
    elif text_of(c.get("description_html")) and c.get("description_visible", True):
        body = clean(c["description_html"])[0]
    body = strip_leading_heading(body, name, c["name"], lp and lp["title"])
    if name in t_cats:
        if "map_to_existing" not in d:
            warnings.append(f"category '{name}' already exists on the target; products are linked to it")
        cat_kind[name] = t_cats[name]["cat"]
        categories.append({"ref": c["slug"], "action": "existing", "name": name, "source_url": c["url"],
                           "seocontent": body if body and d.get("fill_existing_content") else ""})
        continue
    cat_kind[name] = d["cat"]
    categories.append({
        "ref": c["slug"], "action": "create", "name": name, "cat": d["cat"],
        "title": seo_title or f"{name} | {SITE}",
        "description": seo_desc or first_sentences(text_of(body)),
        "headingone": d.get("headingone", display_name), "seocontent": body,
        "landing_page": lp["url"] if lp else None,
        "source_ref": str(c["slug"]), "source_url": c["url"],
        "notes": ([] if seo_title else ["no SEO title on the old site; one was generated"]),
    })
for n_, c_ in t_cats.items():
    cat_kind.setdefault(n_, c_["cat"])

# ------------------------------------------------------------------ products
products, prod_slug_by_src = [], {}
used_slugs = {fsiab_slug(x["slug"]) for x in SRCD["products"]}


def _w(s):
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 2 and w not in {"the", "and", "with", "for", "copy"}}


def unique_slug(base):
    new, n_ = base, 2
    while new in used_slugs or new in t_prods or new in RESERVED:
        new, n_ = f"{base}-{n_}", n_ + 1
    used_slugs.add(new)
    return new


for p in SRCD["products"]:
    dd = D.get("products", {}).get(p["slug"], {})
    old = urlparse(p["url"]).path
    if dd.get("skip"):
        if dd.get("redirect_to"):
            redirects.append({"old": old, "to": {"path": dd["redirect_to"]}})
        continue
    if (GIFT_VOUCHER.search(p["name"]) or GIFT_VOUCHER.search(p.get("product_type") or "")) and not dd.get("keep"):
        manual.append(f"Gift vouchers: '{p['name']}' was a product on the old site. FSIAB has its own gift voucher feature; turn it on and set the amounts. Old URL now points to the voucher page.")
        redirects.append({"old": old, "to": {"path": VOUCHER_URL}})
        continue
    if WORKSHOP.search(p["name"]) and not dd.get("keep"):
        manual.append(f"Workshops: '{p['name']}' belongs in the FSIAB Workshops feature, not the shop. Set it up there.")
        redirects.append({"old": old, "to": {"path": dd.get("redirect_to", "/")}})
        continue
    pw = []
    slug = fsiab_slug(p["slug"])
    if slug != p["slug"]:
        pw.append(f"slug changed from '{p['slug']}' to '{slug}'")
    # Duplicated-then-renamed products keep a meaningless handle (spring-delight-copy-1 for
    # "Spring seasonal Native"). The redirect carries the old URL, so name the new one properly.
    if D.get("rename_mismatched_slugs", True) and (re.search(r"(^copy-of-|-copy(-\d+)?$|-copy-|^option-set-)", slug) or not (_w(slug) & _w(p["name"]))):
        new = unique_slug(fsiab_slug(p["name"]))
        pw.append(f"old URL /{slug}/ did not match the product name; new URL /{new}/ (old URL redirected)")
        slug = new
    if p["slug"].startswith("option-set-"):
        pw.append("this looks like a hidden product made by a Shopify product-options app; on FSIAB make it an addon or an option instead")
    if slug in RESERVED:
        pw.append(f"/{slug}/ is a built-in FSIAB route; product URL is /{safe_slug(slug, 'product')}/ instead (old URL redirected)")
        slug = safe_slug(slug, "product")
    used_slugs.add(slug)
    if slug in t_prods:
        pw.append(f"slug '{slug}' already used by a target product; the platform will append the new ID")
    prod_slug_by_src[p["slug"]] = slug

    tiers = [dict(t) for t in p["tiers"]]
    for t in tiers:
        t["desc"] = t.pop("label") or LABEL1
    tiers.sort(key=lambda t: float(t["price"]))
    if p.get("unpriced_options"):
        pw.append("options listed but never priced on the old site, not imported: " + ", ".join(p["unpriced_options"]))
    if p.get("single_price_options"):
        pw.append(f"old site listed options ({p['single_price_options']}) but charged one price; imported as one price")
    oos = [t["desc"] for t in tiers if not t.get("in_stock", True)]
    if oos and len(tiers) > 1:
        pw.append("out-of-stock options imported anyway: " + ", ".join(oos))
    pw.extend(p.get("notes") or [])
    for t_ in tiers:
        m_ = re.search(r"\$\s*([\d,]+(?:\.\d\d)?)", t_["desc"])
        if m_ and abs(float(m_.group(1).replace(",", "")) - float(t_["price"])) > 0.005:
            pw.append(f"option '{t_['desc']}' is priced at ${t_['price']} on the old site; the label and price disagree")
    if len({t["desc"] for t in tiers}) < len(tiers):
        pw.append("two or more options share a label; check the price list")
    if len(tiers) > 8:
        pw.append(f"{len(tiers)} options; FSIAB holds 8. Dropped: " + ", ".join(t["desc"] for t in tiers[8:]))
        tiers = tiers[:8]
    if not tiers:
        warnings.append(f"product '{p['name']}': no price, skipped")
        continue

    body_src = p["body_html"] if text_of(p["body_html"]) else p["short_html"]
    body_html, rep = clean(body_src)
    short_txt = text_of(p["short_html"]) or first_sentences(text_of(p["body_html"]), 300)
    if rep["iframes"]:
        pw.append(f"embedded media dropped from description: {rep['iframes']}")
    if [i for i in rep["images"] if old_host(i)]:
        pw.append("description contains images hosted on the old site")
    cats = []
    for s in p["categories"]:
        nm = cat_name_for_slug.get(s)
        if nm and nm not in cats:
            cats.append(nm)
        elif not nm and cat_decisions.get(s, {}).get("skip") is None:
            pw.append(f"old category '{s}' is not being imported")
    for nm in extra_members.get(p["slug"], []):
        if nm not in cats:
            cats.append(nm)
            pw.append(f"added to '{nm}' because the old '{nm}' page showed it")
    if dd.get("note"):
        pw.append(dd["note"])
    alcohol = dd.get("alcohol")
    if alcohol is None:
        label_txt = " ".join([p["name"], p.get("product_type") or "", " ".join(p.get("tags") or [])])
        desc_txt = re.sub(r"(?i)\bfree spirit\b", "", text_of(p["short_html"]) + " " + text_of(p["body_html"])[:600])
        desc_txt = re.sub(r"(?i)\b(add|optional|include)\w*\s+(a\s+)?(bottle of\s+)?(wine|bubbles|champagne|prosecco)\b", "", desc_txt)
        m_ = ALCOHOL.search(label_txt) or ALCOHOL_NAME_ONLY.search(label_txt) or ALCOHOL.search(desc_txt)
        alcohol = bool(m_)
        if m_:
            pw.append(f"marked as containing alcohol (found '{m_.group(0)}'); untick in the dashboard if wrong")
    alt0 = p["images"][0]["alt"] if p["images"] else ""
    loc = D.get("location")
    base = {
        "ref": p["slug"], "source_id": p["id"], "source_url": p["url"],
        "name": p["name"], "url": slug,
        "title": p["seo_title"] or f"{p['name']} | {SITE}",
        "descmeta": p["seo_description"] or first_sentences(short_txt),
        "headingone": p["name"], "description": body_html, "shortdesc": short_txt,
        "keywords": ", ".join(p.get("tags") or []),
        "imagealt": alt0 or (f"{p['name']} from {SITE}, {loc} florist" if loc else f"{p['name']} from {SITE}"),
        "instock": "yes" if p["in_stock"] else "no", "alcohol": "yes" if alcohol else "",
        "tiers": tiers, "categories": cats, "colours": p.get("colours") or [],
        "images": [i["src"] for i in p["images"]], "image_alts": [i["alt"] for i in p["images"]],
        "warnings": pw,
    }
    # Add-ons are ticked as one item at one price on FSIAB, so an add-on with several prices
    # becomes one add-on product per price ("Balloons - Helium", "Balloons - Small Stick").
    is_addon = any(cat_kind.get(n) == "addon" for n in cats)
    if is_addon and len(tiers) > 1 and D.get("split_addons", True):
        for i, t in enumerate(tiers):
            sp = dict(base)
            sp["ref"] = p["slug"] if i == 0 else f"{p['slug']}#{fsiab_slug(t['desc'])}"
            sp["name"] = f"{p['name']} - {t['desc']}"
            sp["headingone"] = sp["name"]
            sp["url"] = slug if i == 0 else unique_slug(fsiab_slug(f"{slug}-{t['desc']}"))
            sp["title"] = f"{p['name']} - {t['desc']} | {SITE}" if i else base["title"]
            sp["tiers"] = [dict(t)]
            sp["images"] = base["images"] if i == 0 else base["images"][:1]
            sp["image_alts"] = base["image_alts"] if i == 0 else base["image_alts"][:1]
            sp["colours"] = []
            sp["split_of"] = p["slug"]
            sp["warnings"] = list(pw) + [f"add-on with {len(tiers)} prices split into separate add-on products; this is '{t['desc']}'"]
            products.append(sp)
        redirects.append({"old": old, "to": {"type": "product", "ref": p["slug"]}})
        continue
    products.append(base)
    redirects.append({"old": old, "to": {"type": "product", "ref": p["slug"]}})

# ------------------------------------------------------------------ posts
posts = []
post_slug_by_src = {}
for bp in SRCD.get("posts", []):
    slug = fsiab_slug(bp["slug"])
    post_slug_by_src[bp["slug"]] = slug

# ------------------------------------------------------------------ link map (old path -> new)
page_by_path = {norm(urlparse(pg["url"]).path): pg for pg in SRCD["pages"]}
cat_by_path = {norm(urlparse(c["url"]).path): c for c in SRCD["categories"]}
prod_by_path = {norm(urlparse(p["url"]).path): p for p in SRCD["products"]}
post_by_path = {norm(urlparse(bp["url"]).path): bp for bp in SRCD.get("posts", [])}


def link_map(href):
    pu = urlparse(href)
    if pu.scheme in ("mailto", "tel", "javascript") or href.startswith("#"):
        return None
    if pu.netloc and pu.netloc.lower() not in SRC_HOSTS:
        return None
    path = norm(pu.path)
    frag = ("#" + pu.fragment) if pu.fragment else ""
    if "/wp-content/" in path or path.startswith("/cdn/") or "/files/" in path:
        return None  # files on the old host: reported, left alone
    if path in prod_by_path:
        s = prod_by_path[path]["slug"]
        return ("/" + prod_slug_by_src[s] + "/" + frag) if s in prod_slug_by_src else "/"
    m = re.match(r"^/collections/[^/]+/products/([^/]+)$", path)  # shopify product-in-collection URL
    if m and m.group(1) in prod_slug_by_src:
        return "/" + prod_slug_by_src[m.group(1)] + "/"
    if path in post_by_path:
        return f"/{BLOG_DIR}/{post_slug_by_src[post_by_path[path]['slug']]}/" + frag
    if path in cat_by_path:
        c = cat_by_path[path]
        nm = cat_name_for_slug.get(c["slug"])
        if nm:
            return "{{category:" + nm + "}}"
        return cat_decisions.get(c["slug"], {}).get("redirect_to", "/")
    if path in page_by_path:
        pg = page_by_path[path]
        if pg["slug"] in landing_pages and landing_pages[pg["slug"]] in cat_name_for_slug:
            return "{{category:" + cat_name_for_slug[landing_pages[pg["slug"]]] + "}}"
        for rule in D.get("page_rules", []):
            if "suburb_page" in rule and re.search(rule["match"], pg["slug"]):
                return "{{seo:" + pg["slug"] + "}}"
        return "{{page:" + pg["slug"] + "}}" + frag
    if path == "/":
        return "/"
    return (pu.path or "/") + frag  # unknown internal path: root-relative so a redirect can catch it


# ------------------------------------------------------------------ pages
pages, seo_pages, alias_redirects = [], [], []
for p in SRCD["pages"]:
    old = urlparse(p["url"]).path
    if p["slug"] in landing_pages and landing_pages[p["slug"]] in cat_name_for_slug:
        pages.append({"ref": p["slug"], "action": "category", "pagetitle": p["title"],
                      "reason": f"this page was the '{cat_name_for_slug[landing_pages[p['slug']]]}' category page; its content, meta and products went to that category",
                      "source_url": p["url"]})
        continue
    d = D.get("pages", {}).get(p["slug"])
    if d is None:
        d = {}
        for rule in D.get("page_rules", []):
            m = re.search(rule["match"], p["slug"])
            if m:
                if "alias_of" in rule:
                    d = {"alias_of": m.expand(rule["alias_of"])}
                elif "suburb_page" in rule:
                    raw_sub = (m.groupdict().get("suburb") or (m.group(1) if m.groups() else p["slug"]))
                    d = {"suburb_page": dict(rule["suburb_page"], suburb=raw_sub.replace("-", " ").title())}
                else:
                    d = {k: v for k, v in rule.items() if k != "match"}
                break
    if d.get("alias_of"):
        pages.append({"ref": p["slug"], "action": "skip", "pagetitle": p["title"], "reason": f"duplicate of /{d['alias_of']}; redirected there", "source_url": p["url"]})
        alias_redirects.append((old, d["alias_of"]))
        continue
    if d.get("suburb_page"):
        body, rep = clean(p["html"], link_map=link_map)
        sp = d["suburb_page"]
        seo_pages.append({
            "ref": p["slug"], "source_url": p["url"], "suburb": sp["suburb"], "city": sp["city"],
            "title": strip_site_suffix(p["seo_title"]) if p["seo_title"] else p["title"],
            "description": p["seo_description"] or first_sentences(text_of(body)),
            "heading": p["title"] or f"Florist {sp['suburb']}",
            "content": strip_leading_heading(body, p["title"]),
            "content_images": sorted({i for i in rep["images"] if old_host(i)}),
            "words": len(text_of(body).split()),
            "warnings": ([f"embeds dropped: {rep['iframes']}"] if rep["iframes"] else []),
        })
        redirects.append({"old": old, "to": {"type": "seo", "ref": p["slug"]}})
        pages.append({"ref": p["slug"], "action": "suburb", "pagetitle": p["title"], "reason": f"suburb page for {sp['suburb']}, {sp['city']}", "source_url": p["url"]})
        continue
    if d.get("skip"):
        pages.append({"ref": p["slug"], "action": "skip", "pagetitle": p["title"], "reason": d["skip"], "source_url": p["url"]})
        if d.get("redirect_to") and norm(d["redirect_to"]) != norm(old):
            redirects.append({"old": old, "to": {"path": d["redirect_to"]}})
        continue
    if p.get("noindex"):
        pages.append({"ref": p["slug"], "action": "skip", "pagetitle": p["title"], "reason": "noindex on the old site", "source_url": p["url"]})
        continue
    if WORKSHOP.search(p["slug"]) or WORKSHOP.search(p["title"]):
        manual.append(f"Workshops: the old '{p['title']}' page belongs in the FSIAB Workshops feature. Its copy is in this review for setting that up.")
        pages.append({"ref": p["slug"], "action": "skip", "pagetitle": p["title"], "reason": "workshops belong in the FSIAB Workshops feature", "source_url": p["url"],
                      "pagecontent": clean(p["html"], link_map=link_map, keep_h1=True)[0]})
        redirects.append({"old": old, "to": {"path": "/"}})
        continue
    body, rep = clean(p["html"], link_map=link_map, keep_h1=True)
    if "<h1" not in body:
        body = f"<h1>{html.escape(p['title'])}</h1>\n" + body
    policy = p.get("policy")
    slug = fsiab_slug(d.get("slug") or (policy or p["slug"]))
    pw = []
    if slug in RESERVED:
        pw.append(f"/{slug} is a built-in FSIAB route (e.g. the photo gallery module); page URL is /{safe_slug(slug, 'page')} instead")
        slug = safe_slug(slug, "page")
    files = [l for l in rep["links"] if "/wp-content/" in l or "cdn.shopify.com" in l or "/files/" in urlparse(l).path]
    if files:
        pw.append(f"links to files on the old site (break at cutover): {files}")
    if rep["bg_images"]:
        pw.append(f"{len(rep['bg_images'])} banner/background image(s) not carried over: {rep['bg_images']}")
    if rep["iframes"]:
        pw.append(f"embeds dropped: {rep['iframes']}")
    if rep["dropped_forms"]:
        pw.append(f"{rep['dropped_forms']} form(s) dropped; FSIAB forms need setting up separately")
    if re.search(r"<video|\.mp4", p["html"]):
        pw.append("video dropped; host on YouTube or Vimeo and embed")
    old_host_imgs = sorted({i for i in rep["images"] if old_host(i)})
    entry = {
        "ref": p["slug"], "source_id": p["id"], "source_url": p["url"],
        "pagetitle": strip_site_suffix(p["seo_title"]) if p["seo_title"] else p["title"], "pagemenu": p["title"],
        "pagefilename": slug, "sid": T_SID,
        "pagedescription": p["seo_description"] or first_sentences(text_of(body)),
        "pagecontent": body, "content_images": old_host_imgs,
        "external_images": sorted(set(rep["images"]) - set(old_host_imgs)),
        "words": len(text_of(body).split()), "warnings": pw,
    }
    # Does the target already have this page (same slug, or the usual FSIAB name for it)?
    targets = [d["fill_placeholder"]] if "fill_placeholder" in d else [slug] + PAGE_SYNONYMS.get(policy or p["slug"], [])
    decided = False
    for tslug in targets:
        if isinstance(tslug, int):
            tp = next((x for x in t_pages.values() if int(x["pageid"]) == tslug), None)
            kind, full = ("placeholder", tp) if tp else (None, None)
        else:
            kind, full = target_page_kind(tslug)
        if not kind:
            continue
        tfn = full.get("pagefilename")
        if kind == "placeholder":
            entry.update({"action": "fill", "pageid": int(full["pageid"]), "pagefilename": tfn})
            if tfn != slug:
                pw.append(f"filled the site's own /{tfn} page")
        elif kind == "designer":
            entry.update({"action": "skip", "reason": f"the site has a designed /{tfn} page; copy left for the designer", "designer_target": tfn})
            manual.append(f"Designer page /{tfn}: the old '{p['title']}' copy is in this review for the designer to use.")
            redirects.append({"old": old, "to": {"path": "/" + tfn}})
        else:
            entry.update({"action": "skip", "reason": f"the site already has real content at /{tfn}; decide whether to replace it"})
            warnings.append(f"page '{p['title']}': /{tfn} already has content on the target; needs a decision")
        decided = True
        break
    if not decided:
        entry["action"] = "create"
    if slug in prod_slug_by_src.values():
        pw.append(f"a product also uses the slug '{slug}'")
    pages.append(entry)
    if entry["action"] in ("create", "fill"):
        redirects.append({"old": old, "to": {"type": "page", "ref": p["slug"]}})

for old, of in alias_redirects:
    tgt = next((x for x in seo_pages if x["ref"] == of), None)
    if tgt:
        redirects.append({"old": old, "to": {"type": "seo", "ref": of}})
        continue
    pg = next((x for x in pages if x["ref"] == of and x["action"] in ("create", "fill")), None)
    redirects.append({"old": old, "to": {"type": "page", "ref": of} if pg else {"path": "/"}})

# ------------------------------------------------------------------ posts (after pages, so links resolve)
for bp in SRCD.get("posts", []):
    body, rep = clean(bp["html"], link_map=link_map)
    body = strip_leading_heading(body, bp["title"])
    title = strip_site_suffix(bp["seo_title"]) if bp.get("seo_title") else bp["title"]
    posts.append({
        "ref": bp["slug"], "source_url": bp["url"], "url": post_slug_by_src[bp["slug"]],
        "title": bp["title"], "seo_title": title, "heading": bp["title"],
        "description": bp.get("seo_description") or first_sentences(text_of(body)),
        "content": body, "date": bp.get("date") or "", "author": bp.get("author") or "",
        "categories": bp.get("categories") or [], "image": bp.get("image") or "",
        "content_images": sorted({i for i in rep["images"] if old_host(i)}),
        "words": len(text_of(body).split()),
        "warnings": ([f"embeds dropped: {rep['iframes']}"] if rep["iframes"] else []),
    })
    redirects.append({"old": urlparse(bp["url"]).path, "to": {"type": "post", "ref": bp["slug"]}})

manifest = {"source": SRC, "platform": SRCD["platform"], "site_name": SITE, "import_batch": D["import_batch"],
            "blog_directory": BLOG_DIR,
            "categories": categories, "products": products, "pages": pages, "seo_pages": seo_pages, "posts": posts,
            "redirects": redirects, "warnings": warnings, "manual": sorted(set(manual))}
json.dump(manifest, open(os.path.join(WORK, "manifest.json"), "w"), ensure_ascii=False, indent=1)

cnt = lambda xs, a: sum(1 for x in xs if x.get("action") == a)
print(f"categories: create {cnt(categories,'create')}, existing {cnt(categories,'existing')}, skip {cnt(categories,'skip')}")
print(f"products:   {len(products)}  tiers {sum(len(p['tiers']) for p in products)}  images {sum(len(p['images']) for p in products)}  "
      f"colours {sum(len(p['colours']) for p in products)}  alcohol {sum(1 for p in products if p['alcohol'])}  split add-ons {sum(1 for p in products if p.get('split_of'))}")
print(f"pages:      create {cnt(pages,'create')}, fill {cnt(pages,'fill')}, skip {cnt(pages,'skip')}, to category {cnt(pages,'category')}  content images {len({i for p in pages for i in p.get('content_images', [])})}")
print(f"suburb pages: {len(seo_pages)}   posts: {len(posts)} (images {len({i for p in posts for i in p['content_images']})})")
print(f"redirects:  {len(redirects)}")
for w in warnings:
    print("WARN", w)
for m in manifest["manual"]:
    print("MANUAL", m)
