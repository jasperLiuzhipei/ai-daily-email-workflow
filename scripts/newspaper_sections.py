"""Keep newspaper section roles separate from source-selection depth scores."""
from copy import deepcopy

ROLES = {'lead', 'supplement', 'brief'}


def section_indices(news, *, categories=None):
    """Choose two feature positions without changing rank, score or display_tier.

    Explicit editorial placements win. Otherwise prefer deep items and the
    supplied editorial order. Untyped legacy issues retain their old category
    placement so rebuilding a historical issue does not move its topics.
    """
    if not news:
        return None, None
    categories = categories if categories is not None else [n.get('category') for n in news]
    if len(categories) != len(news):
        raise ValueError('Section categories must match the news list.')
    explicit = {}
    for index, item in enumerate(news):
        role = item.get('newspaper_role')
        if not role:
            continue
        if role not in ROLES:
            raise ValueError('Unknown newspaper_role: ' + str(role))
        if role in {'lead', 'supplement'}:
            if role in explicit:
                raise ValueError('Duplicate newspaper_role: ' + role)
            explicit[role] = index
    if any(item.get('newspaper_role') for item in news):
        if 'lead' not in explicit or (len(news) > 1 and 'supplement' not in explicit):
            raise ValueError('Incomplete newspaper section roles; recompose the missing feature before building.')
        return explicit['lead'], explicit.get('supplement')
    ordered = any(n.get('event_id') or n.get('daily_rank') or n.get('display_tier') in {'deep', 'brief'} for n in news)
    deep = [i for i, item in enumerate(news) if item.get('display_tier') == 'deep']
    methods = [i for i, category in enumerate(categories) if category == 'methods_and_views']
    lead = deep[0] if deep else (0 if ordered else methods[-1] if methods else 0)
    remaining = [i for i in range(len(news)) if i != lead]
    if not remaining:
        return lead, None
    supplement = next((i for i in deep if i != lead), remaining[0])
    if not ordered and methods and methods[0] != lead:
        supplement = methods[0]
    return lead, supplement


def planned_news_sources(sources, *, categories=None, allow_partial=False):
    """Attach layout roles before editorial generation; never promote scores."""
    planned = deepcopy(sources)
    # Targeted retries receive a subset of an already planned issue. Retain
    # its placements; the whole-issue build still requires complete sections.
    if allow_partial and planned and all(n.get('newspaper_role') in ROLES for n in planned):
        features = [n['newspaper_role'] for n in planned if n['newspaper_role'] != 'brief']
        if len(features) != len(set(features)):
            raise ValueError('Duplicate newspaper_role in targeted generation.')
        return planned
    lead, supplement = section_indices(planned, categories=categories)
    for index, item in enumerate(planned):
        item['newspaper_role'] = 'lead' if index == lead else 'supplement' if index == supplement else 'brief'
    return planned
