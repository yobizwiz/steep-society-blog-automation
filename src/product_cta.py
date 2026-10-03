"""Exact, schedule-owned CTA verification. No generic product fallback or post-review insertion."""
from html_checks import cta_issues


def ensure_product_cta(gql, entry, cols, body_html, domain, log=print):
    handle = entry.get('cta_product')
    expected = domain.rstrip('/') + '/products/' + handle if handle else cols[entry['cta_collection']]['url']
    # A product mention elsewhere cannot satisfy the closing CTA. Ambiguity is held.
    ok = not cta_issues(body_html, expected)
    return body_html, handle, ok


def build_store_context(env, entry, cols, domain, blog_handle):
    # Inventory/specification/price claims come from dated primary evidence snapshots.
    # Do not present a heuristic catalog search or broad bestseller shelf as topic relevance.
    return None, None
