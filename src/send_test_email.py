#!/usr/bin/env python3
"""Send a test email using the same template as send_email.py but with dummy data.
Lets us verify SMTP credentials + email rendering without running the full pipeline."""
from __future__ import annotations
import smtplib, sys
from email.message import EmailMessage
sys.path.insert(0, "src")
from send_email import render_html, render_text, load_env

DUMMY_REPORT = {
    "start": "2026-05-04",
    "days": 7,
    "summaries": [
        {
            "date": "2026-05-04", "status": "success",
            "title": "Best Caffeine-Free Tea Gifts: 5 Blends That Get Reordered (테스트)",
            "type": "longtail",
            "handle": "best-caffeine-free-tea-gifts",
            "scheduled_at": "2026-05-04T07:00:00Z",
            "admin_url": "https://admin.shopify.com/store/iicic2-ap/articles/000000000",
            "public_url": "https://steep-society.com/blogs/steep-society-journal/best-caffeine-free-tea-gifts",
            "score": 10, "article_id": "000000000",
        },
        {
            "date": "2026-05-05", "status": "success",
            "title": "Mother's Day Tea Gift Hub: Where to Start (테스트)",
            "type": "hub",
            "handle": "mothers-day-tea-gift-hub",
            "scheduled_at": "2026-05-05T07:00:00Z",
            "admin_url": "https://admin.shopify.com/store/iicic2-ap/articles/000000001",
            "public_url": "https://steep-society.com/blogs/steep-society-journal/mothers-day-tea-gift-hub",
            "score": 10, "article_id": "000000001",
        },
        {
            "date": "2026-05-06", "status": "success",
            "title": "Best Tea for Spring Hosting (테스트)",
            "type": "longtail",
            "handle": "best-tea-for-spring-hosting",
            "scheduled_at": "2026-05-06T07:00:00Z",
            "admin_url": "https://admin.shopify.com/store/iicic2-ap/articles/000000002",
            "public_url": "https://steep-society.com/blogs/steep-society-journal/best-tea-for-spring-hosting",
            "score": 10, "article_id": "000000002",
        },
    ]
}

DUMMY_ARTICLES = {
    "2026-05-04": {
        "title": "Best Caffeine-Free Tea Gifts: 5 Blends That Get Reordered (테스트)",
        "url_slug": "best-caffeine-free-tea-gifts",
        "internal_judgment": {
            "content_quality": {
                "score": 10,
                "reason": "Distinct angle on 'gifts that actually get reordered' with specific reorder data. Clear Quick Pick decision in 2nd paragraph, no fluff. 5-row pairing table, FAQ JSON-LD inline."
            },
            "onpage_seo": {
                "score": 10,
                "reason": "Title 58 chars, meta_title 56, meta_description 152, primary keyword 'caffeine-free tea gifts' appears in title/slug/intro/H2. FAQPage + Article JSON-LD both inline."
            },
            "conversion_alignment": {
                "score": 10,
                "reason": "Single CTA below Quick Recap matches 'Tea Gift Sets & Samplers' collection 1:1. No orphan product mentions — all body product names linked or CTA-matched."
            },
            "body_judgment": "10/10 across all body criteria. Page-level deductions for site-wide FAQ/coffee image are template issues, excluded from score."
        }
    },
    "2026-05-05": {
        "title": "Mother's Day Tea Gift Hub: Where to Start Based on Mom's Tea Style (테스트)",
        "url_slug": "mothers-day-tea-gift-hub",
        "internal_judgment": {
            "content_quality": {
                "score": 10,
                "reason": "Hub structure: decision tree by mom's tea preference, 5-row routing table, 4 internal long-tail links, FAQ + Article schema."
            },
            "onpage_seo": {
                "score": 10,
                "reason": "Hub title 62 chars, primary kw 'Mother's Day Tea Gift Hub'. Meta 156 chars. 4 cross-links to long-tail posts. Both schemas inline."
            },
            "conversion_alignment": {
                "score": 10,
                "reason": "Hub Shortcut at top routes by category. Single CTA 'Shop Tea Gift Sets & Samplers' below Quick Recap, 1:1 with collection name."
            },
            "body_judgment": "Hub-pattern executed cleanly: landing → Hub Shortcut → 5-row table → internal links → guides → FAQ → Final Steep → Quick Recap → CTA."
        }
    },
    "2026-05-06": {
        "title": "Best Tea for Spring Hosting: 7 Blends + Brewing Tips (테스트)",
        "url_slug": "best-tea-for-spring-hosting",
        "internal_judgment": {
            "content_quality": {
                "score": 10,
                "reason": "Specific guest scenarios (brunch, garden party, baby shower) with matching brew temps. F first / (C) parens followed throughout."
            },
            "onpage_seo": {
                "score": 10,
                "reason": "Primary kw 'tea for spring hosting' in title/meta/slug/intro. 5-row hosting table. FAQ + Article JSON-LD inline."
            },
            "conversion_alignment": {
                "score": 10,
                "reason": "Quick Pick in 2nd paragraph routes to gift-set sampler. CTA matches collection 1:1. Zero orphan product mentions."
            },
            "body_judgment": "10/10 body. Hosting-occasion angle gives unique reorder reason, not generic 'best teas' list."
        }
    }
}


def main():
    env = load_env()
    smtp_user = env.get("SMTP_USER", "yobizwiz@gmail.com")
    smtp_pass = env.get("SMTP_PASSWORD")
    email_to = env.get("EMAIL_TO", smtp_user)
    if not smtp_pass:
        print("[test-email] SMTP_PASSWORD 미설정 — 종료")
        sys.exit(1)

    msg = EmailMessage()
    msg["Subject"] = "[Steep Society] 📨 테스트 메일 — 5/4-5/10 결과 미리보기"
    msg["From"] = smtp_user
    msg["To"] = email_to
    msg.set_content(
        "이 메일은 테스트 발송입니다. 실제 5/1에 자동 실행되면 이 형식으로 7일치 결과가 도착합니다.\n\n"
        + render_text(DUMMY_REPORT, DUMMY_ARTICLES)
    )
    intro = (
        "<div style='background:#fff7d8;border:1px solid #f3d97a;border-radius:10px;"
        "padding:14px 16px;margin-bottom:18px;font-size:14px;'>"
        "<b>📨 이 메일은 테스트 발송입니다.</b><br>실제 5/1 (뉴욕시간 금요일 오전 8시 EDT) "
        "에 자동 실행되면 이 형식으로 7일치 진짜 결과가 도착합니다."
        "</div>"
    )
    html = render_html(DUMMY_REPORT, DUMMY_ARTICLES).replace(
        "<div class='wrap'>", "<div class='wrap'>" + intro, 1
    )
    msg.add_alternative(html, subtype="html")

    print(f"[test-email] sending to {email_to} via {smtp_user} ...")
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(smtp_user, smtp_pass)
        smtp.send_message(msg)
    print("[test-email] sent.")


if __name__ == "__main__":
    main()
