#!/usr/bin/env python3
"""Fix two hub collections: enrich descriptionHtml (keep good SEO) + publish to all channels."""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from utils import load_env
from shopify_pub import _gql

BREW = """<p><strong>Tea, Your Way</strong><br>Every tea lover has a favorite way to brew and sip — and this collection organizes our teas by exactly that: how you make them and how you like to drink them, from slow overnight steeps to quick cafe-style lattes.</p>
<p>Reach for <a href="https://steep-society.com/collections/cold-brew-tea">cold brew tea</a> when you want smooth, low-bitterness refreshment steeped in the fridge, or <a href="https://steep-society.com/collections/iced-tea-blends">iced tea blends</a> built to stay bright over ice. Short on time? Instant &amp; on-the-go teas deliver premium flavor in seconds, anywhere. And when you want to treat yourself, cafe-style, milk tea, and latte-friendly blends whisk up rich, creamy, coffeehouse-worthy cups at home.</p>
<p>Browse by the experience you're after — whether that's a 12-hour cold steep or a five-minute latte — and find a tea that fits the moment.</p>"""

FLAV = """<p><strong>Find Your Flavor</strong><br>Tea is a world of taste and scent. This collection lets you shop by the notes you love most — so whether you crave bright citrus, cozy spice, or delicate florals, you can go straight to your kind of cup.</p>
<p>Explore fruity and tropical blends bursting with berry, mango, and stone-fruit sweetness; floral teas layered with jasmine, rose, and lavender; and spiced chais warmed with cinnamon, ginger, and clove. Prefer something bolder or cooler? Discover smoky, citrus, minty, and sweet dessert profiles, each curated for a distinct mood and moment.</p>
<p>Pick a profile that matches your taste and let your palate lead the way.</p>"""

TARGETS = {"brewing-style-experience": BREW, "flavor-aroma": FLAV}

def main():
    env = load_env()
    scopes = [s["handle"] for s in _gql(env, "{ currentAppInstallation { accessScopes { handle } } }")["currentAppInstallation"]["accessScopes"]]
    print("token scopes:", scopes)
    has_prod = any("write_products" in s for s in scopes)
    has_pub = any("write_publications" in s for s in scopes)
    print(f"write_products={has_prod} | write_publications={has_pub}\n")
    pubs = [(e["node"]["id"], e["node"]["name"]) for e in _gql(env, "{ publications(first:25){edges{node{id name}}} }")["publications"]["edges"]]
    for handle, html in TARGETS.items():
        cid = _gql(env, '{ collectionByHandle(handle:"'+handle+'"){ id title } }')["collectionByHandle"]
        cid_id = cid["id"]
        print(f"[{handle}] {cid['title']} ({cid_id})")
        # 1) enrich descriptionHtml
        try:
            m = "mutation($input:CollectionInput!){ collectionUpdate(input:$input){ collection{id descriptionHtml} userErrors{field message} } }"
            r = _gql(env, m, {"input": {"id": cid_id, "descriptionHtml": html}})
            ue = r["collectionUpdate"]["userErrors"]
            print(f"   descriptionHtml update: {'OK ('+str(len(r['collectionUpdate']['collection']['descriptionHtml']))+' chars)' if not ue else ue}")
        except Exception as e:
            print(f"   descriptionHtml update FAILED: {str(e)[:160]}")
        # 2) publish to all channels
        try:
            pm = "mutation($id:ID!,$input:[PublicationInput!]!){ publishablePublish(id:$id, input:$input){ userErrors{field message} } }"
            r = _gql(env, pm, {"id": cid_id, "input": [{"publicationId": pid} for pid,_ in pubs]})
            ue = r["publishablePublish"]["userErrors"]
            print(f"   publish to {len(pubs)} channels: {'OK' if not ue else ue}")
        except Exception as e:
            print(f"   publish FAILED: {str(e)[:160]}")
        print()

if __name__ == "__main__":
    main()
