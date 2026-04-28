"""Multi-pass content generation - Claude API."""
from __future__ import annotations

import json, re, urllib.error, urllib.request
from utils import load_env, load_few_shot_articles, load_system_prompt, log


def _strip_html(html):
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


def _build_few_shot_block(few_shot, max_chars_per=3500):
    if not few_shot:
        return ""
    blocks = []
    for art in few_shot:
        text = _strip_html(art.get("body_html", ""))[:max_chars_per]
        blocks.append(
            "### Reference [" + art["type"] + "] - " + art["title"] + "\n"
            "URL: " + art["url"] + "\n"
            "Tags: " + str(art.get("tags", "")) + "\n"
            "Body excerpt:\n" + text + "\n"
        )
    return "## REFERENCE POSTS (brand voice)\n\n" + "\n\n".join(blocks)


def _claude_call(api_key, model, system, messages, max_tokens=8000, temperature=0.7):
    body = json.dumps({"model": model, "max_tokens": max_tokens, "temperature": temperature,
                       "system": system, "messages": messages}).encode("utf-8")
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body,
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="ignore")[:1000]
        raise RuntimeError("Claude API HTTP " + str(e.code) + ": " + body_text)
    parts = data.get("content", [])
    text_parts = [p.get("text", "") for p in parts if p.get("type") == "text"]
    return "\n".join(text_parts).strip()


def _extract_json(text):
    fence = re.search(r"```(?:json)?\s*\n?(\{.*?\})\s*\n?```", text, re.DOTALL)
    if fence:
        return json.loads(fence.group(1))
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start:end+1])
    raise ValueError("No JSON in response")


OUTPUT_SCHEMA_INSTRUCTION = """OUTPUT FORMAT - return ONE JSON object with EXACTLY these fields:

{
  "title": "Article title",
  "body_html": "Full body HTML - NO H1. intro -> Quick Answer -> table (max 5 rows) -> detail -> Common Mistakes -> FAQ + JSON-LD schema -> Final Steep -> Quick Recap -> CTA. Insert <!-- IMG:body-1 --> and <!-- IMG:body-2 -->.",
  "summary": "1-2 sentences",
  "meta_title": "50-60 chars",
  "meta_description": "140-160 chars",
  "url_slug": "lowercase-with-hyphens",
  "tags": ["tag1", "tag2"],
  "images": [
    {"role": "featured", "section": null, "prompt": "...", "filename": "...", "alt": "..."},
    {"role": "body", "section": "...", "prompt": "...", "filename": "...", "alt": "..."},
    {"role": "body", "section": "...", "prompt": "...", "filename": "...", "alt": "..."}
  ],
  "internal_judgment": {
    "content_quality": {"score": 10, "reason": "..."},
    "onpage_seo": {"score": 10, "reason": "..."},
    "conversion_alignment": {"score": 10, "reason": "..."},
    "body_judgment": "...",
    "page_judgment": "page-level acknowledges template deductions are template issues, not body issues",
    "deductions": []
  }
}

HARD RULES (auto-fail if violated):
- Body NOT contain h1.
- Tables max 5 data rows.
- Exactly ONE CTA block immediately after Quick Recap. No content below CTA.
- All links absolute https://steep-society.com/.
- Temperature: F first, (C) parens.
- General: 1 featured + 2 body images. Hub: 1 featured + 3 body images.
- FAQ section MUST be followed by JSON-LD FAQPage script tag inline in body.
- Body product mentions MUST be CTA-matched OR linked inline (no orphan purchase intent).
- Honest scores. Do not lie 10/10.
"""


def _build_user_prompt(*, date, topic, post_type, subtype, cta, hub_links, extra_notes):
    type_str = "Post type: " + post_type + (" / " + subtype if subtype else "")
    cta_block = (
        "CTA Collection (USE EXACTLY THIS):\n"
        "  Display name (button text): " + cta["title"] + "\n"
        "  Handle: " + cta["handle"] + "\n"
        "  Full URL: " + cta["url"] + "\n"
    )
    image_count = ("1 featured + 3 body images (4 total)" if post_type == "hub"
                   else "1 featured + 2 body images (3 total)")
    hub_block = ""
    if hub_links:
        lines = "\n".join("  - " + h["title"] + " -> https://steep-society.com/blogs/steep-society-journal/" + h["slug"] for h in hub_links)
        hub_block = "Hub-related internal links allowed (4-5 max for hub posts):\n" + lines
    notes = ("\nAdditional notes: " + extra_notes) if extra_notes else ""
    return (
        "Article publish date: " + date + " (Steep Society Journal)\n"
        "Topic: " + topic + "\n"
        + type_str + "\n"
        "Image budget: " + image_count + "\n"
        + cta_block + hub_block + notes + "\n\n"
        "Generate the complete article now per the system prompt's output format.\n\n"
        "CRITICAL FIRST-PASS 10/10 STANDARD:\n"
        "Your FIRST output MUST score 10/10/10. Before returning, verify ALL 17 pre-flight items in section 14c:\n"
        "  STRUCTURE (10): no h1, table <=5 rows, exactly 1 CTA after Quick Recap, no content below CTA, "
        "CTA button text = collection name 1:1, all links https://steep-society.com/, F first/(C) parens, "
        "correct image count, body image placeholders inserted, slug lowercase+hyphens.\n"
        "  SEO (5): title 50-70 chars, meta_title 50-60, meta_description 140-160, primary keyword in title/slug/meta/intro, "
        "FAQ section IMMEDIATELY followed by JSON-LD FAQPage <script> tag in body_html.\n"
        "  CONVERSION (2): every product category mentioned in body is CTA-matched or has inline link "
        "(zero orphan purchase intent), Quick Answer in first 2-3 paragraphs.\n"
        "If any item fails, FIX it before returning. Mark 10/10 only if every item passes."
    )


def generate_draft(*, topic, date, post_type, subtype, cta, hub_links=None, env=None):
    env = env or load_env()
    sys_prompt = load_system_prompt()
    few_shot = _build_few_shot_block(load_few_shot_articles())
    full_system = sys_prompt + "\n\n" + few_shot + "\n\n" + OUTPUT_SCHEMA_INSTRUCTION
    user_msg = _build_user_prompt(date=date, topic=topic, post_type=post_type,
                                   subtype=subtype, cta=cta, hub_links=hub_links, extra_notes=None)
    last_err = None
    for attempt in range(1, 4):
        log("[Pass 1] draft attempt " + str(attempt) + "/3 (model=" + env["ANTHROPIC_MODEL"] + ")")
        try:
            raw = _claude_call(api_key=env["ANTHROPIC_API_KEY"], model=env["ANTHROPIC_MODEL"],
                              system=full_system, messages=[{"role": "user", "content": user_msg}],
                              max_tokens=12000, temperature=0.7)
            draft = _extract_json(raw)
            log("[Pass 1] draft done - " + draft.get("title", "?")[:60])
            return draft
        except (ValueError, Exception) as e:
            last_err = e
            log("[Pass 1] JSON parse failed: " + str(e)[:120], "WARN")
            if attempt < 3:
                log("[Pass 1] retrying...", "WARN")
    raise RuntimeError("draft 3회 시도 모두 실패: " + str(last_err))


CRITIQUE_SYSTEM = """You are a senior tea-blog editor reviewing a Steep Society draft. Find weaknesses ruthlessly. Quality target: 10/10.

Output JSON:
{
  "content_weaknesses": ["..."],
  "seo_weaknesses": ["..."],
  "conversion_weaknesses": ["..."],
  "structure_violations": ["..."],
  "specific_rewrites": [{"location": "...", "issue": "...", "suggested": "..."}],
  "overall_priority": "..."
}

Be specific. Cite phrases. Only list issues."""


def _call_and_parse_with_retry(*, label, max_attempts, call_fn):
    """Run call_fn (which returns raw text), parse JSON, retry on parse failure.
    label: prefix for logs (e.g. '[Pass 2]')
    call_fn: callable -> raw string
    """
    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            raw = call_fn()
            return _extract_json(raw)
        except Exception as e:
            last_err = e
            log(label + " JSON parse failed: " + str(e)[:120], "WARN")
            if attempt < max_attempts:
                log(label + " retrying...", "WARN")
    raise RuntimeError(label + " " + str(max_attempts) + "회 시도 모두 실패: " + str(last_err))


def self_critique(draft, env):
    log("[Pass 2] self-critique")
    user_msg = "Review this draft article JSON:\n\n```json\n" + json.dumps(draft, ensure_ascii=False, indent=2) + "\n```"
    def _call():
        return _claude_call(api_key=env["ANTHROPIC_API_KEY"], model=env["ANTHROPIC_MODEL"],
                          system=CRITIQUE_SYSTEM, messages=[{"role": "user", "content": user_msg}],
                          max_tokens=4000, temperature=0.3)
    crit = _call_and_parse_with_retry(label="[Pass 2]", max_attempts=3, call_fn=_call)
    n = sum(len(crit.get(k, [])) for k in ("content_weaknesses", "seo_weaknesses", "conversion_weaknesses", "structure_violations"))
    log("[Pass 2] critique done - " + str(n) + " issues")
    return crit


def revise(draft, critique, env, *, original_user_prompt):
    log("[Pass 3] revise")
    sys_prompt = load_system_prompt()
    few_shot = _build_few_shot_block(load_few_shot_articles())
    full_system = sys_prompt + "\n\n" + few_shot + "\n\n" + OUTPUT_SCHEMA_INSTRUCTION
    user_msg = (
        original_user_prompt + "\n\n"
        "## YOUR PREVIOUS DRAFT\n\n```json\n" + json.dumps(draft, ensure_ascii=False, indent=2) + "\n```\n\n"
        "## EDITOR CRITIQUE\n\n```json\n" + json.dumps(critique, ensure_ascii=False, indent=2) + "\n```\n\n"
        "Now produce REVISED article JSON. Address every weakness."
    )
    def _call():
        return _claude_call(api_key=env["ANTHROPIC_API_KEY"], model=env["ANTHROPIC_MODEL"],
                          system=full_system, messages=[{"role": "user", "content": user_msg}],
                          max_tokens=12000, temperature=0.5)
    out = _call_and_parse_with_retry(label="[Pass 3]", max_attempts=3, call_fn=_call)
    log("[Pass 3] revise done")
    return out


def cross_review(revised, env):
    review_model = env.get("ANTHROPIC_REVIEW_MODEL", env["ANTHROPIC_MODEL"])
    log("[Pass 4] cross-review (model=" + review_model + ")")
    sys_prompt = load_system_prompt()
    few_shot = _build_few_shot_block(load_few_shot_articles())
    suffix = "\n\n## CROSS-MODEL FINAL POLISH\nFinal polish. Tighten weak sentences, fix subtle SEO, verify all hard rules. Return SAME JSON schema."
    full_system = sys_prompt + "\n\n" + few_shot + "\n\n" + OUTPUT_SCHEMA_INSTRUCTION + suffix
    user_msg = "Polish this revised draft:\n\n```json\n" + json.dumps(revised, ensure_ascii=False, indent=2) + "\n```"
    def _call():
        return _claude_call(api_key=env["ANTHROPIC_API_KEY"], model=review_model,
                          system=full_system, messages=[{"role": "user", "content": user_msg}],
                          max_tokens=12000, temperature=0.4)
    out = _call_and_parse_with_retry(label="[Pass 4]", max_attempts=3, call_fn=_call)
    log("[Pass 4] cross-review done")
    return out


def generate_full_article(*, topic, date, post_type, subtype, cta, hub_links=None,
                          target_score=10, max_perfection_passes=2):
    env = load_env()
    user_prompt = _build_user_prompt(date=date, topic=topic, post_type=post_type,
                                      subtype=subtype, cta=cta, hub_links=hub_links, extra_notes=None)
    draft = generate_draft(topic=topic, date=date, post_type=post_type,
                            subtype=subtype, cta=cta, hub_links=hub_links, env=env)
    critique = self_critique(draft, env)
    revised = revise(draft, critique, env, original_user_prompt=user_prompt)
    best = cross_review(revised, env)

    from perfection import perfection_pass, min_score
    best_score = min_score(best)
    log("\n--- after cross-review min: " + str(best_score) + "/10 ---")
    for i in range(max_perfection_passes):
        if best_score >= target_score:
            log("target " + str(target_score) + "/10 reached")
            break
        log("\n--- perfection iter " + str(i+1) + "/" + str(max_perfection_passes) + " ---")
        try:
            cand = perfection_pass(best, env)
        except Exception as e:
            log("perfection failed: " + str(e), "WARN")
            break
        cs = min_score(cand)
        if cs >= best_score:
            best = cand
            best_score = cs
            log("improved -> " + str(best_score) + "/10")
        else:
            log("score dropped (" + str(cs) + " < " + str(best_score) + ") - keep previous", "WARN")
            break
    log("\n=== final min score: " + str(best_score) + "/10 ===")
    return best
