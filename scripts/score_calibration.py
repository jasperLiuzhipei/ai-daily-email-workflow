#!/usr/bin/env python3
"""Measure editorial decisions against explicit human labels; never invent labels."""
import argparse
from collections import Counter
import json
from pathlib import Path

ACTIONS={'include','exclude','defer'}


def evaluate(rows):
    matrix=Counter();deltas=[];unreviewed=0;seen=set()
    for row in rows:
        if row['event_id'] in seen:raise ValueError('Duplicate calibration event')
        seen.add(row['event_id'])
        actual=row['actual_action'];expected=row.get('expected_action')
        if actual not in ACTIONS:raise ValueError('Invalid actual action')
        if not expected:
            unreviewed+=1;continue
        if expected not in ACTIONS or not row.get('reviewed_by') or not str(row.get('reason','')).strip():
            raise ValueError('A label requires a reviewer and substantive reason')
        matrix[expected,actual]+=1
        scores=row.get('repeated_scores') or []
        if scores:
            if any(type(s) not in (int,float) or not 0<=s<=100 for s in scores):raise ValueError('Invalid score')
            deltas.append(max(scores)-min(scores))
    true=matrix['include','include'];published=sum(n for (e,a),n in matrix.items() if a=='include');important=sum(n for (e,a),n in matrix.items() if e=='include')
    return {'labelled_events':sum(matrix.values()),'unreviewed_events':unreviewed,
            'selection_precision':true/published if published else None,
            'important_event_recall':true/important if important else None,
            'mean_repeat_score_range':sum(deltas)/len(deltas) if deltas else None,
            'confusion':[{'expected':e,'actual':a,'count':n} for (e,a),n in sorted(matrix.items())],
            'status':'measured' if matrix else 'human_labels_required'}


def template(selection):
    return [{'event_id':c['event_id'],'title':c['title'],'score':c['editorial_assessment'].get('score'),
             'actual_action':c['editorial_decision']['action'],'expected_action':None,'reviewed_by':'','reason':''}
            for c in selection['all_candidates_scored']]


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--selection',type=Path);p.add_argument('--labels',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    if bool(a.selection)==bool(a.labels):p.error('Choose --selection (export labels) or --labels (measure)')
    result=template(json.loads(a.selection.read_text())) if a.selection else evaluate(json.loads(a.labels.read_text()))
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')

if __name__=='__main__':main()
