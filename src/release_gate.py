"""Deterministic gates and persistent artifacts. No import-time IO or network."""
import hashlib
import json
import re
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from utils import OUTPUT_DIR


class ReviewRequired(RuntimeError):
    pass


DIMENSIONS = ('content_quality', 'onpage_seo', 'conversion_alignment', 'ai_search_optimization', 'eeat')
MIN_SCORE = 8


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def strict_min_score(article):
    judgment = article.get('internal_judgment') or {}
    scores = []
    reviews = [judgment]
    if 'gemini_review' in judgment: reviews.append(judgment['gemini_review'])
    for review in reviews:
        if not isinstance(review, dict): return 0
        for dim in DIMENSIONS:
            item = review.get(dim)
            if not isinstance(item, dict): return 0
            score = item.get('score')
            if type(score) not in (int, float) or not 0 <= score <= 10: return 0
            scores.append(score)
    return min(scores)


def require_valid(article, *, post_type='longtail', final=False):
    if final:
        require_release_context(article)
    from validators import validate
    result = validate(article, post_type=post_type, final=final)
    if strict_min_score(article) < MIN_SCORE:
        result['violations'].append({'rule': 'editorial_score', 'detail': 'All five dimensions must be numeric and >= 8; scores are not factual evidence'})
    if article.get('generation_review_errors'):
        result['violations'].append({'rule': 'review_error', 'detail': article['generation_review_errors']})
    result['ok'] = not result['violations']
    if not result['ok']: raise ReviewRequired(json.dumps(result, ensure_ascii=False))
    return result


def require_release_context(article):
    context = article.get('_release_context')
    if not isinstance(context, dict):
        raise ReviewRequired('release_context: trusted schedule context is required')
    try:
        day = context['date']
        if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
            raise ValueError('Invalid date')
        destination = context['cta_url']
        if not isinstance(destination, str): raise ValueError('Invalid CTA URL')
        url = urlsplit(destination)
        if (url.scheme != 'https' or not url.hostname
                or url.username or url.password or any(c.isspace() for c in destination)):
            raise ValueError('Invalid CTA URL')
    except (KeyError, TypeError, ValueError) as exc:
        raise ReviewRequired('release_context: ISO date and absolute HTTPS CTA URL are required') from exc
    return context


def block_legacy_write():
    raise ReviewRequired('Legacy maintenance is disabled before API calls; migrate through the complete scheduled-article review pipeline first')


def configured_image_model(env):
    model = env.get('IMAGEN_MODEL')
    if not isinstance(model, str) or not model.strip():
        raise ReviewRequired('Configure IMAGEN_MODEL explicitly before generating images')
    return model.strip()


def save_candidate(article, stage, report):
    slug = article.get('url_slug', '')
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', slug): slug = 'invalid-' + digest(article)[:12]
    write_json(OUTPUT_DIR / f'{slug}-{stage}.json', article)
    write_json(OUTPUT_DIR / f'{slug}-{stage}.check.json', report)
    (OUTPUT_DIR / f'{slug}-{stage}.html').write_text(article.get('body_html', ''), encoding='utf-8')


@contextmanager
def publication_lock(env):
    scope = digest([env['SHOPIFY_STORE_URL'], env['SHOPIFY_BLOG_HANDLE']])[:24]
    path = OUTPUT_DIR / 'locks' / (scope + '.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    try: f = path.open('x', encoding='utf-8')
    except FileExistsError as exc:
        raise ReviewRequired(f'Active or stale publication lock: {path}') from exc
    try:
        with f: f.write(datetime.now(timezone.utc).isoformat())
        yield
    finally: path.unlink()
