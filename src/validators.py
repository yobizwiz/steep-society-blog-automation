"""Deterministic checks on generated and final HTML."""
import json
import re
from urllib.parse import urlsplit
from html_checks import Document, cta_issues


def _cta_blocks(html): return len(Document(html).ctas())


def validate(article, *, post_type='longtail', final=False):
    body = article.get('body_html') or ''
    doc = Document(body)
    violations, warnings = [], []
    def fail(rule, detail): violations.append({'rule': rule, 'detail': detail})
    if not body.strip(): fail('body', 'Missing body_html')
    if doc.nodes('h1'): fail('no_h1', 'Body contains H1')
    for table in doc.nodes('table'):
        if sum(n.tag == 'tr' for n in table.walk()) > 6: fail('table_rows', 'More than five data rows plus header')
    violations.extend(cta_issues(body, (article.get('_release_context') or {}).get('cta_url')))
    for a in doc.nodes('a'):
        href = a.attrs.get('href', '')
        p = urlsplit(href)
        if not href.startswith(('#', 'mailto:')) and not (p.scheme in ('http', 'https') and p.hostname):
            fail('absolute_urls', href)
    images = article.get('images') or []
    count = sum(im.get('role') == 'body' for im in images)
    expected = 4 if post_type == 'hub' else 3
    if len(images) != expected or sum(im.get('role') == 'featured' for im in images) != 1:
        fail('image_count', f'Expected one featured and {expected-1} body images')
    if any(im.get('role') not in ('featured', 'body') or not im.get('alt') for im in images):
        fail('image_metadata', 'All images need role and descriptive alt text')
    markers = re.findall(r'<!--\s*IMG:body-(\d+)\s*-->', body, re.I)
    if final:
        if markers or len(doc.nodes('img')) != count: fail('final_images', 'Missing images or remaining placeholders')
        uploaded = article.get('uploaded_images') or []
        if len(uploaded) != len(images) or any(im.get('vision_verified') is not True for im in uploaded):
            fail('image_verification', 'Final images require verified upload records for this article')
        uploaded_body = [im.get('url') for im in uploaded if im.get('role') == 'body']
        if [im.attrs.get('src') for im in doc.nodes('img')] != uploaded_body:
            fail('image_upload_binding', 'Final body images differ from verified upload records')
        featured = [im.get('url') for im in uploaded if im.get('role') == 'featured']
        if featured != [article.get('featured_image_url')]:
            fail('featured_upload_binding', 'Featured image differs from verified upload record')
        for im in doc.nodes('img'):
            if not im.attrs.get('alt') or not im.attrs.get('src', '').startswith('https://'):
                fail('final_image_attributes', 'HTTPS src and alt required')
    elif sorted(markers) != [str(i) for i in range(1, count+1)]:
        fail('image_placeholders', 'Exactly one numbered placeholder per body image required')
    measured = {k: len(article.get(k) or '') for k in ('meta_title', 'meta_description')}
    for key, (low, high) in {'meta_title': (35, 65), 'meta_description': (130, 165)}.items():
        if not measured[key]: fail(key, 'Missing metadata')
        elif not low <= measured[key] <= high:
            warnings.append({'rule': key+'_length', 'detail': f'Actual length {measured[key]} (editorial target {low}-{high})'})
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', article.get('url_slug', '')):
        fail('url_slug_format', 'Missing or invalid slug')
    def visit(value):
        if isinstance(value, list):
            for item in value: visit(item)
        elif isinstance(value, dict):
            typ = value.get('@type')
            types = typ if isinstance(typ, list) else [typ]
            if final and any(t in ('Article', 'BlogPosting', 'NewsArticle') for t in types):
                if value.get('image') != article.get('featured_image_url'):
                    fail('schema_image', 'Article schema image differs from uploaded featured image')
            for item in value.values(): visit(item)
    for script in doc.nodes('script'):
        if script.attrs.get('type') != 'application/ld+json': fail('script', 'Only JSON-LD scripts allowed')
        else:
            try: visit(json.loads(script.text))
            except (ValueError, TypeError): fail('json_ld', 'Malformed JSON-LD')
    return {'ok': not violations, 'violations': violations, 'warnings': warnings, 'measured': measured}
