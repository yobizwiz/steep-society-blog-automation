#!/usr/bin/env python3
"""Repair articles whose body images were dropped by the upgrade bug.

Symptom: body_html still contains <!-- IMG:body-N --> placeholders but has 0 <img> tags.
This happened because the review/upgrade "upgraded" path wrote the perfection body
(which re-emits placeholders) WITHOUT running insert_body_images.

This script:
  1. Lists all articles, finds the broken ones (placeholders present, 0 <img>).
  2. For each, generates body image SPECS (prompt/filename/alt) from the article
     text, following the repo's own system_prompt image tone rules (Steep = warm
     editorial, SERA = bright airy). NO people, still-life/product/scene only.
  3. Generates the images (Imagen) + vision-verifies them, uploads to Shopify,
     and inserts them into the existing placeholders. Body TEXT is untouched.
  4. Featured image is left as-is (it was never lost).

Idempotent: re-running only processes articles that are still broken. Use --limit
to batch under the Actions timeout; re-run until "remaining broken = 0".
"""
from __future__ import annotations
import argparse, json, re, sys, time, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import load_env, load_system_prompt, log
from images import generate_image_for_slot
from shopify_pub import upload_image, insert_body_images
from content import _claude_call, _extract_json

API = "2024-10"


def shop_req(env, path, method="GET", payload=None):
    store = env["SHOPIFY_STORE_URL"].replace("https://", "").replace("http://", "").rstrip("/")
    url = f"https://{store}/admin/api/{API}/{path}"
    data = json.dumps(payload).encode() if payload else None
    req = urllib.request.Request(url, method=method, data=data,
        headers={"X-Shopify-Access-Token": env["SHOPIFY_ADMIN_TOKEN"],
                 "Accept": "application/json", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read()), r.headers.get("Link", "")


def get_blog_id(env):
    data, _ = shop_req(env, "blogs.json")
    handle = env.get("SHOPIFY_BLOG_HANDLE", "")
    for b in data["blogs"]:
        if not handle or b["handle"] == handle:
            return b["id"]
    return data["blogs"][0]["id"]


def fetch_all(env, blog_id):
    arts = []
    path = f"blogs/{blog_id}/articles.json?limit=250&fields=id,title,handle,body_html,published_at"
    while path:
        data, link = shop_req(env, path)
        arts += data["articles"]
        m = re.search(r'page_info=([^&>]+)>;\s*rel="next"', link) if 'rel="next"' in link else None
        path = f"blogs/{blog_id}/articles.json?limit=250&page_info={m.group(1)}" if m else None
    return arts


def n_img(h):
    return len(re.findall(r"<img\b", h or "", re.I))


def placeholders(h):
    """Return ordered list of placeholder indices present, e.g. [1,2] or [1,2,3]."""
    nums = [int(m) for m in re.findall(r"<!--\s*IMG:body-(\d+)\s*-->", h or "", re.I)]
    return sorted(set(nums))


def _strip_html(h):
    t = re.sub(r"<script[\s\S]*?</script>", " ", h or "", flags=re.I)
    t = re.sub(r"<style[\s\S]*?</style>", " ", t, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"&[a-z]+;", " ", t)
    return re.sub(r"\s+", " ", t).strip()


SPEC_INSTRUCTION = """You are generating BODY IMAGE specifications for an existing,
already-written blog article. The article body has {n} image placeholder slot(s) that
need images. Produce EXACTLY {n} image spec(s), one per slot, in reading order.

Vary BOTH the SCENE/SETTING and the camera ANGLE across the {n} images so the article reads like a real lifestyle blog, NOT repetitive product shots and NOT all tight macro close-ups. Use varied lived-in home settings such as: a kitchen dining table beside a window with softly blurred garden/outdoor scenery (bokeh), a cozy living-room side table, a sunlit windowsill, a warm wooden counter with a blurred interior background — each with shallow depth of field and a soft out-of-focus background for a sense of place. STRONGLY PREFER non-aerial angles — eye-level three-quarter and 45-degree side angles are the default. Use top-down/overhead flat-lay for AT MOST 1 image per article (prefer 0). NEVER make all or most images top-down. Top-down is only acceptable when the subject is genuinely a flat ingredient layout that would be hard to read otherwise. Keep NO people. State the scene and the camera angle at the start of each prompt. 
Follow the image tone/style rules from the system prompt above EXACTLY (brand tone,
still-life / product / scene only). ABSOLUTE RULE: no people or body parts of any kind
(no person, hand, finger, face, mom, family, etc.) — describe objects, food/tea, props,
surfaces, light, settings only. Each prompt must visually MATCH the section of the
article it sits in, so the photo feels connected to the surrounding text.

Return ONE JSON object, nothing else:
{{
  "images": [
    {{"prompt": "detailed image prompt (objects/scene only)", "filename": "lowercase-hyphens", "alt": "descriptive alt text"}}
  ]
}}
Exactly {n} item(s) in "images"."""


def make_specs(title, body_text, n, env):
    sys_prompt = load_system_prompt()
    full_system = sys_prompt + "\n\n" + SPEC_INSTRUCTION.format(n=n)
    user = (
        f"Article title: {title}\n\n"
        f"Article body (plain text, in order):\n{body_text[:6000]}\n\n"
        f"Produce exactly {n} body image spec(s) matching the sections of this article."
    )
    raw = _claude_call(
        api_key=env["ANTHROPIC_API_KEY"],
        model=env.get("ANTHROPIC_REVIEW_MODEL", env["ANTHROPIC_MODEL"]),
        system=full_system,
        messages=[{"role": "user", "content": user}],
        max_tokens=1500, temperature=0.5,
    )
    obj = _extract_json(raw)
    imgs = obj.get("images", [])
    if len(imgs) < n:
        raise RuntimeError(f"spec count {len(imgs)} < needed {n}")
    return imgs[:n]


def fill_body_placeholders(env, body_html, title):
    """Given a body_html that still contains <!-- IMG:body-N --> placeholders and 0
    real images, generate matching images and return the filled body_html.

    Raises if the result would still contain unfilled placeholders (caller must NOT
    write a body that still has placeholders). Reusable by both the repair script and
    the review/upgrade pipeline so the image-drop bug can never recur.
    """
    ph = placeholders(body_html)
    n = len(ph)
    if n == 0:
        return body_html  # nothing to fill

    body_text = _strip_html(body_html)
    specs = make_specs(title, body_text, n, env)

    google_key = env["GOOGLE_API_KEY"]
    imagen_model = env.get("IMAGEN_MODEL", "imagen-4.0-generate-001")
    variants = int(env.get("IMAGE_VARIANTS_PER_SLOT", "1"))

    body_uploaded = []
    for spec in specs:
        r = generate_image_for_slot(
            prompt=spec["prompt"], filename_base=spec["filename"],
            api_key=google_key, model=imagen_model, variants=variants,
            aspect_ratio="16:9", anthropic_key=env["ANTHROPIC_API_KEY"],
            max_vision_retries=2)
        url = upload_image(env, webp_bytes=r["webp_bytes"], filename=r["filename"], alt=spec["alt"])
        body_uploaded.append({"url": url, "alt": spec["alt"], "filename": r["filename"]})

    new_body = insert_body_images(body_html, body_uploaded)
    if re.search(r"<!--\s*IMG:body-\d+\s*-->", new_body, re.I):
        raise RuntimeError(f"placeholders remain after fill ({n_img(new_body)}/{n} filled)")
    if n_img(new_body) < n:
        raise RuntimeError(f"image count low after fill ({n_img(new_body)}/{n})")
    return new_body


def repair_one(env, art):
    aid = art["id"]
    title = art.get("title", "")
    body = art.get("body_html", "")
    n = len(placeholders(body))
    if n == 0:
        return {"id": aid, "title": title[:50], "status": "no_placeholder_skip"}

    log(f"repair: {title[:50]} (slots={n})")
    new_body = fill_body_placeholders(env, body, title)

    shop_req(env, f"blogs/{art['__blog_id']}/articles/{aid}.json", method="PUT",
             payload={"article": {"id": int(aid), "body_html": new_body}})
    return {"id": aid, "title": title[:50], "status": "repaired", "images_added": n}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20, help="max articles to repair this run")
    ap.add_argument("--dry-run", action="store_true", help="only report broken articles")
    args = ap.parse_args()

    env = load_env()
    blog_id = get_blog_id(env)
    arts = fetch_all(env, blog_id)
    for a in arts:
        a["__blog_id"] = blog_id

    broken = [a for a in arts if n_img(a.get("body_html")) == 0 and placeholders(a.get("body_html"))]
    log(f"전체 {len(arts)}편 / 깨진(본문0+placeholder) {len(broken)}편")

    if args.dry_run:
        for a in sorted(broken, key=lambda x: (x.get("published_at") or "")):
            log(f"  BROKEN {a.get('published_at','')[:10]} slots={len(placeholders(a['body_html']))} {a.get('title','')[:45]}")
        print(json.dumps({"total": len(arts), "broken": len(broken)}, ensure_ascii=False))
        return

    todo = sorted(broken, key=lambda x: (x.get("published_at") or ""))[:args.limit]
    results = []
    quota_done = False
    for a in todo:
        try:
            res = repair_one(env, a)
        except Exception as e:
            if "DAILY_QUOTA_EXHAUSTED" in str(e):
                log("일일 Imagen 할당량 소진 — 이번 실행 종료(다음 리셋 후 자동 재개)", "WARN")
                results.append({"id": a["id"], "title": a.get("title", "")[:50], "status": "quota_exhausted"})
                quota_done = True
                break
            import traceback; traceback.print_exc()
            res = {"id": a["id"], "title": a.get("title", "")[:50], "status": "error", "error": str(e)[:200]}
        log(f"  -> {res['status']}")
        results.append(res)

    repaired = sum(1 for r in results if r["status"] == "repaired")
    remaining = len(broken) - repaired
    log(f"\n=== 이번 실행: {repaired}편 복구 / 남은 깨진 글 약 {remaining}편 ===")
    print(json.dumps({"processed": len(results), "repaired": repaired,
                      "remaining": remaining, "results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
