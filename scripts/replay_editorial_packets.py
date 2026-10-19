#!/usr/bin/env python3
"""Reassess frozen evidence in an isolated folder, without publishing or fetching.

Only editorial_packet data is sent to the configured text provider. Prior
scores, titles rewritten by the evaluator, decisions and labels are withheld.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

from event_editorial import checked_policy, eligibility, digest
from event_editorial_runtime import Evaluator, assess_event, save, PROMPT_PATH, POLICY_PATH
from editorial_runtime_support import RuntimeBudget


def replay(selection_path, output, ids=None, policy_path=POLICY_PATH):
    selection_path, output = Path(selection_path), Path(output)
    policy = checked_policy(json.loads(Path(policy_path).read_text()))
    selection = json.loads(selection_path.read_text())
    candidates = selection['all_candidates_scored']
    if ids:
        wanted = set(ids)
        candidates = [c for c in candidates if c['event_id'] in wanted]
        if {c['event_id'] for c in candidates} != wanted:
            raise ValueError('Unknown replay event id')
    budget = RuntimeBudget(policy)
    evaluator = Evaluator(budget=budget)
    prompt = PROMPT_PATH.read_text()
    def run(c):
        original = c['editorial_packet']
        if c['editorial_assessment'].get('input_sha256') != digest(original):
            raise ValueError('Historical packet hash mismatch: ' + c['event_id'])
        materials = {s['source_id']: s for s in original['sources']}
        event = {'event_id': c['event_id'], 'merge_reason': original.get('merge_reason', ''),
                 'sources': [{'editorial_source_id': sid} for sid in materials]}
        assessment, packet = assess_event(event, materials, policy, evaluator,
                                         output / 'assessments', prompt)
        decision, reason = eligibility(assessment, policy)
        result = {'event_id': c['event_id'], 'title': assessment.get('event_title', c['title']),
                  'old_score': c['editorial_assessment'].get('score'),
                  'old_action': c.get('editorial_decision', {}).get('action'),
                  'new_score': assessment.get('score'), 'new_action': decision,
                  'admission_route': assessment.get('admission_route'), 'reason': reason,
                  'assessment': assessment, 'packet': packet}
        save(output / 'events' / (c['event_id'] + '.json'), result)
        print(json.dumps({k: result[k] for k in ['event_id', 'title', 'old_score', 'new_score', 'new_action']}, ensure_ascii=False), flush=True)
        return result
    with ThreadPoolExecutor(max_workers=policy['workers']) as pool:
        rows = list(pool.map(run, candidates))
    report = {'kind': 'frozen_evidence_reassessment_not_publication',
              'selection_sha256': digest(selection), 'policy': policy,
              'prompt_sha256': digest(prompt), 'runtime': budget.summary(), 'events': rows}
    save(output / 'report.json', report)
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--selection', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--event-id', action='append')
    p.add_argument('--policy', type=Path, default=POLICY_PATH)
    a = p.parse_args()
    replay(a.selection, a.output, a.event_id, a.policy)
