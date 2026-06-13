#!/usr/bin/env python3
"""One-off: re-score a live article with the FIXED Gemini judge (full body + facts).
Does NOT modify the article — just prints new 5-dim scores for comparison."""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from utils import load_env
from shopify_pub import _api, get_blog_id
from content import gemini_review

TARGET = "summer-hosting-tea-hub"
DIMS = ("content_quality","onpage_seo","conversion_alignment","ai_search_optimization","eeat")

def main():
    env = load_env()
    blog_id = get_blog_id(env, env["SHOPIFY_BLOG_HANDLE"])
    arts = _api(env, f"blogs/{blog_id}/articles.json?limit=250&published_status=any")["articles"]
    a = next((x for x in arts if x.get("handle") == TARGET), None)
    if not a:
        print("ARTICLE NOT FOUND:", TARGET); return
    aid = a["id"]
    full = _api(env, f"blogs/{blog_id}/articles/{aid}.json")["article"]
    # meta title/desc from metafields
    mt = md = ""
    try:
        mfs = _api(env, f"blogs/{blog_id}/articles/{aid}/metafields.json").get("metafields", [])
        for m in mfs:
            if m.get("namespace") == "global" and m.get("key") == "title_tag": mt = m.get("value", "")
            if m.get("namespace") == "global" and m.get("key") == "description_tag": md = m.get("value", "")
    except Exception as e:
        print("metafield fetch warn:", e)
    article = {
        "title": full.get("title"),
        "body_html": full.get("body_html", "") or "",
        "meta_title": mt,
        "meta_description": md,
        "url_slug": full.get("handle"),
    }
    print(f"Re-scoring: {article['title']}")
    print(f"  body_html: {len(article['body_html'])} chars | meta_title {len(mt)} | meta_desc {len(md)}")
    print("="*80)
    gem = gemini_review(article, env)
    if not gem:
        print("Gemini returned None"); return
    print("NEW Gemini scores (fixed judge — full body + structural facts):")
    vals = []
    for k in DIMS:
        o = gem.get(k) or {}
        sc = o.get("score")
        if isinstance(sc, (int, float)): vals.append(sc)
        print(f"  {k:24}: {sc}  — {str(o.get('reason',''))[:110]}")
    print("-"*80)
    print(f"  Gemini MIN across 5 dims: {min(vals) if vals else 'n/a'}/10")
    print(f"  (OLD broken judge had: conversion=4, AISO=8 from truncated 6000-char excerpt)")
    wk = gem.get("top_3_weaknesses")
    if wk: print("  weaknesses:", wk)

if __name__ == "__main__":
    main()
