"""Perfection push pass - separate file to avoid OneDrive sync truncation."""
import json
from utils import load_system_prompt, load_few_shot_articles, log


def min_score(article):
    """Min score across 5 dimensions: content / SEO / conversion / AISO / E-E-A-T."""
    j = article.get("internal_judgment", {}) or {}
    scores = [
        (j.get("content_quality") or {}).get("score", 0),
        (j.get("onpage_seo") or {}).get("score", 0),
        (j.get("conversion_alignment") or {}).get("score", 0),
        (j.get("ai_search_optimization") or {}).get("score", 0),
        (j.get("eeat") or {}).get("score", 0),
    ]
    try:
        return min(int(s) for s in scores)
    except (TypeError, ValueError):
        return 0


PERFECTION_SYS = (
    "You are doing a final perfectionist pass on an article. The previous draft scored "
    "below 10/10 on at least one of FIVE dimensions: content_quality, onpage_seo, "
    "conversion_alignment, ai_search_optimization (AISO), eeat.\n\n"
    "CRITICAL — SCORE PRESERVATION RULES:\n"
    "1. The article ALREADY has strengths in some dimensions. Do NOT weaken those.\n"
    "2. Identify which dimension(s) scored below 10 from internal_judgment.\n"
    "3. ONLY surgically edit the parts that address those specific weaknesses.\n"
    "4. Make MINIMAL changes elsewhere — preserve good sentences verbatim.\n"
    "5. NEVER remove content that earned a high score (e.g., don't drop FAQ if onpage_seo was 10).\n\n"
    "Specific guidance per low-scoring dimension:\n"
    "- content_quality < 10: Add specific data/numbers/research citations. Remove fluff. Add original first-person insight (e.g., '30-day test' framing).\n"
    "- onpage_seo < 10: Fix meta title length (50-60), meta description (140-160), missing JSON-LD (FAQPage + Article), table > 5 rows trim, missing primary keyword in title/slug/meta/intro.\n"
    "- conversion_alignment < 10: Move Quick Answer into first 2-3 paragraphs. Ensure single CTA below Quick Recap with collection-name-1:1 button. Remove orphan product mentions (link or remove).\n"
    "- ai_search_optimization < 10: Rewrite as single-fact atomic sentences with numbers (temperatures, ratios, times). Add anchor IDs to H2s. Inline FAQPage + Article JSON-LD.\n"
    "- eeat < 10: Add specific personal experience signals ('I tested X for 30 days', '5 different beans compared'). Use exact data (200°F, 3-5 min, 1:16 ratio). Cite study/journal names when relevant. Consistent brand voice.\n\n"
    "Strict rules:\n"
    "- Do NOT inflate scores. Only return 10 if genuinely no improvement possible.\n"
    "- Maintain ALL hard rules (no h1, table max 5 data rows, single CTA after Quick Recap, absolute URLs, F/C notation, exact image counts).\n"
    "- page_judgment excludes Shopify template-level deductions.\n\n"
    "Return the SAME JSON schema with improvements applied. "
    "If you genuinely cannot improve a dimension further, KEEP the existing body for that section verbatim."
)


def _format_collections_context(env):
    """Load collections.yaml and format as bulleted list for LLM context."""
    try:
        from utils import load_yaml, CONFIG_DIR
        cols = load_yaml(CONFIG_DIR / "collections.yaml")
        lines = []
        for handle, info in cols.items():
            title = info.get("title", handle)
            url = info.get("url", "")
            lines.append(f"- {title} → {url} (handle: {handle})")
        return "\n".join(lines)
    except Exception as e:
        return "(collections unavailable: " + str(e) + ")"


def perfection_pass(article, env):
    from content import _build_few_shot_block, _claude_call, _extract_json, OUTPUT_SCHEMA_INSTRUCTION, _call_and_parse_with_retry
    log("[Pass 5] perfection (10/10 push)")
    sys_prompt = load_system_prompt()
    few_shot = _build_few_shot_block(load_few_shot_articles())
    collections_ctx = _format_collections_context(env)
    
    cta_addendum = (
        "\n\n## CRITICAL — IF MISSING, ADD THESE STRUCTURAL ELEMENTS:\n\n"
        "If the article body lacks any of these required elements, ADD them surgically. "
        "Many older articles have no CTA/Quick Recap — your job is to add them based on the article's topic.\n\n"
        "1. **Quick Recap section** (right before CTA) — bulleted list of 3-5 key takeaways from the article.\n"
        "2. **Single CTA block** (immediately after Quick Recap, nothing below) — pick the BEST-matching collection from the list below based on the article's topic.\n\n"
        "### Available collections (USE EXACTLY ONE — title text + url must match 1:1):\n"
        + collections_ctx + "\n\n"
        "### CTA HTML template (use EXACTLY this format):\n"
        '<div style="border: 1px solid #ded6c8; padding: 22px; margin: 32px 0 0; border-radius: 14px; background: #faf7f1;">\n'
        '  <p style="margin: 0 0 8px; font-size: 18px; line-height: 1.4;"><strong>CTA headline.</strong></p>\n'
        '  <p style="margin: 0 0 16px; line-height: 1.6;">CTA support sentence (1 sentence).</p>\n'
        '  <p style="margin: 0;">\n'
        '    <a href="COLLECTION_URL_FROM_LIST_ABOVE" style="display: inline-block; padding: 11px 18px; border-radius: 999px; background: #2b2118; color: #ffffff; text-decoration: none; font-weight: 600;">EXACT_COLLECTION_TITLE_FROM_LIST</a>\n'
        '  </p>\n'
        '</div>\n\n'
        "Critical rules:\n"
        "- The button text MUST match the collection title 1:1 (no paraphrasing).\n"
        "- The href MUST be the exact URL from the list above.\n"
        "- Remove any content that appears AFTER the CTA block (CTA must be the last element).\n"
        "- If Quick Recap exists but is in wrong position, move it to right before CTA.\n"
    )
    
    full_system = sys_prompt + "\n\n" + few_shot + "\n\n" + OUTPUT_SCHEMA_INSTRUCTION + "\n\n" + PERFECTION_SYS + cta_addendum
    user_msg = (
        "Polish until every dimension is genuinely 10/10. If Quick Recap or CTA is missing, ADD them using a collection from the available list. Return improved JSON.\n\n"
        "```json\n" + json.dumps(article, ensure_ascii=False, indent=2) + "\n```"
    )
    review_model = env.get("ANTHROPIC_REVIEW_MODEL", env["ANTHROPIC_MODEL"])
    def _call():
        return _claude_call(
            api_key=env["ANTHROPIC_API_KEY"],
            model=review_model,
            system=full_system,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=14000,
            temperature=0.3,
        )
    out = _call_and_parse_with_retry(label="[Pass 5]", max_attempts=3, call_fn=_call)
    log("[Pass 5] done - min: " + str(min_score(out)) + "/10")
    return out
