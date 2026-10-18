"""Bring an already-imported site in line with a new manifest by editing in place.

Usage: sync.py WORKDIR TARGET_KEY [--apply] [--old manifest-run1.json]
  Without --apply it only lists the changes.
  --old  the manifest the site was loaded from (default WORKDIR/manifest-run1.json), used to
         find categories that were renamed or changed kind.

What it does (then run load.py for anything new: split add-ons, colours, posts, pages, redirects):
  categories  same name: update SEO title/description, heading and content
              renamed or kind changed: create the new one, relink products, delete the old one
              no longer wanted: delete (products keep their other categories)
  products    in the manifest: update name, description, heading, short description, image alt,
              alcohol, SEO title/meta/keywords, all 8 price tiers (unused ones cleared), stock,
              URL and categories (replaced). Product ID, images, gallery stay.
              no longer wanted (gift vouchers, workshops...): delete (to the dashboard trash)
  pages       created by the import but no longer wanted (e.g. WordPress landing pages that are
              now category content): delete (to the dashboard page trash)
"""
import sys, os, json, re, urllib.parse
from fsiab import Fsiab, ApiError

WORK, KEY = sys.argv[1], sys.argv[2]
APPLY = "--apply" in sys.argv
OLD_F = os.path.join(WORK, sys.argv[sys.argv.index("--old") + 1] if "--old" in sys.argv else "manifest-run1.json")
M = json.load(open(os.path.join(WORK, "manifest.json")))
OLD = json.load(open(OLD_F)) if os.path.exists(OLD_F) else {"categories": []}
SF = os.path.join(WORK, "state.json")
S = json.load(open(SF))
api = Fsiab(KEY, pause=1.1, log=open(os.path.join(WORK, "sync-log.jsonl"), "a"))
S.setdefault("pending", {})
changes, errors = [], []
WORDS = ["one", "two", "three", "four", "five", "six", "seven", "eight"]


def save():
    json.dump(S, open(SF, "w"), ensure_ascii=False, indent=1)


def do(label, fn):
    changes.append(label)
    if not APPLY:
        print("  would", label)
        return None
    try:
        r = fn()
        print("  ok  ", label)
        return r if r is not None else True
    except ApiError as e:
        errors.append(f"{label}: {e}")
        print("  FAIL", label, "->", e)
        return None


def resolve(html):
    """Swap rehosted image URLs and category placeholders (same as load.py)."""
    html = re.sub(r'(<img[^>]*\bsrc=")([^"]+)(")', lambda m: m.group(1) + S["images"].get(m.group(2), m.group(2)) + m.group(3), html or "")
    return re.sub(r"\{\{category:([^}]+)\}\}", lambda m: urllib.parse.urlparse((S["categories"].get(m.group(1)) or {}).get("url") or "/").path, html)


q = lambda n: urllib.parse.quote(n, safe="")


def unmojibake(s):
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError, AttributeError):
        return s

# ------------------------------------------------------------------ categories
print("== categories")
old_by_ref = {c["ref"]: c for c in OLD.get("categories", []) if c.get("action") == "create"}
new_create = [c for c in M["categories"] if c["action"] == "create"]
wanted_names = {c["name"] for c in M["categories"] if c["action"] in ("create", "existing")}
retire = []
live_kind = {x["name"]: x["cat"] for x in api.get("/categories")["categories"]}
for c in new_create:
    oc = old_by_ref.get(c["ref"])
    old_name = oc["name"] if oc else None
    # compare with the site as it is now, so a second run does not redo a change
    kind_changed = old_name is not None and old_name in live_kind and live_kind[old_name] != c["cat"]
    if c["name"] in S["categories"] and not kind_changed:
        do(f"update category '{c['name']}' SEO and content",
           lambda c=c: (api.put(f"/categories/{q(c['name'])}/meta", {"title": c["title"], "description": c["description"]}),
                        api.put(f"/categories/{q(c['name'])}/content", {"headingone": c["headingone"], "seocontent": resolve(c["seocontent"])})))
        continue
    if old_name and old_name in S["categories"]:
        if kind_changed and old_name == c["name"]:
            # same name, different kind (e.g. addon -> flower): the URL changes, so recreate
            if do(f"delete category '{old_name}' (kind {oc.get('cat')} -> {c['cat']})", lambda n=old_name: api.delete(f"/categories/{q(n)}")) and APPLY:
                S["categories"].pop(old_name, None)
                save()
        else:
            retire.append(old_name)
    body = {"name": c["name"], "cat": c["cat"], "title": c["title"], "description": c["description"], "headingone": c["headingone"],
            "seocontent": resolve(c["seocontent"]), "import_batch": M["import_batch"], "source_platform": M["platform"],
            "source_ref": c["ref"] + "-v2", "source_url": c["source_url"]}
    r = do(f"create category '{c['name']}' ({c['cat']})" + (f", replacing '{old_name}'" if old_name and old_name != c["name"] else ""),
           lambda b=body: api.post("/categories", b))
    if APPLY and isinstance(r, dict):
        S["categories"][c["name"]] = {"url": r.get("url"), "already_existed": False}
        save()
for name in list(S["categories"]):
    if name not in wanted_names and name not in retire:
        retire.append(name)

# ------------------------------------------------------------------ products
print("== products")
wanted_refs = {p["ref"] for p in M["products"]}
for p in M["products"]:
    st = S["products"].get(p["ref"])
    if not st or not st.get("cat_id"):
        continue  # new (e.g. a split add-on): load.py creates it
    pid = st["cat_id"]
    content = {"description": p["description"], "headingone": p["headingone"], "shortdesc": p["shortdesc"], "imagealt": p["imagealt"]}
    extra = {"name": p["name"], "alcohol": p.get("alcohol") or ""}
    pricing = {}
    for i, w in enumerate(WORDS):
        t = p["tiers"][i] if i < len(p["tiers"]) else None
        pricing[f"price_{w}"] = t["price"] if t else ""
        pricing[f"desc_{w}"] = t["desc"] if t else ""
        pricing[f"sku_{w}"] = (t.get("sku") or "") if t else ""
    if not pricing["sku_one"]:
        pricing.pop("sku_one")  # keep the dashboard default (the product id)

    def upd(p=p, pid=pid, content=content, extra=extra, pricing=pricing):
        api.put(f"/products/{pid}/content", dict(content, **extra))
        # an API without name/alcohol support ignores them silently: check they stuck
        got = api.get(f"/products/{pid}")
        name_ok = got.get("name") == extra["name"] or unmojibake(got.get("name")) == extra["name"]
        if not name_ok or (got.get("alcohol") or "") != extra["alcohol"]:
            S["pending"][p["ref"]] = extra  # re-run sync once the API accepts name/alcohol
        else:
            S["pending"].pop(p["ref"], None)
        api.put(f"/products/{pid}/meta", {"title": p["title"], "descmeta": p["descmeta"], "keywords": p["keywords"]})
        api.put(f"/products/{pid}/pricing", pricing)
        api.put(f"/products/{pid}/stock", {"instock": p["instock"]})
    do(f"update product '{p['name']}' ({pid})", upd)
for ref, st in list(S["products"].items()):
    if ref not in wanted_refs and st.get("cat_id"):
        if do(f"delete product {ref} ({st['cat_id']}): no longer imported", lambda st=st: api.delete(f"/products/{st['cat_id']}")) and APPLY:
            S["products"].pop(ref, None)
            S.get("colours", {}).pop(ref, None)
            save()

# ------------------------------------------------------------------ pages
print("== pages")
new_pages = {pg["ref"]: pg for pg in M["pages"]}
for ref, st in list(S["pages"].items()):
    pg = new_pages.get(ref)
    if st.get("action") == "create" and (not pg or pg["action"] != "create"):
        why = pg["reason"] if pg and pg.get("reason") else (f"now fills /{pg['pagefilename']}" if pg and pg["action"] == "fill" else "no longer imported")
        if do(f"delete page /{st['pagefilename']} ({st['pageid']}): {why}", lambda st=st: api.delete(f"/pages/{st['pageid']}")) and APPLY:
            S["pages"].pop(ref, None)
            save()

if APPLY:
    save()

# ------------------------------------------------------------------ product category links (after categories exist)
print("== product categories")
for p in M["products"]:
    st = S["products"].get(p["ref"])
    if st and st.get("cat_id"):
        do(f"set categories of '{p['name']}' -> {p['categories']}",
           lambda st=st, p=p: api.post(f"/products/{st['cat_id']}/categories", {"categories": p["categories"], "replace": True}) if p["categories"] or True else None)

# ------------------------------------------------------------------ retire old categories last
print("== retire categories")
for name in retire:
    if name in wanted_names:
        continue
    if do(f"delete category '{name}'", lambda n=name: api.delete(f"/categories/{q(n)}")) and APPLY:
        S["categories"].pop(name, None)
        save()

if APPLY:
    save()
print(f"{'applied' if APPLY else 'planned'} {len(changes)} changes, {len(errors)} errors, {len(S['pending'])} products waiting for name/alcohol support")
for e in errors:
    print("  ERROR", e)
