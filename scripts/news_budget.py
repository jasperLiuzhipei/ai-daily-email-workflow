"""Publication volume limits applied only to already-qualified news.

Research slots and source/evidence scores are unchanged. Excess candidates are
deferred with a receipt; a short issue is valid and never padded to a quota.
"""
from copy import deepcopy


def checked_budget(raw):
    if not isinstance(raw, dict) or type(raw.get('enabled')) is not bool:
        raise ValueError('news_budget.enabled must be boolean')
    result = dict(raw)
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
        a = c['editorial_assessment']
        return (-a['score'], a.get('urgency') != 'time_sensitive', c['event_id'])
    def catchup(c):
        return (c.get('quiet_day_admission', {}).get('label') == '近期补漏'
                or not c.get('editorial_news_window_eligible', True))
    fresh = sorted((c for c in items if not catchup(c)), key=rank)
    old = sorted((c for c in items if catchup(c)), key=rank)
    selected = fresh[:normal_max]
    expansions = []
    for item in fresh[normal_max:]:
        assessment = item['editorial_assessment']
        if len(selected) >= hard_max:
            break
        if (assessment['score'] >= settings['expansion_score']
                or assessment.get('urgency') == 'time_sensitive'):
            selected.append(item)
            expansions.append({'event_id': item['event_id'], 'title': item['title'],
                               'score': assessment['score'], 'urgency': assessment.get('urgency'),
                               'reason': '高价值或有时限的新进展，使用扩容名额。'})
    catchup_slots = min(settings['max_catchup'], max(0, settings['normal_min'] - len(fresh)),
                       max(0, hard_max - len(selected)))
    selected.extend(old[:catchup_slots])
    selected.sort(key=rank)
    selected_ids = {c['event_id'] for c in selected}
    deferred = []
    for c in items:
        if c['event_id'] in selected_ids:
            continue
        code = 'catchup_budget' if catchup(c) else 'publication_volume_budget'
        reason = ('当天新闻充足或补漏名额已满，保留候选但不占本期篇幅。' if catchup(c)
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
    return selected, deferred, audit
