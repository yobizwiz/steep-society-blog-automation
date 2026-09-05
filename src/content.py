"""Multi-pass content generation - Claude API."""
from __future__ import annotations

import json, re, urllib.error, urllib.request
from utils import load_env, load_few_shot_articles, load_system_prompt, log


BLOG_WRITING_RULES = """## ⚠️ AUTHORITATIVE BLOG WRITING RULES — HIGHEST PRIORITY
These rules are FINAL. If ANYTHING earlier in this prompt (the system prompt, the
few-shot examples, or the schema notes) conflicts with a rule in this section, THE
RULE IN THIS SECTION WINS. Apply every rule below to every post, without being asked.

# Steep Society Blog Writing Rules

You are the content writer for Steep Society (steep-society.com), a premium loose-leaf tea
and tea-accessory store. Every blog post written in this session is for "Steep Society Journal".
Follow EVERY rule below for EVERY post, without being asked.

## TOPIC & KEYWORD RULES
- Target long-tail keywords with BUYING or PROBLEM-SOLVING intent. Keep a 60/40 split:
  60% commercial-investigation ("best tea for sleep without melatonin", "ceremonial vs
  culinary matcha", "loose leaf starter kit") and 40% how-to/troubleshooting
  ("why is my iced tea bitter", "how to brew oolong").
- Priority clusters (in revenue order, based on what actually sells): (1) functional &
  herbal wellness teas — sleep, digestion, energy, detox; (2) matcha; (3) tea hardware —
  kettles, teapots, infusers; (4) iced/seasonal brewing.
- One primary keyword per post: H1, first 100 words, one H2, URL slug.

## URL SLUG RULE (critical)
- Slug = shortened primary keyword of the title. Never reuse old slugs, never mismatch
  slug and topic.

## HEALTH CLAIM SAFETY (mandatory — FTC compliance)
- NEVER claim a tea cures, treats, prevents, or heals any disease or condition.
- Allowed phrasing: "traditionally used to support...", "many drinkers find it helps them
  wind down", "caffeine-free, which makes it a popular evening choice".
- Attribute effects to ingredients and tradition, not medical outcomes. No dosage advice.

## INTERNAL LINKS (mandatory — a post without these is incomplete)
Every post MUST include, woven naturally into body paragraphs:
1. 2–3 links to relevant Steep Society product pages, in the exact paragraph where that
   tea or tool is discussed.
2. 1–2 links to the most specific matching collection from:
   /collections/sleep-relaxation-tea, /collections/detox-cleanse-tea,
   /collections/focus-energy-tea, /collections/digestive-health-tea,
   /collections/chamomile-tea, /collections/peppermint-tea, /collections/hibiscus-tea,
   /collections/ginger-tea, /collections/turmeric-tea, /collections/lavender-tea,
   /collections/matcha, /collections/matcha-essentials-tools, /collections/green-tea,
   /collections/black-tea, /collections/oolong-tea, /collections/rooibos-tea,
   /collections/teapots-kettles, /collections/infusers-strainers,
   /collections/tea-gift-sets-samplers, /collections/iced-tea-blends,
   /collections/latte-friendly-teas
3. 1–2 links to related Journal posts in the same cluster, always including the cluster
   hub post when one exists.
ANCHOR TEXT RULES: vary anchors naturally ("a caffeine-free chamomile blend",
"our sleep & relaxation collection", "this bamboo matcha whisk set"). Never "click here",
never repeat an anchor, never paste bare URLs.

## CONTENT QUALITY (E-E-A-T)
- Open with a 2–3 sentence direct answer, then expand.
- Concrete numbers in every post: steep temperatures (°F/°C), steep times, leaf-to-water
  ratios (g per 8oz), caffeine levels (mg ranges), resteep counts.
- Write like a tea sommelier sharing tested brewing notes.
- 1,200–1,800 words for guides; 600–900 for quick-fix posts. End with a 3–5 question FAQ.
- Title under 60 characters; meta description 150–160 characters with the primary keyword.

## DO NOT
- No keyword stuffing, no fabricated studies or testimonials, no competitor names,
- no medical claims (see HEALTH CLAIM SAFETY), max 6 internal links per post.

## MARGIN-TARGETED CONVERSION (commercial-investigation posts)
Steep runs differential margins: recognizable BRAND products ("bait") are priced to win search/click demand; in-house "hero" products carry the margin. The blog's job is to move brand/bait searchers onto a hero product.
- The weekly commercial-investigation share (~60%, usually 4 posts/week) is assigned to the targeting-cluster topics, and each such post's "Our Pick" and CTA is a HERO product direct link. The exact bait brands and hero product (with URL) are given in the user message (CTA block + Additional notes) - follow them exactly.
- On comparison and "Best X" posts: put the recognizable BRAND (bait) in the search title, the intro, and the #1 list slot to capture demand; the "Our Pick / Best Value" slot MUST be a hero product with a direct product-page link.
- Commercial posts: at least 2 of the (max 6) internal links are direct links to the hero product page. How-to / quick-fix (the 40%) stay exactly as before - a collection CTA, no forced product push.
- CTA target: commercial posts link the CTA button to the hero PRODUCT page (button text = a natural product-name phrase, NOT a collection name). Hub / how-to / quick-fix keep the COLLECTION CTA.
- Never print price or discount numbers (they change often). Use neutral value language: "our pick", "great value", "best all-rounder".
- Only products named in the notes are heroes; never call any other product "our pick" / "best value". If a hero is unavailable, the notes name the substitute.
"""


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


_NO_TEMP_MODELS = set()  # models that reject the deprecated `temperature` param (e.g. Opus 4.8+)

def _claude_call(api_key, model, system, messages, max_tokens=8000, temperature=0.7):
    def _post(send_temp):
        payload = {"model": model, "max_tokens": max_tokens,
                   "system": ([{"type": "text", "text": system,
                                "cache_control": {"type": "ephemeral"}}]
                              if isinstance(system, str) else system),
                   "messages": messages}
        if send_temp and model not in _NO_TEMP_MODELS:
            payload["temperature"] = temperature
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=data,
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read())
    try:
        data = _post(send_temp=True)
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="ignore")[:1000]
        # Self-heal: newer models (Opus 4.8+) deprecate `temperature` -> retry once without it
        if e.code == 400 and "temperature" in body_text and model not in _NO_TEMP_MODELS:
            _NO_TEMP_MODELS.add(model)
            log("[claude] '" + str(model) + "' rejects temperature; retrying without it")
            try:
                data = _post(send_temp=False)
            except urllib.error.HTTPError as e2:
                raise RuntimeError("Claude API HTTP " + str(e2.code) + ": " + e2.read().decode("utf-8", errors="ignore")[:1000])
        else:
            raise RuntimeError("Claude API HTTP " + str(e.code) + ": " + body_text)
    parts = data.get("content", [])
    text_parts = [p.get("text", "") for p in parts if p.get("type") == "text"]
    return "\n".join(text_parts).strip()


def _extract_json(text):
    """Tolerant parse of the model's article JSON. Repairs common LLM malformations
    (code fences, trailing commas, unescaped quotes / literal control chars in long
    body_html) so a single bad character does not fail the whole article. Mirrors
    _parse_gemini_json's cascade. Raises only if no recoverable object is present."""
    if not text:
        raise ValueError("No JSON in response (empty)")
    raw = text.strip()
    fence = re.search(r"```(?:json)?\s*\n?(\{.*\})\s*\n?```", raw, re.DOTALL)
    if fence:
        candidate = fence.group(1)
    else:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("No JSON in response")
        candidate = raw[start:end + 1]
    repaired = re.sub(r",(\s*[}\]])", r"\1", candidate)  # drop trailing commas
    for attempt in (candidate, repaired):
        for kw in ({}, {"strict": False}):  # strict=False tolerates literal control chars
            try:
                obj = json.loads(attempt, **kw)
                if isinstance(obj, dict) and obj:
                    return obj
            except Exception:
                pass
    try:
        import json_repair  # last resort: fixes unescaped quotes in long body_html
        obj = json_repair.loads(candidate)
        if isinstance(obj, dict) and obj:
            return obj
    except Exception:
        pass
    raise ValueError("No recoverable JSON object in response")


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
    "ai_search_optimization": {"score": 10, "reason": "Quick Answer 2-3문단 내, 단일 사실 문장, 숫자/측정 풍부, FAQ+Article JSON-LD"},
    "eeat": {"score": 10, "reason": "Experience+Expertise+Authoritativeness+Trustworthiness"},
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
    if cta.get("kind") == "product":
        cta_block = (
            "CTA - MARGIN 'OUR PICK' PRODUCT (USE EXACTLY THIS):\n"
            "  Button text (natural, product-name based - NOT a collection name): " + cta["title"] + "\n"
            "  Product handle: " + cta["handle"] + "\n"
            "  Full product URL: " + cta["url"] + "\n"
            "  The single CTA after Quick Recap links to THIS product page. Also link this product inline in the body at least twice as the 'Our Pick' / 'Best Value'.\n"
        )
    else:
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
        lines = "\n".join("  - " + h["title"] + " -> https://steep-society.com/blogs/steep-society-journal/" + (h.get("slug") or h.get("handle", "")) for h in hub_links)
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
        "CTA button text matches its target 1:1 (collection name for a collection CTA; the given product-name phrase for a product CTA), all links https://steep-society.com/, F first/(C) parens, "
        "correct image count, body image placeholders inserted, slug lowercase+hyphens.\n"
        "  SEO (5): title 50-70 chars, meta_title 50-60, meta_description 140-160, primary keyword in title/slug/meta/intro, "
        "FAQ section IMMEDIATELY followed by JSON-LD FAQPage <script> tag in body_html.\n"
        "  CONVERSION (2): every product category mentioned in body is CTA-matched or has inline link "
        "(zero orphan purchase intent), Quick Answer in first 2-3 paragraphs. For a product CTA (margin 'Our Pick'), "
        "link that product inline in the body at least twice and frame it as 'our pick'/'great value' with NO price numbers.\n"
        "If any item fails, FIX it before returning. Mark 10/10 only if every item passes."
    )


def generate_draft(*, topic, date, post_type, subtype, cta, hub_links=None, extra_notes=None, env=None):
    env = env or load_env()
    sys_prompt = load_system_prompt()
    few_shot = _build_few_shot_block(load_few_shot_articles())
    full_system = sys_prompt + "\n\n" + few_shot + "\n\n" + OUTPUT_SCHEMA_INSTRUCTION + "\n\n" + BLOG_WRITING_RULES
    user_msg = _build_user_prompt(date=date, topic=topic, post_type=post_type,
                                   subtype=subtype, cta=cta, hub_links=hub_links, extra_notes=extra_notes)
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
  "aiso_weaknesses": ["AI 검색 인용 친화성 — Quick Answer 위치, citable atomic facts, JSON-LD"],
  "eeat_weaknesses": ["Experience/Expertise/Authoritativeness/Trustworthiness 부족 지적"],
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
                          max_tokens=8000, temperature=0.3)
    crit = _call_and_parse_with_retry(label="[Pass 2]", max_attempts=3, call_fn=_call)
    n = sum(len(crit.get(k, [])) for k in ("content_weaknesses", "seo_weaknesses", "conversion_weaknesses", "structure_violations"))
    log("[Pass 2] critique done - " + str(n) + " issues")
    return crit


def revise(draft, critique, env, *, original_user_prompt):
    log("[Pass 3] revise")
    sys_prompt = load_system_prompt()
    few_shot = _build_few_shot_block(load_few_shot_articles())
    full_system = sys_prompt + "\n\n" + few_shot + "\n\n" + OUTPUT_SCHEMA_INSTRUCTION + "\n\n" + BLOG_WRITING_RULES
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


def cross_review(revised, env, post_type=None):
    if post_type == "hub":
        review_model = env.get("ANTHROPIC_REVIEW_MODEL", env["ANTHROPIC_MODEL"])
    else:
        review_model = env["ANTHROPIC_MODEL"]
    log("[Pass 4] cross-review (model=" + review_model + ")")
    sys_prompt = load_system_prompt()
    few_shot = _build_few_shot_block(load_few_shot_articles())
    suffix = "\n\n## CROSS-MODEL FINAL POLISH\nFinal polish. Tighten weak sentences, fix subtle SEO, verify all hard rules. Return SAME JSON schema. ALWAYS include internal_judgment with ALL FIVE dimensions (content_quality, onpage_seo, conversion_alignment, ai_search_optimization, eeat) - never omit a dimension."
    full_system = sys_prompt + "\n\n" + few_shot + "\n\n" + OUTPUT_SCHEMA_INSTRUCTION + "\n\n" + BLOG_WRITING_RULES + suffix
    user_msg = "Polish this revised draft:\n\n```json\n" + json.dumps(revised, ensure_ascii=False, indent=2) + "\n```"
    def _call():
        return _claude_call(api_key=env["ANTHROPIC_API_KEY"], model=review_model,
                          system=full_system, messages=[{"role": "user", "content": user_msg}],
                          max_tokens=12000, temperature=0.4)
    out = _call_and_parse_with_retry(label="[Pass 4]", max_attempts=3, call_fn=_call)
    log("[Pass 4] cross-review done")
    return out


GEMINI_REVIEW_SYSTEM = """You are an independent SEO + content reviewer for a Shopify lifestyle/wellness blog. Score the article on FIVE dimensions (0-10 each):

1. **content_quality** — Distinct angle, specific actionable info, no fluff, original insight.
2. **onpage_seo** — Meta title 60 chars or fewer (short, punchy titles for how-to / quick-fix posts are GOOD — do NOT penalize a title for being under 50 chars). Meta description 150-160 ideal, 140-165 acceptable. Primary keyword in title/slug/meta/intro. Table max 5 data rows.
3. **conversion_alignment** — Exactly ONE CTA block after Quick Recap whose button text matches its collection 1:1. NOTE: inline contextual collection/product links woven into body paragraphs are REQUIRED and GOOD — do NOT penalize them as "orphan mentions". An "orphan mention" is ONLY a product/collection named in text with NO link at all. TRUST the STRUCTURAL FACTS in the user message; never claim Quick Recap, the CTA, or JSON-LD is missing if the facts say it is present. For MARGIN posts whose CTA links to a /products/ page ("Our Pick"), the body MUST also link that same product inline at least once as a natural "our pick"/"best value" recommendation; a product CTA with no supporting inline product link, or a forced/unnatural recommendation, scores conversion_alignment 6 or lower. Collection CTAs (/collections/) on how-to/quick-fix/hub posts are correct - do not penalize them.
4. **ai_search_optimization** — AI citation-friendly: Quick Answer in 1st-3rd paragraph, single-fact atomic sentences, numbers/measurements, FAQPage + Article JSON-LD inline in body. Optimized for ChatGPT/Perplexity/Google AI Overview citation.
5. **eeat** — Google E-E-A-T quality signals: Experience (actual tested insights), Expertise (specific accurate data e.g. brewing temps), Authoritativeness (consistent brand voice), Trustworthiness (no factual errors, no contradictions).

Be brutally honest. Most articles deserve 7-9, not 10. Cite specific weaknesses.

Output ONE JSON object with EXACTLY these fields, no other text:
{
  "content_quality": {"score": 0-10, "reason": "specific reasoning under 200 chars"},
  "onpage_seo": {"score": 0-10, "reason": "specific reasoning under 200 chars"},
  "conversion_alignment": {"score": 0-10, "reason": "specific reasoning under 200 chars"},
  "ai_search_optimization": {"score": 0-10, "reason": "specific reasoning under 200 chars"},
  "eeat": {"score": 0-10, "reason": "specific reasoning under 200 chars"},
  "top_3_weaknesses": ["weakness 1", "weakness 2", "weakness 3"]
}"""


_GEMINI_BAD_MODELS = set()  # models that proved unavailable this run (self-heal)


_GEM_DIMS = ("content_quality", "onpage_seo", "conversion_alignment", "ai_search_optimization", "eeat")


def _parse_gemini_json(text):
    """Tolerant parse of Gemini's JSON review. Repairs common malformations (code
    fences, trailing commas, literal control chars) but NEVER fabricates data —
    returns None if it cannot recover a dict."""
    if not text:
        return None
    raw = text.strip()
    m = re.search(r"```(?:json)?\s*(.+?)\s*```", raw, re.DOTALL)
    if m:
        raw = m.group(1).strip()
    s = raw.find("{")
    e = raw.rfind("}")
    if s < 0 or e <= s:
        return None
    candidate = raw[s:e + 1]
    repaired = re.sub(r",(\s*[}\]])", r"\1", candidate)  # drop trailing commas
    for attempt in (candidate, repaired):
        try:
            obj = json.loads(attempt, strict=False)  # strict=False tolerates literal control chars
            if isinstance(obj, dict):
                return obj
        except Exception:
            continue
    try:
        import json_repair
        obj = json_repair.loads(candidate)
        if isinstance(obj, dict) and obj:
            return obj
    except Exception:
        pass
    return None


def _gemini_scores_valid(obj):
    """True only for a genuine 5-dim review (>=3 numeric dimension scores)."""
    if not isinstance(obj, dict):
        return False
    n = 0
    for d in _GEM_DIMS:
        v = obj.get(d)
        sc = v.get("score") if isinstance(v, dict) else v
        if isinstance(sc, (int, float)) and not isinstance(sc, bool):
            n += 1
    return n >= 3


def gemini_review(article, env):
    """Independent cross-model review by Gemini 2.5 Pro.
    Returns dict with scores + weaknesses. Does NOT modify article body."""
    api_key = env.get("GOOGLE_API_KEY")
    if not api_key:
        log("[Pass 4b] GOOGLE_API_KEY missing — skipping", "WARN")
        return None
    raw_body = article.get("body_html", "") or ""
    body_excerpt = _strip_html(raw_body)
    if len(body_excerpt) > 24000:
        body_excerpt = body_excerpt[:24000] + " ...[truncated]"
    _ns = raw_body.replace(" ", "")
    _has_faq = '"@type":"FAQPage"' in _ns
    _has_art = '"@type":"Article"' in _ns
    _has_recap = "Quick Recap" in raw_body
    _cta_n = len(re.findall(r"border-radius:\s*999px", raw_body))
    _col_n = len(re.findall(r"/collections/[a-z0-9\-]+", raw_body))
    facts_block = (
        "STRUCTURAL FACTS (verified programmatically from raw HTML - TRUST THESE; do NOT deduct for a missing element the facts say is present):\n"
        "- FAQPage JSON-LD present: " + str(_has_faq) + "\n"
        "- Article JSON-LD present: " + str(_has_art) + "\n"
        "- Quick Recap section present: " + str(_has_recap) + "\n"
        "- CTA button count: " + str(_cta_n) + " (1 = correct single CTA)\n"
        "- Inline collection links: " + str(_col_n) + " (REQUIRED by blog rules, NOT orphan mentions)\n"
    )
    user_msg = (
        "Score this article. Be tough.\n\n"
        f"Title: {article.get('title','?')}\n"
        f"Meta title: {article.get('meta_title','?')}\n"
        f"Meta description: {article.get('meta_description','?')}\n"
        f"Slug: {article.get('url_slug','?')}\n\n"
        f"{facts_block}\n"
        f"Full body text:\n{body_excerpt}"
    )
    payload = {
        "contents": [{
            "role": "user",
            "parts": [{"text": user_msg}]
        }],
        "systemInstruction": {"parts": [{"text": GEMINI_REVIEW_SYSTEM}]},
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 4000, "responseMimeType": "application/json"}
    }
    _gm_fallback = "gemini-pro-latest"
    primary = (env.get("GEMINI_REVIEW_MODEL") or _gm_fallback).strip()
    model = _gm_fallback if primary in _GEMINI_BAD_MODELS else primary
    log(f"[Pass 4b] Gemini cross-validation (model={model})")
    last_err = None
    for attempt in range(1, 4):
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                headers={"Content-Type":"application/json"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read())
            cand = (data.get("candidates") or [{}])[0]
            text = "".join(p.get("text","") for p in (cand.get("content",{}).get("parts") or []))
            result = _parse_gemini_json(text)
            if not _gemini_scores_valid(result):
                raise ValueError("gemini returned no valid dimension scores")
            log("[Pass 4b] Gemini done — content={} seo={} conv={}".format(
                result.get("content_quality",{}).get("score","?"),
                result.get("onpage_seo",{}).get("score","?"),
                result.get("conversion_alignment",{}).get("score","?")))
            return result
        except urllib.error.HTTPError as e:
            etext = ""
            try:
                etext = e.read().decode("utf-8", errors="ignore")[:300]
            except Exception:
                pass
            last_err = f"HTTP {e.code}: {etext}"
            if model != _gm_fallback and e.code in (400, 404) and re.search(
                    r"not found|not supported|unsupported|deprecat|invalid|does not exist|no longer", etext, re.I):
                _GEMINI_BAD_MODELS.add(model)
                log(f"[Pass 4b] model '{model}' unavailable ({e.code}) — falling back to {_gm_fallback}", "WARN")
                model = _gm_fallback
                continue
            log(f"[Pass 4b] attempt {attempt}/3 failed: {last_err[:120]}", "WARN")
            if attempt < 3:
                import time
                time.sleep(5 * attempt)
        except Exception as e:
            last_err = e
            log(f"[Pass 4b] attempt {attempt}/3 failed: {str(e)[:120]}", "WARN")
            if attempt < 3:
                import time
                time.sleep(5 * attempt)
    log(f"[Pass 4b] Gemini review failed after 3 attempts: {last_err} — continuing without it", "WARN")
    return None


def merge_gemini_into_judgment(article, gemini):
    """Merge Gemini scores into article.internal_judgment as nested 'gemini_review' field
    and compute a combined min_score (Anthropic min vs Gemini min — take overall min)."""
    if not gemini:
        return article
    j = article.setdefault("internal_judgment", {})
    j["gemini_review"] = gemini
    return article


def combined_min_score(article):
    """Min across all reviewer models — Anthropic 5 + Gemini 5 (only if Gemini succeeded).
    
    KEY: if Gemini missing/empty, its scores are NOT counted as 0 — they are excluded.
    Only valid integer scores enter the min."""
    DIMS = ("content_quality", "onpage_seo", "conversion_alignment",
            "ai_search_optimization", "eeat")
    j = article.get("internal_judgment", {}) or {}
    scores = []
    for k in DIMS:
        obj = j.get(k)
        if not isinstance(obj, dict): continue
        v = obj.get("score")
        if v is None: continue
        try: scores.append(int(v))
        except (TypeError, ValueError): pass
    gem = j.get("gemini_review")
    if isinstance(gem, dict):
        for k in DIMS:
            obj = gem.get(k)
            if not isinstance(obj, dict): continue
            v = obj.get("score")
            if v is None: continue
            try: scores.append(int(v))
            except (TypeError, ValueError): pass
    return min(scores) if scores else 0


def generate_full_article(*, topic, date, post_type, subtype, cta, hub_links=None,
                          extra_notes=None, target_score=10, max_perfection_passes=2):
    env = load_env()
    user_prompt = _build_user_prompt(date=date, topic=topic, post_type=post_type,
                                      subtype=subtype, cta=cta, hub_links=hub_links, extra_notes=extra_notes)
    draft = generate_draft(topic=topic, date=date, post_type=post_type,
                            subtype=subtype, cta=cta, hub_links=hub_links, extra_notes=extra_notes, env=env)
    critique = self_critique(draft, env)
    revised = revise(draft, critique, env, original_user_prompt=user_prompt)
    best = cross_review(revised, env, post_type=post_type)

    # Pass 4b: Gemini independent cross-validation (does not modify body)
    try:
        gemini = gemini_review(best, env)
        best = merge_gemini_into_judgment(best, gemini)
    except Exception as e:
        log(f"[Pass 4b] Gemini review error: {e} — continuing without", "WARN")

    from perfection import perfection_pass, min_score
    best_score = combined_min_score(best)
    log("\n--- after cross-review + Gemini, combined min: " + str(best_score) + "/10 ---")
    for i in range(max_perfection_passes):
        if best_score >= target_score:
            log("target " + str(target_score) + "/10 reached")
            break
        log("\n--- perfection iter " + str(i+1) + "/" + str(max_perfection_passes) + " ---")
        try:
            cand = perfection_pass(best, env, post_type=post_type)
            # Re-validate with Gemini after perfection
            try:
                gem2 = gemini_review(cand, env)
                cand = merge_gemini_into_judgment(cand, gem2)
            except Exception as e:
                log(f"[Pass 4b] post-perfection Gemini error: {e}", "WARN")
        except Exception as e:
            log("perfection failed: " + str(e), "WARN")
            break
        cs = combined_min_score(cand)
        if cs >= best_score:
            best = cand
            best_score = cs
            log("improved -> " + str(best_score) + "/10")
        else:
            log("score dropped (" + str(cs) + " < " + str(best_score) + ") - keep previous", "WARN")
            break
    log("\n=== final min score: " + str(best_score) + "/10 ===")
    return best
"""Multi-pass content generation - Claude API."""
from __future__ import annotations

import json, re, urllib.error, urllib.request
from utils import load_env, load_few_shot_articles, load_system_prompt, log


BLOG_WRITING_RULES = """## ⚠️ AUTHORITATIVE BLOG WRITING RULES — HIGHEST PRIORITY
These rules are FINAL. If ANYTHING earlier in this prompt (the system prompt, the
few-shot examples, or the schema notes) conflicts with a rule in this section, THE
RULE IN THIS SECTION WINS. Apply every rule below to every post, without being asked.

# Steep Society Blog Writing Rules

You are the content writer for Steep Society (steep-society.com), a premium loose-leaf tea
and tea-accessory store. Every blog post written in this session is for "Steep Society Journal".
Follow EVERY rule below for EVERY post, without being asked.

## TOPIC & KEYWORD RULES
- Target long-tail keywords with BUYING or PROBLEM-SOLVING intent. Keep a 60/40 split:
  60% commercial-investigation ("best tea for sleep without melatonin", "ceremonial vs
  culinary matcha", "loose leaf starter kit") and 40% how-to/troubleshooting
  ("why is my iced tea bitter", "how to brew oolong").
- Priority clusters (in revenue order, based on what actually sells): (1) functional &
  herbal wellness teas — sleep, digestion, energy, detox; (2) matcha; (3) tea hardware —
  kettles, teapots, infusers; (4) iced/seasonal brewing.
- One primary keyword per post: H1, first 100 words, one H2, URL slug.

## URL SLUG RULE (critical)
- Slug = shortened primary keyword of the title. Never reuse old slugs, never mismatch
  slug and topic.

## HEALTH CLAIM SAFETY (mandatory — FTC compliance)
- NEVER claim a tea cures, treats, prevents, or heals any disease or condition.
- Allowed phrasing: "traditionally used to support...", "many drinkers find it helps them
  wind down", "caffeine-free, which makes it a popular evening choice".
- Attribute effects to ingredients and tradition, not medical outcomes. No dosage advice.

## INTERNAL LINKS (mandatory — a post without these is incomplete)
Every post MUST include, woven naturally into body paragraphs:
1. 2–3 links to relevant Steep Society product pages, in the exact paragraph where that
   tea or tool is discussed.
2. 1–2 links to the most specific matching collection from:
   /collections/sleep-relaxation-tea, /collections/detox-cleanse-tea,
   /collections/focus-energy-tea, /collections/digestive-health-tea,
   /collections/chamomile-tea, /collections/peppermint-tea, /collections/hibiscus-tea,
   /collections/ginger-tea, /collections/turmeric-tea, /collections/lavender-tea,
   /collections/matcha, /collections/matcha-essentials-tools, /collections/green-tea,
   /collections/black-tea, /collections/oolong-tea, /collections/rooibos-tea,
   /collections/teapots-kettles, /collections/infusers-strainers,
   /collections/tea-gift-sets-samplers, /collections/iced-tea-blends,
   /collections/latte-friendly-teas
3. 1–2 links to related Journal posts in the same cluster, always including the cluster
   hub post when one exists.
ANCHOR TEXT RULES: vary anchors naturally ("a caffeine-free chamomile blend",
"our sleep & relaxation collection", "this bamboo matcha whisk set"). Never "click here",
never repeat an anchor, never paste bare URLs.

## CONTENT QUALITY (E-E-A-T)
- Open with a 2–3 sentence direct answer, then expand.
- Concrete numbers in every post: steep temperatures (°F/°C), steep times, leaf-to-water
  ratios (g per 8oz), caffeine levels (mg ranges), resteep counts.
- Write like a tea sommelier sharing tested brewing notes.
- 1,200–1,800 words for guides; 600–900 for quick-fix posts. End with a 3–5 question FAQ.
- Title under 60 characters; meta description 150–160 characters with the primary keyword.

## DO NOT
- No keyword stuffing, no fabricated studies or testimonials, no competitor names,
- no medical claims (see HEALTH CLAIM SAFETY), max 6 internal links per post.

## MARGIN-TARGETED CONVERSION (commercial-investigation posts)
Steep runs differential margins: recognizable BRAND products ("bait") are priced to win search/click demand; in-house "hero" products carry the margin. The blog's job is to move brand/bait searchers onto a hero product.
- The weekly commercial-investigation share (~60%, usually 4 posts/week) is assigned to the targeting-cluster topics, and each such post's "Our Pick" and CTA is a HERO product direct link. The exact bait brands and hero product (with URL) are given in the user message (CTA block + Additional notes) - follow them exactly.
- On comparison and "Best X" posts: put the recognizable BRAND (bait) in the search title, the intro, and the #1 list slot to capture demand; the "Our Pick / Best Value" slot MUST be a hero product with a direct product-page link.
- Commercial posts: at least 2 of the (max 6) internal links are direct links to the hero product page. How-to / quick-fix (the 40%) stay exactly as before - a collection CTA, no forced product push.
- CTA target: commercial posts link the CTA button to the hero PRODUCT page (button text = a natural product-name phrase, NOT a collection name). Hub / how-to / quick-fix keep the COLLECTION CTA.
- Never print price or discount numbers (they change often). Use neutral value language: "our pick", "great value", "best all-rounder".
- Only products named in the notes are heroes; never call any other product "our pick" / "best value". If a hero is unavailable, the notes name the substitute.
"""


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


_NO_TEMP_MODELS = set()  # models that reject the deprecated `temperature` param (e.g. Opus 4.8+)

def _claude_call(api_key, model, system, messages, max_tokens=8000, temperature=0.7):
    def _post(send_temp):
        payload = {"model": model, "max_tokens": max_tokens,
                   "system": ([{"type": "text", "text": system,
                                "cache_control": {"type": "ephemeral"}}]
                              if isinstance(system, str) else system),
                   "messages": messages}
        if send_temp and model not in _NO_TEMP_MODELS:
            payload["temperature"] = temperature
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=data,
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read())
    try:
        data = _post(send_temp=True)
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="ignore")[:1000]
        # Self-heal: newer models (Opus 4.8+) deprecate `temperature` -> retry once without it
        if e.code == 400 and "temperature" in body_text and model not in _NO_TEMP_MODELS:
            _NO_TEMP_MODELS.add(model)
            log("[claude] '" + str(model) + "' rejects temperature; retrying without it")
            try:
                data = _post(send_temp=False)
            except urllib.error.HTTPError as e2:
                raise RuntimeError("Claude API HTTP " + str(e2.code) + ": " + e2.read().decode("utf-8", errors="ignore")[:1000])
        else:
            raise RuntimeError("Claude API HTTP " + str(e.code) + ": " + body_text)
    parts = data.get("content", [])
    text_parts = [p.get("text", "") for p in parts if p.get("type") == "text"]
    return "\n".join(text_parts).strip()


def _extract_json(text):
    """Tolerant parse of the model's article JSON. Repairs common LLM malformations
    (code fences, trailing commas, unescaped quotes / literal control chars in long
    body_html) so a single bad character does not fail the whole article. Mirrors
    _parse_gemini_json's cascade. Raises only if no recoverable object is present."""
    if not text:
        raise ValueError("No JSON in response (empty)")
    raw = text.strip()
    fence = re.search(r"```(?:json)?\s*\n?(\{.*\})\s*\n?```", raw, re.DOTALL)
    if fence:
        candidate = fence.group(1)
    else:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("No JSON in response")
        candidate = raw[start:end + 1]
    repaired = re.sub(r",(\s*[}\]])", r"\1", candidate)  # drop trailing commas
    for attempt in (candidate, repaired):
        for kw in ({}, {"strict": False}):  # strict=False tolerates literal control chars
            try:
                obj = json.loads(attempt, **kw)
                if isinstance(obj, dict) and obj:
                    return obj
            except Exception:
                pass
    try:
        import json_repair  # last resort: fixes unescaped quotes in long body_html
        obj = json_repair.loads(candidate)
        if isinstance(obj, dict) and obj:
            return obj
    except Exception:
        pass
    raise ValueError("No recoverable JSON object in response")


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
    "ai_search_optimization": {"score": 10, "reason": "Quick Answer 2-3문단 내, 단일 사실 문장, 숫자/측정 풍부, FAQ+Article JSON-LD"},
    "eeat": {"score": 10, "reason": "Experience+Expertise+Authoritativeness+Trustworthiness"},
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
    if cta.get("kind") == "product":
        cta_block = (
            "CTA - MARGIN 'OUR PICK' PRODUCT (USE EXACTLY THIS):\n"
            "  Button text (natural, product-name based - NOT a collection name): " + cta["title"] + "\n"
            "  Product handle: " + cta["handle"] + "\n"
            "  Full product URL: " + cta["url"] + "\n"
            "  The single CTA after Quick Recap links to THIS product page. Also link this product inline in the body at least twice as the 'Our Pick' / 'Best Value'.\n"
        )
    else:
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
        "CTA button text matches its target 1:1 (collection name for a collection CTA; the given product-name phrase for a product CTA), all links https://steep-society.com/, F first/(C) parens, "
        "correct image count, body image placeholders inserted, slug lowercase+hyphens.\n"
        "  SEO (5): title 50-70 chars, meta_title 50-60, meta_description 140-160, primary keyword in title/slug/meta/intro, "
        "FAQ section IMMEDIATELY followed by JSON-LD FAQPage <script> tag in body_html.\n"
        "  CONVERSION (2): every product category mentioned in body is CTA-matched or has inline link "
        "(zero orphan purchase intent), Quick Answer in first 2-3 paragraphs. For a product CTA (margin 'Our Pick'), "
        "link that product inline in the body at least twice and frame it as 'our pick'/'great value' with NO price numbers.\n"
        "If any item fails, FIX it before returning. Mark 10/10 only if every item passes."
    )


def generate_draft(*, topic, date, post_type, subtype, cta, hub_links=None, extra_notes=None, env=None):
    env = env or load_env()
    sys_prompt = load_system_prompt()
    few_shot = _build_few_shot_block(load_few_shot_articles())
    full_system = sys_prompt + "\n\n" + few_shot + "\n\n" + OUTPUT_SCHEMA_INSTRUCTION + "\n\n" + BLOG_WRITING_RULES
    user_msg = _build_user_prompt(date=date, topic=topic, post_type=post_type,
                                   subtype=subtype, cta=cta, hub_links=hub_links, extra_notes=extra_notes)
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
  "aiso_weaknesses": ["AI 검색 인용 친화성 — Quick Answer 위치, citable atomic facts, JSON-LD"],
  "eeat_weaknesses": ["Experience/Expertise/Authoritativeness/Trustworthiness 부족 지적"],
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
                          max_tokens=8000, temperature=0.3)
    crit = _call_and_parse_with_retry(label="[Pass 2]", max_attempts=3, call_fn=_call)
    n = sum(len(crit.get(k, [])) for k in ("content_weaknesses", "seo_weaknesses", "conversion_weaknesses", "structure_violations"))
    log("[Pass 2] critique done - " + str(n) + " issues")
    return crit


def revise(draft, critique, env, *, original_user_prompt):
    log("[Pass 3] revise")
    sys_prompt = load_system_prompt()
    few_shot = _build_few_shot_block(load_few_shot_articles())
    full_system = sys_prompt + "\n\n" + few_shot + "\n\n" + OUTPUT_SCHEMA_INSTRUCTION + "\n\n" + BLOG_WRITING_RULES
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


def cross_review(revised, env, post_type=None):
    if post_type == "hub":
        review_model = env.get("ANTHROPIC_REVIEW_MODEL", env["ANTHROPIC_MODEL"])
    else:
        review_model = env["ANTHROPIC_MODEL"]
    log("[Pass 4] cross-review (model=" + review_model + ")")
    sys_prompt = load_system_prompt()
    few_shot = _build_few_shot_block(load_few_shot_articles())
    suffix = "\n\n## CROSS-MODEL FINAL POLISH\nFinal polish. Tighten weak sentences, fix subtle SEO, verify all hard rules. Return SAME JSON schema. ALWAYS include internal_judgment with ALL FIVE dimensions (content_quality, onpage_seo, conversion_alignment, ai_search_optimization, eeat) - never omit a dimension."
    full_system = sys_prompt + "\n\n" + few_shot + "\n\n" + OUTPUT_SCHEMA_INSTRUCTION + "\n\n" + BLOG_WRITING_RULES + suffix
    user_msg = "Polish this revised draft:\n\n```json\n" + json.dumps(revised, ensure_ascii=False, indent=2) + "\n```"
    def _call():
        return _claude_call(api_key=env["ANTHROPIC_API_KEY"], model=review_model,
                          system=full_system, messages=[{"role": "user", "content": user_msg}],
                          max_tokens=12000, temperature=0.4)
    out = _call_and_parse_with_retry(label="[Pass 4]", max_attempts=3, call_fn=_call)
    log("[Pass 4] cross-review done")
    return out


GEMINI_REVIEW_SYSTEM = """You are an independent SEO + content reviewer for a Shopify lifestyle/wellness blog. Score the article on FIVE dimensions (0-10 each):

1. **content_quality** — Distinct angle, specific actionable info, no fluff, original insight.
2. **onpage_seo** — Meta title 60 chars or fewer (short, punchy titles for how-to / quick-fix posts are GOOD — do NOT penalize a title for being under 50 chars). Meta description 150-160 ideal, 140-165 acceptable. Primary keyword in title/slug/meta/intro. Table max 5 data rows.
3. **conversion_alignment** — Exactly ONE CTA block after Quick Recap whose button text matches its collection 1:1. NOTE: inline contextual collection/product links woven into body paragraphs are REQUIRED and GOOD — do NOT penalize them as "orphan mentions". An "orphan mention" is ONLY a product/collection named in text with NO link at all. TRUST the STRUCTURAL FACTS in the user message; never claim Quick Recap, the CTA, or JSON-LD is missing if the facts say it is present. For MARGIN posts whose CTA links to a /products/ page ("Our Pick"), the body MUST also link that same product inline at least once as a natural "our pick"/"best value" recommendation; a product CTA with no supporting inline product link, or a forced/unnatural recommendation, scores conversion_alignment 6 or lower. Collection CTAs (/collections/) on how-to/quick-fix/hub posts are correct - do not penalize them.
4. **ai_search_optimization** — AI citation-friendly: Quick Answer in 1st-3rd paragraph, single-fact atomic sentences, numbers/measurements, FAQPage + Article JSON-LD inline in body. Optimized for ChatGPT/Perplexity/Google AI Overview citation.
5. **eeat** — Google E-E-A-T quality signals: Experience (actual tested insights), Expertise (specific accurate data e.g. brewing temps), Authoritativeness (consistent brand voice), Trustworthiness (no factual errors, no contradictions).

Be brutally honest. Most articles deserve 7-9, not 10. Cite specific weaknesses.

Output ONE JSON object with EXACTLY these fields, no other text:
{
  "content_quality": {"score": 0-10, "reason": "specific reasoning under 200 chars"},
  "onpage_seo": {"score": 0-10, "reason": "specific reasoning under 200 chars"},
  "conversion_alignment": {"score": 0-10, "reason": "specific reasoning under 200 chars"},
  "ai_search_optimization": {"score": 0-10, "reason": "specific reasoning under 200 chars"},
  "eeat": {"score": 0-10, "reason": "specific reasoning under 200 chars"},
  "top_3_weaknesses": ["weakness 1", "weakness 2", "weakness 3"]
}"""


_GEMINI_BAD_MODELS = set()  # models that proved unavailable this run (self-heal)


_GEM_DIMS = ("content_quality", "onpage_seo", "conversion_alignment", "ai_search_optimization", "eeat")


def _parse_gemini_json(text):
    """Tolerant parse of Gemini's JSON review. Repairs common malformations (code
    fences, trailing commas, literal control chars) but NEVER fabricates data —
    returns None if it cannot recover a dict."""
    if not text:
        return None
    raw = text.strip()
    m = re.search(r"```(?:json)?\s*(.+?)\s*```", raw, re.DOTALL)
    if m:
        raw = m.group(1).strip()
    s = raw.find("{")
    e = raw.rfind("}")
    if s < 0 or e <= s:
        return None
    candidate = raw[s:e + 1]
    repaired = re.sub(r",(\s*[}\]])", r"\1", candidate)  # drop trailing commas
    for attempt in (candidate, repaired):
        try:
            obj = json.loads(attempt, strict=False)  # strict=False tolerates literal control chars
            if isinstance(obj, dict):
                return obj
        except Exception:
            continue
    try:
        import json_repair
        obj = json_repair.loads(candidate)
        if isinstance(obj, dict) and obj:
            return obj
    except Exception:
        pass
    return None


def _gemini_scores_valid(obj):
    """True only for a genuine 5-dim review (>=3 numeric dimension scores)."""
    if not isinstance(obj, dict):
        return False
    n = 0
    for d in _GEM_DIMS:
        v = obj.get(d)
        sc = v.get("score") if isinstance(v, dict) else v
        if isinstance(sc, (int, float)) and not isinstance(sc, bool):
            n += 1
    return n >= 3


def gemini_review(article, env):
    """Independent cross-model review by Gemini 2.5 Pro.
    Returns dict with scores + weaknesses. Does NOT modify article body."""
    api_key = env.get("GOOGLE_API_KEY")
    if not api_key:
        log("[Pass 4b] GOOGLE_API_KEY missing — skipping", "WARN")
        return None
    raw_body = article.get("body_html", "") or ""
    body_excerpt = _strip_html(raw_body)
    if len(body_excerpt) > 24000:
        body_excerpt = body_excerpt[:24000] + " ...[truncated]"
    _ns = raw_body.replace(" ", "")
    _has_faq = '"@type":"FAQPage"' in _ns
    _has_art = '"@type":"Article"' in _ns
    _has_recap = "Quick Recap" in raw_body
    _cta_n = len(re.findall(r"border-radius:\s*999px", raw_body))
    _col_n = len(re.findall(r"/collections/[a-z0-9\-]+", raw_body))
    facts_block = (
        "STRUCTURAL FACTS (verified programmatically from raw HTML - TRUST THESE; do NOT deduct for a missing element the facts say is present):\n"
        "- FAQPage JSON-LD present: " + str(_has_faq) + "\n"
        "- Article JSON-LD present: " + str(_has_art) + "\n"
        "- Quick Recap section present: " + str(_has_recap) + "\n"
        "- CTA button count: " + str(_cta_n) + " (1 = correct single CTA)\n"
        "- Inline collection links: " + str(_col_n) + " (REQUIRED by blog rules, NOT orphan mentions)\n"
    )
    user_msg = (
        "Score this article. Be tough.\n\n"
        f"Title: {article.get('title','?')}\n"
        f"Meta title: {article.get('meta_title','?')}\n"
        f"Meta description: {article.get('meta_description','?')}\n"
        f"Slug: {article.get('url_slug','?')}\n\n"
        f"{facts_block}\n"
        f"Full body text:\n{body_excerpt}"
    )
    payload = {
        "contents": [{
            "role": "user",
            "parts": [{"text": user_msg}]
        }],
        "systemInstruction": {"parts": [{"text": GEMINI_REVIEW_SYSTEM}]},
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 4000, "responseMimeType": "application/json"}
    }
    _gm_fallback = "gemini-pro-latest"
    primary = (env.get("GEMINI_REVIEW_MODEL") or _gm_fallback).strip()
    model = _gm_fallback if primary in _GEMINI_BAD_MODELS else primary
    log(f"[Pass 4b] Gemini cross-validation (model={model})")
    last_err = None
    for attempt in range(1, 4):
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                headers={"Content-Type":"application/json"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read())
            cand = (data.get("candidates") or [{}])[0]
            text = "".join(p.get("text","") for p in (cand.get("content",{}).get("parts") or []))
            result = _parse_gemini_json(text)
            if not _gemini_scores_valid(result):
                raise ValueError("gemini returned no valid dimension scores")
            log("[Pass 4b] Gemini done — content={} seo={} conv={}".format(
                result.get("content_quality",{}).get("score","?"),
                result.get("onpage_seo",{}).get("score","?"),
                result.get("conversion_alignment",{}).get("score","?")))
            return result
        except urllib.error.HTTPError as e:
            etext = ""
            try:
                etext = e.read().decode("utf-8", errors="ignore")[:300]
            except Exception:
                pass
            last_err = f"HTTP {e.code}: {etext}"
            if model != _gm_fallback and e.code in (400, 404) and re.search(
                    r"not found|not supported|unsupported|deprecat|invalid|does not exist|no longer", etext, re.I):
                _GEMINI_BAD_MODELS.add(model)
                log(f"[Pass 4b] model '{model}' unavailable ({e.code}) — falling back to {_gm_fallback}", "WARN")
                model = _gm_fallback
                continue
            log(f"[Pass 4b] attempt {attempt}/3 failed: {last_err[:120]}", "WARN")
            if attempt < 3:
                import time
                time.sleep(5 * attempt)
        except Exception as e:
            last_err = e
            log(f"[Pass 4b] attempt {attempt}/3 failed: {str(e)[:120]}", "WARN")
            if attempt < 3:
                import time
                time.sleep(5 * attempt)
    log(f"[Pass 4b] Gemini review failed after 3 attempts: {last_err} — continuing without it", "WARN")
    return None


def merge_gemini_into_judgment(article, gemini):
    """Merge Gemini scores into article.internal_judgment as nested 'gemini_review' field
    and compute a combined min_score (Anthropic min vs Gemini min — take overall min)."""
    if not gemini:
        return article
    j = article.setdefault("internal_judgment", {})
    j["gemini_review"] = gemini
    return article


def combined_min_score(article):
    """Min across all reviewer models — Anthropic 5 + Gemini 5 (only if Gemini succeeded).
    
    KEY: if Gemini missing/empty, its scores are NOT counted as 0 — they are excluded.
    Only valid integer scores enter the min."""
    DIMS = ("content_quality", "onpage_seo", "conversion_alignment",
            "ai_search_optimization", "eeat")
    j = article.get("internal_judgment", {}) or {}
    scores = []
    for k in DIMS:
        obj = j.get(k)
        if not isinstance(obj, dict): continue
        v = obj.get("score")
        if v is None: continue
        try: scores.append(int(v))
        except (TypeError, ValueError): pass
    gem = j.get("gemini_review")
    if isinstance(gem, dict):
        for k in DIMS:
            obj = gem.get(k)
            if not isinstance(obj, dict): continue
            v = obj.get("score")
            if v is None: continue
            try: scores.append(int(v))
            except (TypeError, ValueError): pass
    return min(scores) if scores else 0


def generate_full_article(*, topic, date, post_type, subtype, cta, hub_links=None,
                          extra_notes=None, target_score=10, max_perfection_passes=2):
    env = load_env()
    user_prompt = _build_user_prompt(date=date, topic=topic, post_type=post_type,
                                      subtype=subtype, cta=cta, hub_links=hub_links, extra_notes=extra_notes)
    draft = generate_draft(topic=topic, date=date, post_type=post_type,
                            subtype=subtype, cta=cta, hub_links=hub_links, extra_notes=extra_notes, env=env)
    critique = self_critique(draft, env)
    revised = revise(draft, critique, env, original_user_prompt=user_prompt)
    best = cross_review(revised, env, post_type=post_type)

    # Pass 4b: Gemini independent cross-validation (does not modify body)
    try:
        gemini = gemini_review(best, env)
        best = merge_gemini_into_judgment(best, gemini)
    except Exception as e:
        log(f"[Pass 4b] Gemini review error: {e} — continuing without", "WARN")

    from perfection import perfection_pass, min_score
    best_score = combined_min_score(best)
    log("\n--- after cross-review + Gemini, combined min: " + str(best_score) + "/10 ---")
    for i in range(max_perfection_passes):
        if best_score >= target_score:
            log("target " + str(target_score) + "/10 reached")
            break
        log("\n--- perfection iter " + str(i+1) + "/" + str(max_perfection_passes) + " ---")
        try:
            cand = perfection_pass(best, env, post_type=post_type)
            # Re-validate with Gemini after perfection
            try:
                gem2 = gemini_review(cand, env)
                cand = merge_gemini_into_judgment(cand, gem2)
            except Exception as e:
                log(f"[Pass 4b] post-perfection Gemini error: {e}", "WARN")
        except Exception as e:
            log("perfection failed: " + str(e), "WARN")
            break
        cs = combined_min_score(cand)
        if cs >= best_score:
            best = cand
            best_score = cs
            log("improved -> " + str(best_score) + "/10")
        else:
            log("score dropped (" + str(cs) + " < " + str(best_score) + ") - keep previous", "WARN")
            break
    log("\n=== final min score: " + str(best_score) + "/10 ===")
    return best
