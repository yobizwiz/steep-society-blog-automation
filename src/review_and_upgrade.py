#!/usr/bin/env python3
"""Bulk review + upgrade existing Shopify articles to 5-dim 10/10.

Process:
1. Load cursor (which articles already processed)
2. Fetch next batch of articles from Shopify (blog handle from env)
3. For each article:
   - Score 5 dim with Anthropic (score_article) + Gemini (gemini_review)
   - If combined min < 10: run perfection_pass to upgrade body
   - Re-score after upgrade
   - Update Shopify article body_html
4. Save cursor + send Telegram progress
"""
from __future__ import annotations
import argparse, datetime as _dt, json, os, re, sys, time, traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils import OUTPUT_DIR, ensure_dirs, load_env, log
from content import (gemini_review, merge_gemini_into_judgment, combined_min_score,
                     _claude_call, _extract_json, _strip_html, _build_few_shot_block,
                     OUTPUT_SCHEMA_INSTRUCTION)
from utils import load_system_prompt, load_few_shot_articles
from perfection import perfection_pass
import urllib.request

CURSOR_FILE = OUTPUT_DIR / "upgrade-cursor.json"


SCORE_SYSTEM = """You are a brutal, independent SEO + content reviewer for a Shopify lifestyle/wellness blog. Score the existing article on 5 dimensions (0-10 each):

1. content_quality — Distinct angle, depth, specific actionable info, no fluff.
2. onpage_seo — Meta title 50-60, meta description 140-160, primary keyword in title/slug/meta/intro, 5-row table max, body uses H2/H3 structure.
3. conversion_alignment — Single CTA after Quick Recap matching collection name 1:1, no orphan product mentions, Quick Answer in 1st-3rd paragraph.
4. ai_search_optimization — AI citation-friendly: Quick Answer placement, single-fact atomic sentences, numbers/measurements, FAQPage + Article JSON-LD inline in body.
5. eeat — Experience (first-hand insight tone), Expertise (specific accurate data), Authoritativeness (consistent brand voice), Trustworthiness (no factual errors, no contradictions).

Be tough. Most articles deserve 7-9 not 10.

Output ONE JSON object exactly:
{
  "content_quality": {"score": 0-10, "reason": "..."},
  "onpage_seo": {"score": 0-10, "reason": "..."},
  "conversion_alignment": {"score": 0-10, "reason": "..."},
  "ai_search_optimization": {"score": 0-10, "reason": "..."},
  "eeat": {"score": 0-10, "reason": "..."},
  "body_judgment": "overall assessment under 200 chars",
  "page_judgment": "any template-level deductions",
  "deductions": []
}"""


def score_article(article, env):
    """Anthropic 5-dim score for an existing article (no body modification)."""
    body_excerpt = _strip_html(article.get("body_html",""))[:8000]
    user_msg = (
        "Score this existing article. Title, meta, body excerpt provided.\n\n"
        f"Title: {article.get('title','?')}\n"
        f"Meta title: {article.get('meta_title','?')}\n"
        f"Meta description: {article.get('meta_description','?')}\n"
        f"Slug: {article.get('url_slug','?')}\n"
        f"Tags: {', '.join(article.get('tags',[]))}\n\n"
        f"Body (excerpt):\n{body_excerpt}"
    )
    raw = _claude_call(
        api_key=env["ANTHROPIC_API_KEY"],
        model=env.get("ANTHROPIC_REVIEW_MODEL", env["ANTHROPIC_MODEL"]),
        system=SCORE_SYSTEM,
        messages=[{"role":"user","content":user_msg}],
        max_tokens=2000, temperature=0.2,
    )
    return _extract_json(raw)


def shopify_fetch_all_articles(env, blog_handle):
    """Fetch all articles in the given blog (paginated GraphQL)."""
    store = env["SHOPIFY_STORE_URL"]
    token = env["SHOPIFY_ADMIN_TOKEN"]
    out = []
    cursor = None
    while True:
        after = f', after: "{cursor}"' if cursor else ""
        q = '{ articles(first: 100' + after + ') { edges { cursor node { id title handle blog { handle } } } pageInfo { hasNextPage } } }'
        req = urllib.request.Request(
            f"https://{store}/admin/api/2025-01/graphql.json",
            data=json.dumps({"query": q}).encode(),
            headers={"X-Shopify-Access-Token": token, "Content-Type":"application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read())
        edges = d['data']['articles']['edges']
        for e in edges:
            n = e['node']
            if n['blog']['handle'] == blog_handle:
                out.append({'id': n['id'].split('/')[-1], 'title': n['title'], 'handle': n['handle']})
        if d['data']['articles']['pageInfo']['hasNextPage'] and edges:
            cursor = edges[-1]['cursor']
        else:
            break
    return out


def shopify_fetch_article_body(env, article_id):
    """REST: fetch full article body_html + metadata."""
    store = env["SHOPIFY_STORE_URL"]
    token = env["SHOPIFY_ADMIN_TOKEN"]
    url = f"https://{store}/admin/api/2025-01/articles/{article_id}.json"
    req = urllib.request.Request(url, headers={"X-Shopify-Access-Token": token})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())['article']


def shopify_update_body(env, article_id, body_html):
    store = env["SHOPIFY_STORE_URL"]
    token = env["SHOPIFY_ADMIN_TOKEN"]
    url = f"https://{store}/admin/api/2025-01/articles/{article_id}.json"
    payload = {"article": {"id": int(article_id), "body_html": body_html}}
    req = urllib.request.Request(url, method="PUT",
        data=json.dumps(payload).encode(),
        headers={"X-Shopify-Access-Token": token, "Content-Type":"application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())['article']


def telegram_send(env, text):
    token = env.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = env.get("TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    body = json.dumps({"chat_id": chat_id, "text": text, "parse_mode":"HTML", "disable_web_page_preview": True}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
        data=body, headers={"Content-Type":"application/json"})
    try:
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as e:
        log(f"[telegram] send failed: {e}", "WARN")


def process_one(article_info, env):
    """Score → upgrade if needed → update Shopify."""
    aid = article_info['id']
    # Fetch full article body
    full = shopify_fetch_article_body(env, aid)
    article = {
        "title": full.get("title",""),
        "body_html": full.get("body_html",""),
        "meta_title": full.get("title",""),  # Shopify article title (no separate meta_title in REST)
        "meta_description": full.get("summary_html") or "",
        "url_slug": full.get("handle",""),
        "tags": [t.strip() for t in (full.get("tags","") or "").split(",") if t.strip()],
    }
    
    # Score with Anthropic
    try:
        anthropic_score = score_article(article, env)
    except Exception as e:
        log(f"  Anthropic score fail: {e}", "WARN")
        return {"id": aid, "title": full.get("title","")[:50], "status": "anthropic_score_error", "error": str(e)[:200]}
    article["internal_judgment"] = anthropic_score
    
    # Score with Gemini (cross-validation)
    try:
        gem = gemini_review(article, env)
        if gem:
            article = merge_gemini_into_judgment(article, gem)
    except Exception as e:
        log(f"  Gemini score fail: {e}", "WARN")
    
    before_min = combined_min_score(article)
    log(f"  Initial combined min: {before_min}/10")
    
    if before_min >= 10:
        return {"id": aid, "title": full.get("title","")[:50], "status": "already_10", 
                "before": before_min, "after": before_min, "anthropic": anthropic_score, "gemini": article.get("internal_judgment",{}).get("gemini_review")}
    
    # Upgrade with perfection_pass
    try:
        upgraded = perfection_pass(article, env)
        # Re-validate with Gemini
        try:
            gem2 = gemini_review(upgraded, env)
            if gem2:
                upgraded = merge_gemini_into_judgment(upgraded, gem2)
        except Exception:
            pass
        after_min = combined_min_score(upgraded)
        log(f"  After perfection min: {after_min}/10")
        
        # If still not 10, try once more
        if after_min < 10:
            try:
                upgraded2 = perfection_pass(upgraded, env)
                try:
                    gem3 = gemini_review(upgraded2, env)
                    if gem3: upgraded2 = merge_gemini_into_judgment(upgraded2, gem3)
                except Exception: pass
                if combined_min_score(upgraded2) > after_min:
                    upgraded = upgraded2
                    after_min = combined_min_score(upgraded2)
                    log(f"  After 2nd perfection: {after_min}/10")
            except Exception as e:
                log(f"  2nd perfection skip: {e}", "WARN")
        
        # Update Shopify body
        new_body = upgraded.get("body_html","")
        if new_body and new_body != article["body_html"]:
            # Apply paragraph spacing (same as new articles)
            from shopify_pub import _apply_paragraph_spacing
            new_body = _apply_paragraph_spacing(new_body)
            shopify_update_body(env, aid, new_body)
            return {"id": aid, "title": full.get("title","")[:50], "status": "upgraded",
                    "before": before_min, "after": after_min, "anthropic": upgraded.get("internal_judgment",{}),
                    "gemini": upgraded.get("internal_judgment",{}).get("gemini_review")}
        else:
            return {"id": aid, "title": full.get("title","")[:50], "status": "no_body_change",
                    "before": before_min, "after": after_min}
    except Exception as e:
        log(f"  perfection error: {e}", "WARN")
        traceback.print_exc()
        return {"id": aid, "title": full.get("title","")[:50], "status": "perfection_error", "error": str(e)[:200]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--blog_handle", required=True, help="e.g. steep-society-journal or home-cafe-baking")
    ap.add_argument("--batch_size", type=int, default=15)
    ap.add_argument("--max_time_min", type=int, default=80, help="Stop processing if elapsed exceeds this")
    args = ap.parse_args()
    
    ensure_dirs()
    env = load_env()
    
    log("=" * 60)
    log(f"# Bulk Review + Upgrade — blog={args.blog_handle}")
    log(f"# Batch: {args.batch_size} articles, max {args.max_time_min} min")
    log("=" * 60)
    
    # Load cursor
    cursor_path = OUTPUT_DIR / f"upgrade-cursor-{args.blog_handle}.json"
    cursor = {"processed_ids": [], "started_at": None, "all_articles": None}
    if cursor_path.exists():
        try:
            cursor = json.loads(cursor_path.read_text())
        except Exception:
            pass
    if cursor.get("started_at") is None:
        cursor["started_at"] = _dt.datetime.utcnow().isoformat()
    
    # Get all articles (cache in cursor)
    if not cursor.get("all_articles"):
        log("Fetching all articles in blog...")
        all_arts = shopify_fetch_all_articles(env, args.blog_handle)
        cursor["all_articles"] = all_arts
        log(f"Total: {len(all_arts)} articles")
    else:
        all_arts = cursor["all_articles"]
    
    processed_set = set(cursor.get("processed_ids", []))
    remaining = [a for a in all_arts if a["id"] not in processed_set]
    
    log(f"Total: {len(all_arts)}, processed: {len(processed_set)}, remaining: {len(remaining)}")
    
    if not remaining:
        log("✅ All articles already processed!")
        telegram_send(env, f"✅ <b>{args.blog_handle}</b> 일괄 review 완료\n전체 {len(all_arts)}개 처리됨")
        # Final cleanup — keep cursor for record
        return
    
    start_time = time.time()
    results = []
    
    for art in remaining[:args.batch_size]:
        elapsed_min = (time.time() - start_time) / 60
        if elapsed_min > args.max_time_min:
            log(f"⏰ Time budget exceeded ({elapsed_min:.1f}/{args.max_time_min} min) — stopping batch")
            break
        log(f"\n--- [{len(processed_set)+1}/{len(all_arts)}] {art['handle'][:50]} ---")
        try:
            r = process_one(art, env)
        except Exception as e:
            log(f"  unexpected error: {e}", "ERROR")
            traceback.print_exc()
            r = {"id": art["id"], "title": art.get("title","")[:50], "status": "exception", "error": str(e)[:200]}
        results.append(r)
        processed_set.add(art["id"])
        # Save cursor after each article (safe)
        cursor["processed_ids"] = sorted(processed_set)
        cursor_path.write_text(json.dumps(cursor, ensure_ascii=False, indent=2))
        time.sleep(1)
    
    # Summary
    upgraded = sum(1 for r in results if r["status"] == "upgraded")
    already_10 = sum(1 for r in results if r["status"] == "already_10")
    errors = sum(1 for r in results if "error" in r["status"])
    
    log("\n" + "=" * 60)
    log(f"Batch done — {len(results)} processed")
    log(f"  upgraded: {upgraded} / already 10/10: {already_10} / errors: {errors}")
    log(f"  total progress: {len(processed_set)}/{len(all_arts)}")
    
    # Telegram report
    remain = len(all_arts) - len(processed_set)
    if remain > 0:
        next_msg = f"\n다음 batch 자동 시작 — 남은 {remain}개"
    else:
        next_msg = "\n🎉 모든 글 처리 완료!"
    
    lines = []
    for r in results[:10]:  # show first 10 in message
        emoji = "🆙" if r["status"] == "upgraded" else ("✅" if r["status"] == "already_10" else "⚠️")
        bef = r.get("before","?")
        aft = r.get("after","?")
        lines.append(f"{emoji} {r['title'][:50]} ({bef}→{aft})")
    
    msg = (
        f"📊 <b>{args.blog_handle}</b> review batch\n"
        f"진행: {len(processed_set)}/{len(all_arts)}\n"
        f"이번 batch: ⬆️{upgraded} ✅{already_10} ⚠️{errors}\n"
        f"\n" + "\n".join(lines) +
        next_msg
    )
    telegram_send(env, msg)
    
    # Save batch results to file
    batch_path = OUTPUT_DIR / f"upgrade-batch-{_dt.datetime.utcnow().strftime('%Y%m%d-%H%M%S')}-{args.blog_handle}.json"
    batch_path.write_text(json.dumps({
        "blog_handle": args.blog_handle,
        "timestamp": _dt.datetime.utcnow().isoformat(),
        "total": len(all_arts),
        "progress": len(processed_set),
        "results": results,
    }, indent=2, ensure_ascii=False))
    log(f"Report saved: {batch_path}")


if __name__ == "__main__":
    main()
