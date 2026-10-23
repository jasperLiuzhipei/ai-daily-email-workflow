"""Evidence-backed news placement, independent of explanatory richness.

The original weighted assessment remains intact. Priority never grants
admission, overrides a date/duplicate check, or infers value from popularity.
"""
CONTRACT = 'publication-priority-v1'
LEVELS = {'standard': 0, 'important': 1, 'lead': 2}


def validate_priority(a, evidence, readable_evidence):
    p = a.get('publication_priority')
    if (not isinstance(p, dict) or p.get('level') not in LEVELS
            or not isinstance(p.get('reason'), str) or len(p['reason'].strip()) < 20
            or not isinstance(p.get('evidence_refs'), list)
            or any(type(i) is not int or not 0 <= i < len(evidence) for i in p['evidence_refs'])):
        raise ValueError('Missing evidenced publication priority')
    if p['level'] != 'standard':
        if (a.get('editorial_recommendation') != 'include'
                or a.get('evidence_status') not in {'supported', 'limited'}
                or a.get('novelty') not in {'new', 'incremental'}
                or not readable_evidence.intersection(p['evidence_refs'])):
            raise ValueError('Elevated priority needs an admissible new fact and readable evidence')
    if p['level'] == 'lead':
        levels = {k: v.get('level') or 0 for k, v in a['dimensions'].items()}
        if (levels['impact'] < 3 or levels['reader_value'] < 3
                or max(levels['change'], levels['learning_value']) < 3):
            raise ValueError('Lead priority conflicts with substantive impact or reader value')


def enabled(settings):
    return (settings or {}).get('priority_mode') == CONTRACT


def level(candidate):
    a = candidate.get('editorial_assessment', {})
    return LEVELS.get(a.get('publication_priority', {}).get('level'), 0)


def news_rank(candidate, settings=None):
    a = candidate['editorial_assessment']
    legacy = (-(a.get('score') or 0), a.get('urgency') != 'time_sensitive', candidate['event_id'])
    return (-level(candidate), *legacy) if enabled(settings) else legacy


def needs_source_followup(a, policy):
    """Readable secondary reporting can still leave a major primary-source gap."""
    return (policy.get('priority_contract') == CONTRACT
            and a.get('assessment_status') == 'complete'
            and a.get('editorial_recommendation') == 'include'
            and a.get('evidence_status') == 'limited'
            and a.get('publication_priority', {}).get('level') in {'lead', 'important'})
