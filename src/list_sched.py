#!/usr/bin/env python3
"""One-off: true scheduling state of June articles via REST(list)+GraphQL(node)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from utils import load_env
from shopify_pub import _api, _gql, get_blog_id

def main():
    env = load_env()
    blog_id = get_blog_id(env, env["SHOPIFY_BLOG_HANDLE"])
    arts = _api(env, f"blogs/{blog_id}/articles.json?limit=250&published_status=any")["articles"]
    june = [a for a in arts if (a.get("created_at") or "").startswith("2026-06") or (a.get("published_at") or "").startswith("2026-06")]
    # GraphQL state per id
    ids = [f'gid://shopify/Article/{a["id"]}' for a in june]
    q = "query($ids:[ID!]!){ nodes(ids:$ids){ ... on Article { id handle isPublished publishedAt } } }"
    nodes = _gql(env, q, {"ids": ids})["nodes"]
    gmap = {n["id"]: n for n in nodes if n}
    print(f"June articles: {len(june)}")
    print(f"{'handle':46} {'REST pub_at':22} {'GQL isPub':9} {'GQL publishedAt'}")
    print("-"*110)
    def key(a): return (a.get("published_at") or a.get("created_at") or "")
    for a in sorted(june, key=key):
        g = gmap.get(f'gid://shopify/Article/{a["id"]}', {})
        print(f"{(a.get('handle') or '')[:44]:46} {str(a.get('published_at'))[:20]:22} {str(g.get('isPublished')):9} {g.get('publishedAt')}")
    print("-"*110)
    for d in ["2026-06-13","2026-06-14","2026-06-15"]:
        hit=[(a,gmap.get(f'gid://shopify/Article/{a["id"]}',{})) for a in june if a.get("handle") in
             ("fruit-iced-tea-pairing-ideas","how-to-make-iced-tea-for-a-group","summer-hosting-tea-hub")]
    # explicit
    for h in ("fruit-iced-tea-pairing-ideas","how-to-make-iced-tea-for-a-group","summer-hosting-tea-hub"):
        a=next((x for x in june if x.get("handle")==h),None)
        if a:
            g=gmap.get(f'gid://shopify/Article/{a["id"]}',{})
            print(f"{h}: isPublished={g.get('isPublished')} publishedAt={g.get('publishedAt')} REST_pub={a.get('published_at')}")
        else:
            print(f"{h}: NOT FOUND")

if __name__ == "__main__":
    main()
