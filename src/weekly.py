"""Weekly runner: every terminal status is explicit and failures set a failing exit code."""
import argparse
from datetime import date as Date, timedelta
import json
import sys
from utils import CONFIG_DIR, OUTPUT_DIR, ensure_dirs, load_env, load_yaml, log
from content import generate_full_article
from images import generate_image_for_slot
from shopify_pub import (get_blog_id, find_article_by_publish_date, upload_image, insert_body_images,
                         create_article, update_article_body, admin_url, public_url)
from safe_publish import DOMAIN
from release_gate import ReviewRequired, require_valid, save_candidate, strict_min_score, write_json, configured_image_model
from fact_review import review_facts


def choose_cta(entry, cols):
    if entry.get('cta_product'):
        return {'title': entry.get('cta_label') or 'View product details',
                'handle': entry['cta_product'], 'kind': 'product',
                'url': DOMAIN + '/products/' + entry['cta_product']}
    if entry.get('cta_collection') not in cols: raise ReviewRequired('Missing scheduled CTA collection')
    return cols[entry['cta_collection']]


def prepare_article(date, env, entry, cols, *, use_existing=True):
    path = OUTPUT_DIR / f'{date}-article.json'
    cta = choose_cta(entry, cols)
    if use_existing and path.exists(): article = json.loads(path.read_text(encoding='utf-8'))
    else:
        article = generate_full_article(topic=entry['title'], date=date,
            post_type=entry.get('type', 'longtail'), subtype=entry.get('subtype'), cta=cta)
    # Set trusted schedule context after generation; never accept a model-supplied destination.
    article['_release_context'] = {'date': date, 'post_type': entry.get('type', 'longtail'),
                                   'cta_url': cta['url'], 'topic': entry['title']}
    report = {'status': 'review_required'}
    try:
        report['validation'] = require_valid(article, post_type=entry.get('type', 'longtail'))
        report['facts'] = review_facts(article, env)
        report['status'] = 'passed'
        return article
    except Exception as exc:
        report['error'] = str(exc)
        raise
    finally:
        write_json(path, article)
        save_candidate(article, 'generated', report)


def publish_prepared(date, env, entry, cols, article, *, existing=None,
                     publish_mode='scheduled', scheduled_at=None):
    model = configured_image_model(env)
    generated = []
    for im in article['images']:
        result = generate_image_for_slot(prompt=im['prompt'], filename_base=im['filename'],
            api_key=env['GOOGLE_API_KEY'], model=model,
            variants=int(env.get('IMAGE_VARIANTS_PER_SLOT') or '1'), aspect_ratio='16:9',
            anthropic_key=env['ANTHROPIC_API_KEY'], max_vision_retries=2)
        if result.get('vision_verified') is not True: raise ReviewRequired('Image not verified')
        (OUTPUT_DIR / f"{date}-{result['filename']}").write_bytes(result['webp_bytes'])
        generated.append({**im, **result})
    # Do not upload any image until every image passed.
    uploaded = []
    for im in generated:
        url = upload_image(env, webp_bytes=im['webp_bytes'], filename=im['filename'], alt=im['alt'])
        uploaded.append({'url': url, 'filename': im['filename'], 'alt': im['alt'], 'role': im['role'],
                         'vision_verified': True})
    article['uploaded_images'] = uploaded
    featured = next(im for im in uploaded if im['role'] == 'featured')
    body = insert_body_images(article['body_html'], [im for im in uploaded if im['role'] == 'body'])
    if existing:
        created = update_article_body(env, existing['id'], body,
                    image={'src': featured['url'], 'alt': featured['alt']}, article=article)
        created = {**created, 'handle': existing['handle']}
    else:
        created = create_article(env, blog_id=get_blog_id(env, env['SHOPIFY_BLOG_HANDLE']), article=article,
            body_html=body, featured_image_url=featured['url'], featured_image_alt=featured['alt'],
            publish_mode=publish_mode, scheduled_at=scheduled_at)
    return created


def process_one_day(date, env, sched, cols, targeting=None, force=False):
    summary = {'date': date, 'status': 'pending', 'error': None}
    if date not in sched:
        return {**summary, 'status': 'missing_schedule', 'error': f'No schedule entry for {date}'}
    entry = sched[date]
    summary.update(title=entry['title'], type=entry.get('type', 'longtail'))
    try:
        existing = find_article_by_publish_date(env, date)  # Lookup failure cannot mean absence.
        if existing and not force:
            return {**summary, 'status': 'already_exists', 'article_id': existing['id'], 'handle': existing['handle']}
        article = prepare_article(date, env, entry, cols, use_existing=not force)
        summary['score'] = strict_min_score(article)
        scheduled = f'{date}T07:00:00Z'
        created = publish_prepared(date, env, entry, cols, article, existing=existing if force else None,
                                   scheduled_at=scheduled)
        summary.update(status='success', article_id=created['id'], handle=created.get('handle'),
                       scheduled_at=created.get('published_at') or created.get('publishedAt'),
                       admin_url=admin_url(env, str(created['id']).split('/')[-1]),
                       public_url=public_url(created.get('handle', ''), env=env))
    except ReviewRequired as exc:
        summary.update(status='review_required', error=str(exc))
    except Exception as exc:
        summary.update(status='failed', error=str(exc))
    log(f"{date}: {summary['status']}")
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start')
    parser.add_argument('--days', type=int, default=7)
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.days <= 31: parser.error('--days must be between 1 and 31')
    today = Date.today()
    start = Date.fromisoformat(args.start) if args.start else today + timedelta(days=(7-today.weekday()) % 7 or 7)
    ensure_dirs()
    env, sched, cols = load_env(), load_yaml(CONFIG_DIR/'schedule.yaml'), load_yaml(CONFIG_DIR/'collections.yaml')
    summaries = [process_one_day((start+timedelta(days=i)).isoformat(), env, sched, cols, force=args.force)
                 for i in range(args.days)]
    write_json(OUTPUT_DIR / f'weekly-batch-{today.isoformat()}.json',
               {'start': start.isoformat(), 'days': args.days, 'summaries': summaries})
    return 0 if all(s['status'] in ('success', 'already_exists') for s in summaries) else 1


if __name__ == '__main__': sys.exit(main())
