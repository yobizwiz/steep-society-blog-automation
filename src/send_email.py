#!/usr/bin/env python3
"""Send weekly batch summary email with per-article scores.

Reads:
- output/weekly-batch-*.json  (most recent)
- output/{date}-article.json  (per-day for scoring detail)

Sends HTML email via SMTP (Gmail).
"""
from __future__ import annotations
import glob, html, json, os, smtplib, sys
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "output"


def load_env():
    """Load env from api-keys.txt + os.environ."""
    env = {}
    keys_file = ROOT / "api-keys.txt"
    if keys_file.exists():
        for line in keys_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    for k in ("SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO"):
        if k in os.environ:
            env[k] = os.environ[k]
    return env


def fmt_score(label, judgment_obj, key):
    """Return (score_str, reason_str) for a judgment dict."""
    obj = (judgment_obj or {}).get(key) or {}
    score = obj.get("score", "?")
    reason = obj.get("reason", "")
    return label, str(score), reason


def render_html(report, articles_by_date):
    """Build the HTML body."""
    summaries = report.get("summaries", [])
    success = [s for s in summaries if s.get("status") == "success"]
    failed = [s for s in summaries if s.get("status") == "failed"]
    skipped = [s for s in summaries if s.get("status") == "skipped"]

    css = """
    <style>
      body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#2b2118;background:#faf7f1;padding:24px;}
      .wrap{max-width:680px;margin:0 auto;background:#fff;padding:28px;border-radius:14px;border:1px solid #ded6c8;}
      h1{margin:0 0 6px;font-size:22px;}
      .summary{padding:14px 16px;background:#faf7f1;border-radius:10px;margin:14px 0 22px;font-size:15px;}
      .day{border:1px solid #ded6c8;border-radius:12px;padding:18px 20px;margin:14px 0;}
      .day h2{margin:0 0 4px;font-size:17px;}
      .day .date{color:#6f6354;font-size:13px;margin-bottom:10px;}
      .day .meta{font-size:13px;color:#6f6354;margin-bottom:10px;}
      table.scores{width:100%;border-collapse:collapse;margin:8px 0 14px;font-size:14px;}
      table.scores th,table.scores td{padding:8px 10px;border-bottom:1px solid #f1ebe0;text-align:left;vertical-align:top;}
      table.scores th{background:#faf7f1;font-weight:600;width:140px;}
      .score-num{font-weight:700;font-size:15px;color:#2b2118;width:70px;}
      .score-10{color:#1f7d3a;}
      .score-9{color:#5a8a2a;}
      .score-low{color:#b35a1a;}
      .links a{color:#2b2118;text-decoration:underline;font-size:13px;margin-right:14px;}
      .fail{background:#fbe9e7;border-color:#f1c0b8;}
      .fail h2{color:#b3261e;}
      .fail .err{font-family:monospace;font-size:12px;background:#fff;padding:8px;border-radius:6px;margin-top:8px;color:#b3261e;}
    </style>
    """

    head = (
        f"<h1>Steep Society 주간 자동화 결과</h1>"
        f"<div class='summary'>성공 <b>{len(success)}</b> / 실패 <b>{len(failed)}</b> / 건너뜀 <b>{len(skipped)}</b>"
        f"</div>"
    )

    blocks = []
    for s in summaries:
        date = s.get("date", "?")
        if s.get("status") == "failed":
            blocks.append(
                f"<div class='day fail'>"
                f"<div class='date'>{date}</div>"
                f"<h2>❌ FAILED — {html.escape(s.get('title') or '(no title)')}</h2>"
                f"<div class='err'>{html.escape(s.get('error','') or '')[:600]}</div>"
                f"</div>"
            )
            continue
        if s.get("status") == "skipped":
            blocks.append(
                f"<div class='day'>"
                f"<div class='date'>{date}</div>"
                f"<h2>⏭ SKIPPED</h2>"
                f"<div class='err'>{html.escape(s.get('error','') or '')}</div>"
                f"</div>"
            )
            continue

        # success
        article = articles_by_date.get(date) or {}
        title = article.get("title") or s.get("title") or "(no title)"
        slug = article.get("url_slug") or s.get("handle") or ""
        judgment = article.get("internal_judgment") or {}

        rows = []
        for label, key in (
            ("Content Quality (콘텐츠 품질)", "content_quality"),
            ("On-page SEO (온페이지 SEO)", "onpage_seo"),
            ("Conversion Alignment (전환 정합성)", "conversion_alignment"),
        ):
            obj = (judgment.get(key) or {})
            score = obj.get("score", "?")
            reason = obj.get("reason", "(no reason)")
            try:
                s_int = int(score)
                cls = "score-10" if s_int == 10 else ("score-9" if s_int == 9 else "score-low")
            except (TypeError, ValueError):
                cls = ""
            rows.append(
                f"<tr><th>{label}</th>"
                f"<td class='score-num {cls}'>{html.escape(str(score))}/10</td>"
                f"<td>{html.escape(str(reason))}</td></tr>"
            )

        body_judgment = judgment.get("body_judgment") or ""
        admin_url = s.get("admin_url") or ""
        public_url = s.get("public_url") or ""
        scheduled = s.get("scheduled_at") or ""

        blocks.append(
            f"<div class='day'>"
            f"<div class='date'>{date} · 예약 발행: {scheduled}</div>"
            f"<h2>✅ {html.escape(title)}</h2>"
            f"<div class='meta'>handle: <code>{html.escape(slug)}</code></div>"
            f"<table class='scores'><tbody>{''.join(rows)}</tbody></table>"
            + (f"<div class='meta'><b>총평:</b> {html.escape(str(body_judgment))[:400]}</div>" if body_judgment else "")
            + f"<div class='links'>"
            + (f"<a href='{html.escape(admin_url)}'>Shopify 관리자에서 보기</a>" if admin_url else "")
            + (f"<a href='{html.escape(public_url)}'>공개 URL (발행 후 활성화)</a>" if public_url else "")
            + f"</div>"
            f"</div>"
        )

    return f"<!doctype html><html><head><meta charset='utf-8'>{css}</head><body><div class='wrap'>{head}{''.join(blocks)}</div></body></html>"


def render_text(report, articles_by_date):
    summaries = report.get("summaries", [])
    success = [s for s in summaries if s.get("status") == "success"]
    failed = [s for s in summaries if s.get("status") == "failed"]
    skipped = [s for s in summaries if s.get("status") == "skipped"]
    lines = [
        f"Steep Society 주간 자동화 결과",
        f"성공 {len(success)} / 실패 {len(failed)} / 건너뜀 {len(skipped)}",
        "",
    ]
    for s in summaries:
        date = s.get("date")
        if s.get("status") != "success":
            lines.append(f"[{date}] {s.get('status').upper()} — {s.get('error','')[:200]}")
            lines.append("")
            continue
        article = articles_by_date.get(date) or {}
        title = article.get("title") or s.get("title") or "?"
        j = article.get("internal_judgment") or {}
        cq = (j.get("content_quality") or {})
        seo = (j.get("onpage_seo") or {})
        conv = (j.get("conversion_alignment") or {})
        lines.append(f"[{date}] {title}")
        lines.append(f"  예약 발행: {s.get('scheduled_at','?')}")
        lines.append(f"  콘텐츠 품질: {cq.get('score','?')}/10 — {cq.get('reason','')[:200]}")
        lines.append(f"  온페이지 SEO: {seo.get('score','?')}/10 — {seo.get('reason','')[:200]}")
        lines.append(f"  전환 정합성: {conv.get('score','?')}/10 — {conv.get('reason','')[:200]}")
        if s.get("admin_url"):
            lines.append(f"  관리자: {s.get('admin_url')}")
        lines.append("")
    return "\n".join(lines)


def main():
    env = load_env()
    smtp_user = env.get("SMTP_USER") or env.get("EMAIL_FROM") or "yobizwiz@gmail.com"
    smtp_pass = env.get("SMTP_PASSWORD")
    email_to = env.get("EMAIL_TO") or smtp_user
    if not smtp_pass:
        print("[email] SMTP_PASSWORD 미설정 — 메일 발송 건너뜀")
        sys.exit(0)

    files = sorted(glob.glob(str(OUTPUT / "weekly-batch-*.json")))
    if not files:
        print("[email] weekly-batch JSON 없음 — 종료")
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
                print(f"[email] {d}-article.json 파싱 실패: {e}")

    summaries = report.get("summaries", [])
    dates = [s.get("date","") for s in summaries if s.get("date")]
    success = sum(1 for s in summaries if s.get("status") == "success")
    failed = sum(1 for s in summaries if s.get("status") == "failed")
    range_str = f"{dates[0]} ~ {dates[-1]}" if dates else "(no dates)"

    subject = f"[Steep Society] 자동 발행 결과 {range_str} — 성공 {success} / 실패 {failed}"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = email_to
    msg.set_content(render_text(report, articles_by_date))
    msg.add_alternative(render_html(report, articles_by_date), subtype="html")

    print(f"[email] sending to {email_to} via {smtp_user} ...")
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(smtp_user, smtp_pass)
        smtp.send_message(msg)
    print(f"[email] sent: {subject}")


if __name__ == "__main__":
    main()
