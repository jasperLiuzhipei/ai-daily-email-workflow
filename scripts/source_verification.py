"""Bounded primary reading BEFORE final selection; fetch failures remain visible.

An official hostname is a lead, never evidence that the page was read. Records
bind actual response excerpts to a URL and hash; this is not fact validation.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urldefrag

from bs4 import BeautifulSoup
from fetch_selected_source_material import fetch_html, safe_public_http_url
from model_update_priority import subject_prior
from source_document import readable


def host(url):
    return (urlsplit(str(url)).hostname or '').lower().removeprefix('www.')


def high_value_reasons(candidate, policy, model_policy):
    title = str(candidate.get('title', ''))
    text = title + ' ' + str(candidate.get('description') or candidate.get('source_excerpt', ''))
    reasons = []
    tier, _ = subject_prior(title, model_policy)
    if (candidate.get('source_type') == 'model_release' or candidate.get('selection_group') == 'model_release') and tier == 'established':
        if not re.search(r'补丁|小版本|bug.?fix|minor update', title, re.I):
            reasons.append('important_model_update')
    if host(candidate.get('primary_url', '')) in policy.get('engineering_hosts', []) and re.search(
        r'agent|copilot|runtime|migration|migrat|evaluation|inference|harness|智能体|迁移|评估|推理|架构|工程|复盘', text, re.I
    ):
        reasons.append('official_engineering_practice')
    if candidate.get('aihot_daily_featured') and (candidate.get('aihot_daily_rank') or 999) <= policy.get('featured_rank_max', 15):
        reasons.append('aggregator_featured')
    if (candidate.get('bestblogs_score') or 0) >= policy.get('bestblogs_featured_min_score', 90):
        if 'aggregator_featured' not in reasons:
            reasons.append('aggregator_featured')
    if candidate.get('timing_review_required'):
        reasons.append('date_only_window_overlap')
    return reasons




def relevant(candidate, page_title, text):
    # A shell, error page or unrelated redirected article is not primary proof.
    if len(text) < 300 or re.search(r'captcha|access denied|verify you are human|just a moment', (page_title + ' ' + text[:250]).lower()):
        return False
    tokens = re.findall(r'[a-z][a-z0-9.-]{3,}', candidate.get('title', '').lower())
    tokens = set(t for t in tokens if t not in {'with', 'from', 'using', 'into', 'that', 'this', 'the', 'release', 'introducing', 'model', 'models', 'available', 'update'})
    if tokens:
        return sum(t in (page_title + ' ' + text).lower() for t in tokens) >= min(2, len(tokens))
    chars = set(re.findall(r'[\u4e00-\u9fff]', candidate.get('title', '')))
    return len(chars & set(text)) >= min(5, len(chars)) and bool(chars)


def verify_candidate(candidate, *, policy, output_dir, fetcher=fetch_html):
    c = dict(candidate)
    original_url = c.get('primary_url') or c.get('source_url', '')
    allowed = set(policy.get('official_hosts', []))
    record = {'status': 'pending', 'requested_url': original_url, 'attempts': [],
              'checked_at': datetime.now(timezone.utc).isoformat()}
    c['primary_verification'] = record
    queue = [original_url]
    # Follow only links actually present in the lead. Never invent article URLs.
    queue.extend(re.findall(r'https?://[^\s<>"\)]+', str(c.get('description', ''))))
    visited = set()
    while queue and len(visited) < 3:
        url = urldefrag(queue.pop(0).rstrip('.,;'))[0]
        if url in visited or not safe_public_http_url(url):
            continue
        if visited and host(url) not in allowed:
            continue
        visited.add(url)
        attempt = {'url': url}
        record['attempts'].append(attempt)
        try:
            raw, final_url, status = fetcher(url, timeout_seconds=policy.get('timeout_seconds', 12), max_bytes=policy.get('max_bytes', 1500000))
            attempt.update({'http_status': status, 'resolved_url': final_url})
            if not safe_public_http_url(final_url) or status != 200:
                raise ValueError('unsafe redirect or unsuccessful HTTP status')
            if host(url) == 'qwen.ai' and urlsplit(url).path.startswith('/api/v2/article/'):
                payload = json.loads(raw)
                data = payload.get('data') or {}
                expected = parse_qs(urlsplit(url).query).get('path', [''])[0]
                if payload.get('success') is not True or data.get('path') != expected:
                    raise ValueError('Qwen API article identity mismatch')
                raw = data.get('content', '')
                if data.get('extra', {}).get('date'):
                    record['api_published_at'] = data['extra']['date']
            title, body, dates = readable(raw)
            attempt['readable_chars'] = len(body)
            if host(final_url) in allowed and relevant(c, title, body):
                digest = hashlib.sha256(raw.encode()).hexdigest()
                path = Path(output_dir) / (digest + '.html')
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(raw)
                record.update({'status': 'verified', 'evidence_url': final_url,
                               'page_title': title, 'response_sha256': digest,
                               'response_path': str(path), 'html_published_at': dates})
                if record.get('api_published_at') and dates and record['api_published_at'] not in dates:
                    record['date_conflict'] = True
                c['discovery_excerpt'] = c.get('description') or c.get('source_excerpt', '')
                c['source_text'] = body[:policy.get('max_chars', 16000)]
                c['description'] = c['source_text']
                c['source_quality_tier'] = 'primary'
                c['source_authority'] = 'official_primary_read'
                # The public article stays the editorial link for API adapters.
                if '/api/v2/article/' not in final_url:
                    c['primary_url'] = final_url
                break
            attempt['result'] = 'not_readable_primary_article'
            if host(final_url) == 'qwen.ai' and urlsplit(final_url).path == '/blog':
                slug = parse_qs(urlsplit(final_url).query).get('id', [''])[0]
                if re.fullmatch(r'[a-zA-Z0-9._-]+', slug):
                    # Public GET route observed in Qwen's own blog frontend.
                    queue.insert(0, 'https://qwen.ai/api/v2/article/?' + urlencode({'language': 'en-US', 'path': slug, 'type': 'qwen_ai'}))
            soup = BeautifulSoup(raw, 'html.parser')
            for a in soup.select('a[href]'):
                link = urljoin(final_url, a['href'])
                if host(link) in allowed and urlsplit(link).path not in {'', '/'}:
                    queue.append(link)
        except Exception as exc:
            attempt['error'] = str(exc)[:400]
    if record['status'] != 'verified':
        record['status'] = 'unresolved'
        record['reason'] = 'No readable matching first-party article obtained; this is a retrieval limitation, not a finding that official information does not exist.'
    return c


def enrich_high_value_candidates(candidates, *, policy, model_policy, output_dir, rescore, fetcher=fetch_html):
    if not policy.get('enabled', True):
        return candidates
    result = list(candidates)
    targets = []
    for i, c in enumerate(result):
        c['high_value_reasons'] = high_value_reasons(c, policy, model_policy)
        if c['high_value_reasons']:
            targets.append(i)
    # Retrieval scheduling must not depend on historical content scores.
    targets.sort(key=lambda i: (result[i].get('aihot_daily_rank') or 999,
                               str(result[i].get('primary_url') or '')))
    limit = policy.get('max_candidates', 24)
    for i in targets[limit:]:
        result[i]['primary_verification'] = {'status': 'not_attempted_budget', 'reason': 'Bounded retrieval budget reached; requires coverage review.'}
    def work(i):
        return i, verify_candidate(result[i], policy=policy, output_dir=output_dir, fetcher=fetcher)
    with ThreadPoolExecutor(max_workers=policy.get('workers', 4)) as pool:
        for i, updated in pool.map(work, targets[:limit]):
            result[i] = rescore(updated)
    return result
