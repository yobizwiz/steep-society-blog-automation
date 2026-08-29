"""Guarantee one real product CTA link in every generated post.

- If the schedule entry has cta_product, verify the link survived generation;
  if missing, deterministically inject it into the closing CTA box.
- If the entry has no cta_product, auto-pick a product from the post's CTA
  collection (best-selling, in stock, >= $10, not blacklisted) and inject.
Fail-open: never raises; returns original body if anything goes wrong.
"""
from __future__ import annotations
import re

BLACKLIST = ("pipi", "tmktmk")

_PICK_Q = """
query($h: String!) {
  collectionByHandle(handle: $h) {
    products(first: 40, sortKey: BEST_SELLING) {
      nodes {
        handle title status totalInventory
        priceRangeV2 { minVariantPrice { amount } }
      }
    }
  }
}"""


_STOP = {"best", "for", "your", "the", "how", "to", "guide", "quick", "fix", "and",
         "of", "in", "a", "an", "vs", "what", "before", "every", "should", "own",
         "top", "picks", "season", "seasonal", "with", "you", "buy", "at", "home",
         "why", "when", "that", "this", "our", "from", "on", "or", "is", "are",
         "make", "made", "step", "by", "complete", "ideas", "tips", "routine"}

_SEARCH_Q = """
query($q: String!) {
  products(first: 25, query: $q, sortKey: RELEVANCE) {
    nodes {
      handle title status totalInventory
      priceRangeV2 { minVariantPrice { amount } }
    }
  }
}"""


def _first_qualifying(nodes):
    for p in nodes:
        try:
            if p.get("status") != "ACTIVE":
                continue
            if (p.get("totalInventory") or 0) < 5:
                continue
            amt = float(((p.get("priceRangeV2") or {}).get("minVariantPrice") or {}).get("amount") or 0)
            if amt < 10:
                continue
            tl = (p.get("title") or "").lower()
            if any(b in tl for b in BLACKLIST):
                continue
            return {"handle": p["handle"], "title": p["title"]}
        except Exception:
            continue
    return None


def pick_product_by_keywords(gql, post_title):
    words = [w for w in re.findall(r"[A-Za-z]+", (post_title or "").lower()) if w not in _STOP]
    if not words:
        return None
    for n in (3, 2, 1):
        kws = words[:n]
        q = " AND ".join("title:*" + w + "*" for w in kws) + " AND status:active"
        try:
            data = gql(_SEARCH_Q, {"q": q})
            nodes = ((data.get("products") or {}).get("nodes")) or []
        except Exception:
            nodes = []
        hit = _first_qualifying(nodes)
        if hit:
            return hit
    return None


def pick_product_from_collection(gql, collection_handle):
    if not collection_handle:
        return None
    try:
        data = gql(_PICK_Q, {"h": collection_handle})
        nodes = (((data.get("collectionByHandle") or {}).get("products") or {}).get("nodes")) or []
    except Exception:
        return None
    return _first_qualifying(nodes)


def get_product_title(gql, handle):
    try:
        d = gql('query($h: String!){ productByHandle(handle: $h){ title } }', {"h": handle})
        return ((d.get("productByHandle") or {}).get("title"))
    except Exception:
        return None


def short_name(title, handle):
    t = (title or "").strip()
    if not t:
        t = handle.replace("-", " ").strip().title()
    t = re.split(r"\s[-–—|:]\s|[,(\[]", t)[0].strip()
    words = t.split()
    out = " ".join(words[:7])
    return out[:70].strip()


def inject_into_closing_box(body_html, url, name):
    anchor = body_html.rfind("background: #faf7f1")
    if anchor == -1:
        return None
    seg = body_html[anchor:]
    m = re.search(r'(<p style="margin: 0 0 16px; line-height: 1\.6;">)(.*?)(</p>)', seg, re.S)
    if not m:
        return None
    desc = m.group(2).rstrip()
    ins = desc + (' ' if desc.endswith(('.', '!', '?')) else '. ') + \
        'Our pick: <a href="' + url + '" style="color:#2b2118; font-weight:600;">' + name + '</a>.'
    seg2 = seg[:m.start(2)] + ins + seg[m.end(2):]
    return body_html[:anchor] + seg2


def ensure_product_cta(gql, entry, cols, body_html, domain, log=print):
    """Returns (body_html, product_handle, ok)."""
    try:
        handle = entry.get("cta_product")
        title = None
        if not handle:
            pick = pick_product_by_keywords(gql, entry.get("title"))
            if not pick:
                ckey = entry.get("cta_collection")
                chandle = ((cols.get(ckey) or {}).get("handle")) if ckey else None
                pick = pick_product_from_collection(gql, chandle)
            if pick:
                handle, title = pick["handle"], pick["title"]
        if not entry.get("cta_product") and "/products/" in body_html:
            return body_html, handle, True  # generation already included a product link
        if not handle:
            log(f"[cta-fix] no product candidate (collection={entry.get('cta_collection')})")
            return body_html, None, False
        link = "/products/" + handle
        if link in body_html:
            return body_html, handle, True
        if title is None:
            title = get_product_title(gql, handle)
        name = short_name(title, handle)
        url = domain.rstrip("/") + link
        patched = inject_into_closing_box(body_html, url, name)
        if patched:
            log(f"[cta-fix] injected product link: {handle} ({name})")
            return patched, handle, True
        log("[cta-fix] closing box not found - injection skipped")
        return body_html, handle, False
    except Exception as e:
        log(f"[cta-fix] error (fail-open): {e}")
        return body_html, entry.get("cta_product"), False


_ART_CACHE = {}


def _recent_articles(env):
    """Published articles (title/handle) of this store's blog, cached per run."""
    key = env.get("SHOPIFY_BLOG_HANDLE", "")
    if key in _ART_CACHE:
        return _ART_CACHE[key]
    arts = []
    try:
        from shopify_pub import _api, get_blog_id
        blog_id = get_blog_id(env, env["SHOPIFY_BLOG_HANDLE"])
        data = _api(env, "blogs/" + str(blog_id) +
                    "/articles.json?limit=50&published_status=published&fields=title,handle,published_at")
        for a in data.get("articles", []):
            if a.get("published_at") and a.get("handle"):
                arts.append({"title": a.get("title", ""), "handle": a["handle"]})
    except Exception:
        arts = []
    _ART_CACHE[key] = arts
    return arts


def _match_articles(title, articles, k=3):
    words = set(w for w in re.findall(r"[a-z]+", (title or "").lower())
                if w not in _STOP and len(w) > 2)
    scored = []
    for a in articles:
        aw = set(w for w in re.findall(r"[a-z]+", (a.get("title") or "").lower())
                 if w not in _STOP and len(w) > 2)
        ov = len(words & aw)
        if ov >= 2:
            scored.append((ov, a))
    scored.sort(key=lambda x: -x[0])
    return [a for _, a in scored[:k]]


def _qualify_nodes(nodes, out, seen, n):
    for p in nodes:
        if len(out) >= n:
            break
        try:
            if p.get("status") != "ACTIVE" or (p.get("totalInventory") or 0) < 5:
                continue
            amt = float(((p.get("priceRangeV2") or {}).get("minVariantPrice") or {}).get("amount") or 0)
            if amt < 10:
                continue
            tl = (p.get("title") or "").lower()
            if any(b in tl for b in BLACKLIST) or p["handle"] in seen:
                continue
            seen.add(p["handle"])
            out.append({"handle": p["handle"], "title": p["title"], "price": amt})
        except Exception:
            continue
    return out


def top_products_for_entry(gql, entry, cols, n=4):
    """Real in-stock products for this topic: keyword search first, then CTA collection."""
    out, seen = [], set()
    try:
        words = [w for w in re.findall(r"[A-Za-z]+", (entry.get("title") or "").lower())
                 if w not in _STOP]
        queries = []
        if len(words) >= 2:
            queries.append("title:*" + words[0] + "* AND title:*" + words[1] + "* AND status:active")
        if words:
            queries.append("title:*" + words[0] + "* AND status:active")
        for q in queries:
            try:
                data = gql(_SEARCH_Q, {"q": q})
                nodes = ((data.get("products") or {}).get("nodes")) or []
            except Exception:
                nodes = []
            _qualify_nodes(nodes, out, seen, n)
            if len(out) >= n:
                break
        if len(out) < n:
            ckey = entry.get("cta_collection")
            chandle = ((cols.get(ckey) or {}).get("handle")) if ckey else None
            if chandle:
                try:
                    data = gql(_PICK_Q, {"h": chandle})
                    nodes = (((data.get("collectionByHandle") or {}).get("products") or {}).get("nodes")) or []
                except Exception:
                    nodes = []
                _qualify_nodes(nodes, out, seen, n)
    except Exception:
        pass
    return out


def build_store_context(env, entry, cols, domain, blog_handle):
    """Returns (context_string_or_None, hub_links_or_None) built from real store data.

    context_string: extra_notes block listing real in-stock products (name/price/URL).
    hub_links: [{title, handle}] of related published posts for internal linking.
    Fail-open: any error returns (None, None) upstream via caller's try/except.
    """
    from shopify_pub import _gql

    def gql(q, v=None):
        return _gql(env, q, v)

    parts = []
    prods = top_products_for_entry(gql, entry, cols, n=4)
    if prods:
        lines = "\n".join(
            "  - " + short_name(p["title"], p["handle"]) + " ($" + ("%.2f" % p["price"]) +
            ") -> " + domain.rstrip("/") + "/products/" + p["handle"]
            for p in prods)
        parts.append(
            "REAL IN-STOCK PRODUCTS from our store relevant to this topic "
            "(mention 1-3 naturally where they genuinely help the reader, link at most one "
            "inline in the body; NEVER invent other product names, brands, or prices):\n" + lines)
    links = None
    arts = _match_articles(entry.get("title"), _recent_articles(env), k=3)
    if arts:
        links = [{"title": a["title"], "handle": a["handle"]} for a in arts]
    ctx = "\n\n".join(parts) if parts else None
    return ctx, links
