#!/usr/bin/env python3
"""Send a test Telegram message with dummy data."""
from __future__ import annotations
import sys
sys.path.insert(0, "src")
from send_telegram import tg_send, render_day, split_messages, esc, load_env

DUMMY_REPORT = {
    "summaries": [
        {"date":"2026-05-11","status":"success","title":"Best Tea for Warm Afternoons (테스트)",
         "scheduled_at":"2026-05-11T07:00:00Z",
         "admin_url":"https://admin.shopify.com/store/iicic2-ap/articles/000"},
        {"date":"2026-05-12","status":"success","title":"Herbal Iced Tea for May (테스트)",
         "scheduled_at":"2026-05-12T07:00:00Z",
         "admin_url":"https://admin.shopify.com/store/iicic2-ap/articles/001"},
        {"date":"2026-05-13","status":"success","title":"Fruit Tea for Early Summer (테스트)",
         "scheduled_at":"2026-05-13T07:00:00Z",
         "admin_url":"https://admin.shopify.com/store/iicic2-ap/articles/002"},
    ]
}

DUMMY_ARTICLES = {
    "2026-05-11": {"title": "Best Tea for Warm Afternoons (테스트)",
        "internal_judgment": {"content_quality":{"score":10}, "onpage_seo":{"score":10}, "conversion_alignment":{"score":10}}},
    "2026-05-12": {"title": "Herbal Iced Tea for May: Light, Bright Picks (테스트)",
        "internal_judgment": {"content_quality":{"score":10}, "onpage_seo":{"score":10}, "conversion_alignment":{"score":10}}},
    "2026-05-13": {"title": "Fruit Tea for Early Summer: 5 Blends (테스트)",
        "internal_judgment": {"content_quality":{"score":10}, "onpage_seo":{"score":9}, "conversion_alignment":{"score":10}}},
}


def main():
    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN")
    chat_id = env.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 미설정 — 종료")
        sys.exit(1)

    summaries = DUMMY_REPORT["summaries"]
    success = sum(1 for s in summaries if s.get("status") == "success")
    failed = 0
    skipped = 0
    range_str = "2026-05-11 ~ 2026-05-13 (테스트)"

    header = (
        "📨 <b>테스트 메시지</b> — 5/8(금) 부터 이 형식으로 도착합니다\n\n"
        "✅ <b>Steep Society 자동 발행 결과</b>\n"
        f"<i>{esc(range_str)}</i>\n\n"
        f"성공 <b>{success}</b> / 실패 <b>{failed}</b> / 건너뜀 <b>{skipped}</b>\n"
    )
    day_blocks = [render_day(s, DUMMY_ARTICLES.get(s["date"])) for s in summaries]
    msgs = split_messages(header, day_blocks)

    print(f"sending {len(msgs)} message(s)")
    for i, m in enumerate(msgs, 1):
        print(f"  msg {i}/{len(msgs)} ({len(m)} chars)")
        tg_send(token, chat_id, m, parse_mode="HTML")
    print("done")


if __name__ == "__main__":
    main()
