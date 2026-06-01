#!/usr/bin/env python3
"""One-off: regenerate ONLY specific body-image slots that contain AI text
artifacts (fake brand text / pseudo-labels on tea packaging), replacing each
flagged slot with a fresh natural image whose prompt explicitly forbids any
on-product text. Other images in the article are left completely untouched.

Targeted by article handle + 1-based slot index (reading order).
Run manually via the refine-images workflow (workflow_dispatch).
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import load_env, log
from images import generate_image_for_slot
from shopify_pub import upload_image, _cdn_with_width
from repair_images import shop_req, get_blog_id, fetch_all, make_specs, _strip_html, n_img

IMG_BLOCK = re.compile(r'<(p|div)[^>]*>\s*<img\b[^>]*?>\s*</\1>', re.I)

NOTEXT = (" ABSOLUTELY NO visible text, letters, words, numbers, labels, logos, brand "
          "names, or printed packaging copy anywhere in the image. Every tea pouch, "
          "sachet, tin, canister, jar, gift box and package must be completely unbranded "
          "with blank, plain, label-free surfaces and no writing of any kind.")

# handle -> 1-based slot indices (reading order) to regenerate (front-facing label packaging)
TARGETS = {
    "unlock-the-art-of-tea-tasting-a-guide-to-describing-tea-like-a-sommelier": [2],
    "how-to-choose-a-sampler-pack-fast": [1],
    "how-to-pick-a-tea-gift-without-guessing": [1],
    "mothers-day-tea-gift-guide": [1],
    "best-tea-sampler-gift-ideas": [1],
    "last-minute-tea-gifts-mothers-day": [1],
    "mothers-day-tea-gift-hub": [1, 3],
}


def _esc(s):
    return (s or "").replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def _img_tag(url, alt):
    a = _esc(alt)
    srcset = (f"{_cdn_with_width(url,800)} 800w, "
              f"{_cdn_with_width(url,1200)} 1200w, "
              f"{_cdn_with_width(url,1600)} 1600w")
    sizes = "(max-width: 700px) 800px, (max-width: 1100px) 1200px, 1600px"
    return (
        f'<p style="margin: 28px 0;">'
        f'<img src="{url}" alt="{a}" title="{a}" '
        f'width="1600" height="900" '
        f'loading="lazy" decoding="async" '
        f'srcset="{srcset}" sizes="{sizes}" '
        f'style="width: 100%; height: auto; border-radius: 12px;" />'
        f'</p>'
    )


def refine_one(env, art, slots):
    aid = art["id"]
    title = art.get("title", "")
    body = art.get("body_html", "")
    spans = [m.span() for m in IMG_BLOCK.finditer(body)]
    n = len(spans)
    if n == 0:
        return {"handle": art.get("handle"), "status": "no_img_block"}
    bad = [s for s in slots if 1 <= s <= n]
    if not bad:
        return {"handle": art.get("handle"), "status": f"slots_out_of_range(n={n})"}

    specs = make_specs(title, _strip_html(body), n, env)
    google_key = env["GOOGLE_API_KEY"]
    imagen_model = env.get("IMAGEN_MODEL", "imagen-4.0-generate-001")
    variants = int(env.get("IMAGE_VARIANTS_PER_SLOT", "1"))

    new_tags = {}
    for s in bad:
        spec = specs[s - 1]
        log(f"refine: {title[:45]} slot#{s} ({spec.get('filename','')})")
        r = generate_image_for_slot(
            prompt=spec["prompt"] + NOTEXT, filename_base=spec["filename"],
            api_key=google_key, model=imagen_model, variants=variants,
            aspect_ratio="16:9", anthropic_key=env["ANTHROPIC_API_KEY"],
            max_vision_retries=2)
        url = upload_image(env, webp_bytes=r["webp_bytes"], filename=r["filename"], alt=spec["alt"])
        new_tags[s] = _img_tag(url, spec["alt"])

    new_body = body
    for s in sorted(bad, reverse=True):
        st, en = spans[s - 1]
        new_body = new_body[:st] + new_tags[s] + new_body[en:]

    if n_img(new_body) != n:
        return {"handle": art.get("handle"), "status": f"guard_img_count({n_img(new_body)}!={n})"}

    shop_req(env, f"blogs/{art['__blog_id']}/articles/{aid}.json", method="PUT",
             payload={"article": {"id": int(aid), "body_html": new_body}})
    return {"handle": art.get("handle"), "status": "refined", "slots": bad, "images": n}


def main():
    env = load_env()
    blog_id = get_blog_id(env)
    arts = fetch_all(env, blog_id)
    for a in arts:
        a["__blog_id"] = blog_id
    by_handle = {a.get("handle"): a for a in arts}

    results = []
    for handle, slots in TARGETS.items():
        art = by_handle.get(handle)
        if not art:
            log(f"  {handle} -> not_found")
            results.append({"handle": handle, "status": "not_found"})
            continue
        try:
            res = refine_one(env, art, slots)
        except Exception as e:
            import traceback
            traceback.print_exc()
            res = {"handle": handle, "status": "error", "error": str(e)[:200]}
        log(f"  {handle} -> {res['status']}")
        results.append(res)

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
