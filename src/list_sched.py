#!/usr/bin/env python3
"""One-off: list all blog articles with publish date + status (incl. scheduled)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from utils import load_env
from shopify_pub import _gql

def main():
    env = load_env()
    q = ('{ articles(first: 100, sortKey: PUBLISHED_AT, reverse: true) '
         '{ edges { node { title handle publishedAt isPublished } } } }')
    edges = _gql(env, q).get("articles", {}).get("edges", [])
    rows = []
    for e in edges:
        n = e.get("node") or {}
        pub = n.get("publishedAt") or ""
        rows.append((pub, n.get("isPublished"), n.get("handle"), (n.get("title") or "")[:48]))
    june = [r for r in rows if r[0].startswith("2026-06")]
    print(f"TOTAL articles fetched: {len(rows)} | June 2026: {len(june)}")
    print("-"*92)
    for pub, isp, handle, title in sorted(june):
        flag = "LIVE " if isp else "SCHED"
        print(f"{pub[:16]}  [{flag}]  {handle:46}  {title}")
    print("-"*92)
    # explicit 6/13 check
    found = [r for r in rows if r[0].startswith("2026-06-13")]
    print("6/13 articles:", [(r[2], 'LIVE' if r[1] else 'SCHED', r[0]) for r in found] or "NONE FOUND")

if __name__ == "__main__":
    main()
