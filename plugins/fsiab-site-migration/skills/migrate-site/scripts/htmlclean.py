"""Stdlib-only HTML cleaner for migrated content.

Turns page-builder output (Flatsome, Elementor, Divi, Shopify themes) into plain semantic HTML:
keeps headings, paragraphs, lists, links, images, emphasis, tables, blockquotes; drops wrappers,
classes, ids, inline styles, <style>/<script>/<noscript>/<form>/<iframe> (iframes reported),
empty elements. keep_h1=False demotes h1 to h2 (product and category templates print their own
H1 from headingone); pages keep their H1 because the FSIAB page template does not print one.

clean(html, link_map=callable, img_map=callable) -> (clean_html, report)
  link_map(href) -> new href or None to keep
  img_map(src)   -> new src or None to keep
"""
from html.parser import HTMLParser
from html import escape
import re

KEEP = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "a", "strong", "b", "em", "i", "u",
        "br", "img", "blockquote", "table", "thead", "tbody", "tr", "th", "td", "hr", "figure", "figcaption"}
DROP_WITH_CONTENT = {"style", "script", "noscript", "form", "iframe", "svg", "button", "select", "textarea", "template", "video", "audio", "object"}
VOID = {"br", "img", "hr"}
# Elements that are product listings/widgets from the old platform, not content: dropped whole.
# Matched on class tokens (WooCommerce/Flatsome product grids, Shopify product cards).
DROP_CLASSES = {"product-small", "type-product", "products", "product_list_widget", "woocommerce-loop-product",
                "product-card-wrapper", "card-wrapper", "grid__item--product", "related", "upsells", "cross-sells"}
RENAME = {"b": "strong", "i": "em"}
ATTRS = {"a": ("href", "title", "target"), "img": ("src", "alt", "width", "height"), "td": ("colspan", "rowspan"), "th": ("colspan", "rowspan")}
BLOCK = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "blockquote", "table", "figure", "hr"}
# containers the cleaner drops but which mark a paragraph boundary in the source
BOUNDARY = {"div", "section", "article", "header", "footer", "main", "aside", "nav", "form", "details", "summary",
            "dl", "dt", "dd", "address", "center", "fieldset"}
# elements that already hold running text: no implicit <p> inside them
TEXT_HOLDERS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "td", "th", "blockquote", "figcaption", "ul", "ol", "table", "tr", "thead", "tbody"}


class _Cleaner(HTMLParser):
    def __init__(self, link_map, img_map, keep_h1=False):
        super().__init__(convert_charrefs=True)
        self.rename = dict(RENAME) if keep_h1 else dict(RENAME, h1="h2")
        self.out, self.stack, self.skip = [], [], 0
        self.cskip, self.cskip_tag = 0, None
        self.link_map, self.img_map = link_map, img_map
        self.report = {"images": [], "links": [], "iframes": [], "dropped_forms": 0, "bg_images": []}

    # Loose text and inline tags that sit directly in <div>s (common in page builders and
    # Shopify themes) get wrapped in an implicit <p>, closed at the next block boundary, so
    # paragraphs do not run together once the <div>s are stripped.
    def _implicit_open(self):
        return "p-implicit" in self.stack

    def _close_implicit(self):
        if not self._implicit_open():
            return
        while self.stack:
            top = self.stack.pop()
            if top == "p-implicit":
                self.out.append("</p>")
                return
            if top != "a-unwrapped":
                self.out.append(f"</{top}>")

    def _open_implicit(self):
        if self._implicit_open() or any(x in TEXT_HOLDERS for x in self.stack):
            return
        self.out.append("<p>")
        self.stack.append("p-implicit")

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self.cskip:
            if tag == self.cskip_tag and tag not in VOID:
                self.cskip += 1
            return
        if not self.skip and tag not in VOID and DROP_CLASSES & set((a.get("class") or "").split()):
            self._close_implicit()
            self.cskip, self.cskip_tag = 1, tag
            self.report["dropped_product_blocks"] = self.report.get("dropped_product_blocks", 0) + 1
            return
        if not self.skip and (tag in BOUNDARY or self.rename.get(tag, tag) in BLOCK):
            self._close_implicit()
        if tag in DROP_WITH_CONTENT:
            if tag == "iframe" and a.get("src"):
                self.report["iframes"].append(a["src"])
            if tag == "form":
                self.report["dropped_forms"] += 1
            if tag not in VOID:
                self.skip += 1
            return
        if self.skip:
            return
        # lazy-loaded images keep the real src in data-*
        if tag == "img":
            src = a.get("data-src") or a.get("data-lazy-src") or a.get("src") or ""
            if src.startswith("data:"):
                src = a.get("data-src") or ""
            if not src:
                return
            a["src"] = src
        t = self.rename.get(tag, tag)
        if t not in KEEP:
            return
        # lightbox links around photos point at the full-size file on the old host; the new
        # page has no lightbox, so drop the link and keep the image
        if t == "a" and re.search(r"\.(jpe?g|png|gif|webp|heic|avif)(\?.*)?$", (a.get("href") or "").lower()):
            self.stack.append("a-unwrapped")
            self.report["unwrapped_image_links"] = self.report.get("unwrapped_image_links", 0) + 1
            return
        kept = []
        for k in ATTRS.get(t, ()):
            v = a.get(k)
            if v is None:
                continue
            if t == "a" and k == "href":
                self.report["links"].append(v)
                nv = self.link_map(v) if self.link_map else None
                v = nv if nv is not None else v
            if t == "img" and k == "src":
                self.report["images"].append(v)
                nv = self.img_map(v) if self.img_map else None
                v = nv if nv is not None else v
            if t == "a" and k == "target" and v != "_blank":
                continue
            kept.append(f' {k}="{escape(v, quote=True)}"')
        if t == "a" and a.get("target") == "_blank":
            kept.append(' rel="noopener"')
        if t not in BLOCK and t not in TEXT_HOLDERS:
            self._open_implicit()
        self.out.append(f"<{t}{''.join(kept)}>")
        if t not in VOID:
            self.stack.append(t)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        t = self.rename.get(tag, tag)
        if t in self.stack and t not in VOID and self.stack[-1] == t:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.cskip:
            if tag == self.cskip_tag:
                self.cskip -= 1
            return
        if not self.skip and tag in BOUNDARY:
            self._close_implicit()
        if tag in DROP_WITH_CONTENT:
            if tag not in VOID and self.skip:
                self.skip -= 1
            return
        if self.skip:
            return
        t = self.rename.get(tag, tag)
        if t == "a" and self.stack and self.stack[-1] == "a-unwrapped":
            self.stack.pop()
            return
        if t not in KEEP or t in VOID:
            return
        if t in self.stack:
            while self.stack:
                top = self.stack.pop()
                if top == "a-unwrapped":
                    continue
                if top == "p-implicit":
                    self.out.append("</p>")
                    continue
                self.out.append(f"</{top}>")
                if top == t:
                    break

    def handle_data(self, data):
        if self.skip or self.cskip:
            return
        if data.strip():
            self._open_implicit()
        self.out.append(escape(data, quote=False))


def clean(html, link_map=None, img_map=None, keep_h1=False):
    html = html or ""
    # background images set in builder <style> blocks: report them, they are lost otherwise
    bgs = re.findall(r"background-image:\s*url\(([^)]+)\)", html)
    p = _Cleaner(link_map, img_map, keep_h1)
    p.feed(html)
    p.close()
    while p.stack:
        t = p.stack.pop()
        if t == "p-implicit":
            p.out.append("</p>")
        elif t != "a-unwrapped":
            p.out.append(f"</{t}>")
    s = "".join(p.out)
    s = s.replace("\xa0", " ")
    # collapse whitespace between tags, then drop empty elements (repeat for nesting)
    s = re.sub(r">\s+<", "> <", s)
    for _ in range(4):
        s = re.sub(r"<(p|h[1-6]|li|ul|ol|strong|em|u|blockquote|figure|figcaption|a)(\s[^>]*)?>\s*</\1>", "", s)
    s = re.sub(r"(<br>\s*){3,}", "<br><br>", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    # put block elements on their own lines for readability in the dashboard editor
    s = re.sub(r"\s*<(/?)(p|h[1-6]|ul|ol|li|blockquote|table|tr|figure|hr)(\s[^>]*)?>\s*",
               lambda m: ("\n" if not m.group(1) else "") + m.group(0).strip() + ("\n" if m.group(1) else ""), s)
    s = re.sub(r"\n{2,}", "\n", s).strip()
    p.report["bg_images"] = [b.strip("'\" ") for b in bgs]
    return s, p.report


def text_of(html):
    t = re.sub(r"<[^>]+>", " ", html or "")
    from html import unescape
    return re.sub(r"\s+", " ", unescape(t)).strip()


def first_sentences(text, limit=155):
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    dot = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if dot > 60:
        return cut[:dot + 1]
    sp = cut.rfind(" ")
    return cut[:sp].rstrip(",;:") + "..."
