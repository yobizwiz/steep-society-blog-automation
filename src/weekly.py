#!/usr/bin/env python3
"""Weekly batch runner."""
from __future__ import annotations
import argparse, datetime as _dt, json, sys, traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils import CONFIG_DIR, OUTPUT_DIR, ensure_dirs, load_env, load_yaml, log
from content import generate_full_article
from validators import validate
from images import generate_image_for_slot
from shopify_pub import (admin_url, create_article, find_article_by_publish_date,
                          get_blog_id, insert_body_images, public_url, upload_image)


def process_one_day(date, env, sched, cols):
    log("=" * 60)
    log(f"=== Processing {date} ===")
    log("=" * 60)
    summary = {"date": date, "status": "pending", "error": None}
    if date not in sched:
        summary["status"] = "skipped"
        summary["error"] = f"No schedule entry for {date}"
        log(f"SKIP: {summary['error']}", "WARN")
        return summary

    # Skip if Shopify already has an article scheduled for this date (duplicate prevention)
    try:
        existing = find_article_by_publish_date(env, date)
    except Exception as e:
        log(f"  Shopify 중복 체크 실패 (계속 진행): {e}", "WARN")
        existing = None
    if existing:
        summary["status"] = "skipped"
        summary["error"] = f"이미 Shopify에 발행 예약된 글 있음: {existing['handle']}"
        summary["title"] = sched[date]["title"]
        summary["handle"] = existing["handle"]
        summary["article_id"] = existing["id"].split("/")[-1] if isinstance(existing["id"], str) else existing["id"]
        log(f"⏭ {date}: SKIP (이미 존재) — {existing['handle']}", "WARN")
        return summary

    entry = sched[date]
    cta = cols[entry["cta_collection"]]
    summary["title"] = entry["title"]
    summary["type"] = entry.get("type", "longtail")

    try:
        article_path = OUTPUT_DIR / f"{date}-article.json"
        if article_path.exists():
            log(f"Reusing existing {article_path.name}")
            article = json.loads(article_path.read_text(encoding="utf-8"))
        else:
            article = generate_full_article(
                topic=entry["title"], date=date,
                post_type=entry.get("type", "longtail"),
                subtype=entry.get("subtype"), cta=cta,
            )
            article_path.write_text(json.dumps(article, ensure_ascii=False, indent=2), encoding="utf-8")

        result = validate(article, post_type=entry.get("type", "longtail"))
        summary["validation"] = {"violations": len(result["violations"]), "warnings": len(result["warnings"])}
        summary["score"] = (article.get("internal_judgment") or {}).get("content_quality", {}).get("score")

        log(f"\n--- {date}: 이미지 생성 ---")
        google_key = env["GOOGLE_API_KEY"]
        imagen_model = env.get("IMAGEN_MODEL", "imagen-4.0-generate-001")
        variants = int(env.get("IMAGE_VARIANTS_PER_SLOT", "1"))

        generated = []
        for img in article.get("images", []):
            r = generate_image_for_slot(
                prompt=img["prompt"], filename_base=img["filename"],
                api_key=google_key, model=imagen_model,
                variants=variants, aspect_ratio="16:9",
                anthropic_key=env["ANTHROPIC_API_KEY"],
                max_vision_retries=2,
            )
            (OUTPUT_DIR / f"{date}-{img['filename']}.webp").write_bytes(r["webp_bytes"])
            generated.append({**img, **r})

        log(f"\n--- {date}: Shopify 업로드 ---")
        blog_id = get_blog_id(env, env["SHOPIFY_BLOG_HANDLE"])
        featured = next(g for g in generated if g.get("role") == "featured")
        body_imgs = [g for g in generated if g.get("role") == "body"]

        feat_url = upload_image(env, webp_bytes=featured["webp_bytes"],
                                  filename=featured["filename"], alt=featured["alt"])
        body_uploaded = []
        for bi in body_imgs:
            url = upload_image(env, webp_bytes=bi["webp_bytes"], filename=bi["filename"], alt=bi["alt"])
            body_uploaded.append({"url": url, "alt": bi["alt"], "filename": bi["filename"]})

        body_with_imgs = insert_body_images(article["body_html"], body_uploaded)
        scheduled_utc = f"{date}T07:00:00Z"
        created = create_article(
            env, blog_id=blog_id, article=article,
            featured_image_url=feat_url, featured_image_alt=featured["alt"],
            body_html=body_with_imgs, publish_mode="scheduled",
            scheduled_at=scheduled_utc,
        )
        summary["article_id"] = created["id"]
        summary["handle"] = created.get("handle")
        summary["scheduled_at"] = scheduled_utc
        summary["admin_url"] = admin_url(env, created["id"])
        summary["public_url"] = public_url(created.get("handle", ""))
        summary["status"] = "success"
        log(f"\n✅ {date}: SUCCESS — scheduled at {scheduled_utc}")
    except Exception as e:
        summary["status"] = "failed"
        summary["error"] = str(e)[:500]
        log(f"❌ {date}: FAILED — {e}", "ERROR")
        traceback.print_exc()
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", help="Start date YYYY-MM-DD (default: tomorrow)")
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args()

    if args.start:
        start = _dt.date.fromisoformat(args.start)
    else:
        start = _dt.date.today() + _dt.timedelta(days=1)

    ensure_dirs()
    env = load_env()
    sched = load_yaml(CONFIG_DIR / "schedule.yaml")
    cols = load_yaml(CONFIG_DIR / "collections.yaml")

    log("\n" + "#" * 64)
    log(f"# WEEKLY BATCH — {start} 부터 {args.days}일치")
    log("#" * 64 + "\n")

    summaries = []
    for i in range(args.days):
        d = start + _dt.timedelta(days=i)
        s = process_one_day(d.isoformat(), env, sched, cols)
        summaries.append(s)

    log("\n" + "=" * 64)
    log("# WEEKLY BATCH 완료")
    log("=" * 64)
    success = [s for s in summaries if s["status"] == "success"]
    failed = [s for s in summaries if s["status"] == "failed"]
    skipped = [s for s in summaries if s["status"] == "skipped"]
    log(f"성공: {len(success)} / 실패: {len(failed)} / 건너뜀: {len(skipped)}")
    for s in summaries:
        line = f"  {s['date']}: {s['status']}"
        if s.get("title"): line += f" — {s['title'][:50]}"
        if s.get("score"): line += f" (점수 {s['score']}/10)"
        if s.get("error"): line += f" — ERROR: {s['error'][:80]}"
        log(line)

    today_str = _dt.date.today().isoformat()
    report_path = OUTPUT_DIR / f"weekly-batch-{today_str}.json"
    report_data = {"start": start.isoformat(), "days": args.days, "summaries": summaries}
    report_path.write_text(json.dumps(report_data, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"\n리포트: {report_path}")


if __name__ == "__main__":
    main()
