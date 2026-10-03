"""Shopify Admin API - image upload + blog article create + scheduled publish."""
from __future__ import annotations
import json, time, urllib.error, urllib.request
from utils import log


def _http(method, url, *, headers=None, body=None, timeout=60):
    if isinstance(body, str): body = body.encode("utf-8")
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _api(env, path, *, method="GET", body=None):
    shop = env["SHOPIFY_STORE_URL"]
    token = env["SHOPIFY_ADMIN_TOKEN"]
    url = f"https://{shop}/admin/api/{env.get('SHOPIFY_API_VERSION', '2026-10')}/{path}"
    headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json", "Accept": "application/json"}
    data = json.dumps(body).encode("utf-8") if body else None
    status, raw = _http(method, url, headers=headers, body=data)
    if status >= 300:
        raise RuntimeError(f"Shopify API {method} {path} HTTP {status}: {raw[:500]!r}")
    return json.loads(raw)


def _gql(env, query, variables=None):
    shop = env["SHOPIFY_STORE_URL"]
    token = env["SHOPIFY_ADMIN_TOKEN"]
    url = f"https://{shop}/admin/api/{env.get('SHOPIFY_API_VERSION', '2026-10')}/graphql.json"
    headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json", "Accept": "application/json"}
    body = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
    status, raw = _http("POST", url, headers=headers, body=body)
    if status >= 300:
        raise RuntimeError(f"Shopify GQL HTTP {status}: {raw[:500]!r}")
    data = json.loads(raw)
    if data.get("errors"):
        raise RuntimeError(f"Shopify GQL errors: {data['errors']}")
    return data["data"]








def upload_image(env, *, webp_bytes, filename, alt):
    log(f"  Shopify image upload: {filename}")
    q1 = """mutation stagedUploadsCreate($input: [StagedUploadInput!]!) {
      stagedUploadsCreate(input: $input) {
        stagedTargets { url resourceUrl parameters { name value } }
        userErrors { field message }
      }
    }"""
    v1 = {"input": [{"filename": filename, "mimeType": "image/webp", "httpMethod": "POST",
                     "resource": "FILE", "fileSize": str(len(webp_bytes))}]}
    res = _gql(env, q1, v1)
    if res["stagedUploadsCreate"].get("userErrors") or not res["stagedUploadsCreate"].get("stagedTargets"):
        raise RuntimeError("stagedUploadsCreate rejected upload")
    target = res["stagedUploadsCreate"]["stagedTargets"][0]
    upload_url = target["url"]
    resource_url = target["resourceUrl"]
    params = {p["name"]: p["value"] for p in target["parameters"]}

    boundary = "----StreamFormBoundary" + str(int(time.time() * 1000))
    body_parts = bytearray()
    for k, v in params.items():
        body_parts += f"--{boundary}\r\n".encode()
        body_parts += f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode()
        body_parts += f"{v}\r\n".encode()
    body_parts += f"--{boundary}\r\n".encode()
    body_parts += f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode()
    body_parts += b"Content-Type: image/webp\r\n\r\n"
    body_parts += webp_bytes
    body_parts += f"\r\n--{boundary}--\r\n".encode()

    status, raw = _http("POST", upload_url,
                        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                        body=bytes(body_parts), timeout=120)
    if status not in (200, 201, 204):
        raise RuntimeError(f"S3 upload HTTP {status}")

    q3 = """mutation fileCreate($files: [FileCreateInput!]!) {
      fileCreate(files: $files) {
        files { id alt fileStatus ... on MediaImage { image { url } } }
        userErrors { field message }
      }
    }"""
    res = _gql(env, q3, {"files": [{"originalSource": resource_url, "alt": alt, "contentType": "IMAGE"}]})
    if res["fileCreate"].get("userErrors") or not res["fileCreate"].get("files"):
        raise RuntimeError("fileCreate rejected upload")
    file_id = res["fileCreate"]["files"][0]["id"]

    cdn_url = ""
    for i in range(20):
        time.sleep(1.5)
        res = _gql(env, """query getFile($id: ID!) {
          node(id: $id) { ... on MediaImage { image { url } fileStatus } }
        }""", {"id": file_id})
        node = res.get("node") or {}
        image = node.get("image") or {}
        if image.get("url"):
            cdn_url = image["url"]
            break
        if node.get("fileStatus") == "FAILED":
            raise RuntimeError("fileCreate FAILED")
    if not cdn_url:
        raise RuntimeError("CDN URL not ready")
    log(f"  uploaded")
    return cdn_url


def _cdn_with_width(url, w):
    """Shopify CDN supports ?width= image transformations. Handle existing query strings."""
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}width={w}"


def insert_body_images(body_html, body_image_urls):
    out = body_html
    for idx, img in enumerate(body_image_urls, start=1):
        marker = f"<!-- IMG:body-{idx} -->"
        alt_safe = img["alt"].replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
        url = img["url"]
        srcset = (f"{_cdn_with_width(url, 800)} 800w, "
                  f"{_cdn_with_width(url, 1200)} 1200w, "
                  f"{_cdn_with_width(url, 1600)} 1600w")
        sizes = "(max-width: 700px) 800px, (max-width: 1100px) 1200px, 1600px"
        tag = (
            f'<p style="margin: 28px 0;">'
            f'<img src="{url}" '
            f'alt="{alt_safe}" title="{alt_safe}" '
            f'width="1600" height="900" '
            f'loading="lazy" decoding="async" '
            f'srcset="{srcset}" sizes="{sizes}" '
            f'style="width: 100%; height: auto; border-radius: 12px;" />'
            f'</p>'
        )
        if marker in out:
            out = out.replace(marker, tag)
    return out



def _apply_paragraph_spacing(html):
    """Force consistent paragraph + heading spacing AND font-size across all themes.
    Some Shopify themes ship with tight margins / small body font; this guarantees readable air."""
    import re
    def patch_p(m):
        attrs = m.group(1) or ""
        if "style=" in attrs.lower():
            return m.group(0)
        return f'<p{attrs} style="margin: 0 0 1.4em; line-height: 1.75; font-size: 17px;">'
    def patch_h(m):
        tag = m.group(1)
        attrs = m.group(2) or ""
        if "style=" in attrs.lower():
            return m.group(0)
        size = "1.5em" if tag.lower() == "h2" else "1.25em"
        return f'<{tag}{attrs} style="margin: 1.6em 0 0.6em; line-height: 1.3; font-size: {size};">'
    def patch_li(m):
        attrs = m.group(1) or ""
        if "style=" in attrs.lower():
            return m.group(0)
        return f'<li{attrs} style="margin: 0 0 0.5em; line-height: 1.7; font-size: 17px;">'
    html = re.sub(r"<p(\s+[^>]*)?>", patch_p, html, flags=re.IGNORECASE)
    html = re.sub(r"<(h[23])(\s+[^>]*)?>", patch_h, html, flags=re.IGNORECASE)
    html = re.sub(r"<li(\s+[^>]*)?>", patch_li, html, flags=re.IGNORECASE)
    return html


# ---------------------------------------------------------------------------
# Pre-publish sanitizer (added 2026-09-12): links use the store's primary
# domain, fabricated persona bylines / Person authors are removed, and
# internal links that 404 are unwrapped (anchor text kept).
# Called before the final validation gate; lookup failures block publication.
# ---------------------------------------------------------------------------
_SHOP_INFO = {}
_URL_OK = {}


def _shop_info(env):
    """{'name': 'MERA', 'domain': 'merascent.com'} via shop.json, cached per run."""
    key = env.get("SHOPIFY_STORE_URL", "")
    if key not in _SHOP_INFO:
        try:
            s = _api(env, "shop.json")["shop"]
            _SHOP_INFO[key] = {"name": s.get("name") or "", "domain": s.get("domain") or key}
        except Exception as e:  # never block publishing on this lookup
            log(f"  shop.json lookup failed: {e}", "WARN")
            raise RuntimeError("Shop identity lookup failed") from e
    return _SHOP_INFO[key]


def _url_alive(url):
    """False only for a hard 404; transport and non-404 HTTP errors fail closed."""
    import urllib.error
    import urllib.request
    if url in _URL_OK:
        return _URL_OK[url]
    ok = True
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}, method="HEAD")
        with urllib.request.urlopen(req, timeout=15) as r:
            ok = r.status < 400
    except urllib.error.HTTPError as e:
        if e.code != 404: raise
        ok = False
    except Exception as exc:
        raise RuntimeError("Link verification unavailable") from exc
    _URL_OK[url] = ok
    return ok


def sanitize_body(env, body_html, brand=None):
    """1) myshopify links -> primary domain  2) drop fabricated bylines / Person authors
    3) unwrap internal links that 404 (anchor text kept)."""
    import re
    out = body_html or ""
    try:
        info = _shop_info(env)
        store = env.get("SHOPIFY_STORE_URL", "")
        domain = info["domain"]
        brand = (brand or info["name"] or "Store").replace('"', "")
        base = f"https://{domain}"
        if store and domain and store != domain:
            out = out.replace(f"https://{store}", base)
        # fabricated persona bylines, e.g. <p><em>Tested by Jane Doe, curator at X</em></p>
        out, n_byline = re.subn(
            r'<p[^>]*>\s*<em>\s*(?:Tested|Written|Reviewed|Curated|Edited)\s+by\s+[A-Z][^<]{0,200}</em>\s*</p>\s*',
            "", out, flags=re.I)
        if n_byline:
            log(f"  removed {n_byline} fabricated byline(s)", "WARN")
        # JSON-LD author as Person -> Organization
        out, n_person = re.subn(
            r'"author"\s*:\s*\{\s*"@type"\s*:\s*"Person"[^{}]*(?:\{[^{}]*\}[^{}]*)*\}',
            '"author": {"@type": "Organization", "name": "%s", "url": "%s"}' % (brand, base), out)
        if n_person:
            log(f"  replaced {n_person} Person author(s) with Organization", "WARN")

        # internal links that 404 -> plain text
        def _fix(m):
            href = m.group(2)
            if href.startswith(base + "/") and not _url_alive(href):
                log(f"  removed broken link: {href}", "WARN")
                return m.group(4)
            return m.group(0)
        out = re.sub(r'(<a\b[^>]*href=")([^"]+)("[^>]*>)(.*?)</a>', _fix, out, flags=re.S | re.I)
    except Exception as e:
        raise RuntimeError("Final HTML sanitization failed") from e
    return out




def admin_url(env, article_id):
    shop = env["SHOPIFY_STORE_URL"].replace(".myshopify.com", "")
    return f"https://admin.shopify.com/store/{shop}/articles/{article_id}"




from safe_publish import (create_article, update_article_body, find_article_by_publish_date, get_blog_id, public_url)
