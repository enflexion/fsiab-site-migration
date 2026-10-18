"""SEO fallback for WordPress sites without Yoast's REST fields (e.g. Rank Math, AIOSEO, SEOPress).

Usage: wp_seo_fetch.py WORKDIR     (run after wp_fetch.py, before wp_extract.py)
Reads the live <title>, meta description and robots from every category, product, page and
post URL in WORKDIR/raw and writes WORKDIR/raw/seo_html.json keyed by URL.
wp_extract.py uses it only where yoast_head_json is missing. Throttled, read only, resumable.
"""
import sys, os, json, re, html, time, urllib.request, urllib.error

WORK = sys.argv[1]
RAW = os.path.join(WORK, "raw")
OUTF = os.path.join(RAW, "seo_html.json")
UA = "Mozilla/5.0 (Macintosh; FSIAB site migration)"
PAUSE = 0.8
raw = lambda n: json.load(open(os.path.join(RAW, n)))

urls = []
CPTS = [(f, "link") for f in sorted(os.listdir(RAW)) if f.startswith("cpt_") and f.endswith(".json")]
for name, key in (("wp_product_cat.json", "link"), ("wp_products.json", "link"), ("pages.json", "link"), ("posts.json", "link"), *CPTS):
    try:
        urls += [x[key] for x in raw(name) if x.get(key)]
    except FileNotFoundError:
        pass

done = json.load(open(OUTF)) if os.path.exists(OUTF) else {}
u = lambda s: re.sub(r"\s+", " ", html.unescape(s or "")).strip()


def meta(doc, name):
    for m in re.finditer(r"<meta\s[^>]*>", doc, re.I):
        tag = m.group(0)
        if re.search(rf"""(name|property)\s*=\s*["']{name}["']""", tag, re.I):
            c = re.search(r"""content\s*=\s*(["'])(.*?)\1""", tag, re.I | re.S)
            return u(c.group(2)) if c else ""
    return ""


todo = [x for x in dict.fromkeys(urls) if x not in done]
print(f"{len(urls)} URLs, {len(todo)} to fetch")
for i, url in enumerate(todo, 1):
    time.sleep(PAUSE)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            doc = r.read(400_000).decode("utf-8", "replace")
        head = doc.split("</head>", 1)[0]
        t = re.search(r"<title[^>]*>(.*?)</title>", head, re.I | re.S)
        done[url] = {"title": u(t.group(1)) if t else "", "description": meta(head, "description"),
                     "robots": meta(head, "robots"), "og_image": meta(head, "og:image")}
    except urllib.error.HTTPError as e:
        done[url] = {"error": e.code}
    except Exception as e:
        done[url] = {"error": str(e)[:200]}
    if i % 25 == 0 or i == len(todo):
        json.dump(done, open(OUTF, "w"), ensure_ascii=False, indent=1)
        print(f"  {i}/{len(todo)}", flush=True)
print("done")
