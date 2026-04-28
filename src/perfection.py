"""Perfection push pass - separate file to avoid OneDrive sync truncation."""
import json
from utils import load_system_prompt, load_few_shot_articles, log


def min_score(article):
    j = article.get("internal_judgment", {}) or {}
    scores = [
        (j.get("content_quality") or {}).get("score", 0),
        (j.get("onpage_seo") or {}).get("score", 0),
        (j.get("conversion_alignment") or {}).get("score", 0),
    ]
    try:
        return min(int(s) for s in scores)
    except (TypeError, ValueError):
        return 0


PERFECTION_SYS = (
    "You are doing a final perfectionist pass on a Steep Society article. "
    "The previous draft scored below 10/10 on at least one dimension. Your job: "
    "identify EVERY remaining weakness - even minor ones - and produce a version "
    "that genuinely earns 10/10 on content quality, on-page SEO, and conversion "
    "alignment.\n\n"
    "Strict rules:\n"
    "- Do NOT inflate scores. Only return 10 if genuinely no improvement possible.\n"
    "- If you find a weakness, FIX it in the body, then update the score.\n"
    "- Maintain ALL hard rules (no h1, table max 5 data rows, single CTA after "
    "Quick Recap, absolute URLs, F/C notation, exact image counts).\n"
    "- Surgical edits only.\n"
    "- page_judgment excludes Shopify template-level deductions. Body and on-page "
    "should both be 10/10 if right.\n\n"
    "Return the SAME JSON schema with improvements applied."
)


def perfection_pass(article, env):
    from content import _build_few_shot_block, _claude_call, _extract_json, OUTPUT_SCHEMA_INSTRUCTION, _call_and_parse_with_retry
    log("[Pass 5] perfection (10/10 push)")
    sys_prompt = load_system_prompt()
    few_shot = _build_few_shot_block(load_few_shot_articles())
    full_system = sys_prompt + "\n\n" + few_shot + "\n\n" + OUTPUT_SCHEMA_INSTRUCTION + "\n\n" + PERFECTION_SYS
    user_msg = (
        "Polish until every dimension is genuinely 10/10. Return improved JSON.\n\n"
        "```json\n" + json.dumps(article, ensure_ascii=False, indent=2) + "\n```"
    )
    review_model = env.get("ANTHROPIC_REVIEW_MODEL", env["ANTHROPIC_MODEL"])
    def _call():
        return _claude_call(
            api_key=env["ANTHROPIC_API_KEY"],
            model=review_model,
            system=full_system,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=12000,
            temperature=0.3,
        )
    out = _call_and_parse_with_retry(label="[Pass 5]", max_attempts=3, call_fn=_call)
    log("[Pass 5] done - min: " + str(min_score(out)) + "/10")
    return out
