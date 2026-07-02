#!/usr/bin/env python3
"""Full SEO audit of all collections with products (read-only)."""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from utils import load_env
from shopify_pub import _gql

def main():
    env = load_env()
    cols=[]; cursor=None
    while True:
        after = f', after:"{cursor}"' if cursor else ""
        q = "{ collections(first:100"+after+"){ pageInfo{hasNextPage endCursor} edges{ node{ title handle productsCount{count} seo{title description} descriptionHtml resourcePublicationsCount{count} } } } }"
        d=_gql(env,q)["collections"]
        cols += [e["node"] for e in d["edges"]]
        if d["pageInfo"]["hasNextPage"]: cursor=d["pageInfo"]["endCursor"]
        else: break
    withp=[c for c in cols if c["productsCount"]["count"]>0]
    print(f"전체 {len(cols)}개 | 상품보유 {len(withp)}개 | 빈컬렉션 {len(cols)-len(withp)}개\n")
    def score(c):
        seo=c.get("seo") or {}; t=seo.get("title") or ""; ds=seo.get("description") or ""; dh=c.get("descriptionHtml") or ""
        return len(t),len(ds),len(dh)
    print(f"{'handle':40} {'prod':>5} {'tLen':>4} {'dLen':>4} {'htmlLen':>7} {'pub':>4}")
    print("-"*80)
    weak=[]
    for c in sorted(withp,key=lambda x:-x["productsCount"]["count"]):
        tl,dl,hl=score(c); pub=(c.get("resourcePublicationsCount") or {}).get("count")
        flag=""
        if not(50<=tl<=70): flag+="T"
        if not(120<=dl<=170): flag+="D"
        if hl<400: flag+="H"
        if flag: weak.append((c["handle"],c["productsCount"]["count"],tl,dl,hl,flag))
        print(f"{c['handle'][:40]:40} {c['productsCount']['count']:>5} {tl:>4} {dl:>4} {hl:>7} {str(pub):>4}  {flag}")
    print(f"\n=== 보강 필요(약함) {len(weak)}개 (T=제목 D=메타설명 H=본문<400자) ===")
    for h,p,tl,dl,hl,fl in weak: print(f"  {h} (prod {p}) [{fl}] t{tl}/d{dl}/html{hl}")
    # best template = highest-scoring well-optimized
    best=sorted([c for c in withp if score(c)[2]>=800 and 50<=score(c)[0]<=70 and 120<=score(c)[1]<=170], key=lambda x:-score(x)[2])[:3]
    print("\n=== 잘 된 템플릿 후보 top3 ===")
    for c in best:
        tl,dl,hl=score(c); print(f"  {c['handle']} — t{tl}/d{dl}/html{hl}, prod {c['productsCount']['count']}")
    print("===JSON==="); print(json.dumps([{"h":c["handle"],"p":c["productsCount"]["count"],"t":score(c)[0],"d":score(c)[1],"html":score(c)[2]} for c in withp]))

if __name__=="__main__": main()
