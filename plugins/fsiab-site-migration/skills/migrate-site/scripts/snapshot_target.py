"""Print what already exists on the target FSIAB site. Usage: snapshot_target.py KEY"""
import sys, json
from fsiab import Fsiab

api = Fsiab(sys.argv[1], pause=0.3)
cats = api.get("/categories")["categories"]
print(f"== categories ({len(cats)})")
for c in cats:
    print(f"  {c['name']!r:40} {c['cat']:9} status={c['status']} order={c['sort_order']}")
prods = api.get("/products")["products"]
print(f"== products ({len(prods)})")
for p in prods:
    print(f"  {p['cat_id']}  {p['name']!r}  /{p['url']}/")
pages = api.get("/pages", status="all")["pages"]
print(f"== pages ({len(pages)})")
for p in pages:
    print(f"  {p.get('pageid')}  {p.get('pagetitle')!r:40} {p.get('pagedirectory') or ''}/{p.get('pagefilename')}  {p.get('pagestatus')}")
secs = api.get("/pages/sections")["sections"]
print(f"== sections ({len(secs)})")
for s in secs:
    print("  ", s)
