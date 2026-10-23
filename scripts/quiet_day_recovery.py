"""One bounded recovery pass over deferred events and observed alternate links.

No invented URLs, search snippets, score changes or claims of official status.
"""
from copy import deepcopy
from pathlib import Path
import re
import time
from urllib.parse import urlsplit, urljoin

from event_editorial import canonical_url, digest, eligibility
from fetch_selected_source_material import source_material, safe_public_http_url
from source_verification import material_readability
from editorial_runtime_support import atomic_json
from quiet_day_selection import checked_quiet_policy
from editorial_value import CONTRACT
from source_urls import clean_source_url
from aihot_discovery import recovery_query
from publication_priority import needs_source_followup, LEVELS


def article_reference_links(candidate, material):
    """Observed article citations only; relevance is checked again after fetch.

    Repository/document hosts are scheduling hints, not claims of authorship.
    No destination is synthesized from a company or product name.
    """
    base = material.get('url') or material.get('resolved_url') or candidate.get('primary_url', '')
    identity = material.get('document_identity') or {}
    if identity.get('body_kind') == 'page':
        return []
    compact = lambda s: re.sub(r'[^a-z0-9\u4e00-\u9fff]', '', str(s).lower())
    term = compact(recovery_query(candidate))
    result, seen = [], set()
    for ref in identity.get('article_references', []):
        if not isinstance(ref, dict):
            continue
        url = clean_source_url(urljoin(base, str(ref.get('href') or ''))).rstrip('.,;')
        key, parsed = canonical_url(url), urlsplit(url)
        label = str(ref.get('label') or '')
        if (not safe_public_http_url(url) or key in seen or key == canonical_url(base)
                or parsed.username or parsed.password or parsed.path in {'', '/'}
                or parsed.hostname in {'aihot.news', 'aihot.virxact.com'}
                or re.search(r'/(?:login|signin|signup|logout|share|search)(?:/|$)', parsed.path, re.I)):
            continue
        specific = len(term) >= 4 and term in compact(parsed.path + ' ' + label)
        citation = bool(re.search(r'原文|官方|源码|仓库|论文|项目地址|技术报告|github|source|paper|announcement|repository|model card', label, re.I))
        if not specific and not citation:
            continue
        seen.add(key)
        result.append({'url': url, 'discovered_from': base, 'anchor': label,
                       'specific': specific})
    result.sort(key=lambda r: (not r['specific'],
        urlsplit(r['url']).hostname not in {'github.com', 'huggingface.co', 'arxiv.org'}, r['url']))
    return result[:6]


def terminal_reference(url):
    p = urlsplit(url)
    parts = p.path.strip('/').split('/')
    return (p.hostname == 'github.com' and len(parts) == 2
            or p.hostname == 'huggingface.co' and len(parts) == 2
            or p.hostname == 'arxiv.org' and p.path.startswith('/abs/'))


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


def recovery_plan(candidate, material, known_failures=None, references=()):
    """Try observed alternatives first; keep transient failures retryable."""
    urls, skipped = [], []
    primary = canonical_url(clean_source_url(candidate.get('primary_url')))
    failed = {primary, canonical_url(clean_source_url(material.get('url')))}
    reason = repeat_failure_reason(material)
    observed = {canonical_url(r['url']) for r in references}
    candidates = list(dict.fromkeys([r['url'] for r in references] + recovery_urls(candidate)))
    for url in candidates:
        skip_reason = (known_failures.get(canonical_url(url), '') if known_failures is not None
                       else reason if canonical_url(url) in failed else '')
        if skip_reason:
            skipped.append({'url': url, 'reason': skip_reason})
        else:
            urls.append(url)
    urls.sort(key=lambda url: (canonical_url(url) not in observed, canonical_url(url) == primary))
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
             'search_discovery': 'upstream AIHOT alternate-report discovery; article citations followed here',
             'sources_enriched': 0}
    value_mode = policy.get('admission_contract') == CONTRACT
    if not value_mode and (not q['enabled'] or news_count >= q['min_news_trigger']):
        return assessed, audit
    audit['triggered'] = True
    pending = [i for i, (a, packet) in enumerate(assessed)
               if a.get('assessment_status') != 'complete' or (value_mode and
                   (a.get('editorial_recommendation') == 'defer' or
                    needs_source_followup(a, policy) or
                    any(s.get('read_status') != 'read' for s in packet.get('sources', []))))]
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
    plans, references_by_source, skipped_events = {}, {}, []
    for i in pending:
        event = events[i]
        plans[i] = {}
        needs_more = (assessed[i][0].get('assessment_status') != 'complete'
            or assessed[i][0].get('editorial_recommendation') == 'defer'
            or needs_source_followup(assessed[i][0], policy))
        readable_urls = {canonical_url(materials.get(c['editorial_source_id'], {}).get('url'))
                         for c in event['sources'] if materials.get(c['editorial_source_id'], {}).get('read_status') == 'read'}
        for c in event['sources']:
            sid = c['editorial_source_id']
            material = materials.get(sid, {})
            if material.get('read_status') == 'read' and not needs_more:
                continue
            references = [ref for parent in event['sources']
                if materials.get(parent['editorial_source_id'], {}).get('read_status') == 'read'
                for ref in article_reference_links(c, materials[parent['editorial_source_id']])
                if canonical_url(ref['url']) not in readable_urls]
            references_by_source[(i, sid)] = {canonical_url(r['url']): r for r in references}
            urls, skipped = recovery_plan(c, material, known_failures, references)
            if material.get('read_status') == 'read':
                urls = [u for u in urls if canonical_url(u) in references_by_source[(i, sid)]]
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
    pending.sort(key=lambda i: (-LEVELS.get(assessed[i][0].get('publication_priority', {}).get('level'), 0)
        if policy.get('priority_contract') else 0,
        not any(c.get('aihot_hot_topics') or c.get('high_value_reasons') or c.get('aihot_daily_featured')
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
        priority_followup = needs_source_followup(assessed[i][0], policy)
        audit['events_attempted'] += 1
        attempted.append(i)
        changed = False
        for original in list(event['sources']):
            sid = original['editorial_source_id']
            if not plans[i].get(sid):
                continue
            refs = dict(references_by_source.get((i, sid), {}))
            queue = [(url, 1 if canonical_url(url) in refs else 0) for url in plans[i][sid]]
            was_read = materials[sid]['read_status'] == 'read'
            if was_read:
                already_read = {canonical_url(materials[x['editorial_source_id']].get('url'))
                                for x in event['sources'] if materials[x['editorial_source_id']]['read_status'] == 'read'}
                queue = [(u, d) for u, d in queue if canonical_url(u) not in already_read]
            visited, best, chain = set(), None, []
            while queue and len(visited) < q['recovery_max_urls']:
                url, depth = queue.pop(0)
                key = canonical_url(url)
                if key in visited or key in known_failures:
                    continue
                if left() < 1:
                    audit['status'] = 'exhausted'; break
                visited.add(key)
                c = deepcopy(original)
                c.pop('editorial_assessment', None)
                c.pop('primary_verification', None)
                c.update(primary_url=url, source_excerpt='')
                attempt = {'event_id': event['event_id'], 'source_id': sid, 'url': url, 'reference_depth': depth}
                if key in refs:
                    attempt.update({k: refs[key][k] for k in ('discovered_from', 'anchor')})
                try:
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
                        best = ({**m, 'resolved_url': final_url}, reason)
                        chain.append({'url': final_url, 'discovered_from': attempt.get('discovered_from'),
                                      'reference_depth': depth})
                        # Read at most two reference hops within the existing
                        # per-source URL/time budgets. Keep a readable report
                        # if its citation fails; never upgrade it to official.
                        next_refs = article_reference_links(original, {**m, 'url': final_url}) if depth < 2 and not terminal_reference(final_url) else []
                        next_refs = [r for r in next_refs if canonical_url(r['url']) not in visited
                                     and canonical_url(r['url']) not in known_failures
                                     and canonical_url(r['url']) != canonical_url(original.get('primary_url'))]
                        refs.update({canonical_url(r['url']): r for r in next_refs})
                        queue = [(r['url'], depth + 1) for r in next_refs] + ([] if not next_refs else queue)
                except Exception as exc:
                    ok = False
                    attempt.update(read=False, error=type(exc).__name__)
                audit['attempts'].append(attempt)
            if best:
                m, reason = best
                body = m['source_text']
                original_date = materials[sid].get('published_at', '')
                same_url = canonical_url(m.get('resolved_url')) == canonical_url(materials[sid].get('url'))
                dates = m.get('published_dates', [])
                target_sid = digest({'event_id': event['event_id'], 'reference_url': canonical_url(m['resolved_url'])})[:20] if was_read else sid
                if was_read:
                    # Preserve the dated announcement alongside the new
                    # repository/document evidence. Never replace all readable
                    # reports with the same undated reference.
                    if any(canonical_url(materials[x['editorial_source_id']].get('url')) == canonical_url(m['resolved_url'])
                           for x in event['sources'] if materials[x['editorial_source_id']]['read_status'] == 'read'):
                        continue
                    supporting = deepcopy(original)
                    supporting.update(primary_url=m['resolved_url'], editorial_source_id=target_sid,
                                      reference_from_url=original.get('primary_url'))
                    supporting.pop('primary_verification', None)
                    event['sources'].append(supporting)
                audit['sources_enriched' if was_read else 'sources_recovered'] += 1
                materials[target_sid] = {**materials[sid], 'source_id': target_sid,
                    'url': m.get('resolved_url') or m.get('primary_url'), 'text': body,
                    'read_status': 'read', 'text_sha256': digest(body), 'origin': 'quiet_day_recovery',
                    'fetch_status': m.get('fetch_status'), 'fetch_error': '',
                    'readability_reason': reason, 'document_identity': m.get('document_identity', {}),
                    'published_at': original_date if same_url else (dates[0] if dates else ''),
                    'discovery_published_at': materials[sid].get('discovery_published_at', original_date),
                    'observed_published_dates': dates,
                    'recovered_from_url': original.get('primary_url'), 'reference_chain': chain}
                atomic_json(Path(output_dir) / 'recovered_materials' / (target_sid + '.json'), materials[target_sid])
                changed = True
                if priority_followup:
                    # One material addition, then reassess. A heavily reported
                    # story must not spend the whole budget revisiting citations.
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
