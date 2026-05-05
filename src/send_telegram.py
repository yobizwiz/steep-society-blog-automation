#!/usr/bin/env python3
"""Send weekly batch summary to Telegram with per-article scores.

Reads:
- output/weekly-batch-*.json  (most recent)
- output/{date}-article.json  (per-day for scoring detail)

Sends compact HTML message via Telegram Bot API.
Splits into multiple messages if >4000 chars (Telegram limit 4096).
"""
from __future__ import annotations
import glob, html, json, os, sys, urllib.error, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "output"
TG_LIMIT = 4000


def load_env():
    env = {}
    keys_file = ROOT / "api-keys.txt"
    if keys_file.exists():
        for line in keys_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        if k in os.environ:
            env[k] = os.environ[k]
    return env


def tg_send(token, chat_id, text, parse_mode="HTML"):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Telegram HTTP {e.code}: {err_body[:300]}")


def esc(s):
    return html.escape(str(s) if s is not None else "")


def render_day(s, article):
    date = s.get("date", "?")
    status = s.get("status", "?")
    if status == "failed":
        return (
            f"\n📅 <b>{esc(date)}</b>\n"
            f"❌ <b>FAILED</b> — {esc((s.get('title') or '?')[:60])}\n"
            f"<i>{esc((s.get('error') or '')[:200])}</i>\n"
        )
    if status == "skipped":
        return (
            f"\n📅 <b>{esc(date)}</b>\n"
            f"⏭ <b>SKIPPED</b> — {esc((s.get('error') or '')[:120])}\n"
        )
    title = (article.get("title") if article else None) or s.get("title") or "?"
    j = (article or {}).get("internal_judgment") or {}
    cq = (j.get("content_quality") or {}).get("score", "?")
    seo = (j.get("onpage_seo") or {}).get("score", "?")
    conv = (j.get("conversion_alignment") or {}).get("score", "?")
    admin_url = s.get("admin_url") or ""

    return (
        f"\n📅 <b>{esc(date)}</b>\n"
        f"<b>{esc(title[:80])}</b>\n"
        f"콘텐츠 <b>{esc(cq)}/10</b> · SEO <b>{esc(seo)}/10</b> · 전환 <b>{esc(conv)}/10</b>\n"
        + (f'🔗 <a href="{esc(admin_url)}">Shopify 관리자</a>\n' if admin_url else "")
    )


def split_messages(header, day_blocks, footer=""):
    msgs = []
    current = header
    for b in day_blocks:
        if len(current) + len(b) > TG_LIMIT:
            msgs.append(current)
            current = b
        else:
            current += b
    if footer and len(current) + len(footer) <= TG_LIMIT:
        current += footer
    elif footer:
        msgs.append(current)
        current = footer
    if current.strip():
        msgs.append(current)
    return msgs


def main():
    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN")
    chat_id = env.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[telegram] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 미설정 — 발송 건너뜀")
        sys.exit(0)

    files = sorted(glob.glob(str(OUTPUT / "weekly-batch-*.json")))
    if not files:
        print("[telegram] weekly-batch JSON 없음")
        sys.exit(0)
    report = json.loads(Path(files[-1]).read_text(encoding="utf-8"))

    articles_by_date = {}
    for s in report.get("summaries", []):
        d = s.get("date")
        if not d:
            continue
        ap = OUTPUT / f"{d}-article.json"
        if ap.exists():
            try:
                articles_by_date[d] = json.loads(ap.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"[telegram] {d}-article.json 파싱 실패: {e}")

    summaries = report.get("summaries", [])
    success = sum(1 for s in summaries if s.get("status") == "success")
    failed = sum(1 for s in summaries if s.get("status") == "failed")
    skipped = sum(1 for s in summaries if s.get("status") == "skipped")
    dates = [s.get("date","") for s in summaries if s.get("date")]
    range_str = f"{dates[0]} ~ {dates[-1]}" if dates else "(no dates)"

    summary_emoji = "✅" if failed == 0 else "⚠️"
    header = (
        f"{summary_emoji} <b>Steep Society 자동 발행 결과</b>\n"
        f"<i>{esc(range_str)}</i>\n\n"
        f"성공 <b>{success}</b> / 실패 <b>{failed}</b> / 건너뜀 <b>{skipped}</b>\n"
    )

    day_blocks = [render_day(s, articles_by_date.get(s.get("date"))) for s in summaries]

    msgs = split_messages(header, day_blocks)
    print(f"[telegram] {len(msgs)} message(s) to send")
    for i, m in enumerate(msgs, 1):
        print(f"[telegram] sending msg {i}/{len(msgs)} ({len(m)} chars)")
        try:
            tg_send(token, chat_id, m, parse_mode="HTML")
        except RuntimeError as e:
            print(f"[telegram] HTML send failed: {e} — retrying as plain text")
            import re as _re
            plain = m.replace("<b>","").replace("</b>","").replace("<i>","").replace("</i>","")
            plain = _re.sub(r'<a href="[^"]+">([^<]+)</a>', r'\1', plain)
            tg_send(token, chat_id, plain, parse_mode=None)
    print("[telegram] sent.")


if __name__ == "__main__":
    main()
