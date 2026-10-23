"""Keep independently valid model groups; never choose an overlapping merge."""
from collections import Counter

from event_editorial import EditorialError, group_sources

VERSION = 'disjoint-group-recovery-v1'


def recover_disjoint_groups(sources, proposed):
    if not isinstance(proposed, list):
        return [], {'status': 'unmerged_invalid_shape', 'retained_groups': 0}
    # Count across the complete response, including malformed groups. A source
    # mentioned in contradictory groups must not be assigned to either one.
    counts = Counter(sid for group in proposed if isinstance(group, dict)
                     for sid in (group.get('source_ids') if isinstance(group.get('source_ids'), list) else [])
                     if isinstance(sid, str))
    overlaps = {sid for sid, count in counts.items() if count > 1}
    accepted, rejected = [], []
    for index, group in enumerate(proposed):
        try:
            group_sources(sources, [group])
            if overlaps.intersection(group['source_ids']):
                raise EditorialError('Source appears in more than one proposed group')
        except (EditorialError, TypeError, AttributeError, KeyError):
            rejected.append(index)
            continue
        accepted.append(group)
    group_sources(sources, accepted)
    return accepted, {'status': 'partial_disjoint_recovery' if accepted else 'unmerged_after_invalid_grouping',
                      'retained_groups': len(accepted), 'rejected_group_indices': rejected,
                      'quarantined_source_ids': sorted(overlaps)}
