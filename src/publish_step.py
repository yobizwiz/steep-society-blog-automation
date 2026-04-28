"""Step-by-step publisher for scheduled article publishing.

Usage:
  python src/publish_step.py --date 2026-04-30 --step img-N --index N
  python src/publish_step.py --date 2026-04-30 --step article --scheduled-utc 2026-04-30T07:00:00Z
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils import OUTPUT_DIR, load_env, log
from images import generate_image_for_slot
from shopify_pub import (
    admin_url, create_article, get_blog_id,
    insert_body_images, public_url, upload_image,
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--step", required=True, choices=["img", "article"])
    ap.add_argument("--index", type=int, default=0,
                    help="image index 0=featured, 1=body-1, 2=body-2")
    ap.add_argument("--scheduled-utc", default=None,
                    help="ISO 8601 UTC publish time, e.g. 2026-04-30T07:00:00Z")
    ap.add_argument("--variants", type=int, default=1,
                    help="image variants per slot (1=fastest)")
    args = ap.parse_args()

    env = load_env()
    date = args.date

    article_path = OUTPUT_DIR / (date + "-article.json")
    article = json.loads(article_path.read_text(encoding="utf-8"))
    images = article.get("images", [])

    if args.step == "img":
        idx = args.index
        if idx >= len(images):
            log("invalid index", "ERROR")
            sys.exit(1)
        im = images[idx]
        log("generating image " + str(idx) + ": " + im.get("filename", "?"))
        result = generate_image_for_slot(
            prompt=im["prompt"],
            filename_base=im["filename"],
            api_key=env["GOOGLE_API_KEY"],
            model=env.get("IMAGEN_MODEL", "imagen-3.0-generate-002"),
            variants=args.variants,
            aspect_ratio="16:9",
        )
        # save webp
        webp_path = OUTPUT_DIR / (date + "-img-" + str(idx) + ".webp")
        webp_path.write_bytes(result["webp_bytes"])
        log("saved webp: " + str(webp_path) + " (" + str(len(result["webp_bytes"])) + "B)")

        # upload to Shopify
        url = upload_image(env, webp_bytes=result["webp_bytes"],
                           filename=result["filename"], alt=im["alt"])
        # save url state
        state_path = OUTPUT_DIR / (date + "-img-urls.json")
        state = {}
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
        state[str(idx)] = {"url": url, "alt": im["alt"], "filename": result["filename"], "role": im.get("role")}
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        log("uploaded -> " + url)

    elif args.step == "article":
        state_path = OUTPUT_DIR / (date + "-img-urls.json")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        # ensure all 3
        if len(state) < len(images):
            log("missing image uploads (" + str(len(state)) + "/" + str(len(images)) + ")", "ERROR")
            sys.exit(1)

        featured = None
        body_uploaded = []
        for i, im in enumerate(images):
            up = state[str(i)]
            if im.get("role") == "featured":
                featured = up
            else:
                body_uploaded.append({"url": up["url"], "alt": up["alt"], "filename": up["filename"]})

        body_html = insert_body_images(article["body_html"], body_uploaded)

        blog_id = get_blog_id(env, env["SHOPIFY_BLOG_HANDLE"])
        publish_mode = "scheduled" if args.scheduled_utc else "draft"
        created = create_article(
            env, blog_id=blog_id, article=article,
            featured_image_url=featured["url"], featured_image_alt=featured["alt"],
            body_html=body_html, publish_mode=publish_mode,
            scheduled_at=args.scheduled_utc,
        )
        log("admin url: " + admin_url(env, created["id"]))
        log("public url (after publish): " + public_url(created.get("handle", "")))

        result_path = OUTPUT_DIR / (date + "-publish-result.json")
        result_path.write_text(json.dumps({
            "article_id": created["id"],
            "handle": created.get("handle"),
            "scheduled_at": args.scheduled_utc,
            "admin_url": admin_url(env, created["id"]),
            "public_url": public_url(created.get("handle", "")),
        }, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
