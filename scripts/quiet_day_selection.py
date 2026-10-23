"""Bounded quiet-day admission; scores and evidence contracts stay unchanged."""
from copy import deepcopy
from datetime import datetime, timedelta
from editorial_value import CONTRACT
import publication_priority

DEFAULTS = {'enabled': False, 'min_news_trigger': 3, 'thresholds': [65, 60],
            'max_supplemental_news': 4, 'catchup_hours': 72,
            'recovery_max_events': 12, 'recovery_max_urls': 3,
            'recovery_budget_seconds': 180}


def checked_quiet_policy(raw):
    q = {**DEFAULTS, **(raw or {})}
    if type(q['enabled']) is not bool:
        raise ValueError('quiet_day.enabled must be boolean')
    for key in DEFAULTS.keys() - {'enabled', 'thresholds'}:
        if type(q[key]) is not int or q[key] <= 0:
            raise ValueError('quiet_day positive integer required: ' + key)
    t = q['thresholds']
    if (not isinstance(t, list) or not t or any(type(x) is not int or not 60 <= x <= 100 for x in t)
            or t != sorted(set(t), reverse=True)):
        raise ValueError('Quiet-day thresholds must descend, with a floor of 60')
    return q


def is_catchup(c, window, hours):
    if c.get('editorial_news_window_eligible', True):
        return False
    # Explicit paper-lookback records remain research, not old news filler.
    if c.get('research_window_strategy') in {'paper_lookback', 'direct_research_lookback', 'content_reclassified_research_lookback'}:
        return False
    try:
        end = datetime.fromisoformat(window['end_at'].replace('Z', '+00:00'))
        published = datetime.fromisoformat(str(c['published_at']).replace('Z', '+00:00'))
        return end - timedelta(hours=hours) <= published <= end
    except (KeyError, TypeError, ValueError):
        return False


def select_quiet_day(candidates, policy, window, recovery, research_count=1, max_news_count=None):
    settings = policy.get('news_budget')
    if not settings or not settings.get('enabled'):
        return _select_quiet_day(candidates, policy, window, recovery, research_count, max_news_count)
    # Build the complete qualified pool first. A caller's hard cap must not
    # discard timely candidates before catch-up and expansion decisions.
    research, news, rejected, audit = _select_quiet_day(candidates, policy, window, recovery, research_count, None)
    from news_budget import apply_budget
    news, deferred, budget = apply_budget(news, settings, max_news_count)
    rejected.extend(deferred)
    kept = {c['event_id'] for c in news}
    audit.update(max_news_count=max_news_count, final_news_count=len(news), news_budget=budget,
                 added_event_ids=[key for key in audit['added_event_ids'] if key in kept])
    return research, news, rejected, audit


def _select_quiet_day(candidates, policy, window, recovery, research_count=1, max_news_count=None):
    from event_editorial import select_events, eligibility
    q = checked_quiet_policy(policy.get('quiet_day'))
    items = deepcopy(candidates)
    for c in items:
        c.pop('quiet_day_admission', None)
    research, news, rejected = select_events(items, policy, research_count, max_news_count)
    audit = {'enabled': q['enabled'], 'triggered': False, 'normal_news_count': len(news),
             'base_threshold': policy['publish_threshold'], 'effective_threshold': policy['publish_threshold'],
             'added_event_ids': [], 'max_news_count': max_news_count, 'research_count': research_count}
    if policy.get('admission_contract') == CONTRACT:
        # Preserve bounded, labelled catch-up without the former minimum-count
        # trigger or lower score thresholds. Paper-lookback never becomes news.
        from event_editorial import event_family_key
        families = {event_family_key(c) for c in research + news} - {''}
        additions = []
        def rank(c):
            if publication_priority.enabled(policy.get('news_budget')):
                return publication_priority.news_rank(c, policy['news_budget'])
            return (-(c.get('editorial_assessment', {}).get('score') or 0), c['event_id'])
        for c in sorted(rejected, key=rank):
            family = event_family_key(c)
            if (c.get('editorial_news_window_eligible', True)
                    or not is_catchup(c, window, q['catchup_hours'])
                    or c.get('backlog_status') in {'published', 'dismissed', 'expired_review'}
                    or c.get('content_duplicate_check', {}).get('is_duplicate')
                    or c.get('event_duplicate_check', {}).get('is_duplicate')
                    or (family and family in families)
                    or eligibility(c.get('editorial_assessment', {}), policy)[0] != 'include'):
                continue
            if max_news_count is not None and len(news) + len(additions) >= max_news_count:
                continue
            c['quiet_day_admission'] = {'label': '近期补漏', 'original_published_at': str(c.get('published_at', '')),
                'base_threshold': policy['publish_threshold'], 'admission_threshold': policy['publish_threshold'],
                'score_unchanged': c['editorial_assessment']['score']}
            c['display_tier'] = 'deep' if (publication_priority.enabled(policy.get('news_budget'))
                and publication_priority.level(c) == 2) else 'brief'
            c['editorial_decision'] = {'action': 'include', 'reason': '近期补漏，按同一价值准入规则：' + c['editorial_assessment']['decision_reason']}
            additions.append(c)
            if family: families.add(family)
        news.extend(additions)
        news.sort(key=rank)
        audit.update(mode='value_qualified_catchup', added_event_ids=[c['event_id'] for c in additions],
                     final_news_count=len(news))
        added = set(audit['added_event_ids'])
        rejected = [c for c in rejected if c['event_id'] not in added]
        for rank, c in enumerate(news, 1): c['daily_rank'] = rank
        return research, news, rejected, audit
    if not q['enabled'] or len(news) >= q['min_news_trigger']:
        return research, news, rejected, audit
    audit['triggered'] = True
    if not recovery or recovery.get('status') not in {'completed', 'exhausted', 'nothing_pending'}:
        audit['blocked_reason'] = 'Recovery must finish before threshold relaxation'
        return research, news, rejected, audit
    from event_editorial import event_family_key
    used = {c['event_id'] for c in research + news}
    used_families = {event_family_key(c) for c in research + news} - {''}
    pool = []
    for c in rejected:
        a = c.get('editorial_assessment', {})
        family = event_family_key(c)
        if (c['event_id'] in used or c.get('event_duplicate_check', {}).get('is_duplicate')
                or c.get('content_duplicate_check', {}).get('is_duplicate')
                or (family and family in used_families)):
            continue
        if c.get('backlog_status') in {'published', 'dismissed', 'expired_review'}:
            continue
        if not c.get('editorial_news_window_eligible', True) and not is_catchup(c, window, q['catchup_hours']):
            continue
        # At least one concrete reader or learning benefit is still required.
        dims = a.get('dimensions', {})
        if max((dims.get(k, {}).get('level') or 0) for k in ('reader_value', 'learning_value')) < 3:
            continue
        if eligibility(a, {**policy, 'publish_threshold': q['thresholds'][-1]})[0] == 'include':
            pool.append(c)
    pool.sort(key=lambda c: (-c['editorial_assessment']['score'], c['event_id']))
    available = q['max_supplemental_news']
    if max_news_count is not None:
        available = min(available, max(0, max_news_count - len(news)))
    additions = []
    # First try recovered/catch-up stories at the normal threshold.
    for threshold in [policy['publish_threshold']] + q['thresholds']:
        additions = []
        round_families = set(used_families)
        for c in pool:
            if c['editorial_assessment']['score'] < threshold or len(additions) >= available:
                continue
            family = event_family_key(c)
            if family and family in round_families:
                continue
            additions.append(c)
            if family:
                round_families.add(family)
        audit['effective_threshold'] = threshold
        if len(news) + len(additions) >= q['min_news_trigger']:
            break
    for c in additions:
        catchup = is_catchup(c, window, q['catchup_hours'])
        label = '近期补漏' if catchup else '补充观察'
        c['quiet_day_admission'] = {'label': label, 'original_published_at': str(c.get('published_at', '')),
            'base_threshold': policy['publish_threshold'], 'admission_threshold': audit['effective_threshold'],
            'score_unchanged': c['editorial_assessment']['score']}
        c['display_tier'] = 'brief'
        c['editorial_decision'] = {'action': 'include', 'reason': label + '：补漏后依据限定门槛入选；' + c['editorial_assessment']['decision_reason']}
    news.extend(additions)
    audit['added_event_ids'] = [c['event_id'] for c in additions]
    rejected = [c for c in rejected if c['event_id'] not in set(audit['added_event_ids'])]
    for rank, c in enumerate(news, 1):
        c['daily_rank'] = rank
    audit['final_news_count'] = len(news)
    audit['below_target'] = len(news) < q['min_news_trigger']
    return research, news, rejected, audit


def apply_quiet_labels(news, materials):
    """Labels come from selected provenance, never from model compliance."""
    from event_editorial import canonical_url
    by_url = {canonical_url(m.get('resolved_url') or m.get('primary_url')): m for m in materials}
    for card in news:
        source = by_url.get(canonical_url(card.get('url')), {})
        admission = source.get('quiet_day_admission')
        if not admission:
            continue
        label = admission['label']
        prefix = f'【{label}】'
        if not card.get('title', '').startswith(prefix):
            card['title'] = prefix + card.get('title', '')
        card['quiet_day_admission'] = deepcopy(admission)
        if label == '近期补漏':
            note = '原始发布时间：' + admission['original_published_at'] + '；作为近期未刊发内容补充。'
            if note not in card.get('boundary', ''):
                card['boundary'] = note + ' ' + card.get('boundary', '')
    return news
