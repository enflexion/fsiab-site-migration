"""Fetch WordPress custom post types (e.g. location, wedding, event) into the raw cache.

Usage: wp_cpt_fetch.py https://site.com.au WORKDIR type [type ...]
Writes WORKDIR/raw/cpt_{type}.json. wp_extract.py adds them to pages (with post_type set),
so page_rules / decisions route them (suburb pages, skips, redirects). Run before wp_seo_fetch.py.
"""
import sys, os, json, time, urllib.request, urllib.error

SITE = sys.argv[1].rstrip("/")
RAW = os.path.join(sys.argv[2], "raw")
UA = "Mozilla/5.0 (Macintosh; FSIAB site migration)"
for t in sys.argv[3:]:
    items, page = [], 1
    while True:
        time.sleep(0.8)
        url = f"{SITE}/wp-json/wp/v2/{t}?per_page=100&page={page}&status=publish"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60) as r:
                batch, pages = json.loads(r.read()), int(r.headers.get("X-WP-TotalPages") or 1)
        except urllib.error.HTTPError as e:
            if e.code == 400:
                break
            raise
        items += batch
        if page >= pages or not batch:
            break
        page += 1
    json.dump(items, open(os.path.join(RAW, f"cpt_{t}.json"), "w"), ensure_ascii=False, indent=1)
    print(f"  cpt_{t}.json: {len(items)}")
