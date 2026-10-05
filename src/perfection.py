"""Improve supported content while preserving the trusted scheduled CTA."""
import json
from urllib.parse import urlsplit
from utils import load_system_prompt, load_few_shot_articles, log
from release_gate import ReviewRequired, strict_min_score
from writer_evidence import ground_writer_prompt


PERFECTION_SYS = """Improve only supported content and structural issues. Remove unsupported assertions instead of adding measurements, experience, rankings, prices or safety claims to raise scores. Score all five dimensions honestly and explain unresolved weaknesses. Return the same article JSON schema. The single closing CTA follows Quick Recap, has exactly one link, and uses the supplied scheduled destination. Never substitute a best-selling product or another collection. If the destination is unsuitable, report an unresolved conversion weakness; do not invent a replacement."""


def perfection_pass(article, env, post_type=None, *, cta=None):
    from content import _build_few_shot_block, _claude_call, _extract_json, OUTPUT_SCHEMA_INSTRUCTION
    if not isinstance(cta, dict) or not isinstance(cta.get('url'), str):
        raise ReviewRequired('Editorial improvement requires the trusted scheduled CTA')
    url = urlsplit(cta['url'])
    if url.scheme != 'https' or not url.hostname or url.username or url.password:
        raise ReviewRequired('Scheduled CTA must be an absolute HTTPS URL')
    system = (load_system_prompt() + '\n\n' + _build_few_shot_block(load_few_shot_articles())
              + '\n\n' + OUTPUT_SCHEMA_INSTRUCTION + '\n\n' + PERFECTION_SYS)
    user = json.dumps({'scheduled_cta': cta, 'article': article}, ensure_ascii=False)
    system, user = ground_writer_prompt(system, user)
    out = _extract_json(_claude_call(api_key=env['ANTHROPIC_API_KEY'],
        model=(env.get('ANTHROPIC_REVIEW_MODEL') or env['ANTHROPIC_MODEL']) if post_type == 'hub' else env['ANTHROPIC_MODEL'],
        system=system, messages=[{'role':'user', 'content':user}], max_tokens=14000, temperature=0.3))
    if article.get('generation_review_errors'):
        out['generation_review_errors'] = article['generation_review_errors']
    log('[Editorial improvement] minimum score: ' + str(min_score(out)))
    return out


def min_score(article):
    return strict_min_score(article)
