"""Publication volume limits applied only to already-qualified news.

Research slots and source/evidence scores are unchanged. Excess candidates are
deferred with a receipt; a short issue is valid and never padded to a quota.
"""
from copy import deepcopy
import publication_priority


def checked_budget(raw):
    if not isinstance(raw, dict) or type(raw.get('enabled')) is not bool:
        raise ValueError('news_budget.enabled must be boolean')
    result = dict(raw)
    if result.get('priority_mode') not in (None, publication_priority.CONTRACT):
        raise ValueError('Unknown news priority mode')
    if 'lead_catchup_extra' in result and (
            type(result['lead_catchup_extra']) is not int or not 0 <= result['lead_catchup_extra'] <= 2
            or not publication_priority.enabled(result)):
        raise ValueError('Lead catchup exception must be bounded to 0..2 under priority mode')
    if 'catchup_competes' in result and type(result['catchup_competes']) is not bool:
        raise ValueError('news_budget.catchup_competes must be boolean')
    if 'catchup_preserve_mix' in result and type(result['catchup_preserve_mix']) is not bool:
        raise ValueError('news_budget.catchup_preserve_mix must be boolean')
    for key in ('normal_min', 'normal_max', 'hard_max', 'max_catchup', 'expansion_score'):
        if type(result.get(key)) is not int:
            raise ValueError('news_budget integer required: ' + key)
    if not (0 < result['normal_min'] <= result['normal_max'] <= result['hard_max'] <= 20
            and 0 <= result['max_catchup'] <= result['normal_min']
            and 0 <= result['expansion_score'] <= 100):
        raise ValueError('Invalid news budget bounds')
    return result


def apply_budget(news, settings, explicit_max=None):
    settings = checked_budget(settings)
    items = deepcopy(news)
    if explicit_max is not None and (type(explicit_max) is not int or explicit_max < 0):
        raise ValueError('Explicit news maximum must be a non-negative integer')
    hard_max = min(settings['hard_max'], explicit_max) if explicit_max is not None else settings['hard_max']
    normal_max = min(settings['normal_max'], hard_max)
    def rank(c):
        return publication_priority.news_rank(c, settings)
    def catchup(c):
        return (c.get('quiet_day_admission', {}).get('label') == '近期补漏'
                or not c.get('editorial_news_window_eligible', True))
    fresh = sorted((c for c in items if not catchup(c)), key=rank)
    old = sorted((c for c in items if catchup(c)), key=rank)
    selected = fresh[:normal_max]
    replacements = []
    lead_exceptions = []
    if settings.get('catchup_competes', False):
        catchup_pool = old[:settings['max_catchup']]
        extra = [c for c in old[settings['max_catchup']:] if publication_priority.level(c) == 2
                 and publication_priority.enabled(settings)][:settings.get('lead_catchup_extra', 0)]
        catchup_pool += extra
        for item in catchup_pool:
            if len(selected) < min(settings['normal_min'], hard_max):
                selected.append(item)
                if item in extra: lead_exceptions.append(item['event_id'])
                continue
            replaceable = [c for c in selected if not catchup(c)]
            if publication_priority.enabled(settings) and replaceable:
                lowest = min(publication_priority.level(c) for c in replaceable)
                replaceable = [c for c in replaceable if publication_priority.level(c) == lowest]
            if settings.get('catchup_preserve_mix', False):
                def lane(c):
                    return c['editorial_assessment'].get('content_kind') in {'research', 'engineering'}
                peers = [c for c in replaceable if lane(c) == lane(item)]
                if peers:
                    replaceable = peers
            weakest = max(replaceable, key=rank) if replaceable else None
            better = weakest and (rank(item)[:-1] < rank(weakest)[:-1] if publication_priority.enabled(settings)
                                  else item['editorial_assessment']['score'] > weakest['editorial_assessment']['score'])
            if better:
                selected.remove(weakest)
                selected.append(item)
                if item in extra: lead_exceptions.append(item['event_id'])
                replacements.append({'included_event_id': item['event_id'],
                                     'displaced_event_id': weakest['event_id'],
                                     'reason': '已合格补漏在总篇幅内替换价值较低的候选；优先同类替换以保留新闻与方法的覆盖。'})
    expansions = []
    for item in fresh:
        if any(c['event_id'] == item['event_id'] for c in selected):
            continue
        assessment = item['editorial_assessment']
        if len(selected) >= hard_max:
            break
        if (assessment['score'] >= settings['expansion_score']
                or publication_priority.enabled(settings) and publication_priority.level(item) == 2
                or assessment.get('urgency') == 'time_sensitive'):
            selected.append(item)
            expansions.append({'event_id': item['event_id'], 'title': item['title'],
                               'score': assessment['score'], 'urgency': assessment.get('urgency'),
                               'reason': '高价值或有时限的新进展，使用扩容名额。'})
    catchup_slots = min(settings['max_catchup'], max(0, settings['normal_min'] - len(fresh)),
                       max(0, hard_max - len(selected)))
    if not settings.get('catchup_competes', False):
        selected.extend(old[:catchup_slots])
    selected.sort(key=rank)
    selected_ids = {c['event_id'] for c in selected}
    deferred = []
    for c in items:
        if c['event_id'] in selected_ids:
            continue
        code = 'catchup_budget' if catchup(c) else 'publication_volume_budget'
        catchup_reason = ('补漏名额已满或编辑价值未超过可替换候选，保留候选但不占本期篇幅。'
                          if settings.get('catchup_competes', False) else
                          '当天新闻充足或补漏名额已满，保留候选但不占本期篇幅。')
        reason = (catchup_reason if catchup(c)
                  else '已满足内容准入，因本期新闻数量预算暂缓；不降低评分、不视为生成失败。')
        c['editorial_decision'] = {'action': 'defer', 'code': code, 'reason': reason}
        deferred.append(c)
    for index, c in enumerate(selected, 1):
        c['daily_rank'] = index
    audit = {'schema_version': 1, 'policy': settings, 'explicit_max': explicit_max,
             'effective_hard_max': hard_max, 'eligible_fresh_count': len(fresh),
             'eligible_catchup_count': len(old), 'selected_count': len(selected),
             'selected_catchup_count': sum(catchup(c) for c in selected),
             'below_normal_range': len(selected) < settings['normal_min'],
             'expansions': expansions,
             'deferred': [{'event_id': c['event_id'], 'title': c['title'], **c['editorial_decision']} for c in deferred]}
    if settings.get('catchup_competes', False):
        audit['catchup_replacements'] = replacements
    if publication_priority.enabled(settings):
        audit['priority_contract'] = publication_priority.CONTRACT
        audit['lead_catchup_exceptions'] = lead_exceptions
        audit['selected_priorities'] = [{'event_id': c['event_id'],
            **c['editorial_assessment']['publication_priority']} for c in selected]
    return selected, deferred, audit
