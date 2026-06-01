#!/usr/bin/env python3
"""Read-only: report 5-dimension upgrade coverage for this shop's blog.

Counts how many published articles carry the custom.upgraded_v2 metafield
(= retroactively scored on all 5 dims + perfected to 10/10) vs not, and lists
the not-yet-upgraded handles. No writes, no image generation, no quota use.
"""
import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import load_env, log
from review_and_upgrade import shopify_fetch_all_articles


def main():
    env = load_env()
    handle = env.get("SHOPIFY_BLOG_HANDLE", "")
    arts = shopify_fetch_all_articles(env, handle)
    total = len(arts)
    notup = [a for a in arts if not a["upgraded"]]
    up = total - len(notup)
    pct = round(up / total * 100, 1) if total else 0
    log(f"blog={handle} total={total} upgraded_v2={up} ({pct}%) not_upgraded={len(notup)}")
    for a in notup:
        log(f"  NOT_UPGRADED: {a['handle']}")
    print(json.dumps({
        "blog": handle, "total": total, "upgraded": up, "pct": pct,
        "not_upgraded": len(notup),
        "not_upgraded_handles": [a["handle"] for a in notup],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
