"""Fail-closed publication protocol used by every supported entry point."""
from copy import deepcopy
from datetime import date, datetime, timezone
import json
import re
from html import escape
from urllib.parse import urlsplit
from utils import OUTPUT_DIR
from release_gate import ReviewRequired, digest, publication_lock, require_valid, save_candidate, write_json
from fact_review import review_facts
from html_checks import Document

BLOGS_QUERY = '''query Blogs($after: String) {
  blogs(first: 100, after: $after) { nodes { id handle } pageInfo { hasNextPage endCursor } }
}'''
ARTICLES_QUERY = '''query BlogArticles($id: ID!, $after: String) {
  blog(id: $id) { id handle articles(first: 100, after: $after) {
    nodes { id title handle publishedAt isPublished } pageInfo { hasNextPage endCursor }
  } }
}'''
STATE_QUERY = '''query ArticleState($id: ID!) {
  article(id: $id) { id body publishedAt isPublished blog { id handle } }
}'''
UPDATE_MUTATION = '''mutation UpdateArticle($id: ID!, $article: ArticleUpdateInput!) {
  articleUpdate(id: $id, article: $article) {
    article { id body publishedAt isPublished } userErrors { field message }
  }
}'''


def _shop():
    import shopify_pub
    return shopify_pub


def _next_page(connection, seen):
    if not isinstance(connection, dict) or not isinstance(connection.get('nodes'), list):
        raise RuntimeError('Incomplete Shopify connection')
    info = connection.get('pageInfo')
    if not isinstance(info, dict) or type(info.get('hasNextPage')) is not bool:
        raise RuntimeError('Missing pagination state')
    if not info['hasNextPage']: return None
    cursor = info.get('endCursor')
    if not cursor or cursor in seen: raise RuntimeError('Non-advancing pagination cursor')
    seen.add(cursor)
    return cursor


def get_blog_id(env, blog_handle):
    after, seen, matches = None, set(), []
    while True:
        conn = _shop()._gql(env, BLOGS_QUERY, {'after': after}).get('blogs')
        after = _next_page(conn, seen)
        matches.extend(n for n in conn['nodes'] if n.get('handle') == blog_handle)
        if after is None: break
    if len(matches) != 1: raise RuntimeError('Blog handle not found or ambiguous: ' + blog_handle)
    return matches[0]['id'].split('/')[-1]


def _articles(env, blog_id=None):
    blog_id = blog_id or get_blog_id(env, env['SHOPIFY_BLOG_HANDLE'])
    gid = 'gid://shopify/Blog/' + str(blog_id).split('/')[-1]
    after, seen = None, set()
    while True:
        blog = _shop()._gql(env, ARTICLES_QUERY, {'id': gid, 'after': after}).get('blog')
        if not blog or blog.get('id') != gid or blog.get('handle') != env['SHOPIFY_BLOG_HANDLE']:
            raise RuntimeError('Blog scope mismatch or missing blog')
        conn = blog.get('articles')
        after = _next_page(conn, seen)
        for node in conn['nodes']:
            if not all(k in node for k in ('id', 'handle', 'publishedAt', 'isPublished')):
                raise RuntimeError('Incomplete article lookup')
            yield node
        if after is None: break


def _instant(value):
    out = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if out.tzinfo is None: raise ValueError('Timestamp must include timezone')
    return out.astimezone(timezone.utc)


def find_article_by_publish_date(env, date_str):
    target = date.fromisoformat(date_str)
    matches = [n for n in _articles(env) if n.get('publishedAt') and _instant(n['publishedAt']).date() == target]
    if len(matches) > 1: raise ReviewRequired('Multiple articles occupy the requested blog/date')
    return matches[0] if matches else None


def _mutation_article(data):
    result = data.get('articleUpdate')
    if not result or result.get('userErrors') or not result.get('article'):
        raise RuntimeError('articleUpdate failed: ' + json.dumps(result, ensure_ascii=False))
    return result['article']


def sync_schema_images(body, featured_url):
    def patch(match):
        value = json.loads(match.group(2))
        def visit(node):
            if isinstance(node, list):
                for item in node: visit(item)
            elif isinstance(node, dict):
                typ = node.get('@type')
                types = typ if isinstance(typ, list) else [typ]
                if any(t in ('Article', 'BlogPosting', 'NewsArticle') for t in types): node['image'] = featured_url
                for item in node.values(): visit(item)
        visit(value)
        return match.group(1) + json.dumps(value, ensure_ascii=False).replace('</', '<\\/') + match.group(3)
    return re.sub(r'(<script\b[^>]*type=[\"\']application/ld\+json[\"\'][^>]*>)(.*?)(</script\s*>)', patch, body, flags=re.I|re.S)


def prepare_final(env, article, body, featured_url):
    final = deepcopy(article)
    report = {'status': 'review_required'}
    try:
        if hasattr(_shop(), '_apply_paragraph_spacing'): body = _shop()._apply_paragraph_spacing(body)
        if hasattr(_shop(), 'sanitize_body'): body = _shop().sanitize_body(env, body)
        body = sync_schema_images(body, featured_url)
        final.update(body_html=body, featured_image_url=featured_url)
        report['validation'] = require_valid(final, post_type=final.get('_release_context', {}).get('post_type', 'longtail'), final=True)
        # Every surviving internal destination is checked, including single-quoted links.
        for anchor in Document(body).nodes('a'):
            href = anchor.attrs.get('href', '')
            if urlsplit(href).hostname in (urlsplit(DOMAIN).hostname, env['SHOPIFY_STORE_URL']):
                status, _ = _shop()._http('HEAD', href, timeout=15)
                if status == 405:
                    status, _ = _shop()._http('GET', href, timeout=15)
                if not 200 <= status < 300:
                    raise ReviewRequired(f'Final internal link failed verification ({status}): {href}')
        report['facts'] = review_facts(final, env)
        report.update(status='passed', payload_sha256=digest(final))
        return final
    except Exception as exc:
        report['error'] = str(exc)
        raise
    finally: save_candidate(final, 'final', report)


def create_article(env, *, blog_id, article, featured_image_url, featured_image_alt,
                   body_html, publish_mode='draft', scheduled_at=None):
    if publish_mode not in ('draft', 'publish', 'scheduled'): raise ValueError('Invalid publish mode')
    if publish_mode == 'scheduled' and (not scheduled_at or _instant(scheduled_at) <= datetime.now(timezone.utc)):
        raise ValueError('A future timezone-aware schedule is required')
    if not featured_image_url.startswith('https://') or not featured_image_alt:
        raise ReviewRequired('Missing uploaded featured image or alt')
    final = prepare_final(env, article, body_html, featured_image_url)
    context = final.get('_release_context') or {}
    day = context.get('date') or (scheduled_at or '')[:10]
    date.fromisoformat(day)
    key = digest([env['SHOPIFY_STORE_URL'], env['SHOPIFY_BLOG_HANDLE'], day])
    receipt_path = OUTPUT_DIR / 'receipts' / (key + '.json')
    with publication_lock(env):
        if receipt_path.exists(): raise ReviewRequired('Prior publication attempt needs reconciliation: ' + str(receipt_path))
        # Recheck inside the lock immediately before creating. Scan handles including drafts.
        for node in _articles(env, blog_id):
            if node['handle'] == final['url_slug'] or (node['publishedAt'] and _instant(node['publishedAt']).date().isoformat() == day):
                raise ReviewRequired('Existing article/date/handle requires reconciliation: ' + node['id'])
        payload = {'title': final['title'], 'body_html': final['body_html'],
                   'summary_html': '<p>' + escape(final.get('summary', '')) + '</p>',
                   'handle': final['url_slug'], 'tags': ', '.join(final.get('tags', [])),
                   'author': env.get('BLOG_AUTHOR') or BRAND,
                   'image': {'src': featured_image_url, 'alt': featured_image_alt}, 'published': False,
                   'metafields': [{'namespace': 'global', 'key': k, 'value': final.get(v, ''), 'type': 'single_line_text_field'}
                                  for k, v in [('title_tag','meta_title'), ('description_tag','meta_description')]]}
        receipt = {'status': 'creating', 'date': day, 'payload_sha256': digest(payload)}
        write_json(receipt_path, receipt)  # Ambiguous timeout must not trigger a second POST.
        try:
            art = _shop()._api(env, f'blogs/{blog_id}/articles.json', method='POST', body={'article': payload})['article']
            receipt.update(status='created_draft', article_id=art['id'], handle=art.get('handle'))
            write_json(receipt_path, receipt)
            if art.get('handle') != final['url_slug']: raise ReviewRequired('Shopify changed the requested handle')
            if publish_mode != 'draft':
                fields = {'isPublished': publish_mode == 'publish'}
                if scheduled_at: fields['publishDate'] = scheduled_at
                gid = 'gid://shopify/Article/' + str(art['id'])
                _mutation_article(_shop()._gql(env, UPDATE_MUTATION, {'id': gid, 'article': fields}))
                readback = _shop()._gql(env, STATE_QUERY, {'id': gid}).get('article')
                if not readback or readback.get('body') != final['body_html']: raise RuntimeError('Final body readback mismatch')
                if publish_mode == 'scheduled':
                    if readback.get('isPublished') is not False or _instant(readback.get('publishedAt') or '') != _instant(scheduled_at):
                        raise RuntimeError('Schedule readback mismatch')
                elif readback.get('isPublished') is not True: raise RuntimeError('Publish readback mismatch')
                art['published_at'] = readback.get('publishedAt')
            else:
                # Draft creation must also read back the exact stored body and publication state.
                gid = 'gid://shopify/Article/' + str(art['id'])
                readback = _shop()._gql(env, STATE_QUERY, {'id': gid}).get('article')
                if not readback or readback.get('body') != final['body_html'] or readback.get('isPublished') is not False:
                    raise RuntimeError('Draft body/state readback mismatch')
            receipt.update(status='success', mode=publish_mode, scheduled_at=scheduled_at)
            write_json(receipt_path, receipt)
            article.update(final)
            return art
        except Exception as exc:
            receipt.update(status='reconciliation_required', error=str(exc))
            write_json(receipt_path, receipt)
            raise


def update_article_body(env, article_id, body_html, image=None, *, article=None):
    if article is None: raise ReviewRequired('Body updates require full article metadata and final validation')
    gid = 'gid://shopify/Article/' + str(article_id).split('/')[-1]
    with publication_lock(env):
        current = _shop()._gql(env, STATE_QUERY, {'id': gid}).get('article')
        if not current or current.get('blog', {}).get('handle') != env['SHOPIFY_BLOG_HANDLE']:
            raise RuntimeError('Cannot capture article publication state in target blog')
        final = prepare_final(env, article, body_html, (image or {}).get('src') or article.get('featured_image_url', ''))
        fields = {'body': final['body_html'], 'isPublished': current['isPublished']}
        if current.get('publishedAt'): fields['publishDate'] = current['publishedAt']
        if image: fields['image'] = {'url': image['src'], 'altText': image['alt']}
        _mutation_article(_shop()._gql(env, UPDATE_MUTATION, {'id': gid, 'article': fields}))
        saved = _shop()._gql(env, STATE_QUERY, {'id': gid}).get('article')
        if not saved or any(saved.get(k) != current.get(k) for k in ('publishedAt', 'isPublished')) or saved.get('body') != final['body_html']:
            raise RuntimeError('Article body/publication state readback mismatch; reconcile before retry')
        return saved


def public_url(article_handle, blog_handle=None, *, env=None):
    domain = DOMAIN
    if env:
        blog_handle = env['SHOPIFY_BLOG_HANDLE']
        if hasattr(_shop(), '_shop_info'): domain = 'https://' + _shop()._shop_info(env)['domain']
    if not blog_handle: blog_handle = BLOG_HANDLE
    return domain.rstrip('/') + '/blogs/' + blog_handle + '/' + article_handle


BRAND = 'Steep Society'
DOMAIN = 'https://steep-society.com'
BLOG_HANDLE = 'steep-society-journal'
