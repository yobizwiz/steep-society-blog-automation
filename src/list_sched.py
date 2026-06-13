#!/usr/bin/env python3
"""One-off: list ALL blog articles incl. scheduled via REST published_status=any."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from utils import load_env
from shopify_pub import _api, get_blog_id

def main():
    env = load_env()
    blog_id = get_blog_id(env, env["SHOPIFY_BLOG_HANDLE"])
    arts = _api(env, f"blogs/{blog_id}/articles.json?limit=250&published_status=any")["articles"]
    rows = []
    for a in arts:
        pub = a.get("published_at") or ""
        rows.append((pub, bool(a.get("published_at")) and a.get("published_at") <= "2026-06-13T06:40", a.get("handle"), (a.get("title") or "")[:46], a.get("published_at")))
    june = [a for a in arts if (a.get("published_at") or "").startswith("2026-06") or (a.get("created_at") or "").startswith("2026-06")]
    print(f"TOTAL articles: {len(arts)} | June-related: {len(june)}")
    print("-"*96)
    def key(a): return a.get("published_at") or a.get("created_at") or ""
    for a in sorted(june, key=key):
        pa = a.get("published_at")
        status = "LIVE " if pa and pa <= "2026-06-13T06:40:00Z" else ("SCHED" if pa else "DRAFT")
        print(f"pub={str(pa)[:16]:16} [{status}]  {a.get('handle','')[:44]:44} {(a.get('title') or '')[:40]}")
    print("-"*96)
    for d in ["2026-06-13","2026-06-14","2026-06-15"]:
        hit = [a for a in arts if (a.get("published_at") or "").startswith(d)]
        print(f"{d}: " + (", ".join(f"{a.get('handle')} (pub={a.get('published_at')})" for a in hit) if hit else "NONE"))

if __name__ == "__main__":
    main()
