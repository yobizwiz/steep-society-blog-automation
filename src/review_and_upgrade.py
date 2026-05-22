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
from content import generate_full_article, _claude_call, _extract_json
from images import generate_image_for_slot
from shopify_pub import (get_blog_id, upload_image, insert_body_images,
                          _apply_paragraph_spacing)
from utils import load_yaml, CONFIG_DIR
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
    """Fetch all articles in the given blog + their 'upgraded' metafield (paginated GraphQL).
    Articles with custom.upgraded_v2 metafield set are considered already processed."""
    store = env["SHOPIFY_STORE_URL"]
    token = env["SHOPIFY_ADMIN_TOKEN"]
    out = []
    cursor = None
    while True:
        after = f', after: "{cursor}"' if cursor else ""
        q = ('{ articles(first: 100' + after + ') { edges { cursor node { '
             'id title handle blog { handle } '
             'metafield(namespace: "custom", key: "upgraded_v2") { value } '
             '} } pageInfo { hasNextPage } } }')
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
                meta_val = (n.get('metafield') or {}).get('value') if n.get('metafield') else None
                out.append({
                    'id': n['id'].split('/')[-1],
                    'title': n['title'],
                    'handle': n['handle'],
                    'upgraded': bool(meta_val),
                })
        if d['data']['articles']['pageInfo']['hasNextPage'] and edges:
            cursor = edges[-1]['cursor']
        else:
            break
    return out


def shopify_set_upgraded_metafield(env, article_id, value):
    """Mark article as upgraded via metafield custom.upgraded_v2."""
    store = env["SHOPIFY_STORE_URL"]
    token = env["SHOPIFY_ADMIN_TOKEN"]
    url = f"https://{store}/admin/api/2025-01/articles/{article_id}/metafields.json"
    payload = {"metafield": {
        "namespace": "custom",
        "key": "upgraded_v2",
        "type": "single_line_text_field",
        "value": value
    }}
    req = urllib.request.Request(url, method="POST",
        data=json.dumps(payload).encode(),
        headers={"X-Shopify-Access-Token": token, "Content-Type":"application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        # If already exists, try PUT update (need metafield id first)
        if e.code == 422:
            return None
        raise


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


def _guess_collection(title, env):
    """Use Claude to pick the best-matching collection for an article title."""
    try:
        cols = load_yaml(CONFIG_DIR / "collections.yaml")
    except Exception:
        return None
    options = "\n".join(f"- {h}: {info.get('title','')}" for h, info in cols.items())
    prompt = (
        f"Article title: \"{title}\"\n\n"
        f"Available collections:\n{options}\n\n"
        "Pick the SINGLE best-matching collection for this article's CTA. "
        "Reply with ONLY the handle (e.g. 'tea_gift_sets_samplers'), nothing else."
    )
    try:
        raw = _claude_call(api_key=env["ANTHROPIC_API_KEY"], model=env["ANTHROPIC_MODEL"],
                           system="You match blog articles to the best Shopify collection. Reply with only the handle.",
                           messages=[{"role":"user","content":prompt}], max_tokens=50, temperature=0)
        handle = raw.strip().split()[0].strip().strip("\"'`")
        if handle in cols:
            return cols[handle]
    except Exception as e:
        log(f"  collection guess fail: {e}", "WARN")
    # fallback: first collection
    return list(cols.values())[0] if cols else None


def regenerate_article(article_info, env, before_min, before_article):
    """Full regeneration fallback: rewrite article from scratch + new images.
    Keeps handle (URL) + scheduled date. Score guard: only replace if better."""
    aid = article_info["id"]
    title = before_article.get("title","")
    log(f"  🔄 FULL REGENERATION: {title[:50]}")
    
    # Guess collection for CTA
    cta = _guess_collection(title, env)
    if not cta:
        log("  no collection — skip regen", "WARN")
        return None
    
    # Determine post type from title
    t = title.lower()
    post_type = "hub" if "hub" in t else ("quickfix" if ("quick fix" in t or t.endswith("?")) else "longtail")
    
    # Generate fresh article (multi-pass + 5 dim + Gemini inside generate_full_article)
    import datetime as _dt2
    today = _dt2.datetime.utcnow().strftime("%Y-%m-%d")
    new_article = generate_full_article(topic=title, date=today, post_type=post_type,
                                         subtype=None, cta=cta)
    new_min = combined_min_score(new_article)
    log(f"  Regenerated article min: {new_min}/10 (was {before_min})")
    
    if new_min <= before_min:
        log(f"  ⏸ regen no better ({new_min} <= {before_min}) — keep original", "WARN")
        return None
    
    # Generate images
    google_key = env["GOOGLE_API_KEY"]
    imagen_model = env.get("IMAGEN_MODEL", "imagen-4.0-generate-001")
    variants = int(env.get("IMAGE_VARIANTS_PER_SLOT", "1"))
    generated = []
    for img in new_article.get("images", []):
        try:
            r = generate_image_for_slot(prompt=img["prompt"], filename_base=img["filename"],
                                         api_key=google_key, model=imagen_model, variants=variants,
                                         aspect_ratio="16:9", anthropic_key=env["ANTHROPIC_API_KEY"],
                                         max_vision_retries=2)
            generated.append({**img, **r})
        except Exception as e:
            log(f"  image gen fail ({img.get('filename')}): {e}", "WARN")
    
    featured = next((g for g in generated if g.get("role") == "featured"), None)
    body_imgs = [g for g in generated if g.get("role") == "body"]
    
    feat_url = None
    if featured:
        feat_url = upload_image(env, webp_bytes=featured["webp_bytes"],
                                 filename=featured["filename"], alt=featured["alt"])
    body_uploaded = []
    for bi in body_imgs:
        url = upload_image(env, webp_bytes=bi["webp_bytes"], filename=bi["filename"], alt=bi["alt"])
        body_uploaded.append({"url": url, "alt": bi["alt"], "filename": bi["filename"]})
    
    body_with_imgs = insert_body_images(new_article["body_html"], body_uploaded)
    body_with_imgs = _apply_paragraph_spacing(body_with_imgs)
    
    # Update existing article (keep handle/date) via REST
    store = env["SHOPIFY_STORE_URL"]; token = env["SHOPIFY_ADMIN_TOKEN"]
    payload = {"article": {"id": int(aid), "body_html": body_with_imgs}}
    if feat_url:
        payload["article"]["image"] = {"src": feat_url, "alt": featured["alt"]}
    req = urllib.request.Request(f"https://{store}/admin/api/2025-01/articles/{aid}.json",
        method="PUT", data=json.dumps(payload).encode(),
        headers={"X-Shopify-Access-Token": token, "Content-Type":"application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        json.loads(r.read())
    log(f"  ✅ regenerated + updated (min {before_min} → {new_min})")
    return {"id": aid, "title": title[:50], "status": "regenerated",
            "before": before_min, "after": new_min,
            "anthropic": new_article.get("internal_judgment",{}),
            "gemini": new_article.get("internal_judgment",{}).get("gemini_review")}


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
    
    # Upgrade with perfection_pass (Score Guard: never downgrade — keep original if perfection lowers score)
    original_body = article["body_html"]
    best_article = article  # start from original
    best_min = before_min
    
    try:
        for attempt in (1, 2):
            try:
                cand = perfection_pass(best_article, env)
            except Exception as e:
                log(f"  perfection attempt {attempt} fail: {e}", "WARN")
                break
            # Re-validate with Gemini
            try:
                gem_cand = gemini_review(cand, env)
                if gem_cand:
                    cand = merge_gemini_into_judgment(cand, gem_cand)
            except Exception:
                pass
            cand_min = combined_min_score(cand)
            log(f"  Perfection attempt {attempt}: {best_min} → {cand_min}")
            
            # SCORE GUARD: only adopt if cand_min > best_min
            if cand_min > best_min:
                best_article = cand
                best_min = cand_min
                log(f"  ✅ adopted (score improved)")
                if best_min >= 10:
                    break
            else:
                log(f"  ⏸ rejected (no improvement: {cand_min} <= {best_min})")
                break  # if first attempt doesn't improve, second likely won't either
        
        after_min = best_min
        
        # Decide outcome
        if best_min == before_min and before_min < 10:
            # Perfection couldn't improve — try FULL REGENERATION fallback
            log(f"  perfection stuck at {before_min} — attempting full regeneration")
            try:
                regen_result = regenerate_article(article_info, env, before_min, article)
                if regen_result:
                    return regen_result
            except Exception as e:
                log(f"  regeneration failed: {e}", "WARN")
                import traceback; traceback.print_exc()
            # Regen didn't help either — keep original
            return {"id": aid, "title": full.get("title","")[:50], "status": "no_improvement",
                    "before": before_min, "after": before_min,
                    "anthropic": article.get("internal_judgment",{}),
                    "gemini": article.get("internal_judgment",{}).get("gemini_review")}
        if best_min == before_min:
            return {"id": aid, "title": full.get("title","")[:50], "status": "already_max",
                    "before": before_min, "after": before_min}
        
        if best_min < before_min:
            # Should not happen (we never adopt lower) — defensive
            return {"id": aid, "title": full.get("title","")[:50], "status": "kept_original_guard",
                    "before": before_min, "after": before_min,
                    "anthropic": article.get("internal_judgment",{}),
                    "gemini": article.get("internal_judgment",{}).get("gemini_review")}
        
        # Score improved — update Shopify
        new_body = best_article.get("body_html","")
        if new_body and new_body != original_body:
            from shopify_pub import _apply_paragraph_spacing
            new_body = _apply_paragraph_spacing(new_body)
            shopify_update_body(env, aid, new_body)
            return {"id": aid, "title": full.get("title","")[:50], "status": "upgraded",
                    "before": before_min, "after": after_min,
                    "anthropic": best_article.get("internal_judgment",{}),
                    "gemini": best_article.get("internal_judgment",{}).get("gemini_review")}
        else:
            return {"id": aid, "title": full.get("title","")[:50], "status": "no_body_change",
                    "before": before_min, "after": after_min}
    except Exception as e:
        log(f"  perfection error: {e}", "WARN")
        traceback.print_exc()
        return {"id": aid, "title": full.get("title","")[:50], "status": "perfection_error", "error": str(e)[:200]}


STATE_DIR = Path(__file__).resolve().parent.parent / "state"


def load_cursor(blog_handle):
    STATE_DIR.mkdir(exist_ok=True)
    p = STATE_DIR / f"upgrade-cursor-{blog_handle}.json"
    if p.exists():
        try: return json.loads(p.read_text())
        except: pass
    return {"processed_ids": [], "started_at": _dt.datetime.utcnow().isoformat()}


def save_cursor(blog_handle, cursor):
    STATE_DIR.mkdir(exist_ok=True)
    p = STATE_DIR / f"upgrade-cursor-{blog_handle}.json"
    p.write_text(json.dumps(cursor, ensure_ascii=False, indent=2))


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
    
    # Cursor file persists across runs via repo commit (workflow yml commits state/ folder)
    cursor = load_cursor(args.blog_handle)
    processed_set = set(cursor.get("processed_ids", []))
    log(f"Cursor loaded: {len(processed_set)} articles already processed")
    
    # Fetch all articles in blog
    log("Fetching all articles in blog...")
    all_arts = shopify_fetch_all_articles(env, args.blog_handle)
    log(f"Total in blog: {len(all_arts)} articles")
    
    remaining = [a for a in all_arts if a["id"] not in processed_set]
    log(f"Remaining: {len(remaining)}")
    
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
        # Save cursor after each article (committed to repo by workflow)
        cursor["processed_ids"] = sorted(processed_set)
        save_cursor(args.blog_handle, cursor)
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
    
    # Write remaining count to a file for workflow self-trigger
    remain_count = len(all_arts) - len(processed_set)
    (OUTPUT_DIR / "_remaining.txt").write_text(str(remain_count))
    log(f"Remaining for self-trigger: {remain_count}")


if __name__ == "__main__":
    main()
