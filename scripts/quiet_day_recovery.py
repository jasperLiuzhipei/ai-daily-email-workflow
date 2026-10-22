"""One bounded recovery pass over deferred events and observed alternate links.

No invented URLs, search snippets, score changes or claims of official status.
"""
from copy import deepcopy
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

from event_editorial import canonical_url, digest, eligibility
from fetch_selected_source_material import source_material, safe_public_http_url
from source_verification import material_readability
from editorial_runtime_support import atomic_json
from quiet_day_selection import checked_quiet_policy
from editorial_value import CONTRACT
from source_urls import clean_source_url


def recovery_urls(c):
    urls = [c.get('primary_url')]
    urls += c.get('coverage_source_urls', [])
    for field in ('discovery_excerpt', 'description', 'source_excerpt'):
        urls += re.findall(r'https?://[^\s<>"\)]+', str(c.get(field) or ''))
    seen, out = set(), []
    for raw in urls:
        # Older traces canonicalized HTML query keys before decoding them.
        # If this is exactly the original link, use its cleaned spelling rather
        # than treating the encoded duplicate as a newly discovered fallback.
        if canonical_url(raw) == canonical_url(c.get('primary_url')):
            raw = c.get('primary_url')
        url = clean_source_url(raw).rstrip('.,;')
        key = canonical_url(url)
        if not key or key in seen or not safe_public_http_url(url):
            continue
        if urlsplit(key).hostname in {'aihot.news', 'aihot.virxact.com'} and url != c.get('primary_url'):
            continue  # Discovery summaries do not replace an article.
        seen.add(key); out.append(url)
    return out


def repeat_failure_reason(material):
    """Skip unproductive immediate repeats, never blacklist an entire host."""
    if re.fullmatch(r'HTTP (?:401|403|404|410)', material.get('fetch_error', '')):
        return 'same_run_' + material['fetch_error'].replace(' ', '_').lower()
    reason = material.get('readability_reason')
    if material.get('fetch_status') == 'fetched' and reason in {
            'short_body', 'blocked_page', 'unmatched_document', 'conflicting_document_identity'}:
        return 'same_run_' + reason
    return ''


def recovery_plan(candidate, material, known_failures=None):
    """Try observed alternatives first; keep transient failures retryable."""
    urls, skipped = [], []
    primary = canonical_url(clean_source_url(candidate.get('primary_url')))
    failed = {primary, canonical_url(clean_source_url(material.get('url')))}
    reason = repeat_failure_reason(material)
    for url in recovery_urls(candidate):
        skip_reason = (known_failures.get(canonical_url(url), '') if known_failures is not None
                       else reason if canonical_url(url) in failed else '')
        if skip_reason:
            skipped.append({'url': url, 'reason': skip_reason})
        else:
            urls.append(url)
    urls.sort(key=lambda url: canonical_url(url) == primary)
    return urls, skipped


def recover_pending(events, assessed, materials, *, policy, evaluator, output_dir, prompt,
                    remaining, assessor, clock=time.monotonic):
    q = checked_quiet_policy(policy.get('quiet_day'))
    qualified = [e for e, (a, _) in zip(events, assessed)
                 if eligibility(a, policy)[0] == 'include'
                 and any(c.get('editorial_news_window_eligible', True) for c in e['sources'])]
    # Subtract the possible research slot; conservative trigger avoids missing
    # recovery when a qualified event will be displayed as research.
    news_count = max(0, len(qualified) - int(any(a.get('illustration', {}).get('eligible')
        for a, _ in assessed if eligibility(a, policy)[0] == 'include')))
    audit = {'status': 'not_needed', 'triggered': False, 'events_attempted': 0,
             'sources_recovered': 0, 'attempts': [], 'skipped_sources': [], 'http_requests': 0,
             'search_discovery': 'not_configured; observed links only'}
    value_mode = policy.get('admission_contract') == CONTRACT
    if not value_mode and (not q['enabled'] or news_count >= q['min_news_trigger']):
        return assessed, audit
    audit['triggered'] = True
    pending = [i for i, (a, packet) in enumerate(assessed)
               if a.get('assessment_status') != 'complete' or (value_mode and
                   any(s.get('read_status') != 'read' for s in packet.get('sources', [])))]
    if not pending:
        audit['status'] = 'nothing_pending'
        return assessed, audit
    known_failures, fetched = {}, {}
    for event in events:
        for c in event['sources']:
            m = materials.get(c['editorial_source_id'], {})
            reason = repeat_failure_reason(m)
            if reason and m.get('read_status') != 'read':
                for url in (c.get('primary_url'), m.get('url')):
                    if url:
                        known_failures[canonical_url(clean_source_url(url))] = reason
            elif m.get('read_status') == 'read' and m.get('url') and m.get('text'):
                fetched[canonical_url(clean_source_url(m['url']))] = {
                    'fetch_status': 'fetched', 'resolved_url': m['url'], 'source_text': m['text'],
                    'page_title': m.get('page_title', ''), 'document_identity': m.get('document_identity', {})}
    # A readable response for an exact URL can be reused and revalidated even
    # if a different lead failed to identify that page earlier in the run.
    for key in fetched:
        known_failures.pop(key, None)
    plans, skipped_events = {}, []
    for i in pending:
        event = events[i]
        plans[i] = {}
        for c in event['sources']:
            sid = c['editorial_source_id']
            material = materials.get(sid, {})
            if material.get('read_status') == 'read':
                continue
            urls, skipped = recovery_plan(c, material, known_failures)
            plans[i][sid] = urls
            audit['skipped_sources'].extend({'event_id': event['event_id'], 'source_id': sid, **s}
                                            for s in skipped)
        # A pending model decision with readable evidence can still be retried.
        needs_assessment = assessed[i][0].get('assessment_status') != 'complete' and any(
            materials.get(c['editorial_source_id'], {}).get('read_status') == 'read' for c in event['sources'])
        if not any(plans[i].values()) and not needs_assessment:
            skipped_events.append(i)
    pending = [i for i in pending if i not in skipped_events]
    audit['events_skipped_no_recovery_route'] = len(skipped_events)
    # Recovery routes with observed alternatives come first. Priority affects
    # retrieval order, never content scores or publication eligibility.
    pending.sort(key=lambda i: (not any(c.get('high_value_reasons') or c.get('aihot_daily_featured')
        or (c.get('bestblogs_score') or 0) >= 90 for c in events[i]['sources']),
        not any(canonical_url(url) != canonical_url(clean_source_url(c.get('primary_url')))
            for c in events[i]['sources'] for url in plans[i].get(c['editorial_source_id'], [])),
        events[i]['event_id']))
    start = clock()
    def left():
        return min(q['recovery_budget_seconds'] - (clock() - start), remaining())
    audit['status'] = 'completed'
    attempted = []
    for i in pending[:q['recovery_max_events']]:
        if left() < 1:
            audit['status'] = 'exhausted'; break
        event = events[i]
        audit['events_attempted'] += 1
        attempted.append(i)
        changed = False
        for original in event['sources']:
            sid = original['editorial_source_id']
            if materials[sid]['read_status'] == 'read':
                continue
            for url in plans[i].get(sid, [])[:q['recovery_max_urls']]:
                if left() < 1:
                    audit['status'] = 'exhausted'; break
                c = deepcopy(original)
                c.pop('editorial_assessment', None)
                c.pop('primary_verification', None)
                c.update(primary_url=url, source_excerpt='')
                attempt = {'event_id': event['event_id'], 'url': url}
                try:
                    key = canonical_url(url)
                    attempt['response_reused'] = key in fetched
                    if key not in fetched:
                        audit['http_requests'] += 1
                        try:
                            fetched[key] = source_material(c, max_chars=policy['source_chars'],
                                timeout_seconds=max(1, int(min(12, left()))), max_bytes=1500000)
                        except Exception as exc:
                            fetched[key] = {'fetch_status': 'fallback_after_unexpected_error',
                                            'fetch_error': type(exc).__name__}
                    m = fetched[key]
                    body = m.get('source_text', '')
                    final_url = m.get('resolved_url') or url
                    ok, reason = material_readability(original, {**m, 'primary_url': url})
                    attempt.update(fetch_status=m.get('fetch_status'), read=ok, error=m.get('fetch_error'),
                                   readability_reason=reason, resolved_url=final_url, readable_chars=len(body))
                    if ok:
                        materials[sid] = {**materials[sid], 'url': final_url, 'text': body,
                            'read_status': 'read', 'text_sha256': digest(body), 'origin': 'quiet_day_recovery',
                            'fetch_status': m.get('fetch_status'), 'fetch_error': '',
                            'readability_reason': reason, 'document_identity': m.get('document_identity', {}),
                            'recovered_from_url': original.get('primary_url')}
                        atomic_json(Path(output_dir) / 'recovered_materials' / (sid + '.json'), materials[sid])
                        audit['sources_recovered'] += 1
                        changed = True
                except Exception as exc:
                    ok = False
                    attempt.update(read=False, error=type(exc).__name__)
                audit['attempts'].append(attempt)
                if ok:
                    break
        # A bounded proxy charges assessment calls against BOTH runtime budgets.
        class RecoveryEvaluator:
            identity = evaluator.identity
            def __call__(self, system, data):
                if left() < 1:
                    raise ValueError('Quiet-day recovery budget exhausted')
                previous = getattr(evaluator, 'deadline_remaining', None)
                evaluator.deadline_remaining = left
                try:
                    return evaluator(system, data)
                finally:
                    evaluator.deadline_remaining = previous
        needs_assessment = assessed[i][0].get('assessment_status') != 'complete' and any(
            materials[c['editorial_source_id']]['read_status'] == 'read' for c in event['sources'])
        if left() >= 1 and (changed or needs_assessment):
            assessed[i] = assessor(event, materials, policy, RecoveryEvaluator(),
                                  Path(output_dir) / 'assessments', prompt)
    if len(pending) > audit['events_attempted']:
        audit['status'] = 'exhausted'
        audit['exhaustion_reason'] = ('event_limit' if audit['events_attempted'] >= q['recovery_max_events']
                                      else 'time_budget')
    elif not pending:
        audit['status'] = 'no_recovery_routes'
    audit['remaining_pending'] = sum(a.get('assessment_status') != 'complete' for a, _ in assessed)
    audit['remaining_material_gaps'] = sum(not packet.get('sources') or any(s.get('read_status') != 'read'
        for s in packet.get('sources', [])) for _, packet in assessed)
    audit['unattempted_event_ids'] = [events[i]['event_id'] for i in pending if i not in attempted]
    audit['skipped_event_ids'] = [events[i]['event_id'] for i in skipped_events]
    audit['elapsed_seconds'] = round(clock() - start, 3)
    atomic_json(Path(output_dir) / 'quiet_day_recovery.json', audit)
    return assessed, audit
