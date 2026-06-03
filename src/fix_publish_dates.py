#!/usr/bin/env python3
"""One-off: restore the correct publish dates for this week's 7 articles that were
accidentally published all-at-once (publish state lost during a body update).

Past dates -> published on their original date; future dates -> re-scheduled.
Uses the proven GraphQL articleUpdate(publishDate, isPublished) — same as weekly.
"""
import sys, json, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import load_env, log
from shopify_pub import _gql

TARGETS = {
    "best-iced-tea-blends-for-summer": "2026-06-01T07:00:00Z",
    "cold-brew-tea-basics": "2026-06-02T07:00:00Z",
    "how-to-make-tea-less-bitter-when-iced": "2026-06-03T07:00:00Z",
    "summer-iced-tea-hub": "2026-06-04T07:00:00Z",
    "best-herbal-tea-for-hot-days": "2026-06-05T07:00:00Z",
    "citrus-iced-tea-ideas": "2026-06-06T07:00:00Z",
    "mint-iced-tea-quick-fix": "2026-06-07T07:00:00Z",
}

MUT = """mutation update($id: ID!, $article: ArticleUpdateInput!) {
  articleUpdate(id: $id, article: $article) {
    article { id handle isPublished publishedAt }
    userErrors { field message }
  }
}"""


def find_node_by_handle(env, handle):
    q = '{ articles(first: 250) { edges { node { id handle isPublished publishedAt } } } }'
    res = _gql(env, q)
    for e in res.get("articles", {}).get("edges", []):
        n = e.get("node") or {}
        if n.get("handle") == handle:
            return n
    return None


def main():
    env = load_env()
    now = datetime.datetime.now(datetime.timezone.utc)
    results = []
    for handle, date in TARGETS.items():
        n = find_node_by_handle(env, handle)
        if not n:
            log(f"  {handle}: NOT FOUND")
            results.append({"handle": handle, "status": "not_found"})
            continue
        dt = datetime.datetime.fromisoformat(date.replace("Z", "+00:00"))
        is_pub = dt <= now  # past -> publish on that date; future -> schedule
        v = {"id": n["id"], "article": {"isPublished": is_pub, "publishDate": date}}
        res = _gql(env, MUT, v)
        ue = res["articleUpdate"].get("userErrors", [])
        if ue:
            log(f"  {handle}: ERROR {ue}")
            results.append({"handle": handle, "status": "error", "errors": ue})
            continue
        ad = res["articleUpdate"]["article"]
        log(f"  {handle}: was({n.get('publishedAt')},pub={n.get('isPublished')}) -> "
            f"publishedAt={ad['publishedAt']} isPublished={ad['isPublished']}")
        results.append({"handle": handle, "publishedAt": ad["publishedAt"], "isPublished": ad["isPublished"]})
    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
