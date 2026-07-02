#!/usr/bin/env python3
"""Audit: target collections' SEO+publication state + mega-collection SEO pattern."""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from utils import load_env
from shopify_pub import _gql

def main():
    env = load_env()
    q = """{
      publications(first: 25) { edges { node { id name } } }
      collections(first: 250) { edges { node {
        id title handle
        productsCount { count }
        seo { title description }
        descriptionHtml
        resourcePublicationsCount { count }
      } } }
    }"""
    d = _gql(env, q)
    pubs = [e["node"]["name"] for e in d["publications"]["edges"]]
    print(f"판매 채널(publications) 총 {len(pubs)}개: {pubs}")
    cols = [e["node"] for e in d["collections"]["edges"]]
    print(f"컬렉션 총 {len(cols)}개\n")
    def row(c, tag):
        seo = c.get("seo") or {}
        st = seo.get("title"); sd = seo.get("description")
        dh = c.get("descriptionHtml") or ""
        pub = (c.get("resourcePublicationsCount") or {}).get("count")
        print(f"[{tag}] {c['title']}  (handle={c['handle']}, products={c['productsCount']['count']})")
        print(f"   published_to_channels: {pub}/{len(pubs)}")
        print(f"   seo.title: {st!r}")
        print(f"   seo.description: {sd!r}")
        print(f"   descriptionHtml: {len(dh)} chars | {dh[:160]!r}")
        print()
    import re
    targets = [c for c in cols if re.search(r"brew|flavou?r|aroma", c["title"], re.I)]
    print("=== 대상(brew/flavor/aroma) ===")
    for c in targets: row(c, "TARGET")
    print("=== 메가 컬렉션 상위 5 (SEO 패턴 참고) ===")
    for c in sorted(cols, key=lambda x: -x["productsCount"]["count"])[:5]:
        row(c, "MEGA")

if __name__ == "__main__":
    main()
