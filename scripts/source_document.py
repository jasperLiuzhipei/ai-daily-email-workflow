"""Shared article extraction for verification and editorial material acquisition."""
import re
from bs4 import BeautifulSoup

def readable(raw):
    soup = BeautifulSoup(raw, 'html.parser')
    title = soup.title.get_text(' ', strip=True) if soup.title else ''
    dates = [m.get('content') for m in soup.select('meta[property="article:published_time"]') if m.get('content')]
    for element in soup.select('script,style,nav,footer,header,noscript,svg'):
        element.decompose()
    # Some official blogs use <article> for short author bios/recommendations
    # while the actual story is a section inside <main> (e.g. GitHub Blog).
    body = soup.select_one('.post__content, .entry-content, [itemprop="articleBody"]')
    if body is None:
        articles = soup.select('article')
        longest = max(articles, key=lambda e: len(e.get_text()), default=None)
        body = longest if longest is not None and len(longest.get_text()) >= 1000 else soup.select_one('main')
        body = body if body is not None else longest if longest is not None else soup
    return title, re.sub(r'\s+', ' ', body.get_text(' ', strip=True)), dates

