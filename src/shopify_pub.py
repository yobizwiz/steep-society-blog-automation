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
    url = f"https://{shop}/admin/api/2025-01/{path}"
    headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json", "Accept": "application/json"}
    data = json.dumps(body).encode("utf-8") if body else None
    status, raw = _http(method, url, headers=headers, body=data)
    if status >= 300:
        raise RuntimeError(f"Shopify API {method} {path} HTTP {status}: {raw[:500]!r}")
    return json.loads(raw)


def _gql(env, query, variables=None):
    shop = env["SHOPIFY_STORE_URL"]
    token = env["SHOPIFY_ADMIN_TOKEN"]
    url = f"https://{shop}/admin/api/2025-01/graphql.json"
    headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json", "Accept": "application/json"}
    body = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
    status, raw = _http("POST", url, headers=headers, body=body)
    if status >= 300:
        raise RuntimeError(f"Shopify GQL HTTP {status}: {raw[:500]!r}")
    data = json.loads(raw)
    if data.get("errors"):
        raise RuntimeError(f"Shopify GQL errors: {data['errors']}")
    return data["data"]


def find_article_by_publish_date(env, date_str):
    """Look up Shopify article scheduled to publish on date_str (YYYY-MM-DD).
    Returns dict {id, title, handle, publishedAt, isPublished} if exists, else None.
    Used to skip duplicate generation on weekly cron trigger.
    """
    q = (
        '{ articles(first: 50, sortKey: UPDATED_AT, reverse: true) '
        '{ edges { node { id title handle publishedAt isPublished } } } }'
    )
    res = _gql(env, q)
    edges = res.get("articles", {}).get("edges", [])
    for e in edges:
        n = e.get("node") or {}
        pub = (n.get("publishedAt") or "")
        if pub.startswith(date_str):
            return {"id": n["id"], "title": n["title"], "handle": n["handle"],
                    "publishedAt": pub, "isPublished": n.get("isPublished")}
    return None


def get_blog_id(env, blog_handle):
    data = _api(env, "blogs.json")
    for b in data.get("blogs", []):
        if b["handle"] == blog_handle:
            return b["id"]
    raise RuntimeError(f"Blog handle '{blog_handle}' not found")


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


def insert_body_images(body_html, body_image_urls):
    out = body_html
    for idx, img in enumerate(body_image_urls, start=1):
        marker = f"<!-- IMG:body-{idx} -->"
        alt_safe = img["alt"].replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
        tag = (f'<p style="margin: 28px 0;"><img src="{img["url"]}" alt="{alt_safe}" '
               f'loading="lazy" style="width: 100%; height: auto; border-radius: 12px;" /></p>')
        if marker in out:
            out = out.replace(marker, tag)
    return out


def create_article(env, *, blog_id, article, featured_image_url, featured_image_alt,
                    body_html, publish_mode="draft", scheduled_at=None):
    """publish_mode: draft / publish / scheduled (with scheduled_at ISO 8601 UTC)"""
    log(f"  create article (mode={publish_mode}): {article['title'][:60]}")
    pa = {
        "title": article["title"],
        "author": "Steep Society",
        "body_html": body_html,
        "summary_html": f"<p>{article.get('summary', '')}</p>" if article.get("summary") else None,
        "tags": ", ".join(article.get("tags", [])),
        "handle": article.get("url_slug"),
        "image": {"src": featured_image_url, "alt": featured_image_alt},
        "metafields": [
            {"namespace": "global", "key": "title_tag", "value": article.get("meta_title", ""),
             "type": "single_line_text_field"},
            {"namespace": "global", "key": "description_tag", "value": article.get("meta_description", ""),
             "type": "single_line_text_field"},
        ],
        "published": (publish_mode == "publish"),
    }

    # Try with original handle first; on 422 'handle has already been taken' retry with -2,-3,-4,-5
    original_handle = pa["handle"]
    res = None
    last_err = None
    for suffix in [""] + [f"-{i}" for i in range(2, 6)]:
        if suffix:
            pa["handle"] = f"{original_handle}{suffix}"
            log(f"  handle 충돌 — 재시도: {pa['handle']}", "WARN")
        payload = {"article": {k: v for k, v in pa.items() if v is not None}}
        try:
            res = _api(env, f"blogs/{blog_id}/articles.json", method="POST", body=payload)
            break
        except RuntimeError as e:
            msg = str(e)
            if "has already been taken" in msg:
                last_err = e
                continue
            raise
    if res is None:
        raise RuntimeError(f"handle 충돌 5회 재시도 모두 실패: {last_err}")
    art = res["article"]
    article_id = art["id"]
    # Update article dict so downstream (preview, report, email) sees final handle
    article["url_slug"] = art.get("handle", original_handle)
    log(f"  created id={article_id} handle={art.get('handle')}")

    # If scheduled mode, use GraphQL articleUpdate to set future publishedAt
    if publish_mode == "scheduled" and scheduled_at:
        log(f"  scheduling for {scheduled_at} via GraphQL articleUpdate")
        gql = """mutation update($id: ID!, $article: ArticleUpdateInput!) {
          articleUpdate(id: $id, article: $article) {
            article { id isPublished publishedAt }
            userErrors { field message }
          }
        }"""
        v = {"id": f"gid://shopify/Article/{article_id}",
             "article": {"isPublished": False, "publishDate": scheduled_at}}
        res2 = _gql(env, gql, v)
        errs = res2["articleUpdate"].get("userErrors", [])
        if errs:
            log(f"  schedule errors: {errs}", "WARN")
        else:
            ad = res2["articleUpdate"]["article"]
            log(f"  scheduled: publishedAt={ad['publishedAt']}, isPublished={ad['isPublished']}")
            art["published_at"] = ad["publishedAt"]

    log(f"  done id={article_id} handle={art.get('handle')}")
    return art


def admin_url(env, article_id):
    shop = env["SHOPIFY_STORE_URL"].replace(".myshopify.com", "")
    return f"https://admin.shopify.com/store/{shop}/articles/{article_id}"


def public_url(article_handle, blog_handle="steep-society-journal"):
    return f"https://steep-society.com/blogs/{blog_handle}/{article_handle}"
