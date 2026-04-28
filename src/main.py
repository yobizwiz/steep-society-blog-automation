#!/usr/bin/env python3
"""Steep Society 블로그 일일 자동화 — 오케스트레이터.

사용법:
  python src/main.py                    # 오늘 날짜 글 생성·게시
  python src/main.py --date 2026-04-30  # 특정 날짜 글 생성·게시
  python src/main.py --dry-run          # Shopify 게시 없이 글만 생성 (output/에 저장)

흐름:
  1. 스케줄에서 오늘 주제 로드
  2. 컬렉션 매핑에서 CTA 정보 로드
  3. 콘텐츠 멀티패스 생성 (초안→비판→수정→교차검토)
  4. 구조 검증 (위반시 1회 재시도)
  5. 이미지 N장 생성 + WebP 최적화
  6. Shopify Files 업로드 → 본문에 <img> 삽입
  7. 블로그 글 게시 (draft 모드 권장)
  8. 리포트 저장 + 콘솔 출력
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils import CONFIG_DIR, OUTPUT_DIR, ensure_dirs, load_env, load_yaml, log
from content import generate_full_article
from validators import validate
from images import generate_image_for_slot
from shopify_pub import (
    admin_url,
    create_article,
    get_blog_id,
    insert_body_images,
    public_url,
    upload_image,
)


def _write_preview(article: dict, path) -> None:
    """브라우저 검토용 HTML 미리보기 생성. 모든 메타·요약 포함."""
    j = article.get("internal_judgment", {}) or {}
    body = article.get("body_html", "") or ""
    body_imgs = [im for im in article.get("images", []) if im.get("role") == "body"]
    for i, im in enumerate(body_imgs, start=1):
        body = body.replace(
            f"<!-- IMG:body-{i} -->",
            f'<div style="border:2px dashed #ccc;padding:30px;text-align:center;color:#999;font-style:italic;margin:20px 0;background:#fafafa;">'
            f'[Image {i}: {im.get("alt", "")}]<br><small>(prompt: {im.get("prompt", "")[:140]}...)</small></div>'
        )
    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>Preview: {article.get('title', '')}</title>
<style>
body {{ max-width:720px;margin:40px auto;padding:20px;font-family:-apple-system,system-ui,sans-serif;line-height:1.7;color:#1a1a1a; }}
h1 {{ font-size:32px;line-height:1.3; }} h2 {{ font-size:22px;margin-top:32px; }} h3 {{ font-size:18px; }}
table {{ border-collapse:collapse;width:100%;margin:16px 0; }}
th,td {{ border:1px solid #ddd;padding:10px;text-align:left; }} th {{ background:#f5f5f5; }}
.meta-box {{ background:#f9f5ec;padding:16px;margin:24px 0;border-left:4px solid #2b2118;font-size:14px; }}
.meta-box p {{ margin:6px 0; }}
.summary-box {{ background:#eef5ed;padding:14px 18px;margin:18px 0;border-left:4px solid #4a7a4a;font-size:15px;font-style:italic; }}
</style></head><body>
<div class="meta-box">
  <p><strong>Title:</strong> {article.get('title', '')}</p>
  <p><strong>Slug (= Shopify handle):</strong> /{article.get('url_slug', '')}</p>
  <p><strong>Meta Title ({len(article.get('meta_title', ''))}자):</strong> {article.get('meta_title', '')}</p>
  <p><strong>Meta Description ({len(article.get('meta_description', ''))}자):</strong> {article.get('meta_description', '')}</p>
  <p><strong>Summary (Shopify 요약 필드):</strong> {article.get('summary', '')}</p>
  <p><strong>Tags:</strong> {', '.join(article.get('tags', []))}</p>
  <p><strong>Scores:</strong> Content {j.get('content_quality', {}).get('score', '?')}/10 · SEO {j.get('onpage_seo', {}).get('score', '?')}/10 · Conversion {j.get('conversion_alignment', {}).get('score', '?')}/10</p>
</div>
<h1>{article.get('title', '')}</h1>
<div class="summary-box">{article.get('summary', '')}</div>
{body}
</body></html>"""
    path.write_text(html, encoding="utf-8")


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--date", help="발행 날짜 (YYYY-MM-DD), 기본=오늘")
    p.add_argument("--dry-run", action="store_true", help="Shopify 게시 생략, 로컬 저장만")
    p.add_argument("--publish-now", action="store_true", help="즉시 발행 (PUBLISH_MODE 무시)")
    p.add_argument("--scheduled", action="store_true",
                   help="--date에 지정한 날짜에 자동 예약 발행 (기본 시각 09:00 EDT = 13:00 UTC)")
    p.add_argument("--scheduled-time", default="13:00",
                   help="예약 발행 UTC 시각 (HH:MM, 기본 13:00 = 09:00 EDT)")
    p.add_argument("--use-existing", action="store_true",
                   help="기존 output/<date>-article.json 그대로 사용 (콘텐츠 재생성 생략)")
    return p.parse_args()


def load_schedule_entry(date: str) -> dict:
    sched = load_yaml(CONFIG_DIR / "schedule.yaml")
    entry = sched.get(date)
    if not entry:
        raise RuntimeError(f"스케줄에 {date} 항목 없음 — schedule.yaml 확인")
    return entry


def load_collection(key: str) -> dict:
    cols = load_yaml(CONFIG_DIR / "collections.yaml")
    col = cols.get(key)
    if not col:
        raise RuntimeError(f"컬렉션 키 '{key}'를 collections.yaml에서 찾을 수 없음")
    return col


def main():
    args = parse_args()
    ensure_dirs()
    env = load_env()

    date = args.date or _dt.date.today().isoformat()
    log(f"=" * 60)
    log(f"Steep Society 블로그 자동화 시작 — {date}")
    log(f"=" * 60)

    # 1. 스케줄 로드
    entry = load_schedule_entry(date)
    log(f"주제: {entry['title']}")
    log(f"유형: {entry.get('type')}" + (f" / {entry.get('subtype')}" if entry.get("subtype") else ""))

    # 2. CTA 컬렉션
    cta = load_collection(entry["cta_collection"])
    log(f"CTA: {cta['title']} ({cta['url']})")

    # 3. 콘텐츠 생성 (멀티패스) 또는 기존 사용
    existing_path = OUTPUT_DIR / f"{date}-article.json"
    if args.use_existing and existing_path.exists():
        log(f"기존 article 사용: {existing_path}")
        article = json.loads(existing_path.read_text(encoding="utf-8"))
        log(f"  제목: {article.get('title', '?')[:60]}")
    else:
        article = generate_full_article(
            topic=entry["title"],
            date=date,
            post_type=entry.get("type", "longtail"),
            subtype=entry.get("subtype"),
            cta=cta,
        )

    # 4. 검증
    log("\n--- 구조 검증 ---")
    result = validate(article, post_type=entry.get("type", "longtail"))
    if result["violations"]:
        log(f"위반 {len(result['violations'])}건:", "WARN")
        for v in result["violations"]:
            log(f"  - {v['rule']}: {v['detail']}", "WARN")
    if result["warnings"]:
        log(f"경고 {len(result['warnings'])}건:", "WARN")
        for w in result["warnings"]:
            log(f"  - {w['rule']}: {w['detail']}", "WARN")
    if result["ok"]:
        log("검증 통과", "OK")

    # 결과 저장 (검증 결과 포함)
    article_path = OUTPUT_DIR / f"{date}-article.json"
    article_path.write_text(json.dumps(article, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"\n글 JSON 저장: {article_path}")

    # 미리보기 HTML 생성 (브라우저로 검토용)
    preview_path = OUTPUT_DIR / f"{date}-preview.html"
    _write_preview(article, preview_path)
    log(f"미리보기 HTML: {preview_path}")

    if args.dry_run:
        log("\n--dry-run 모드 — Shopify 게시 생략")
        log(f"리포트: {article_path}")
        return

    # 5. 이미지 생성
    log("\n--- 이미지 생성 ---")
    images = article.get("images", [])
    if not images:
        log("이미지 정보 없음 — 게시 중단", "ERROR")
        sys.exit(1)

    google_key = env["GOOGLE_API_KEY"]
    imagen_model = env.get("IMAGEN_MODEL", "imagen-3.0-generate-002")
    variants = int(env.get("IMAGE_VARIANTS_PER_SLOT", "3"))

    generated = []
    for img in images:
        result_img = generate_image_for_slot(
            prompt=img["prompt"],
            filename_base=img["filename"],
            api_key=google_key,
            model=imagen_model,
            variants=vari