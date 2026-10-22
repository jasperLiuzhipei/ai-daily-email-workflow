"""Shared article extraction for verification and editorial material acquisition."""
import hashlib
import re
from urllib.parse import urljoin

from source_urls import x_status_identity

from bs4 import BeautifulSoup


def read_document(raw):
    soup = BeautifulSoup(raw, 'html.parser')
    title = soup.title.get_text(' ', strip=True) if soup.title else ''
    dates = [m.get('content') for m in soup.select('meta[property="article:published_time"]') if m.get('content')]
    urls = [e.get('href') or e.get('content') for e in
            soup.select('link[rel="canonical"], meta[property="og:url"]')]
    urls = [url for url in urls if url]
    og_type = soup.select_one('meta[property="og:type"]')
    for element in soup.select('script,style,nav,footer,header,noscript,svg'):
        element.decompose()
    body, kind = None, 'page'
    # Match the post's own permalink, not a quoted post or a longer reply.
    social = next((url for url in urls if x_status_identity(url)), None)
    if social:
        for article in soup.select('article'):
            if any(x_status_identity(urljoin(social, a['href'])) == x_status_identity(social)
                   and a.find_parent('article') is article
                   for a in article.select('a[href]')):
                body, kind = article, 'social_post'
                break
    # DEV's embedded repository README also has .entry-content; prefer its
    # actual article body. Keep GitHub's author-bio <article> out of selection.
    if body is None:
        body = soup.select_one('.post__content, [itemprop="articleBody"], .crayons-article__body')
        if body is None:
            body = soup.select_one('.entry-content')
        if body is not None:
            kind = 'explicit_article'
    if body is None:
        longest = max(soup.select('article'), key=lambda e: len(e.get_text()), default=None)
        if longest is not None and len(longest.get_text(' ', strip=True)) >= 1000 and not social:
            body, kind = longest, 'article'
        else:
            body = soup.select_one('main')
            kind = 'main' if body is not None else 'page'
            if body is None and longest is not None and not social:
                body, kind = longest, 'article'
            body = body if body is not None else soup
    text = re.sub(r'\s+', ' ', body.get_text(' ', strip=True))
    identity = {'declared_urls': urls, 'og_type': og_type.get('content', '') if og_type else '',
                'body_kind': kind, 'body_chars': len(text),
                'response_sha256': hashlib.sha256(raw.encode()).hexdigest()}
    return title, text, dates, identity


def readable(raw):
    return read_document(raw)[:3]
