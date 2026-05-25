#!/usr/bin/env python3
"""Re-vary body images on OLD articles that were repaired with monotonous (flat-lay only) shots.

These articles already HAVE body images (so no downtime), but every shot is a top-down macro.
This regenerates them with varied scenes + angles (lifestyle settings, bokeh background) using
the updated make_specs rules, then swaps them in atomically — the old image stays live until the
new one is uploaded and the body is PUT in one update.

Target: articles created before 2026-05-12 (the upgrade-damaged "old" set) that currently have
body <img> tags and are NOT yet marked done. Idempotent via an HTML marker comment.
Quota-aware: stops the run cleanly when the daily Imagen quota is exhausted.
"""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import load_env, log
from images import generate_image_for_slot
from shopify_pub import upload_image
from repair_images import shop_req, get_blog_id, fetch_all, make_specs, _strip_html, n_img

MARKER = "<!-- revaried-v2 -->"
CUTOFF = "2026-05-12"  # articles created before this were the upgrade-damaged old set
IMG_BLOCK = re.compile(r'<p[^>]*>\s*<img\b[^>]*?>\s*</p>', re.I)


def _esc(s):
    return (s or "").replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def _img_tag(url, alt):
    return (f'<p style="margin: 28px 0;"><img src="{url}" alt="{_esc(alt)}" '
            f'loading="lazy" style="width: 100%; height: auto; border-radius: 12px;" /></p>')


def revary_one(env, art):
    aid = art["id"]
    title = art.get("title", "")
    body = art.get("body_html", "")
    blocks = IMG_BLOCK.findall(body)
    n = len(blocks)
    if n == 0:
        return {"id": aid, "title": title[:50], "status": "no_body_img_skip"}

    log(f"revary: {title[:50]} (imgs={n})")
    specs = make_specs(title, _strip_html(body), n, env)

    google_key = env["GOOGLE_API_KEY"]
    imagen_model = env.get("IMAGEN_MODEL", "imagen-4.0-generate-001")
    variants = int(env.get("IMAGE_VARIANTS_PER_SLOT", "1"))

    new_tags = []
    for spec in specs:
        r = generate_image_for_slot(
            prompt=spec["prompt"], filename_base=spec["filename"],
            api_key=google_key, model=imagen_model, variants=variants,
            aspect_ratio="16:9", anthropic_key=env["ANTHROPIC_API_KEY"],
            max_vision_retries=2)
        url = upload_image(env, webp_bytes=r["webp_bytes"], filename=r["filename"], alt=spec["alt"])
        new_tags.append(_img_tag(url, spec["alt"]))

    # Replace the existing image blocks in order with the new ones
    idx = {"i": 0}
    def _repl(m):
        i = idx["i"]
        idx["i"] += 1
        return new_tags[i] if i < len(new_tags) else m.group(0)
    new_body = IMG_BLOCK.sub(_repl, body, count=n)

    # Guard: image count must be preserved
    if n_img(new_body) < n:
        return {"id": aid, "title": title[:50], "status": "guard_img_count_low"}
    if MARKER not in new_body:
        new_body = new_body + "\n" + MARKER

    shop_req(env, f"blogs/{art['__blog_id']}/articles/{aid}.json", method="PUT",
             payload={"article": {"id": int(aid), "body_html": new_body}})
    return {"id": aid, "title": title[:50], "status": "revaried", "images": n}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    env = load_env()
    blog_id = get_blog_id(env)
    arts = fetch_all(env, blog_id)
    for a in arts:
        a["__blog_id"] = blog_id

    targets = [a for a in arts
               if (a.get("created_at") or "")[:10] < CUTOFF
               and n_img(a.get("body_html")) >= 1
               and MARKER not in (a.get("body_html") or "")]
    log(f"전체 {len(arts)}편 / 재컨셉 대상(옛날글, 사진있음, 미처리) {len(targets)}편")

    if args.dry_run:
        print(json.dumps({"total": len(arts), "to_revary": len(targets)}, ensure_ascii=False))
        return

    todo = sorted(targets, key=lambda x: (x.get("created_at") or ""))[:args.limit]
    results = []
    for a in todo:
        try:
            res = revary_one(env, a)
        except Exception as e:
            if "DAILY_QUOTA_EXHAUSTED" in str(e):
                log("일일 Imagen 할당량 소진 — 이번 실행 종료(다음 리셋 후 자동 재개)", "WARN")
                results.append({"id": a["id"], "title": a.get("title", "")[:50], "status": "quota_exhausted"})
                break
            import traceback; traceback.print_exc()
            res = {"id": a["id"], "title": a.get("title", "")[:50], "status": "error", "error": str(e)[:200]}
        log(f"  -> {res['status']}")
        results.append(res)

    done = sum(1 for r in results if r["status"] == "revaried")
    remaining = len(targets) - done
    log(f"\n=== 이번 실행: {done}편 재컨셉 / 남은 {remaining}편 ===")
    print(json.dumps({"processed": len(results), "revaried": done, "remaining": remaining}, ensure_ascii=False))


if __name__ == "__main__":
    main()
