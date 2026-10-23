"""Event-level editorial contracts and deterministic selection. No API or I/O.

The model supplies evidenced ordinal judgments; this module owns arithmetic,
eligibility and display tiers. Discovery/legacy scores are never inputs.
"""
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from editorial_value import CONTRACT, value_route
import publication_priority

VERSION = 'event-editorial-v3.1'
DIMENSIONS = ('change', 'impact', 'reader_value', 'learning_value')
DEFAULT_POLICY = {
    'version': VERSION,
    'weights': {'change': 30, 'impact': 25, 'reader_value': 25, 'learning_value': 20},
    'publish_threshold': 75, 'deep_threshold': 85, 'exceptional_review_threshold': 95,
    'workers': 4, 'max_candidates': 300, 'source_chars': 12000,
    'runtime_budget_seconds': 1800, 'max_api_requests': 650,
    'material_cache_ttl_seconds': 21600, 'shared_material_cache': 'daily_runs/.cache/event_materials',
    'paper_batch_size': 4,
    'audience': '产品、研发、用户运营与内部AI提效；兼顾会改变这些读者判断的重要产业与科研进展。',
}


class EditorialError(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     default=str).encode()).hexdigest()


def canonical_url(value):
    p = urlsplit(str(value or '').strip())
    if p.scheme not in {'http', 'https'} or not p.hostname:
        return ''
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.startswith('utm_') and k not in {'fbclid', 'gclid'}]
    return urlunsplit(('https', p.netloc.lower().removeprefix('www.'),
                       p.path.rstrip('/'), urlencode(sorted(query)), ''))


def source_id(candidate):
    url = canonical_url(candidate.get('primary_url') or candidate.get('url') or candidate.get('source_url'))
    if not url:
        raise EditorialError('Candidate has no valid source URL')
    return digest(url)[:20]


def candidate_urls(candidate):
    values = [candidate.get(k) for k in ('url', 'primary_url', 'source_url', 'aihot_url', 'bestblogs_url')]
    values.extend(candidate.get('coverage_source_urls') or [])
    return sorted({canonical_url(v) for v in values if canonical_url(v)})


def news_window_eligible(candidate, window):
    """Backlog recall does not reset an event's publication clock."""
    strategy = candidate.get('research_window_strategy')
    if strategy in {'paper_lookback', 'direct_research_lookback', 'content_reclassified_research_lookback'}:
        return False
    try:
        published = datetime.fromisoformat(str(candidate.get('published_at')).replace('Z', '+00:00'))
        start = datetime.fromisoformat(str(window.get('news_start_at') or window['start_at']))
        end = datetime.fromisoformat(str(window['end_at']))
        if strategy == 'official_release_catchup':
            start = min(start, end - timedelta(hours=72))
        elif strategy == 'major_event_gap_audit' and window.get('gap_audit_start_at'):
            start = datetime.fromisoformat(window['gap_audit_start_at'])
        return start <= published <= end
    except (ValueError, KeyError, TypeError):
        return False


def unique_sources(candidates):
    """Collapse canonical URL aliases, retaining all discovery provenance."""
    by_id = {}
    for original in candidates:
        c = deepcopy(original)
        cid = source_id(c)
        if cid not in by_id:
            c['editorial_source_id'] = cid
            c['coverage_source_urls'] = candidate_urls(c)
            by_id[cid] = c
        else:
            old = by_id[cid]
            aliases = set(old['coverage_source_urls']) | set(candidate_urls(c))
            if len(c.get('source_text') or '') > len(old.get('source_text') or ''):
                c['editorial_source_id'] = cid
                by_id[cid] = c
            by_id[cid]['coverage_source_urls'] = sorted(aliases)
            for key in ('aihot_daily_featured', 'bestblogs_score'):
                by_id[cid][key] = max(old.get(key) or 0, c.get(key) or 0)
    return [by_id[k] for k in sorted(by_id)]


def group_sources(candidates, groups):
    """Only explicit, disjoint model groups merge different URLs."""
    sources = {c['editorial_source_id']: c for c in candidates}
    seen, result = set(), []
    if not isinstance(groups, list):
        raise EditorialError('Event grouping must be a list')
    for group in groups:
        ids = group.get('source_ids')
        if (not isinstance(ids, list) or len(ids) < 2 or len(set(ids)) != len(ids)
                or not set(ids) <= sources.keys() or seen.intersection(ids)
                or not isinstance(group.get('reason'), str) or len(group['reason'].strip()) < 12):
            raise EditorialError('Invalid/overlapping event merge')
        seen.update(ids)
        result.append({'event_id': digest(sorted(ids))[:20], 'sources': [sources[i] for i in sorted(ids)],
                       'merge_reason': group['reason']})
    for sid in sorted(sources.keys() - seen):
        result.append({'event_id': digest([sid])[:20], 'sources': [sources[sid]], 'merge_reason': ''})
    return sorted(result, key=lambda x: x['event_id'])


def checked_policy(raw):
    policy = {**DEFAULT_POLICY, **(raw or {})}
    w = policy['weights']
    if (set(w) != set(DIMENSIONS) or any(type(x) not in (int, float) or x <= 0 for x in w.values())
            or sum(w.values()) != 100):
        raise EditorialError('Four positive editorial weights must total 100')
    if not 0 < policy['publish_threshold'] <= policy['deep_threshold'] <= policy['exceptional_review_threshold'] <= 100:
        raise EditorialError('Invalid editorial thresholds')
    for key in ('workers', 'max_candidates', 'source_chars', 'runtime_budget_seconds', 'max_api_requests', 'material_cache_ttl_seconds'):
        if type(policy[key]) is not int or policy[key] <= 0:
            raise EditorialError('Positive integer required: ' + key)
    if type(policy['paper_batch_size']) is not int or not 1 <= policy['paper_batch_size'] <= 4:
        raise EditorialError('paper_batch_size must be an integer from 1 to 4')
    if 'quiet_day' in policy:
        from quiet_day_selection import checked_quiet_policy
        policy['quiet_day'] = checked_quiet_policy(policy['quiet_day'])
        if policy['quiet_day']['enabled'] and max(policy['quiet_day']['thresholds']) >= policy['publish_threshold']:
            raise EditorialError('Quiet-day thresholds must be below the normal threshold')
    if 'news_budget' in policy:
        from news_budget import checked_budget
        policy['news_budget'] = checked_budget(policy['news_budget'])
    if policy.get('priority_contract') not in (None, publication_priority.CONTRACT):
        raise EditorialError('Unknown publication priority contract')
    if (publication_priority.enabled(policy.get('news_budget'))
            and policy.get('priority_contract') != publication_priority.CONTRACT):
        raise EditorialError('News priority ranking requires evidenced priority assessments')
    contract = policy.get('admission_contract')
    if contract is not None and contract != CONTRACT:
        raise EditorialError('Unknown editorial admission contract')
    if contract == CONTRACT:
        floor = policy.get('brief_value_floor')
        if type(floor) is not int or not 60 <= floor <= policy['publish_threshold']:
            raise EditorialError('Brief value floor must be >=60 and no higher than the general threshold')
    return policy


def normalized_text(value):
    return re.sub(r'\s+', '', str(value)).casefold()


def event_family_key(item):
    """Identify a few high-confidence repeated events across different URLs.

    This is a final safety net after model grouping.  It deliberately requires
    a named artifact and the same kind of action, so two updates by one vendor
    are not merged merely because they share a company or product name.
    """
    title = str(item.get('title') or item.get('event_title') or
                item.get('editorial_assessment', {}).get('event_title') or '')
    lower = title.casefold()
    if re.search(r'发布|推出|上线|更新|release|launch|introduc', lower):
        patterns = (
            r'(?<![a-z0-9])(?:claude\s+)?(?:sonnet|opus|haiku)\s*v?\d+(?:\.\d+)*(?![0-9.])',
            r'(?<![a-z0-9])(?:gpt|gemini|qwen|deepseek|kimi|glm|llama|grok)[-_ ]*v?\d+(?:[._-]\d+)*(?:[-_ ](?:flash|pro|sol|cyber|max|mini|turbo|thinking))?(?![a-z0-9.])',
        )
        for pattern in patterns:
            match = re.search(pattern, lower)
            if match:
                identity = re.sub(r'[^a-z0-9.]', '', match.group())
                if identity.startswith(('claudesonnet', 'claudeopus', 'claudehaiku')):
                    identity = identity.removeprefix('claude')
                return 'model-release:' + identity
    if re.search(r'红队|red[ -]?team', lower) and re.search(r'沙箱|sandbox|测试|test|绕过', lower):
        # Editorial headlines can omit the project name; the selected source
        # synopsis retains it without requiring a new model call.
        identity_text = ' '.join((title, str(item.get('source_excerpt') or ''),
                                  str(item.get('editorial_assessment', {}).get('what_changed') or '')))
        acronyms = re.findall(r'(?<![A-Za-z0-9])[A-Z][A-Z0-9]{3,}(?![A-Za-z0-9])', identity_text)
        generic = {'OPENAI', 'CLAUDE', 'MODEL', 'AGENT', 'HTTPS', 'HTTP', 'API', 'ROOT', 'PYPI'}
        names = [name for name in acronyms if name not in generic]
        if names:
            return 'redteam:' + names[0].casefold()
    return ''


def assessment_policy(policy):
    """Execution limits do not change editorial meaning or invalidate scores."""
    keys = ('version', 'weights', 'publish_threshold', 'deep_threshold',
            'exceptional_review_threshold', 'audience', 'admission_contract', 'brief_value_floor',
            'priority_contract')
    return {k: policy[k] for k in keys if k in policy}


def validate_assessment(raw, packet, policy):
    """Validate provenance and rationale; never manufacture missing scores."""
    a = deepcopy(raw)
    if policy.get('admission_contract') == CONTRACT:
        if a.get('editorial_recommendation') not in {'include', 'exclude', 'defer'}:
            raise EditorialError('Missing structured editorial recommendation')
        if a['editorial_recommendation'] == 'include' and (
                a.get('evidence_status') not in {'supported', 'limited'}
                or a.get('novelty') not in {'new', 'incremental'}):
            raise EditorialError('Recommendation conflicts with evidence or novelty')
    if a.get('event_id') != packet['event_id']:
        raise EditorialError('Assessment event identity mismatch')
    for key in ('event_title', 'what_changed', 'reader_takeaway', 'limitations', 'decision_reason'):
        if not isinstance(a.get(key), str) or len(a[key].strip()) < 8:
            raise EditorialError('Missing substantive field: ' + key)
    if a.get('evidence_status') not in {'supported', 'limited', 'unresolved', 'conflicting'}:
        raise EditorialError('Unknown evidence status')
    if a.get('content_kind') not in {'model_product', 'engineering', 'research', 'industry', 'opinion'}:
        raise EditorialError('Unknown content kind')
    if a.get('novelty') not in {'new', 'incremental', 'repeat', 'unclear'}:
        raise EditorialError('Unknown novelty')
    if a.get('urgency') not in {'routine', 'time_sensitive'}:
        raise EditorialError('Unknown urgency')
    if not isinstance(a.get('urgency_reason'), str):
        raise EditorialError('Missing urgency rationale')
    if a['urgency'] == 'time_sensitive' and len(a['urgency_reason'].strip()) < 12:
        raise EditorialError('Time-sensitive priority needs concrete deadline/consequence')
    sources = {s['source_id']: s for s in packet['sources']}
    primary_id = a.get('primary_source_id')
    if primary_id not in sources:
        raise EditorialError('Model selected an unknown source')
    evidence = a.get('evidence')
    if not isinstance(evidence, list):
        raise EditorialError('Missing evidence list')
    readable_evidence = set()
    for index, proof in enumerate(evidence):
        sid, quote = proof.get('source_id'), proof.get('quote')
        # The prompt requests short excerpts, but an authentic longer sentence
        # is not missing evidence. Keep a bounded tolerance for English quotes;
        # exact source matching below remains mandatory and text is never cut.
        if sid not in sources:
            raise EditorialError(f'Invalid evidence reference: evidence[{index}].source_id={sid!r}; allowed source_ids={list(sources)}')
        if not isinstance(quote, str) or not 8 <= len(quote.strip()) <= 1600:
            raise EditorialError('Invalid evidence reference')
        if normalized_text(quote) not in normalized_text(sources[sid]['text']):
            raise EditorialError('Evidence quote absent from supplied source text')
        if sources[sid]['read_status'] == 'read':
            readable_evidence.add(index)
    if policy.get('admission_contract') == CONTRACT:
        basis = a.get('novelty_basis')
        if (not isinstance(basis, dict) or basis.get('kind') not in
                {'release_announcement', 'documented_change', 'new_findings', 'none'}
                or not isinstance(basis.get('reason'), str) or len(basis['reason'].strip()) < 12
                or not isinstance(basis.get('evidence_refs'), list)
                or any(type(i) is not int or not 0 <= i < len(evidence) for i in basis['evidence_refs'])):
            raise EditorialError('Missing explicit novelty basis and zero-based evidence references')
        if a['novelty'] in {'new', 'incremental'} and (
                basis['kind'] == 'none' or not readable_evidence.intersection(basis['evidence_refs'])):
            raise EditorialError('New/incremental event requires readable evidence of a release, change or new findings')
    if a['evidence_status'] in {'supported', 'limited'}:
        if not readable_evidence or sources[primary_id]['read_status'] != 'read':
            raise EditorialError('Discovery excerpt cannot claim readable source support')
    dimensions = a.get('dimensions')
    if not isinstance(dimensions, dict) or set(dimensions) != set(DIMENSIONS):
        raise EditorialError('Missing editorial dimensions')
    # Recover only an unambiguous one-based numbering convention. Never guess
    # when zero-based and one-based references could describe different quotes.
    refs = [i for d in dimensions.values() if isinstance(d, dict)
            for i in d.get('evidence_refs', [])] if all(
                isinstance(d, dict) and isinstance(d.get('evidence_refs'), list)
                for d in dimensions.values()) else []
    if (refs and all(type(i) is int and 1 <= i <= len(evidence) for i in refs)
            and len(evidence) in refs):
        a['evidence_index_normalization'] = 'unambiguous_one_based_to_zero_based'
        for d in dimensions.values():
            d['evidence_refs'] = [i - 1 for i in d['evidence_refs']]
    unknown = False
    for key in DIMENSIONS:
        d = dimensions[key]
        if not isinstance(d, dict):
            raise EditorialError('Invalid dimension')
        level = d.get('level')
        if level is None:
            unknown = True
        elif type(level) is not int or not 0 <= level <= 4:
            raise EditorialError('Dimension levels must be integers 0..4 or null')
        if not isinstance(d.get('reason'), str) or len(d['reason'].strip()) < 12:
            raise EditorialError('Dimension needs a concrete editorial reason')
        refs = d.get('evidence_refs')
        if not isinstance(refs, list) or any(type(i) is not int or not 0 <= i < len(evidence) for i in refs):
            raise EditorialError('Invalid dimension evidence refs')
        if level is not None and level >= 3 and not readable_evidence.intersection(refs):
            raise EditorialError('High level needs readable source evidence')
    illustration = a.get('illustration')
    if (not isinstance(illustration, dict) or type(illustration.get('eligible')) is not bool
            or not isinstance(illustration.get('reason'), str) or len(illustration['reason'].strip()) < 12):
        raise EditorialError('Missing illustration suitability rationale')
    if illustration['eligible'] and a['content_kind'] not in {'research', 'engineering'}:
        raise EditorialError('Ordinary product/news cannot claim research illustration eligibility')
    if policy.get('priority_contract') == publication_priority.CONTRACT:
        try:
            publication_priority.validate_priority(a, evidence, readable_evidence)
        except ValueError as exc:
            raise EditorialError(str(exc)) from exc
    a['score'] = None if unknown else int(sum(dimensions[k]['level'] * policy['weights'][k] / 4
                                            for k in DIMENSIONS) + 0.5)
    a['policy_version'] = policy['version']
    a['policy_sha256'] = digest(assessment_policy(policy))
    a['input_sha256'] = digest(packet)
    # This is an evaluator judgment, not an independent fact check.
    a['assessment_status'] = 'complete'
    a['admission_route'] = value_route(a, policy) or 'weighted_score'
    return a


def eligibility(a, policy):
    if a.get('assessment_status') != 'complete':
        return 'defer', '正文评估未完成；不得使用旧分数替代。'
    if a.get('score') is None or a.get('evidence_status') in {'unresolved', 'conflicting'}:
        return 'defer', a.get('decision_reason', '证据或评分维度未解决。')
    if a.get('novelty') in {'repeat', 'unclear'}:
        return ('exclude' if a['novelty'] == 'repeat' else 'defer'), a['decision_reason']
    if policy.get('admission_contract') == CONTRACT:
        recommendation = a.get('editorial_recommendation')
        if recommendation not in {'include', 'exclude', 'defer'}:
            return 'defer', '缺少新版结构化编辑决定，需重评，不能复用旧评分。'
        if recommendation == 'defer':
            return 'defer', a['decision_reason']
        if recommendation == 'exclude':
            if a['score'] >= policy['publish_threshold']:
                return 'defer', '评分达标与编辑排除意见冲突，需复核：' + a['decision_reason']
            return 'exclude', a['decision_reason']
    if a['score'] < policy['publish_threshold']:
        if value_route(a, policy):
            return 'include', a['decision_reason']
        if a.get('editorial_recommendation') == 'include':
            return 'defer', '编辑建议报道但准入条件未满足，需补证或复核：' + a['decision_reason']
        return 'exclude', a['decision_reason']
    if a['score'] >= policy['exceptional_review_threshold'] and not a.get('second_review'):
        return 'defer', '异常高分需要第二次按证据复核，不能直接成为头条。'
    return 'include', a['decision_reason']


def select_events(candidates, policy, research_count=1, max_news_count=None):
    """All qualifying independent events enter. Never fill to a minimum."""
    policy = checked_policy(policy)
    eligible, rejected = [], []
    for c in candidates:
        duplicate = c.get('publication_duplicate_check', {})
        if duplicate.get('is_duplicate'):
            c['editorial_decision'] = {'action': 'exclude', 'reason':
                '已刊事件：' + str(duplicate.get('published_on', '')) + ' ' + str(duplicate.get('matched_previous', ''))}
            rejected.append(c)
            continue
        a = c.get('editorial_assessment', {})
        decision, reason = eligibility(a, policy)
        c['editorial_decision'] = {'action': decision, 'reason': reason}
        if decision == 'include':
            eligible.append(c)
        else:
            rejected.append(c)
    eligible.sort(key=lambda c: (-c['editorial_assessment']['score'],
                                 c['editorial_assessment'].get('urgency') != 'time_sensitive',
                                 c['event_id']))
    research_pool = [c for c in eligible if c['editorial_assessment']['illustration']['eligible']]
    research = []
    research_keys = set()
    research_duplicate_ids = set()
    for c in research_pool:
        key = event_family_key(c)
        if key and key in research_keys:
            c['editorial_decision'] = {'action': 'exclude', 'reason': '同一期研究事件重复：' + key}
            rejected.append(c)
            research_duplicate_ids.add(c['event_id'])
            continue
        if len(research) < max(0, research_count):
            research.append(c)
            if key:
                research_keys.add(key)
    used = {c['event_id'] for c in research}
    used_keys = set(research_keys)
    news = []
    for c in sorted(eligible, key=lambda c: publication_priority.news_rank(c, policy.get('news_budget'))):
        if c['event_id'] in research_duplicate_ids:
            continue
        if c['event_id'] in used:
            c['display_tier'] = 'research'
            continue
        key = event_family_key(c)
        if key and key in used_keys:
            c['event_duplicate_check'] = {'is_duplicate': True, 'event_key': key}
            c['editorial_decision'] = {'action': 'exclude', 'reason': '同一期事件重复：' + key}
            rejected.append(c)
            continue
        if not c.get('editorial_news_window_eligible', True):
            c['editorial_decision'] = {'action': 'exclude', 'reason': '研究回看窗口条目未选为研究，不作为新新闻补位。'}
            rejected.append(c)
            continue
        if max_news_count is not None and len(news) >= max_news_count:
            c['editorial_decision'] = {'action': 'defer', 'reason': '本次调用显式限制新闻数量；保留在候选池。'}
            rejected.append(c)
            continue
        c['display_tier'] = 'deep' if (c['editorial_assessment']['score'] >= policy['deep_threshold']
            or publication_priority.enabled(policy.get('news_budget')) and publication_priority.level(c) == 2) else 'brief'
        news.append(c)
        if key:
            used_keys.add(key)
    for rank, c in enumerate(news, 1):
        c['daily_rank'] = rank
    return research, news, rejected


def approved_retrospectives(content, approval):
    """Explicit, edition-bound editorial exception; not a score or new release.

    This records a user's request to send reviewed historical research. Like
    other local editorial records it is not independent authorization proof.
    """
    if not approval:
        return set()
    if (approval.get('kind') != 'user_approved_research_retrospectives'
            or approval.get('content_sha256') != digest(content)
            or not approval.get('user_request') or not approval.get('reason')):
        raise EditorialError('Missing or stale research retrospective approval')
    edition = datetime.fromisoformat(content['presentation']['date']).date()
    papers = content.get('research_items') or ([content['paper']] if isinstance(content.get('paper'), dict) else [])
    items = content.get('news', []) + papers
    allowed = set()
    for record in approval.get('items', []):
        found = [item for item in items if digest(item) == record.get('item_sha256')]
        if len(found) != 1:
            raise EditorialError('Retrospective approval must match exactly one final item')
        item = found[0]
        source_date = item.get('source_date', '')
        urls = [item['url']] if item.get('url') else [x.get('url') for x in item.get('links', [])]
        if (not item.get('title', '').startswith('【研究回顾】')
                or datetime.fromisoformat(source_date).date() >= edition
                or source_date not in item.get('boundary', '')
                or not record.get('source_review')
                or not canonical_url(record.get('primary_url'))
                or canonical_url(record['primary_url']) not in {canonical_url(u) for u in urls}
                or digest(item) in allowed):
            raise EditorialError('Invalid retrospective disclosure, source or duplicate approval')
        allowed.add(digest(item))
    if not allowed:
        raise EditorialError('Empty retrospective approval')
    return allowed


def approved_manual_news(selection, content, approval):
    """Edition-bound editor corrections with explicit source and timing records.

    These are disclosed manual decisions, not model scores or independent
    approval proof. They must match the exact final news item and the frozen
    selection window; ordinary unselected URLs remain blocked.
    """
    if not approval:
        return set()
    if (approval.get('schema_version') != 1
            or approval.get('kind') != 'manual_news_editorial_approval'
            or approval.get('selection_sha256') != digest(selection)
            or approval.get('date') != (content.get('presentation', {}).get('date') or content.get('date'))
            or not str(approval.get('user_request') or '').strip()
            or not str(approval.get('reason') or '').strip()
            or not str(approval.get('editor') or '').strip()):
        raise EditorialError('Missing or stale manual news editorial approval')
    window = selection.get('window', {})
    try:
        start = datetime.fromisoformat(window.get('news_start_at') or window['start_at'])
        end = datetime.fromisoformat(window['end_at'])
    except (ValueError, KeyError, TypeError) as exc:
        raise EditorialError('Manual news approval requires a valid selection window') from exc
    news = content.get('news', [])
    allowed = set()
    urls = set()
    event_keys = set()
    for record in approval.get('items', []):
        found = [item for item in news if digest(item) == record.get('item_sha256')]
        try:
            event_time = datetime.fromisoformat(record['event_date'])
            event_date = event_time.date()
            primary = canonical_url(record['primary_url'])
        except (ValueError, KeyError, TypeError) as exc:
            raise EditorialError('Manual news approval has invalid source or event time') from exc
        event_key = str(record.get('event_key') or '').strip()
        fully_inside_window = start.date() < event_date < end.date()
        exact_time_inside_window = (
            event_time.tzinfo is not None and start <= event_time <= end
        )
        # A date-only official release on the first calendar day overlaps the
        # clock-based window, but its exact hour is unknown. Admit it only as
        # an explicit editor correction that records the timing uncertainty.
        start_day_date_only = (
            event_date == start.date()
            and record.get('time_precision') == 'date_only'
            and len(str(record.get('timing_note') or '').strip()) >= 20
        )
        if (len(found) != 1 or not primary or primary != canonical_url(found[0].get('url'))
                or not (fully_inside_window or exact_time_inside_window or start_day_date_only)
                or not event_key or event_key in event_keys
                or len(str(record.get('source_review') or '').strip()) < 20
                or len(str(record.get('editorial_reason') or '').strip()) < 20
                or digest(found[0]) in allowed or primary in urls):
            raise EditorialError('Manual news approval lacks matched item, evidence, timing, or distinct source')
        allowed.add(digest(found[0]))
        urls.add(primary)
        event_keys.add(event_key)
    if not allowed:
        raise EditorialError('Empty manual news editorial approval')
    return allowed


def validate_final_selection(selection, content, retrospective_approval=None, manual_news_approval=None):
    """Production gate: final primary URLs must belong to approved events."""
    meta = selection.get('event_editorial')
    if not meta:
        return []  # Historical v1 artifacts retain their existing checks.
    policy = checked_policy(meta['policy'])
    try:
        retrospectives = approved_retrospectives(content, retrospective_approval)
        manual_news = approved_manual_news(selection, content, manual_news_approval)
    except (EditorialError, ValueError, KeyError, TypeError) as exc:
        return ['Invalid editorial approval: ' + str(exc)]
    supplemental = {}
    if selection.get('quiet_day_selection', {}).get('added_event_ids'):
        from quiet_day_selection import select_quiet_day
        saved = selection['quiet_day_selection']
        _, expected_news, _, audit = select_quiet_day(selection.get('all_candidates_scored', []), policy,
            selection.get('window', {}), meta.get('quiet_day_recovery'),
            saved.get('research_count', 1), saved.get('max_news_count'))
        if audit != saved:
            return ['Quiet-day selection receipt does not match candidate evidence and policy']
        supplemental = {c['event_id']: c['quiet_day_admission'] for c in expected_news if c.get('quiet_day_admission')}
    approved = selection.get('selected_news', []) + selection.get('selected_research', [])
    allowed = {}
    errors = []
    for c in approved:
        a = c.get('editorial_assessment', {})
        try:
            packet = c.get('editorial_packet', {})
            checked = validate_assessment(a, packet, policy)
            primary = next(s for s in packet['sources'] if s['source_id'] == a['primary_source_id'])
            if (checked['input_sha256'] != a.get('input_sha256') or checked['score'] != a.get('score')
                    or (checked['policy_sha256'] != a.get('policy_sha256')
                        and not (policy.get('admission_contract') != CONTRACT
                                 and a.get('policy_sha256') == digest(meta['policy'])))
                    or (policy.get('admission_contract') == CONTRACT
                        and checked['admission_route'] != a.get('admission_route'))
                    or c.get('source_text') != primary['text']
                    or digest(c['source_text']) != c.get('editorial_source_text_sha256')
                    or canonical_url(c.get('primary_url')) != canonical_url(primary['url'])):
                raise EditorialError('Evidence/score changed after assessment')
        except (EditorialError, KeyError, TypeError, StopIteration):
            errors.append('Stale or invalid assessment evidence: ' + c.get('title', ''))
            continue
        decision, _ = eligibility(a, policy)
        if c.get('quiet_day_admission'):
            if c['quiet_day_admission'] != supplemental.get(c.get('event_id')):
                errors.append('Invalid quiet-day admission: ' + c.get('title', ''))
                continue
            decision, _ = eligibility(a, {**policy, 'publish_threshold': c['quiet_day_admission']['admission_threshold']})
        # V2.1 records hashed their full persisted policy, including execution
        # settings. Keep those historical records verifiable without resending.
        valid_hashes = {digest(assessment_policy(policy)), digest(meta['policy'])}
        if decision != 'include' or a.get('policy_sha256') not in valid_hashes:
            errors.append('Selected event lacks a current qualifying assessment: ' + c.get('title', ''))
            continue
        for source in packet['sources']:
            if source['read_status'] == 'read':
                allowed[canonical_url(source['url'])] = c['event_id']
    seen = set()
    seen_families = set()
    papers = content.get('research_items') or ([content['paper']] if isinstance(content.get('paper'), dict) else [])
    for item in content.get('news', []) + papers:
        family = event_family_key(item)
        if family and family in seen_families:
            errors.append('Repeated final event family: ' + item.get('title', ''))
        if family:
            seen_families.add(family)
        if digest(item) in retrospectives or digest(item) in manual_news:
            continue
        urls = [item['url']] if item.get('url') else [x.get('url') for x in item.get('links', [])]
        ids = {allowed[canonical_url(u)] for u in urls if canonical_url(u) in allowed}
        # Arbitrary coverage aliases may not launder an unapproved primary URL.
        if len(ids) != 1:
            errors.append('Unselected/unscored final source: ' + item.get('title', ''))
        elif seen.intersection(ids):
            errors.append('Repeated final event: ' + item.get('title', ''))
        for eid in ids:
            if eid in supplemental:
                label = supplemental[eid]['label']
                if not item.get('title', '').startswith('【' + label + '】'):
                    errors.append('Missing quiet-day disclosure label: ' + item.get('title', ''))
                if label == '近期补漏' and supplemental[eid]['original_published_at'] not in item.get('boundary', ''):
                    errors.append('Missing original publication date for catch-up item')
        seen.update(ids)
    return errors
