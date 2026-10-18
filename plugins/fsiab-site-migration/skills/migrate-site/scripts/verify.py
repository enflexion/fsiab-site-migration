"""Verify an import against its manifest, through the API and the live storefront.

Usage: verify.py WORKDIR TARGET_KEY SITE_BASE_URL [--redirects OLD_BASE]
Writes WORKDIR/verify.json and prints a summary. Read only.
"""
import sys, os, json, re, time, urllib.request, urllib.error
from urllib.parse import urlparse
from fsiab import Fsiab

WORK, KEY, SITE = sys.argv[1], sys.argv[2], sys.argv[3].rstrip("/")
M = json.load(open(os.path.join(WORK, "manifest.json")))
S = json.load(open(os.path.join(WORK, "state.json")))
api = Fsiab(KEY, pause=0.4)


def fetch(url, follow=True):
    class NoRedir(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    opener = urllib.request.build_opener() if follow else urllib.request.build_opener(NoRedir)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (FSIAB migration verify)"})
    try:
        with opener.open(req, timeout=60) as r:
            return r.status, r.read().decode("utf-8", "replace"), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), e.headers


def text_of_html(h):
    import html as _h
    return re.sub(r"\s+", " ", _h.unescape(re.sub(r"<[^>]+>", " ", h or ""))).strip()


def unmojibake(s):
    """API v3 GET can return non-ASCII text UTF-8-encoded twice (open platform issue,
    2026-09-29). Undo one layer when that is what happened; leave good text alone."""
    if not s:
        return s
    try:
        fixed = s.encode("latin-1").decode("utf-8")
        return fixed
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def title_of(html):
    m = re.search(r"<title>(.*?)</title>", html, re.S)
    import html as h
    return h.unescape(m.group(1).strip()) if m else None


res = {"products": [], "pages": [], "categories": [], "images": [], "redirects": []}
bad = lambda xs: [x for x in xs if x.get("problems")]

print("== products")
for p in M["products"]:
    st = S["products"].get(p["ref"])
    r = {"ref": p["ref"], "problems": []}
    if not st:
        r["problems"].append("not created")
        res["products"].append(r); continue
    d = api.get(f"/products/{st['cat_id']}")
    words = ["one", "two", "three", "four", "five", "six", "seven", "eight"]
    for w, t in zip(words, p["tiers"]):
        if abs(float(d.get(f"price_{w}") or 0) - float(t["price"])) > 0.001 or d.get(f"desc_{w}") != t["desc"]:
            r["problems"].append(f"tier {w}: {d.get(f'desc_{w}')} {d.get(f'price_{w}')} != {t['desc']} {t['price']}")
    if sorted(c["name"] for c in d.get("categories", [])) != sorted(p["categories"]):
        r["problems"].append(f"categories {[c['name'] for c in d.get('categories', [])]} != {p['categories']}")
    if len(d.get("gallery", [])) != max(0, len(p["images"]) - 1):
        r["problems"].append(f"gallery {len(d.get('gallery', []))} != {len(p['images']) - 1}")
    if len(d.get("colours") or []) != len(p.get("colours") or []):
        r["problems"].append(f"colours {len(d.get('colours') or [])} != {len(p.get('colours') or [])}")
    if (d.get("alcohol") or "") != (p.get("alcohol") or ""):
        r["problems"].append(f"alcohol flag {d.get('alcohol')!r} != {p.get('alcohol')!r}")
    if unmojibake(d.get("title")) != p["title"]:
        r["problems"].append("seo title differs")
    code, body, _ = fetch(f"{SITE}/{d['url']}/")
    if code != 200:
        r["problems"].append(f"product page HTTP {code}")
    else:
        if f"images/products/large/{st['cat_id']}.jpg" not in body and f"images/products/medium/{st['cat_id']}.jpg" not in body:
            r["problems"].append("main image not on page")
        if title_of(body) != p["title"]:
            r["problems"].append(f"page title {title_of(body)!r}")
    ic, _, ih = fetch(f"{SITE}/images/products/large/{st['cat_id']}.jpg?t=1")
    if ic != 200 or "image" not in (ih.get("Content-Type") or ""):
        r["problems"].append(f"main image HTTP {ic}")
    for g in d.get("gallery", []):
        gc, _, gh = fetch(g["image_url"] + "?t=1")
        if gc != 200:
            r["problems"].append(f"gallery image {g['id']} HTTP {gc}")
    res["products"].append(r)
print(f"  {len(res['products'])} checked, {len(bad(res['products']))} with problems")

print("== categories")
for c in M["categories"]:
    if c["action"] != "create":
        continue
    st = S["categories"].get(c["name"])
    r = {"ref": c["ref"], "problems": []}
    if not st:
        r["problems"].append("not created")
    else:
        code, body, _ = fetch(st["url"])
        if code != 200:
            r["problems"].append(f"category page HTTP {code}")
        elif title_of(body) != c["title"]:
            r["problems"].append(f"page title {title_of(body)!r}")
        else:
            probe = text_of_html(c.get("seocontent") or "")[:50]
            if probe and probe not in text_of_html(body):
                r["problems"].append("category content not on the page")
    res["categories"].append(r)
print(f"  {len(res['categories'])} checked, {len(bad(res['categories']))} with problems")

print("== pages")
for pg in M["pages"]:
    if pg["action"] not in ("create", "fill"):
        continue
    st = S["pages"].get(pg["ref"])
    r = {"ref": pg["ref"], "problems": []}
    if not st:
        r["problems"].append("not created")
        res["pages"].append(r); continue
    code, body, _ = fetch(f"{SITE}/{st['pagefilename']}")
    r["status"] = code
    if code != 200:
        r["problems"].append(f"page HTTP {code} (new pages are created as pending)")
    else:
        old_imgs = re.findall(r"https?://(?:www\.)?" + re.escape(urlparse(M["source"]).netloc.replace("www.", "")) + r"/wp-content/[^\"' )]+", body)
        if old_imgs:
            r["problems"].append(f"{len(old_imgs)} references to the old host remain")
    if st.get("images_left_on_old_host"):
        r["problems"].append(f"{len(st['images_left_on_old_host'])} images not rehosted")
    res["pages"].append(r)
print(f"  {len(res['pages'])} checked, {len(bad(res['pages']))} with problems")

if M.get("seo_pages"):
    res["seo"] = []
    print("== suburb pages")
    for sp in M["seo_pages"]:
        st = (S.get("seo") or {}).get(sp["ref"])
        r = {"ref": sp["ref"], "problems": []}
        if not st:
            r["problems"].append("not created")
        else:
            code, body, _ = fetch(SITE + urlparse(st["url"]).path)
            if code != 200:
                r["problems"].append(f"suburb page HTTP {code}")
            elif title_of(body) != sp["title"]:
                r["problems"].append(f"page title {title_of(body)!r}")
        res["seo"].append(r)
    print(f"  {len(res['seo'])} checked, {len(bad(res['seo']))} with problems")

print("== content images (sample of 30)")
for src, path in list(S["images"].items())[:30]:
    code, _, h = fetch(f"{SITE}{path}?t=1")
    if code != 200:
        res["images"].append({"src": src, "path": path, "problems": [f"HTTP {code}"]})
print(f"  {len(res['images'])} failures in sample")

if M.get("posts"):
    res["posts"] = []
    print("== blog posts")
    for bp in M["posts"]:
        st = (S.get("posts") or {}).get(bp["ref"])
        r = {"ref": bp["ref"], "problems": []}
        if not st:
            r["problems"].append("not created")
        else:
            code, body, _ = fetch(f"{SITE}/{M.get('blog_directory', 'blog')}/{st['url']}/")
            if code != 200:
                r["problems"].append(f"post page HTTP {code}")
            elif text_of_html(bp["content"])[:40] not in text_of_html(body):
                r["problems"].append("post content not on the page")
        res["posts"].append(r)
    print(f"  {len(res['posts'])} checked, {len(bad(res['posts']))} with problems")

if "--redirects" in sys.argv:
    print("== redirects (on the dev site)")
    for rd in api.get("/redirects")["redirects"]:
        code, _, h = fetch(f"{SITE}{rd['old']}", follow=False)
        loc = h.get("Location") or ""
        ok = code in (301, 302) and urlparse(loc).path == urlparse(rd["new_url"]).path
        res["redirects"].append({"old": rd["old"], "new": rd["new"], "code": code, "location": loc, "problems": [] if ok else [f"{code} -> {loc}"]})
    print(f"  {len(res['redirects'])} checked, {len(bad(res['redirects']))} with problems")

json.dump(res, open(os.path.join(WORK, "verify.json"), "w"), ensure_ascii=False, indent=1)
for k in res:
    for x in bad(res[k])[:15]:
        print(f"  {k}: {x.get('ref') or x.get('old') or x.get('src')}: {x['problems']}")
