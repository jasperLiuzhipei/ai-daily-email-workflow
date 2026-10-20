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


def recovery_urls(c):
    urls = [c.get('primary_url')]
    urls += c.get('coverage_source_urls', [])
    urls += re.findall(r'https?://[^\s<>"\)]+', str(c.get('discovery_excerpt') or c.get('description') or ''))
    seen, out = set(), []
    for raw in urls:
        url = str(raw or '').rstrip('.,;')
        key = canonical_url(url)
        if not key or key in seen or not safe_public_http_url(url):
            continue
        if urlsplit(key).hostname in {'aihot.news', 'aihot.virxact.com'} and url != c.get('primary_url'):
            continue  # Discovery summaries do not replace an article.
        seen.add(key); out.append(url)
    return out


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
             'sources_recovered': 0, 'attempts': [], 'search_discovery': 'not_configured; observed links only'}
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
    # Priority affects retrieval order, never content scores.
    pending.sort(key=lambda i: (not any(c.get('high_value_reasons') or c.get('aihot_daily_featured')
        or (c.get('bestblogs_score') or 0) >= 90 for c in events[i]['sources']), events[i]['event_id']))
    start = clock()
    def left():
        return min(q['recovery_budget_seconds'] - (clock() - start), remaining())
    audit['status'] = 'completed'
    for i in pending[:q['recovery_max_events']]:
        if left() < 1:
            audit['status'] = 'exhausted'; break
        event = events[i]
        audit['events_attempted'] += 1
        for original in event['sources']:
            sid = original['editorial_source_id']
            if materials[sid]['read_status'] == 'read':
                continue
            for url in recovery_urls(original)[:q['recovery_max_urls']]:
                if left() < 1:
                    audit['status'] = 'exhausted'; break
                c = deepcopy(original)
                c.pop('editorial_assessment', None)
                c.pop('primary_verification', None)
                c.update(primary_url=url, source_excerpt='')
                attempt = {'event_id': event['event_id'], 'url': url}
                try:
                    m = source_material(c, max_chars=policy['source_chars'],
                        timeout_seconds=max(1, int(min(12, left()))), max_bytes=1500000)
                    body = m.get('source_text', '')
                    final_url = m.get('resolved_url') or url
                    ok, reason = material_readability(original, m)
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
        if left() >= 1:
            assessed[i] = assessor(event, materials, policy, RecoveryEvaluator(),
                                  Path(output_dir) / 'assessments', prompt)
    if len(pending) > audit['events_attempted']:
        audit['status'] = 'exhausted'
    audit['remaining_pending'] = sum(a.get('assessment_status') != 'complete' for a, _ in assessed)
    audit['remaining_material_gaps'] = sum(not packet.get('sources') or any(s.get('read_status') != 'read'
        for s in packet.get('sources', [])) for _, packet in assessed)
    audit['unattempted_event_ids'] = [events[i]['event_id'] for i in pending[audit['events_attempted']:]]
    audit['elapsed_seconds'] = round(clock() - start, 3)
    atomic_json(Path(output_dir) / 'quiet_day_recovery.json', audit)
    return assessed, audit
