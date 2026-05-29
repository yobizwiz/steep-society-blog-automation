#!/usr/bin/env python3
"""Send a daily image-repair progress report via Telegram.

Reads its own shop's Shopify blog, counts how many articles still have empty body
images (placeholder + 0 <img>) versus normal, and posts a concise summary to the
configured Telegram chat. Each shop's repo runs its own instance so secrets stay
scoped per repo.
"""
import json, os, re, sys, urllib.parse, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import load_env, log

API = "2024-10"


def shop_req(env, path):
    store = env["SHOPIFY_STORE_URL"].replace("https://", "").replace("http://", "").rstrip("/")
    req = urllib.request.Request(
        f"https://{store}/admin/api/{API}/{path}",
        headers={"X-Shopify-Access-Token": env["SHOPIFY_ADMIN_TOKEN"], "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
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
    path = f"blogs/{blog_id}/articles.json?limit=250&fields=id,body_html,created_at,updated_at"
    while path:
        data, link = shop_req(env, path)
        arts += data["articles"]
        m = re.search(r'page_info=([^&>]+)>;\s*rel="next"', link) if 'rel="next"' in link else None
        path = f"blogs/{blog_id}/articles.json?limit=250&page_info={m.group(1)}" if m else None
    return arts


def main():
    env = load_env()
    bot = env.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = env.get("TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID")
    shop = env.get("SHOP_NAME") or os.environ.get("SHOP_NAME") or "Shop"
    if not bot or not chat:
        log("[telegram] BOT_TOKEN/CHAT_ID 미설정 — 종료")
        sys.exit(0)

    blog_id = get_blog_id(env)
    arts = fetch_all(env, blog_id)
    n_total = len(arts)
    n_broken = 0
    n_revaried = 0       # 새 톤으로 재컨셉된 글
    n_old_with_img = 0   # 옛날글이면서 사진 있는데 아직 재컨셉 안 된 글
    REVARY_MARKER = "<!-- revaried-v2 -->"
    CUTOFF = "2026-05-12"  # revary 대상 컷오프와 동일
    for a in arts:
        h = a.get("body_html") or ""
        has_img = len(re.findall(r"<img\b", h, re.I)) > 0
        has_placeholder = bool(re.search(r"<!--\s*IMG:body", h, re.I))
        created = (a.get("created_at") or "")[:10]
        if not has_img and has_placeholder:
            n_broken += 1
        if REVARY_MARKER in h:
            n_revaried += 1
        if has_img and created < CUTOFF and REVARY_MARKER not in h:
            n_old_with_img += 1
    n_ok = n_total - n_broken
    pct = round(n_ok / n_total * 100, 1) if n_total else 0

    msg = (
        f"<b>📊 {shop} 블로그 사진 진행</b>\n\n"
        f"<b>① 사진 복구 (깨진 글)</b>\n"
        f"  전체 {n_total}편 / 정상 <b>{n_ok}편</b> ({pct}%) / 깨짐 <b>{n_broken}편</b>\n\n"
        f"<b>② 새 톤 재컨셉 (revary)</b>\n"
        f"  새 톤 적용 <b>{n_revaried}편</b> / 옛톤 남음 <b>{n_old_with_img}편</b>"
    )
    if n_broken == 0 and n_old_with_img == 0:
        msg += "\n\n🎉 모든 사진 작업 완료!"

    data = urllib.parse.urlencode({
        "chat_id": chat, "text": msg, "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{bot}/sendMessage",
        data=data, headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        json.loads(r.read())
    log(f"[telegram] sent: {shop} {n_ok}/{n_total} ({pct}%)")


if __name__ == "__main__":
    main()
