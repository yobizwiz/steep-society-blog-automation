#!/usr/bin/env python3
"""Upgrade existing body image <img> tags with full SEO attributes (no image regen).

Idempotent: skips articles whose img tags already have decoding="async" (the marker
that they were patched). Safe to re-run.

What it adds: explicit width/height, decoding=async, srcset+sizes for responsive
delivery via Shopify CDN, title attr. Preserves the original src and alt.

Image generation quota is NOT used by this script — it only rewrites HTML.
"""
from __future__ import annotations
import json, re, sys, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import load_env, log

API = "2024-10"


def shop_req(env, path, method="GET", payload=None):
    store = env["SHOPIFY_STORE_URL"].replace("https://", "").replace("http://", "").rstrip("/")
    url = f"https://{store}/admin/api/{API}/{path}"
    data = json.dumps(payload).encode() if payload else None
    req = urllib.request.Request(url, method=method, data=data, headers={
        "X-Shopify-Access-Token": env["SHOPIFY_ADMIN_TOKEN"],
        "Accept": "application/json", "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read()), r.headers.get("Link", "")


def get_blog_id(env):
    data, _ = shop_req(env, "blogs.json")
    handle = env.get("SHOPIFY_BLOG_HANDLE", "")
    for b in data["blogs"]:
        if not handle or b["handle"] == handle:
            return b["id"]
    return data["blogs"][0]["id"]


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
    arts = fetch_all(env, blog_id)
    log(f"전체 {len(arts)}편")
    patched = skipped = errors = 0
    for a in arts:
        try:
            new_body, n = upgrade_body(a.get("body_html") or "")
            if n == 0:
                skipped += 1
                continue
            shop_req(env, f"blogs/{blog_id}/articles/{a['id']}.json", method="PUT",
                     payload={"article": {"id": int(a["id"]), "body_html": new_body}})
            patched += 1
            log(f"  ✅ patched {n} img(s) — {a.get('title','')[:50]}")
        except Exception as e:
            errors += 1
            log(f"  ❌ error on {a.get('title','')[:50]}: {e}", "WARN")
    log(f"\n=== 완료 — 패치 {patched} / 이미 새 형식 {skipped} / 오류 {errors} ===")
    print(json.dumps({"total": len(arts), "patched": patched, "skipped": skipped, "errors": errors}, ensure_ascii=False))


if __name__ == "__main__":
    main()
