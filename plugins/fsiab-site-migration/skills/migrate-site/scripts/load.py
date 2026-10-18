"""Load a migration manifest into an FSIAB site through API v3.

Usage: load.py WORKDIR TARGET_KEY [--dry-run] [--only categories,products,images,pages,redirects]

Order: categories, products (+ main image + gallery), content images, pages, redirects.
State is kept in WORKDIR/state.json so a crashed or interrupted run resumes where it stopped.
Every API call is logged to WORKDIR/load-log.jsonl.
"""
import sys, os, json, re, time, hashlib, subprocess, unicodedata, urllib.request, urllib.parse, urllib.error
from urllib.parse import urlparse
from fsiab import Fsiab, ApiError

WORK, KEY = sys.argv[1], sys.argv[2]
DRY = "--dry-run" in sys.argv
ONLY = None
if "--only" in sys.argv:
    ONLY = set(sys.argv[sys.argv.index("--only") + 1].split(","))
want = lambda step: ONLY is None or step in ONLY

M = json.load(open(os.path.join(WORK, "manifest.json")))
STATE_F = os.path.join(WORK, "state.json" if not DRY else "state-dryrun.json")
S = json.load(open(STATE_F)) if os.path.exists(STATE_F) else {"categories": {}, "products": {}, "images": {}, "pages": {}, "redirects": None, "errors": []}
S.setdefault("seo", {})
S.setdefault("posts", {})
S.setdefault("colours", {})
S.setdefault("existing_categories", {})
S.setdefault("broken_images", {})
IMG_DIR = os.path.join(WORK, "images")
os.makedirs(IMG_DIR, exist_ok=True)
log = open(os.path.join(WORK, "load-log.jsonl" if not DRY else "load-log-dryrun.jsonl"), "a")
api = Fsiab(KEY, pause=1.1, log=log)
BATCH = M["import_batch"]
PLATFORM = M["platform"]


def save():
    json.dump(S, open(STATE_F, "w"), ensure_ascii=False, indent=1)


def err(where, e):
    msg = f"{where}: {e}"
    print("  ERROR", msg)
    S["errors"].append({"t": time.strftime("%H:%M:%S"), "where": where, "error": str(e)})
    save()


def tag(ref, url):
    return {"import_batch": BATCH, "source_platform": PLATFORM, "source_ref": str(ref), "source_url": url}


def slugify(s):  # shared.pl slugify, used for category URLs
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^\w\s-]", "", s).strip().lower()
    return re.sub(r"[-\s]+", "-", s)


def category_path(name, cat):
    sl = slugify(name)
    return {"occasion": f"/{sl}-flowers/", "flower": f"/flower-{sl}/", "addon": f"/addons/{sl}/"}.get(cat, f"/{sl}/")


# existing categories on the target (for {{category:X}} links and redirects)
T_CATS = {c["name"]: c for c in api.get("/categories")["categories"]}
CAT_PATH = {n: category_path(n, c["cat"]) for n, c in T_CATS.items()}
for n, st in S["categories"].items():
    if st.get("url"):
        CAT_PATH[n] = urlparse(st["url"]).path


def download(url):
    """Fetch a source image once into the work cache, converting to JPEG. Returns local path."""
    if url.startswith("//"):
        url = "https:" + url  # Shopify writes protocol-relative image URLs
    h = hashlib.sha1(url.encode()).hexdigest()[:10]
    base = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(urlparse(url).path)) or "image"
    stem = os.path.splitext(base)[0][:60]
    out = os.path.join(IMG_DIR, f"{stem}-{h}.jpg")
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out
    tmp = out + ".src"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (FSIAB site migration)"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
                f.write(r.read())
            break
        except Exception as e:
            if attempt == 3:
                raise
            time.sleep(3 * (attempt + 1))
    with open(tmp, "rb") as f:
        head = f.read(12)
    if head[:3] == b"\xff\xd8\xff":
        os.replace(tmp, out)
    else:  # png, webp, heic, gif -> jpeg (macOS sips)
        subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "85", tmp, "--out", out],
                       check=True, capture_output=True)
        os.remove(tmp)
    return out


# ------------------------------------------------------------------ categories
def load_categories():
    print("== categories")
    for c in M["categories"]:
        if c["action"] != "create":
            continue
        if c["name"] in S["categories"] and not DRY:
            continue
        body = {"name": c["name"], "cat": c["cat"], "title": c["title"], "description": c["description"],
                "headingone": c["headingone"], **tag(c["source_ref"], c["source_url"])}
        if c.get("seocontent"):
            body["seocontent"] = c["seocontent"]
        if DRY:
            body["dry_run"] = True
        try:
            r = api.post("/categories", body)
            url = (r.get("would_create") or r).get("url")
            print(f"  {'dry' if DRY else 'ok '} {c['name']} -> {url}")
            S["categories"][c["name"]] = {"url": url, "already_existed": r.get("already_existed", False)}
            if url:
                CAT_PATH[c["name"]] = urlparse(url).path
            save()
        except ApiError as e:
            err(f"category {c['name']}", e)
    # categories that already existed on the target: add content where the decision asked for it
    for c in M["categories"]:
        if c["action"] != "existing" or not c.get("seocontent") or DRY or S["existing_categories"].get(c["name"]):
            continue
        try:
            api.put(f"/categories/{urllib.parse.quote(c['name'])}/content", {"seocontent": resolve_content(c["seocontent"])})
            S["existing_categories"][c["name"]] = True
            save()
            print(f"  ok  content added to existing category {c['name']}")
        except ApiError as e:
            err(f"content for existing category {c['name']}", e)


# ------------------------------------------------------------------ products
def product_body(p):
    b = {k: p[k] for k in ("name", "url", "title", "descmeta", "headingone", "description", "shortdesc", "keywords", "imagealt", "instock")}
    if p.get("alcohol"):
        b["alcohol"] = p["alcohol"]
    words = ["one", "two", "three", "four", "five", "six", "seven", "eight"]
    for w, t in zip(words, p["tiers"]):
        b[f"price_{w}"], b[f"desc_{w}"] = t["price"], t["desc"]
        if t.get("sku"):
            b[f"sku_{w}"] = t["sku"]
    b["categories"] = p["categories"]
    # split add-ons share one source product, so each needs its own source_ref or the API's
    # import idempotency hands every split the first split's product (found 2026-09-29)
    b.update(tag(p["ref"] if p.get("split_of") else p["source_id"], p["source_url"]))
    return b


def load_products():
    print("== products")
    for p in M["products"]:
        st = S["products"].get(p["ref"], {})
        if not st.get("cat_id") or DRY:
            body = product_body(p)
            if DRY:
                body["dry_run"] = True
                planned = {c["name"] for c in M["categories"] if c["action"] == "create"}
                body["categories"] = [c for c in body["categories"] if c not in planned or c in T_CATS]
            try:
                r = api.post("/products", body)
                if DRY:
                    print(f"  dry {p['name']} -> /{r['would_create']['url']}/  {r.get('warnings') or ''}")
                    continue
                st = {"cat_id": r["cat_id"], "url": r["url"], "already_existed": r.get("already_existed", False)}
                S["products"][p["ref"]] = st
                save()
                print(f"  ok  {p['name']} -> {r['cat_id']} /{r['url']}/{'  (already existed)' if st['already_existed'] else ''}")
            except ApiError as e:
                err(f"product {p['name']}", e)
                continue
        # manifest URL changed since the product was created (e.g. a renamed slug): update it
        if not DRY and st.get("cat_id") and st.get("url") and st["url"] != p["url"]:
            try:
                r = api.put(f"/products/{st['cat_id']}/slug", {"url": p["url"]})
                print(f"  ok  slug {st['url']} -> {r['url']}  ({p['name']})")
                st["url"] = r["url"]
                save()
            except ApiError as e:
                err(f"slug for {p['name']}", e)
        if DRY or not p["images"]:
            continue
        # main image
        if not st.get("image"):
            try:
                fp = download(p["images"][0])
                api.upload(f"/products/{st['cat_id']}/image", [("image", fp)])
                st["image"] = True
                save()
            except Exception as e:
                err(f"image for {p['name']}", e)
        # gallery: remaining images in source order, one file per call. Progress is recorded per
        # source image (gallery_by_src: src -> gallery id, or "rejected"), so a retry never
        # duplicates. A photo the platform's upload check rejects is recorded and skipped; any
        # other failure stops this product's gallery so order is kept, and a re-run resumes.
        extra = p["images"][1:]
        if extra and not st.get("gallery_done"):
            by_src = st.get("gallery_by_src")
            if by_src is None:
                # older state: an ordered list of ids, or rows already on the product
                ids = st.get("gallery") or []
                if not ids:
                    try:
                        ids = [g["id"] for g in api.get(f"/products/{st['cat_id']}/gallery")["gallery"]]
                    except ApiError:
                        ids = []
                by_src = {src: gid for src, gid in zip(extra, ids)}
                st["gallery_by_src"] = by_src
            for i, src in enumerate(extra, start=1):
                if src in by_src:
                    continue
                try:
                    name = p["image_alts"][i] if i < len(p["image_alts"]) and p["image_alts"][i] else p["name"]
                    r = api.upload(f"/products/{st['cat_id']}/gallery", [("image", download(src))], [("name", name)])
                    by_src[src] = r["added"][0]["id"]
                    save()
                except ApiError as e:
                    err(f"gallery image {i} for {p['name']} ({src})", e)
                    if e.code == 400 and "does not look like" in e.message:
                        by_src[src] = "rejected"
                        save()
                        continue
                    break
                except Exception as e:
                    err(f"gallery image {i} for {p['name']} ({src})", e)
                    break
            if all(src in by_src for src in extra):
                st["gallery_done"] = True
                save()
        # colour options (the dashboard's colours table), each with its photo when the source had one
        done_c = S["colours"].setdefault(p["ref"], {})
        for c in p.get("colours") or []:
            if c["colour"] in done_c:
                continue
            try:
                files = [("image", download(c["image"]))] if c.get("image") else []
                r = api.upload(f"/products/{st['cat_id']}/colours", files, [("colour", c["colour"]), ("sku", c.get("sku") or "")])
                done_c[c["colour"]] = r["added"]["id"]
                save()
            except ApiError as e:
                if e.code == 409:
                    done_c[c["colour"]] = "exists"
                    save()
                    continue
                err(f"colour {c['colour']} for {p['name']}", e)


# ------------------------------------------------------------------ content images
def rehost(src):
    """Upload a content image to the site's library once; return its root-relative path."""
    if src in S["images"]:
        return S["images"][src]
    fp = download(src)
    parts = urlparse(src).path.split("/")
    prefix = "-".join(x for x in parts if re.fullmatch(r"\d{2,4}", x))  # wp uploads/yyyy/mm
    name = (prefix + "-" if prefix else "") + re.sub(r"[^A-Za-z0-9._-]", "_", os.path.splitext(os.path.basename(urlparse(src).path))[0]) + ".jpg"
    named = os.path.join(IMG_DIR, "up-" + name)
    if not os.path.exists(named):
        os.link(fp, named) if not os.path.exists(named) else None
    r = api.upload("/images", [("image", named)])
    if r.get("errors"):
        raise ApiError(400, str(r["errors"]))
    path = urlparse(r["images"][0]["url"]).path
    S["images"][src] = path
    save()
    return path


def load_images():
    print("== content images")
    todo = sorted({i for pg in M["pages"] if pg["action"] in ("create", "fill") for i in pg.get("content_images", [])}
                  | {i for sp in M.get("seo_pages", []) for i in sp.get("content_images", [])}
                  | {i for bp in M.get("posts", []) for i in bp.get("content_images", [])}
                  | {i for c in M["categories"] for i in re.findall(r'<img[^>]*\bsrc="([^"]+)"', c.get("seocontent") or "")
                     if re.match(r"https?://|//", i)})
    print(f"  {len(todo)} unique, {sum(1 for i in todo if i in S['images'])} already done")
    if DRY:
        return
    for n, src in enumerate(todo, 1):
        if src in S["images"]:
            continue
        if src in S["broken_images"]:
            continue
        try:
            rehost(src)
            if n % 20 == 0:
                print(f"  {n}/{len(todo)}")
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                S["broken_images"][src] = e.code  # already broken on the old site: dropped from content
                save()
            err(f"content image {src}", e)
        except Exception as e:
            err(f"content image {src}", e)


# ------------------------------------------------------------------ pages
def resolve_content(html):
    # images already broken on the old site (404) are dropped rather than carried across
    html = re.sub(r'<img[^>]*\bsrc="([^"]+)"[^>]*>', lambda m: "" if m.group(1) in S["broken_images"] else m.group(0), html)
    def img(m):
        src = m.group(2)
        return m.group(1) + S["images"].get(src, src) + m.group(3)
    html = re.sub(r'(<img[^>]*\bsrc=")([^"]+)(")', img, html)
    html = re.sub(r"\{\{category:([^}]+)\}\}", lambda m: CAT_PATH.get(m.group(1), "/"), html)
    html = re.sub(r"\{\{seo:([^}]+)\}\}", lambda m: urlparse((S["seo"].get(m.group(1)) or {}).get("url") or "/").path, html)
    html = re.sub(r"\{\{page:([^}]+)\}\}", lambda m: page_path(m.group(1)), html)
    return html


def page_path(ref):
    """Where a source page ended up: its new page, the page it filled, a designer page, or its
    redirect target. Falls back to the home page."""
    st = S["pages"].get(ref)
    if st:
        return "/" + st["pagefilename"]
    pg = next((x for x in M["pages"] if x["ref"] == ref), None)
    if not pg:
        return "/"
    if pg.get("action") in ("create", "fill"):
        return "/" + pg["pagefilename"]
    if pg.get("designer_target"):
        return "/" + pg["designer_target"]
    r = next((x for x in M["redirects"] if urlparse(pg["source_url"]).path == x["old"]), None)
    return (target_path(r["to"]) if r else None) or "/"


REFRESH = "--refresh-pages" in sys.argv


def load_pages():
    print("== pages" + (" (refresh content of pages already loaded)" if REFRESH else ""))
    for pg in M["pages"]:
        if pg["action"] not in ("create", "fill"):
            continue
        if REFRESH and pg["ref"] in S["pages"] and not DRY:
            st = S["pages"][pg["ref"]]
            try:
                api.put(f"/pages/{st['pageid']}", {"pagetitle": pg["pagetitle"], "pagedescription": pg["pagedescription"],
                                                    "pagecontent": resolve_content(pg["pagecontent"])})
                st["images_left_on_old_host"] = [x for x in pg.get("content_images", []) if x not in S["images"]]
                save()
                print(f"  ok  refresh /{st['pagefilename']}")
            except ApiError as e:
                err(f"refresh page {pg['ref']}", e)
            continue
        if pg["ref"] in S["pages"] and not DRY:
            continue
        content = resolve_content(pg["pagecontent"])
        left = [s for s in pg.get("content_images", []) if s not in S["images"]]
        if DRY:
            print(f"  dry {pg['action']:6} /{pg['pagefilename']}  {len(content)} chars  images still on old host: {len(left)}")
            continue
        try:
            if pg["action"] == "create":
                r = api.post("/pages", {"pagetitle": pg["pagetitle"], "pagemenu": pg["pagemenu"], "pagefilename": pg["pagefilename"],
                                        "pagedescription": pg["pagedescription"], "pagecontent": content, "sid": pg.get("sid", 2)})
                pid, fn = r["pageid"], r["pagefilename"]
            else:
                r = api.put(f"/pages/{pg['pageid']}", {"pagetitle": pg["pagetitle"], "pagedescription": pg["pagedescription"], "pagecontent": content})
                pid, fn = pg["pageid"], pg["pagefilename"]
            S["pages"][pg["ref"]] = {"pageid": pid, "pagefilename": fn, "action": pg["action"], "images_left_on_old_host": left}
            save()
            print(f"  ok  {pg['action']:6} /{fn} (page {pid}){'  WARN images left: ' + str(len(left)) if left else ''}")
        except ApiError as e:
            err(f"page {pg['ref']}", e)


# ------------------------------------------------------------------ suburb pages
def load_seo():
    """Suburb landing pages go to FSIAB's suburb SEO pages (the `city` table). If the target
    already has a page for that suburb and city it is updated with the florist's own copy;
    otherwise one is created. Redirects point the old URL at it."""
    sps = M.get("seo_pages", [])
    if not sps:
        return
    print(f"== suburb pages ({len(sps)})")
    existing = {}
    for p in api.get("/seo")["pages"]:
        existing[(p["name"].strip().lower(), (p.get("city") or "").strip().lower())] = p
    for sp in sps:
        if sp["ref"] in S["seo"] and not DRY and "--refresh-seo" not in sys.argv:
            continue
        key = (sp["suburb"].lower(), sp["city"].lower())
        hit = existing.get(key)
        body = {"title": sp["title"], "description": sp["description"], "heading": sp["heading"],
                "content": resolve_content(sp["content"])}
        if DRY:
            url = hit["url"] if hit else f"/{slugify(sp['city'])}-flowers/{slugify(sp['suburb'])}.shtml"
            S["seo"][sp["ref"]] = {"url": url, "action": "update" if hit else "create"}
            print(f"  dry {'update' if hit else 'create'} {sp['suburb']}, {sp['city']} -> {urlparse(url).path}")
            continue
        try:
            if hit:
                r = api.put(f"/seo/{hit['id']}", body)
                S["seo"][sp["ref"]] = {"id": hit["id"], "url": r.get("url") or hit["url"], "action": "update"}
            else:
                r = api.post("/seo", dict(body, name=sp["suburb"], city=sp["city"]))
                S["seo"][sp["ref"]] = {"id": r["id"], "url": r["url"], "action": "create"}
            save()
            print(f"  ok  {S['seo'][sp['ref']]['action']:6} {sp['suburb']} -> {urlparse(S['seo'][sp['ref']]['url']).path}")
        except ApiError as e:
            err(f"suburb page {sp['suburb']}", e)


# ------------------------------------------------------------------ blog posts
def load_posts():
    bps = M.get("posts", [])
    if not bps:
        return
    print(f"== blog posts ({len(bps)})")
    for bp in bps:
        st = S["posts"].get(bp["ref"])
        if DRY:
            print(f"  dry /{M.get('blog_directory', 'blog')}/{bp['url']}/  {bp['title'][:60]}")
            continue
        if st and "--refresh-posts" in sys.argv:
            try:
                api.put(f"/blogs/{st['id']}", {"content": resolve_content(bp["content"])})
            except ApiError as e:
                err(f"refresh post {bp['title']}", e)
        if not st:
            try:
                r = api.post("/blogs", {"title": bp["seo_title"] or bp["title"], "heading": bp["heading"], "description": bp["description"],
                                        "url": bp["url"], "content": resolve_content(bp["content"]), "date": bp["date"] or None,
                                        "status": "yes", "categories": bp["categories"]})
                st = {"id": r.get("id"), "url": r.get("url") or bp["url"]}
                S["posts"][bp["ref"]] = st
                save()
            except ApiError as e:
                err(f"post {bp['title']}", e)
                continue
        # Blog header images and colour images share /images/products/large/{number}.jpg (blog id vs
        # colour row id). Do not overwrite a colour's photo: skip the header image and flag it.
        colour_ids = {str(v) for d_ in S["colours"].values() for v in d_.values()}
        if bp.get("image") and not st.get("image") and str(st.get("id")) in colour_ids:
            if not st.get("image_clash"):
                st["image_clash"] = True
                save()
                err(f"image for post {bp['title']}", "skipped: blog id clashes with a colour image id (platform issue: both use images/products/large/{id}.jpg)")
        elif bp.get("image") and not st.get("image"):
            try:
                api.upload(f"/blogs/{st['id']}/image", [("image", download(bp["image"]))])
                st["image"] = True
                save()
            except Exception as e:
                err(f"image for post {bp['title']}", e)
    print(f"  {len(S['posts'])} posts loaded")


# ------------------------------------------------------------------ redirects
def target_path(to):
    if "path" in to:
        return to["path"]
    if to["type"] == "product":
        st = S["products"].get(to["ref"])
        if st and st.get("url"):
            return f"/{st['url']}/"
        p = next((x for x in M["products"] if x["ref"] == to["ref"]), None)
        return f"/{p['url']}/" if p else None
    if to["type"] == "category":
        return CAT_PATH.get(to["name"])
    if to["type"] == "post":
        st = S["posts"].get(to["ref"])
        bp = next((x for x in M.get("posts", []) if x["ref"] == to["ref"]), None)
        slug = (st or {}).get("url") or (bp or {}).get("url")
        return f"/{M.get('blog_directory', 'blog')}/{slug}/" if slug else None
    if to["type"] == "seo":
        st = S["seo"].get(to["ref"])
        return urlparse(st["url"]).path if st and st.get("url") else None
    if to["type"] == "page":
        st = S["pages"].get(to["ref"])
        if st:
            return "/" + st["pagefilename"]
        pg = next((x for x in M["pages"] if x["ref"] == to["ref"]), None)
        return ("/" + pg["pagefilename"]) if pg and pg.get("action") in ("create", "fill") and not DRY else (("/" + pg["pagefilename"]) if pg and pg.get("pagefilename") else None)
    return None


def load_redirects():
    print("== redirects")
    for c in M["categories"]:
        if c["action"] == "create" and c["name"] not in CAT_PATH:
            CAT_PATH[c["name"]] = category_path(c["name"], c["cat"])
    items, unresolved, held = [], [], []
    for r in M["redirects"]:
        t = target_path(r["to"])
        if not t:
            unresolved.append(r)
            continue
        if r["old"].rstrip("/") == t.rstrip("/") and not (r["old"].endswith("/") and not t.endswith("/")):
            continue  # same URL on both sites, nothing to do
        if "--hold-slash-only" in sys.argv and r["old"].rstrip("/") == t.rstrip("/"):
            held.append(r["old"])
            continue  # older redirects_api (before 2026-09-29) refuses /x/ -> /x
        items.append({"old": r["old"], "new": t, "source_ref": r["old"]})
    for u in unresolved:
        print("  unresolved", u)
    if held:
        print(f"  held back {len(held)} trailing-slash page redirects (--hold-slash-only)")
    total = {}
    for i in range(0, len(items), 400):  # API limit is 500 per call
        body = {"redirects": items[i:i + 400], "import_batch": BATCH, "source_platform": PLATFORM}
        if DRY:
            body["dry_run"] = True
        try:
            r = api.post("/redirects", body)
            print(f"  {r['summary']}  warnings: {r.get('warnings')}")
            for k, v in (r["summary"] or {}).items():
                total[k] = total.get(k, 0) + v if isinstance(v, (int, float)) else v
        except ApiError as e:
            err("redirects", e)
    if not DRY and total:
        S["redirects"] = total
        save()


t0 = time.time()
if want("categories"):
    load_categories()
if want("products"):
    load_products()
if want("images"):
    load_images()
if want("seo"):
    load_seo()
if want("pages"):
    load_pages()
if want("posts"):
    load_posts()
if want("redirects"):
    load_redirects()
print(f"done in {int(time.time() - t0)}s, errors: {len(S['errors'])}")
