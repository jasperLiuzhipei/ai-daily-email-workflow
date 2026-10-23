"""Window-complete public AIHOT discovery; rankings are leads, never evidence.

Raw API pages are kept per run. Cursors are used only within one traversal and
are never reused across runs. Failed/limited traversals report partial coverage.
"""
import json
from pathlib import Path
import re
import time
from urllib.parse import urlencode, urlsplit

from source_candidates import prepare_candidate
from source_feed_adapters import parse_feed_datetime
from source_identity import SourceSelectionError

BASE = 'https://aihot.news/api/v1'
CATEGORIES = {'ai-models': 'Model', 'ai-products': 'Product', 'industry': 'Industry',
              'paper': 'Paper', 'tip': 'Tutorial', 'opinion': 'Opinion'}


class FetchBudget:
    """Charge HTTP work, not unrelated feed/verification work between calls."""
    def __init__(self, fetch, seconds=180, clock=time.monotonic):
        self.fetch, self.remaining, self.clock = fetch, seconds, clock

    def __call__(self, url, output, **kwargs):
        if self.remaining < 1:
            raise SourceSelectionError('AIHOT discovery time budget exhausted')
        started = self.clock()
        try:
            return self.fetch(url, output, **{**kwargs,
                'max_time_seconds': max(1, int(min(20, self.remaining))), 'attempts': 1})
        finally:
            self.remaining = max(0, self.remaining - (self.clock() - started))


def get_json(path, output, fetch):
    # One attempt: on 429/503, leave a partial receipt instead of immediate
    # retries that would ignore Retry-After. A later scheduled run can retry.
    value = json.loads(fetch(BASE + path, output, max_time_seconds=20, attempts=1))
    if not isinstance(value, dict) or value.get('schemaVersion') != 1:
        raise ValueError('Unsupported AIHOT API response')
    return value


def fetch_window(start_at, end_at, folder, fetch, *, max_pages=12, query=None, page_size=100):
    if not 1 <= page_size <= 100:
        raise ValueError('AIHOT page_size must be between 1 and 100')
    folder = Path(folder)
    audit = {'status': 'partial', 'complete': False, 'pages': 0, 'item_count': 0,
             'start_at': start_at.isoformat(), 'end_at': end_at.isoformat(),
             'ordering': 'publishedAtDesc', 'error': '', 'unknown_publication_count': 0}
    rows, seen, cursors = [], set(), set()
    cursor, previous_time = None, None
    try:
        for index in range(max_pages):
            params = {'mode': 'all', 'window': '7d', 'by': 'published', 'limit': page_size}
            if query:
                params['q'] = query
            if cursor:
                params['cursor'] = cursor
            page = get_json('/items?' + urlencode(params), folder / f'page_{index+1:02}.json', fetch)
            if page.get('query', {}).get('ordering') != 'publishedAtDesc':
                raise ValueError('Unexpected pagination ordering')
            items, paging = page['items'], page['page']
            if not isinstance(items, list) or type(paging.get('hasMore')) is not bool:
                raise ValueError('Malformed items/page')
            crossed = False
            for row in items:
                if not isinstance(row, dict) or not isinstance(row.get('id'), str):
                    raise ValueError('Malformed item identity')
                stamp = parse_feed_datetime(row.get('publishedAt') or row.get('discoveredAt'))
                if stamp is None or (previous_time is not None and stamp > previous_time):
                    raise ValueError('Missing or non-monotonic pagination time')
                previous_time = stamp
                if stamp < start_at:
                    crossed = True
                if row['id'] not in seen:
                    seen.add(row['id'])
                    # Preserve undated leads in raw/audit without inventing dates.
                    if not row.get('publishedAt'):
                        audit['unknown_publication_count'] += 1
                    if start_at <= stamp <= end_at:
                        rows.append(row)
            audit['pages'] += 1
            if crossed or not paging['hasMore']:
                audit.update(status='complete', complete=True,
                             stop_reason='window_boundary' if crossed else 'exhausted',
                             oldest_seen_at=previous_time.isoformat() if previous_time else None)
                break
            cursor = paging.get('nextCursor')
            if not items or not cursor or cursor in cursors:
                raise ValueError('Empty page or stalled pagination cursor')
            cursors.add(cursor)
        else:
            audit['error'] = 'page_budget_exhausted_before_window_boundary'
    except (SourceSelectionError, ValueError, KeyError, TypeError, OSError) as exc:
        audit['error'] = str(exc)[:800]
    audit['item_count'] = len(rows)
    return rows, audit


def candidates_from_items(rows, start_at, end_at, previous_items, *, origin='aihot_api', topic=None):
    candidates, duplicates, excluded = [], [], []
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get('title'), str)
                or not isinstance(row.get('links'), dict) or not isinstance(row.get('source'), dict)):
            excluded.append({'reason': 'malformed_item'})
            continue
        published = parse_feed_datetime(row.get('publishedAt'))
        if published is None or not start_at <= published <= end_at:
            excluded.append({'id': row.get('id'), 'title': row.get('title'),
                             'published_at': row.get('publishedAt'),
                             'reason': 'publication_unknown' if published is None else 'outside_window'})
            continue
        links = row.get('links', {})
        primary = links.get('original')
        if not primary or urlsplit(primary).scheme not in {'https', 'http'}:
            excluded.append({'id': row.get('id'), 'reason': 'missing_original_url'})
            continue
        c = {'title': row['title'], 'primary_url': primary, 'aihot_url': links.get('aihot', ''),
             'description': row.get('summary') or row['title'],
             'published_at': published.isoformat(), 'discovered_at': row.get('discoveredAt'),
             'categories': [CATEGORIES.get(row.get('category'), row.get('category') or 'Uncategorized')],
             'feed_source': origin, 'source_name': row.get('source', {}).get('name', ''),
             'research_window_strategy': 'exact_window', 'aihot_selected': bool(row.get('selected')),
             'publication_time_provenance': 'aihot_report_attribution' if origin == 'aihot_hot_topic' else 'aihot_api_publishedAt',
             'timing_review_required': origin == 'aihot_hot_topic',
             'source_time_variants': [{'published_at': published.isoformat(),
                 'discovered_at': row.get('discoveredAt'), 'aihot_url': links.get('aihot'),
                 'feed_source': origin, 'date_only_possible': published.hour in {0, 8} and not published.minute and not published.second}]}
        if topic:
            c['aihot_hot_topics'] = [topic['id']]
        c, duplicate = prepare_candidate(c, previous_items)
        (duplicates if duplicate else candidates).append(duplicate or c)
    return candidates, duplicates, excluded


def fetch_hot_topics(start_at, end_at, folder, previous_items, fetch):
    folder = Path(folder)
    audit = {'status': 'ok', 'topics': [], 'errors': [], 'exclusions': []}
    candidates, duplicates = [], []
    try:
        topics = get_json('/hot-topics', folder / 'topics.json', fetch)['items']
        if not isinstance(topics, list) or len(topics) > 10:
            raise ValueError('Malformed hot topics')
    except (SourceSelectionError, ValueError, KeyError, TypeError, OSError) as exc:
        audit.update(status='partial', errors=[str(exc)[:800]])
        return candidates, duplicates, audit
    for topic in topics:
        if (not isinstance(topic, dict) or not isinstance(topic.get('links'), dict)
                or not re.fullmatch(r'[a-zA-Z0-9_-]+', str(topic.get('id', '')))):
            audit['errors'].append('Malformed hot topic identity/links')
            continue
        record = {k: topic.get(k) for k in ('id', 'rank', 'title', 'latestAt', 'links')}
        record['report_urls'] = list(topic.get('links', {}).values())
        audit['topics'].append(record)
        story = topic.get('links', {}).get('story', '')
        parsed = urlsplit(story)
        if parsed.hostname not in {'aihot.news', 'aihot.virxact.com'} or not re.fullmatch(r'/story/[a-zA-Z0-9_-]+', parsed.path):
            record['status'] = 'story_unavailable'
            continue
        try:
            data = get_json('/stories/' + parsed.path.rsplit('/', 1)[-1], folder / (topic['id'] + '.json'), fetch)
            rows = data['story']['reports']
            a, b, excluded = candidates_from_items(rows, start_at, end_at, previous_items,
                                                   origin='aihot_hot_topic', topic=topic)
            candidates.extend(a); duplicates.extend(b); audit['exclusions'].extend(excluded)
            record['report_urls'] += [u for row in rows for u in row.get('links', {}).values()]
            record.update(status='retrieved', candidate_count=len(a), duplicate_count=len(b))
        except (SourceSelectionError, ValueError, KeyError, TypeError, OSError) as exc:
            record.update(status='error', error=str(exc)[:800])
            audit['errors'].append(record['error'])
    if audit['errors']:
        audit['status'] = 'partial'
    return candidates, duplicates, audit


def recovery_query(candidate):
    title = candidate.get('title', '')
    # Keep named platforms/products together ("Example Cloud") instead of
    # defaulting to the vendor's much broader name. No brand allowlist.
    names = re.findall(r'[A-Z][A-Za-z0-9.-]*(?:\s+[A-Z][A-Za-z0-9.-]*)*', title)
    names = [n for n in names if len(n) >= 4 and n.lower() not in {'with', 'from', 'this', 'that', 'open', 'model'}]
    if names:
        return max(names, key=len)
    words = re.findall(r'[A-Za-z][A-Za-z0-9.-]{3,}', title)
    words = [w for w in words if w.lower() not in {'with', 'from', 'this', 'that', 'open', 'model'}]
    return max(words, key=len) if words else title[:100].strip()


def discover_pending(candidates, start_at, end_at, folder, previous_items, fetch, *, max_queries=4):
    found, duplicates, audit, seen = [], [], [], set()
    pending = [c for c in candidates if c.get('backlog_recall_lane') == 'pending_evidence'
               or (c.get('high_value_reasons') and c.get('primary_verification', {}).get('status')
                   in {'unresolved', 'not_attempted_budget'})]
    pending.sort(key=lambda c: (not bool(c.get('aihot_hot_topics')),
                               c.get('backlog_recall_lane') != 'pending_evidence',
                               c.get('aihot_daily_rank') or 999, c.get('title', '')))
    # Existing hot stories already have the story/report discovery route.
    # Reserve half of this small *additional* search budget for recalled
    # evidence failures, so a busy news day cannot starve that lane entirely.
    reserved, reserved_queries = [], set()
    for c in pending:
        query = recovery_query(c)
        if (c.get('backlog_recall_lane') == 'pending_evidence' and len(query) >= 2
                and query not in reserved_queries and len(reserved) < (max_queries + 1) // 2):
            reserved.append(c)
            reserved_queries.add(query)
    pending = reserved + [c for c in pending if not any(c is item for item in reserved)]
    for c in pending:
        query = recovery_query(c)
        if len(query) < 2 or query in seen or len(seen) >= max_queries:
            continue
        seen.add(query)
        rows, receipt = fetch_window(start_at, end_at, Path(folder) / str(len(seen)), fetch,
                                     max_pages=1, page_size=20, query=query)
        a, b, excluded = candidates_from_items(rows, start_at, end_at, previous_items,
                                               origin='aihot_pending_search')
        found.extend(a); duplicates.extend(b)
        audit.append({'backlog_id': c.get('backlog_id'), 'title': c.get('title'),
                      'trigger': 'backlog_pending' if c.get('backlog_recall_lane') == 'pending_evidence'
                                 else 'current_primary_unresolved', 'query': query, **receipt,
                      'candidate_count': len(a), 'exclusions': excluded})
    return found, duplicates, audit


def coverage_receipts(hot_audit, selection, duplicates):
    from editorial_backlog import identities
    selected = selection.get('selected_news', []) + selection.get('selected_research', [])
    evaluated = selection.get('all_candidates_scored', [])
    receipts = []
    for topic in hot_audit.get('topics', []):
        keys = identities({'coverage_source_urls': topic['report_urls']})
        matches = [c for c in evaluated if keys & identities(c)]
        picked = [c for c in selected if keys & identities(c)]
        prior = [c for c in duplicates if keys & identities(c)]
        status = ('selected' if picked else 'previously_covered' if prior else
                  'reviewed_not_selected' if matches else 'no_eligible_candidate')
        receipts.append({'topic_id': topic['id'], 'title': topic['title'], 'status': status,
                         'events': [{'event_id': c.get('event_id'), 'title': c.get('title'),
                                     'assessment_status': c.get('editorial_assessment', {}).get('assessment_status'),
                                     'decision': c.get('editorial_decision')} for c in matches],
                         'discovery_status': topic['status']})
    return receipts
