"""Remove what an import created on the target, using WORKDIR/state.json.

Usage: rollback.py WORKDIR TARGET_KEY [--yes] [--keep seo,images] [--all-redirects]
  Without --yes it only lists what it would delete.
  --keep seo        leave the suburb pages this import created (their state is kept)
  --keep images     (default) content images stay in the site library and in state, so a
                    re-import reuses them instead of uploading again
  --all-redirects   delete every redirect on the site (use only on a dev site whose redirects
                    all came from this import); otherwise only redirects whose old path is in
                    the manifest(s) in WORKDIR are deleted

Deletes: products (and with them their category links, gallery rows and colours; the API
copies each product to the dashboard trash first), categories this import created, pages
this import created (copied to the dashboard page trash), suburb pages this import created,
blog posts, redirects. Pages this import only filled (placeholders) are left as they are.
The old state is saved as state.rolled-back-<time>.json.
"""
import sys, os, json, time, glob, urllib.parse
from fsiab import Fsiab, ApiError

WORK, KEY = sys.argv[1], sys.argv[2]
YES = "--yes" in sys.argv
KEEP = set(sys.argv[sys.argv.index("--keep") + 1].split(",")) if "--keep" in sys.argv else set()
KEEP.add("images")
ALL_REDIRECTS = "--all-redirects" in sys.argv
S = json.load(open(os.path.join(WORK, "state.json")))
api = Fsiab(KEY, pause=1.1, log=open(os.path.join(WORK, "rollback-log.jsonl"), "a"))
done, failed = [], []


def act(label, fn):
    if not YES:
        print(f"  would delete {label}")
        return True
    try:
        fn()
        done.append(label)
        print(f"  deleted {label}")
        return True
    except ApiError as e:
        if e.code == 404:
            done.append(label + " (already gone)")
            return True
        failed.append(f"{label}: {e}")
        print(f"  FAILED {label}: {e}")
        return False


print("== redirects")
olds = set()
for mf in glob.glob(os.path.join(WORK, "manifest*.json")):
    olds |= {r["old"].lower() for r in json.load(open(mf)).get("redirects", [])}
for r in api.get("/redirects")["redirects"]:
    if ALL_REDIRECTS or r["old"].lower() in olds or r["old"].lower().rstrip("/") in {o.rstrip("/") for o in olds}:
        act(f"redirect {r['old']} -> {r['new']}", lambda r=r: api.delete(f"/redirects/{r['id']}"))

print("== blog posts")
for ref, st in list(S.get("posts", {}).items()):
    if st.get("id") and act(f"post {ref}", lambda st=st: api.delete(f"/blogs/{st['id']}")) and YES:
        S["posts"].pop(ref, None)

print("== products")
for ref, st in list(S.get("products", {}).items()):
    if st.get("cat_id") and act(f"product {ref} ({st['cat_id']})", lambda st=st: api.delete(f"/products/{st['cat_id']}")) and YES:
        S["products"].pop(ref, None)
        S.get("colours", {}).pop(ref, None)

print("== categories")
for name, st in list(S.get("categories", {}).items()):
    if st.get("already_existed"):
        continue
    if act(f"category {name}", lambda name=name: api.delete(f"/categories/{urllib.parse.quote(name)}")) and YES:
        S["categories"].pop(name, None)

print("== pages")
for ref, st in list(S.get("pages", {}).items()):
    if st.get("action") != "create":
        continue
    if act(f"page /{st['pagefilename']} ({st['pageid']})", lambda st=st: api.delete(f"/pages/{st['pageid']}")) and YES:
        S["pages"].pop(ref, None)

if "seo" not in KEEP:
    print("== suburb pages")
    for ref, st in list(S.get("seo", {}).items()):
        if st.get("action") != "create":
            continue
        if act(f"suburb page {st['url']}", lambda st=st: api.delete(f"/seo/{st['id']}")) and YES:
            S["seo"].pop(ref, None)

if YES:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    old = json.load(open(os.path.join(WORK, "state.json")))
    json.dump(old, open(os.path.join(WORK, f"state.rolled-back-{stamp}.json"), "w"), ensure_ascii=False, indent=1)
    S["errors"] = []
    S["redirects"] = None
    S.pop("existing_categories", None)
    json.dump(S, open(os.path.join(WORK, "state.json"), "w"), ensure_ascii=False, indent=1)
    print(f"deleted {len(done)}, failed {len(failed)}; old state saved as state.rolled-back-{stamp}.json")
    for f in failed:
        print("  FAILED", f)
else:
    print("dry run only; add --yes to delete")
