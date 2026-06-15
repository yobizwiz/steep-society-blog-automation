#!/usr/bin/env python3
"""Dump ALL blog articles: handle | publishedAt | isPublished (for indexing triage)."""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from utils import load_env
from shopify_pub import _api, _gql, get_blog_id

def main():
    env = load_env()
    blog_id = get_blog_id(env, env["SHOPIFY_BLOG_HANDLE"])
    arts = _api(env, f"blogs/{blog_id}/articles.json?limit=250&published_status=any&fields=id,handle,created_at,published_at")["articles"]
    ids = [f'gid://shopify/Article/{a["id"]}' for a in arts]
    gmap = {}
    for i in range(0, len(ids), 100):
        q = "query($ids:[ID!]!){ nodes(ids:$ids){ ... on Article { id isPublished publishedAt } } }"
        for n in _gql(env, q, {"ids": ids[i:i+100]})["nodes"]:
            if n: gmap[n["id"]] = n
    rows = []
    for a in arts:
        g = gmap.get(f'gid://shopify/Article/{a["id"]}', {})
        rows.append({"handle": a.get("handle"), "publishedAt": g.get("publishedAt"),
                     "isPublished": g.get("isPublished"), "created_at": a.get("created_at")})
    rows.sort(key=lambda r: (r["publishedAt"] or r["created_at"] or ""))
    print("TOTAL", len(rows))
    print("===JSON_START===")
    print(json.dumps(rows, ensure_ascii=False))
    print("===JSON_END===")

if __name__ == "__main__":
    main()
