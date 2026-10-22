"""Conservative feed-link cleanup and site-specific document identities.

These identities verify fetched pages, not global deduplication: explicit paper
versions remain distinct and no alternate article URLs are invented.
"""
import html
import re
from urllib.parse import urlsplit


def clean_source_url(value):
    # Only complete entities are markup. HTML's permissive decoding of a bare
    # "&amp" prefix would corrupt legitimate query keys such as "amplitude".
    value = re.sub(r'&(?:#[0-9]+|#x[0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]+);',
                   lambda m: html.unescape(m[0]), str(value or '')).strip()
    # Full-width closing punctuation marks prose appended by a feed. Leave
    # percent-encoded punctuation, Unicode paths and ASCII query strings alone.
    return re.split(r'[）】》」』，。；、]', value, maxsplit=1)[0]


def arxiv_identity(url):
    p = urlsplit(url)
    if p.scheme not in {'https', 'http'} or p.netloc.lower() not in {'arxiv.org', 'www.arxiv.org'}:
        return None
    match = re.fullmatch(r'/abs/(\d{4}\.\d{4,5})(v[1-9]\d*)?/?', p.path)
    return (match[1], match[2] or '') if match else None


def x_status_identity(url):
    p = urlsplit(url)
    if p.scheme not in {'https', 'http'} or p.netloc.lower() not in {
            'x.com', 'www.x.com', 'twitter.com', 'www.twitter.com'}:
        return None
    match = re.fullmatch(r'/(?:i/web|[A-Za-z0-9_]+)/status/(\d+)/?', p.path)
    return match[1] if match else None


def conflicting_document_identity(requested, final, declared):
    """A similar headline cannot override an explicit paper/post mismatch."""
    paper, actual = arxiv_identity(requested), arxiv_identity(final)
    if paper and actual:
        ids = [arxiv_identity(url) for url in declared]
        if (paper[0] != actual[0] or paper[1] and paper[1] != actual[1]
                or any(not d or d[0] != actual[0] or d[1] and actual[1] and d[1] != actual[1] for d in ids)
                or len({d[1] for d in ids if d and d[1]}) > 1):
            return True
    post, actual_post = x_status_identity(requested), x_status_identity(final)
    return bool(post and actual_post and (post != actual_post
        or any(x_status_identity(url) != actual_post for url in declared)))


def matching_special_document(requested, final, declared, identity, text):
    """Require the response's own identity and body, even for translated leads."""
    paper = arxiv_identity(requested)
    if paper:
        actual = arxiv_identity(final)
        declarations = [arxiv_identity(url) for url in declared]
        # An explicit requested version must survive the redirect. Canonical
        # unversioned declarations are allowed, but conflicting versions aren't.
        if (actual and actual[0] == paper[0] and (not paper[1] or actual[1] == paper[1])
                and declarations and all(d and d[0] == actual[0]
                    and (not d[1] or not actual[1] or d[1] == actual[1]) for d in declarations)
                and len({d[1] for d in declarations if d and d[1]}) <= 1
                and identity.get('body_kind') in {'main', 'article', 'explicit_article'}):
            version = actual[1] or next((d[1] for d in declarations if d[1]), '')
            marker = re.search(r'\barXiv:' + re.escape(actual[0] + version) + r'(?=\s|\(|$)', text)
            abstract = re.search(r'\bAbstract:\s*(.+?)(?:\s+(?:Comments|Subjects|Cite as):|$)', text)
            if marker and abstract and len(abstract[1].strip()) >= 200:
                return 'matching_arxiv_identity'
    post = x_status_identity(requested)
    if (post and x_status_identity(final) == post and declared
            and all(x_status_identity(url) == post for url in declared)
            and identity.get('og_type') == 'article'
            and identity.get('body_kind') == 'social_post'):
        return 'matching_social_post_identity'
    return ''
