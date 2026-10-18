"""Render a manifest (and, after a load, state + verify results) as a single HTML review sheet.

Usage: review.py WORKDIR [OUTFILE]
"""
import sys, os, json, html, re
from urllib.parse import urlparse

WORK = sys.argv[1]
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(WORK, "review.html")
M = json.load(open(os.path.join(WORK, "manifest.json")))
S = json.load(open(os.path.join(WORK, "state.json"))) if os.path.exists(os.path.join(WORK, "state.json")) else {}
V = json.load(open(os.path.join(WORK, "verify.json"))) if os.path.exists(os.path.join(WORK, "verify.json")) else {}
e = lambda s: html.escape(str(s if s is not None else ""))
vprob = {k: {(x.get("ref") or x.get("old")): x.get("problems", []) for x in V.get(k, [])} for k in V}


def warn_list(ws):
    return "<ul class=w>" + "".join(f"<li>{e(w)}</li>" for w in ws) + "</ul>" if ws else ""


def status(kind, ref):
    ps = vprob.get(kind, {}).get(ref)
    if ps is None:
        return ""
    return "<span class=ok>verified</span>" if not ps else "<span class=bad>" + e("; ".join(ps)) + "</span>"


rows_c = "".join(
    f"<tr class={c['action']}><td>{e(c['action'])}</td><td>{e(c['name'])}</td><td>{e(c.get('cat',''))}</td>"
    f"<td>{e(c.get('title',''))}<div class=m>{e(c.get('description',''))}</div></td>"
    f"<td>{e(c.get('reason',''))}{e((S.get('categories',{}).get(c['name']) or {}).get('url',''))}"
    f"{('<div class=m>content: '+str(len(re.sub('<[^>]+>',' ',c.get('seocontent') or '').split()))+' words'+(' from '+e(urlparse(c['landing_page']).path) if c.get('landing_page') else '')+'</div>') if c.get('seocontent') else ''}</td>"
    f"<td>{status('categories', c['ref'])}</td></tr>"
    for c in M["categories"])

rows_p = "".join(
    f"<tr><td>{('<img src=\"'+e(p['images'][0])+'\" loading=lazy>') if p['images'] else ''}</td>"
    f"<td><b>{e(p['name'])}</b><div class=m>/{e(p['url'])}/</div><div class=m>{len(p['images'])} image(s)</div></td>"
    f"<td>{'<br>'.join(e(t['desc'])+' <b>$'+e(t['price'])+'</b>' for t in p['tiers'])}"
    f"{('<div class=m>colours: '+e(', '.join(c['colour'] for c in p.get('colours') or []))+'</div>') if p.get('colours') else ''}"
    f"{'<div class=m><b>alcohol</b></div>' if p.get('alcohol') else ''}</td>"
    f"<td>{e(', '.join(p['categories']))}</td>"
    f"<td>{e(p['title'])}<div class=m>{e(p['descmeta'])}</div></td>"
    f"<td>{warn_list(p['warnings'])}{status('products', p['ref'])}</td></tr>"
    for p in M["products"])

rows_pg = "".join(
    f"<tr class={pg['action']}><td>{e(pg['action'])}</td><td><b>{e(pg.get('pagemenu') or pg.get('pagetitle'))}</b>"
    f"<div class=m>{e(urlparse(pg['source_url']).path)} &rarr; {('/'+e(pg['pagefilename'])) if pg.get('pagefilename') and pg['action']!='skip' else ''}</div></td>"
    f"<td>{e(pg.get('pagetitle',''))}<div class=m>{e(pg.get('pagedescription',''))}</div></td>"
    f"<td>{e(pg.get('words',''))}</td><td>{len(pg.get('content_images',[]))}</td>"
    f"<td>{e(pg.get('reason',''))}{warn_list(pg.get('warnings',[]))}{status('pages', pg['ref'])}</td></tr>"
    for pg in M["pages"])

rows_s = "".join(
    f"<tr><td><b>{e(sp['suburb'])}</b>, {e(sp['city'])}<div class=m>{e(urlparse(sp['source_url']).path)}</div></td>"
    f"<td>{e(sp['title'])}<div class=m>{e(sp['description'])}</div></td><td>{e(sp.get('words',''))}</td>"
    f"<td>{e((S.get('seo',{}).get(sp['ref']) or {}).get('action',''))} {e((S.get('seo',{}).get(sp['ref']) or {}).get('url',''))}</td></tr>"
    for sp in M.get("seo_pages", []))
suburb_section = ("<h2>Suburb pages</h2><div class=wrap><table><tr><th>Suburb</th><th>SEO title and description</th>"
                  "<th>Words</th><th>Result</th></tr>" + rows_s + "</table></div>") if M.get("seo_pages") else ""

rows_posts = "".join(
    f"<tr><td><b>{e(bp['title'])}</b><div class=m>{e(urlparse(bp['source_url']).path)} &rarr; /{e(M.get('blog_directory','blog'))}/{e(bp['url'])}/</div></td>"
    f"<td>{e(bp['description'])}</td><td>{e(bp.get('date',''))}</td><td>{e(bp.get('words',''))}</td><td>{status('posts', bp['ref'])}</td></tr>"
    for bp in M.get("posts", []))
posts_section = ("<h2>Blog posts</h2><div class=wrap><table><tr><th>Post</th><th>Meta description</th><th>Date</th><th>Words</th><th>Check</th></tr>"
                 + rows_posts + "</table></div>") if M.get("posts") else ""
manual_section = ("<h2>Needs setting up by hand</h2><ul>" + "".join(f"<li>{e(x)}</li>" for x in M.get("manual", [])) + "</ul>") if M.get("manual") else ""

rows_r = "".join(f"<tr><td>{e(r['old'])}</td><td>{e(r['to'])}</td></tr>" for r in M["redirects"])

cnt = lambda xs, a: sum(1 for x in xs if x.get("action") == a)
doc = f"""<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>{e(M['site_name'])} migration review</title><style>
:root{{--bg:#fff;--fg:#1d1d1b;--mut:#6b6b66;--line:#e4e2dc;--ok:#1f7a4d;--bad:#b3261e;--warn:#8a5a00;--skip:#f4f2ee}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1b1b19;--fg:#ecebe6;--mut:#a3a29c;--line:#34332f;--ok:#6fcf97;--bad:#ff8a80;--warn:#e0b25a;--skip:#23221f}}}}
body{{background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif;margin:0;padding:24px 16px;max-width:1300px;margin:auto}}
h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:17px;margin:32px 0 8px}} .m{{color:var(--mut);font-size:12px}}
.wrap{{overflow-x:auto}} table{{border-collapse:collapse;width:100%;min-width:760px}} td,th{{border-bottom:1px solid var(--line);padding:8px 6px;vertical-align:top;text-align:left}}
th{{font-size:12px;color:var(--mut);font-weight:600}} img{{width:64px;height:64px;object-fit:cover;border-radius:6px}}
tr.skip{{background:var(--skip);color:var(--mut)}} ul.w{{margin:0;padding-left:16px;color:var(--warn);font-size:12px}}
.ok{{color:var(--ok);font-size:12px}} .bad{{color:var(--bad);font-size:12px}} .sum span{{display:inline-block;margin-right:18px}}
</style></head><body>
<h1>{e(M['site_name'])}: site migration review</h1>
<div class=m>Source {e(M['source'])} ({e(M['platform'])}) &middot; batch {e(M['import_batch'])}</div>
<p class=sum><span><b>{cnt(M['categories'],'create')}</b> new categories</span><span><b>{len(M['products'])}</b> products</span>
<span><b>{sum(len(p['images']) for p in M['products'])}</b> product images</span><span><b>{cnt(M['pages'],'create')+cnt(M['pages'],'fill')}</b> pages</span>
<span><b>{len(M['redirects'])}</b> redirects</span></p>
{warn_list(M.get('warnings', []))}
{manual_section}
<h2>Categories</h2><div class=wrap><table><tr><th>Action</th><th>Name</th><th>Kind</th><th>SEO title and description</th><th>Note / new URL</th><th>Check</th></tr>{rows_c}</table></div>
<h2>Products</h2><div class=wrap><table><tr><th></th><th>Product</th><th>Prices</th><th>Categories</th><th>SEO title and description</th><th>Notes</th></tr>{rows_p}</table></div>
<h2>Pages</h2><div class=wrap><table><tr><th>Action</th><th>Page</th><th>SEO title and description</th><th>Words</th><th>Images</th><th>Notes</th></tr>{rows_pg}</table></div>
{suburb_section}
{posts_section}
<h2>Redirects</h2><div class=wrap><table><tr><th>Old URL</th><th>Goes to</th></tr>{rows_r}</table></div>
</body></html>"""
open(OUT, "w").write(doc)
print(OUT)
