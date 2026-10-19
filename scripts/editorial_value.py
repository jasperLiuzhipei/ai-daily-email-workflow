"""Content admission and omission review, separate from evidence confidence.

Routes never infer value from a vendor, URL, model name, popularity or rank.
The weighted score remains unchanged and visible for historical comparison.
"""
CONTRACT = 'value-evidence-v3'


def value_route(a, policy):
    """A strong decision OR learning contribution need not excel at both."""
    if policy.get('admission_contract') != CONTRACT:
        return None
    if (a.get('editorial_recommendation') != 'include'
            or a.get('evidence_status') not in {'supported', 'limited'}
            or a.get('novelty') not in {'new', 'incremental'}
            or a.get('score') is None or a['score'] < policy['brief_value_floor']):
        return None
    levels = {k: v.get('level') for k, v in a.get('dimensions', {}).items()}
    if any(levels.get(k) is None for k in ('change', 'impact', 'reader_value', 'learning_value')):
        return None
    if levels['reader_value'] < 3 or levels['impact'] < 2:
        return None
    # Either broad, substantial decision value or one exceptional contribution
    # is required. A pool of narrow 2/3-level papers must not all become news.
    if (levels['change'] >= 3 and levels['learning_value'] >= 1
            and (levels['impact'] >= 3 or
                 max(levels[k] for k in ('change', 'impact', 'reader_value')) == 4)):
        return 'substantive_change'
    if (levels['learning_value'] >= 3 and levels['change'] >= 2
            and (levels['impact'] >= 3 or
                 max(levels[k] for k in ('learning_value', 'impact', 'reader_value')) == 4)):
        return 'substantive_learning'
    return None


def omission_issues(candidate):
    """Completed evaluation alone is not an adequate exclusion review."""
    a = candidate.get('editorial_assessment', {})
    issues = []
    if a.get('assessment_status') != 'complete':
        issues.append('assessment_incomplete')
    if a.get('editorial_recommendation') == 'include':
        issues.append('recommended_but_omitted')
    if any(s.get('read_status') != 'read'
           for s in candidate.get('editorial_packet', {}).get('sources', [])):
        issues.append('event_material_incomplete')
    if a.get('evidence_status') in {'unresolved', 'conflicting'}:
        issues.append('evidence_unresolved')
    # Old records lack a structured recommendation; do not guess their intent
    # from words such as 推荐, which also occur inside 不推荐.
    if not a.get('editorial_recommendation'):
        issues.append('legacy_recommendation_unknown')
    return issues
