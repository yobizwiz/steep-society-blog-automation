#!/usr/bin/env python3
"""One-off GSC Tier-A refresh: update SEO title tags for 3 articles + add a
Focus & Energy CTA box to the energy article (which lacked one). Live, published
articles — body update preserves publish state."""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from utils import load_env, log
from shopify_pub import _gql, _api, update_article_body

TITLE_TAGS = {
    "why-does-iced-tea-taste-weak-quick-fix": "Why Does Iced Tea Taste Weak? 5 Quick Fixes That Work",
    "herbal-tea-too-weak-quick-fix": "Herbal Tea Too Weak? 6 Quick Fixes for Bolder Flavor",
    "tea-gift-basket-ideas": "Tea Gift Basket Ideas: Build the Perfect Set for Any Tea Lover",
}
CTA_ARTICLE = "best-tea-for-energy-without-jitters"
CTA_HTML = ('<div style="border: 1px solid #ded6c8; padding: 22px; margin: 32px 0 0; border-radius: 14px; background: #faf7f1;">\n'
            '  <p style="margin: 0 0 8px; font-size: 18px; line-height: 1.4;"><strong>Ready to skip the jitters?</strong></p>\n'
            '  <p style="margin: 0 0 16px; line-height: 1.6;">Explore matcha, yerba mate, and balanced-caffeine blends made for smooth, steady sipping all day.</p>\n'
            '  <p style="margin: 0;">\n'
            '    <a href="https://steep-society.com/collections/focus-energy-tea" style="display: inline-block; padding: 11px 18px; border-radius: 999px; background: #2b2118; color: #ffffff; text-decoration: none; font-weight: 600;">Shop Focus &amp; Energy</a>\n'
            '  </p>\n'
            '</div>')


def ids_by_handle(env, handles):
    out = {}; cursor = None
    while True:
        after = f', after: "{cursor}"' if cursor else ""
        q = '{ articles(first: 100' + after + ') { edges { cursor node { id handle } } pageInfo { hasNextPage } } }'
        d = _gql(env, q)
        for e in d["articles"]["edges"]:
            n = e["node"]
            if n["handle"] in handles:
                out[n["handle"]] = n["id"]
        if d["articles"]["pageInfo"]["hasNextPage"] and d["articles"]["edges"]:
            cursor = d["articles"]["edges"][-1]["cursor"]
        else:
            break
    return out


def main():
    env = load_env()
    ids = ids_by_handle(env, list(TITLE_TAGS) + [CTA_ARTICLE])
    results = []
    mut = ('mutation($m:[MetafieldsSetInput!]!){ metafieldsSet(metafields:$m){ '
           'userErrors{ field message } } }')
    for h, title in TITLE_TAGS.items():
        gid = ids.get(h)
        if not gid:
            log(f"  {h}: NOT FOUND"); results.append({"handle": h, "status": "not_found"}); continue
        r = _gql(env, mut, {"m": [{"ownerId": gid, "namespace": "global", "key": "title_tag",
                                   "type": "single_line_text_field", "value": title}]})
        ue = r["metafieldsSet"]["userErrors"]
        log(f"  title_tag {h}: {'OK -> '+title if not ue else ue}")
        results.append({"handle": h, "status": "title_updated" if not ue else "error", "errors": ue})
    gid = ids.get(CTA_ARTICLE)
    if gid:
        aid = gid.split("/")[-1]
        body = (_api(env, f"articles/{aid}.json")["article"].get("body_html") or "")
        if 'collections/focus-energy-tea"' in body and "border-radius: 999px" in body:
            log(f"  {CTA_ARTICLE}: CTA box already present — skip"); results.append({"handle": CTA_ARTICLE, "status": "cta_already"})
        else:
            update_article_body(env, aid, body.rstrip() + "\n" + CTA_HTML)
            log(f"  {CTA_ARTICLE}: Focus & Energy CTA added")
            results.append({"handle": CTA_ARTICLE, "status": "cta_added"})
    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
