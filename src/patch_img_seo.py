#!/usr/bin/env python3
"""Read-only image SEO candidate report. Does not update Shopify articles."""
from __future__ import annotations
import json, re, sys, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import load_env, log, OUTPUT_DIR
from release_gate import write_json

API = "2026-10"


def shop_req(env, path):
    store = env["SHOPIFY_STORE_URL"].replace("https://", "").replace("http://", "").rstrip("/")
    url = f"https://{store}/admin/api/{API}/{path}"
    req = urllib.request.Request(url, method="GET", headers={
        "X-Shopify-Access-Token": env["SHOPIFY_ADMIN_TOKEN"],
        "Accept": "application/json", "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read()), r.headers.get("Link", "")


def get_blog_id(env):
    from safe_publish import get_blog_id as scoped_blog_id
    return scoped_blog_id(env, env['SHOPIFY_BLOG_HANDLE'])


def fetch_all(env, blog_id):
    arts = []
    path = f"blogs/{blog_id}/articles.json?limit=250&fields=id,title,body_html"
    while path:
        data, link = shop_req(env, path)
        arts += data["articles"]
        m = re.search(r'page_info=([^&>]+)>;\s*rel="next"', link) if 'rel="next"' in link else None
        path = f"blogs/{blog_id}/articles.json?limit=250&page_info={m.group(1)}" if m else None
    return arts


def _cdn_w(url, w):
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}width={w}"


def _esc(s):
    return (s or "").replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


# Capture entire <p>...<img>...</p> block; preserve only src/alt from inside.
P_IMG_BLOCK = re.compile(
    r'<p[^>]*>\s*<img\b([^>]*?)/?>\s*</p>',
    re.IGNORECASE,
)


def _attr(attrs, name):
    m = re.search(rf'{name}\s*=\s*"([^"]*)"', attrs, re.I)
    return m.group(1) if m else ""


def upgrade_body(body_html):
    """Return (new_body, n_patched). Skips img tags already containing decoding=async."""
    n = 0
    def repl(m):
        nonlocal n
        attrs = m.group(1)
        if re.search(r'decoding\s*=\s*"async"', attrs, re.I):
            return m.group(0)  # already new format
        src = _attr(attrs, "src")
        alt = _attr(attrs, "alt")
        if not src:
            return m.group(0)  # weird — skip
        srcset = (f"{_cdn_w(src, 800)} 800w, "
                  f"{_cdn_w(src, 1200)} 1200w, "
                  f"{_cdn_w(src, 1600)} 1600w")
        sizes = "(max-width: 700px) 800px, (max-width: 1100px) 1200px, 1600px"
        a = _esc(alt)
        n += 1
        return (
            f'<p style="margin: 28px 0;">'
            f'<img src="{src}" alt="{a}" title="{a}" '
            f'width="1600" height="900" '
            f'loading="lazy" decoding="async" '
            f'srcset="{srcset}" sizes="{sizes}" '
            f'style="width: 100%; height: auto; border-radius: 12px;" />'
            f'</p>'
        )
    new_body = P_IMG_BLOCK.sub(repl, body_html or "")
    return new_body, n


def main():
    env = load_env()
    blog_id = get_blog_id(env)
    articles = fetch_all(env, blog_id)
    candidates = []
    for article in articles:
        _, count = upgrade_body(article.get('body_html') or '')
        if count:
            candidates.append({'article_id': article['id'], 'title': article.get('title', ''),
                               'image_count': count, 'status': 'review_required'})
    report = {'mode': 'report_only', 'total': len(articles), 'candidates': candidates, 'writes': 0}
    write_json(OUTPUT_DIR / 'image-seo-candidates.json', report)
    log('Read-only image SEO report: ' + str(len(candidates)) + ' candidates; 0 writes')
    return report


if __name__ == '__main__':
    main()
