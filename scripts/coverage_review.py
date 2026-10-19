"""Reconcile high-value candidates with final content before production send.

Review decisions are explicit editor records, not independent approval proof.
Unselected leads can remain pending advisories without blocking delivery.
Refreshing the report never turns an empty or stale decision into a pass.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path

from daily_config import load_selection_policy
from selection_diagnostics import aihot_id, final_items, item_urls, matches, normalize_url
from source_verification import high_value_reasons
from event_editorial import validate_final_selection
from editorial_value import omission_issues


class CoverageReviewError(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def specific_reason(reason):
    value = str(reason).strip()
    # Reject common placeholder/score-only decisions; substance still requires
    # editorial judgment, which a string validator cannot establish.
    return bool(value) and not re.fullmatch(
        r'[\s\W]*(?:待定|待复核|略|不重要|低分|分数低|分数不足|未达阈值|名额已满|条数限制|篇幅限制|时间有限|'
        r'low score|score too low|count limit|reading budget|tbd)[\s\W]*', value, re.I)


def refresh_review(selection_path, content_path, review_path, policy=None):
    policy = policy or load_selection_policy()
    omission_policy = policy.get('coverage_review', {}).get('omission_policy', 'blocking')
    if omission_policy not in {'blocking', 'advisory'}:
        raise CoverageReviewError('Unknown coverage omission policy: ' + str(omission_policy))
    selection = json.loads(Path(selection_path).read_text())
    content = json.loads(Path(content_path).read_text())
    if not isinstance(selection, dict) or not isinstance(selection.get('all_candidates_scored'), list) or not isinstance(content, dict):
        raise CoverageReviewError('Coverage review requires a scored candidate list and a content object.')
    review_path = Path(review_path)
    old = json.loads(review_path.read_text()) if review_path.exists() else {}
    old_rows = {r['id']: r for r in old.get('items', [])}
    final = final_items(content)
    chosen = selection.get('selected_news', []) + selection.get('selected_research', [])
    retrospective_path = review_path.parent / 'research_retrospective_approval.json'
    retrospective_approval = json.loads(retrospective_path.read_text()) if retrospective_path.exists() else None
    manual_news_path = review_path.parent / 'manual_news_editorial_approval.json'
    manual_news_approval = json.loads(manual_news_path.read_text()) if manual_news_path.exists() else None
    final_errors = validate_final_selection(selection, content, retrospective_approval, manual_news_approval)
    rows, seen = [], set()
    pool = [dict(c) for c in selection.get('all_candidates_scored', []) + selection.get('excluded_duplicates', []) + chosen]
    # Featured items can fail before scoring (missing feed match or original
    # URL resolution). They must still have an accountable omission record.
    for supplement in selection.get('source', {}).get('supplemental_news_sources', []):
        for entry in supplement.get('featured_entries', []):
            existing = next((c for c in pool if entry.get('item_id') in
                             {aihot_id(u) for u in item_urls(c)}), None)
            if existing is None:
                existing = {'title': entry['title'], 'aihot_url': 'https://aihot.news/items/' + entry['item_id'],
                            'primary_verification': {'status':'unresolved', 'reason':'Featured item has no scored feed candidate; check freshness, history and original source.'}}
                pool.append(existing)
            existing.update(aihot_daily_featured=True, aihot_daily_rank=entry.get('rank'))
        for error in supplement.get('primary_url_resolution_errors', []):
            pool.append({'title':error.get('title'), 'bestblogs_url':error.get('url'),
                         'bestblogs_score':policy.get('source_verification', {}).get('bestblogs_featured_min_score', 90),
                         'primary_verification':{'status':'unresolved', 'reason':error.get('reason')}})
    for c in pool:
        signals = high_value_reasons(c, policy.get('source_verification', {}), policy.get('model_update_priority', {}))
        urls = sorted(item_urls(c))
        target = {'title': c.get('title', ''), 'urls': urls}
        included = any(matches(item, target) for item in final)
        auto_picked = any(item_urls(x) & set(urls) for x in chosen)
        selected_missing = auto_picked and not included
        verification = c.get('primary_verification', {})
        needs_date_review = bool(verification.get('date_conflict'))
        if selected_missing:
            signals.append('selected_but_missing')
        if included and needs_date_review:
            signals.append('included_timing_conflict')
        assessment = c.get('editorial_assessment', {})
        if selection.get('event_editorial') and assessment:
            # Every unfinished assessment stays visible, including ordinary
            # candidates that lack a major-brand/aggregator flag.
            if c.get('editorial_decision', {}).get('action') == 'defer':
                signals.append('editorial_assessment_pending')
            if assessment.get('editorial_recommendation') == 'include':
                signals.append('editor_recommended')
        if not signals:
            continue
        identity = digest(normalize_url(c.get('primary_url') or c.get('source_url') or c.get('aihot_url') or c.get('bestblogs_url', '')) or c.get('title'))[:20]
        if identity in seen:
            continue
        seen.add(identity)
        if included and not needs_date_review:
            continue
        # An exclusion can depend on overlapping coverage in the final email.
        # Rewriting that email invalidates the decision even for omitted rows.
        fingerprint = digest({'review_contract': 3, 'candidate': c, 'signals': signals, 'included': included,
                              'final_content': content})
        prior = old_rows.get(identity, {})
        disposition = prior.get('disposition', '') if prior.get('fingerprint') == fingerprint else ''
        reason = prior.get('reason', '') if prior.get('fingerprint') == fingerprint else ''
        editor = prior.get('editor', '') if prior.get('fingerprint') == fingerprint else ''
        # Only an explicit, complete exclusion with no unresolved material or
        # selection discrepancy can close itself. Recommendations cannot be
        # recycled as exclusion rationales; old records need a fresh decision.
        issues = omission_issues(c) if assessment else []
        if verification and verification.get('status') != 'verified':
            issues.append('primary_retrieval_unresolved')
        if selected_missing:
            issues.append('selected_but_missing')
        if (selection.get('event_editorial') and not reason and not included and not needs_date_review
                and not issues and assessment.get('editorial_recommendation') == 'exclude'
                and assessment.get('assessment_status') == 'complete'
                and c.get('editorial_decision', {}).get('action') == 'exclude'):
            disposition = 'exclude'
            reason = c['editorial_decision']['reason']
            editor = 'event-evaluator (model judgment; not independent verification)'
        duplicate = c.get('content_duplicate_check', {})
        scores = c.get('scores', {})
        if needs_date_review:
            machine_reason = 'Official date fields conflict; establish timing before presenting this as a fresh release.'
        elif duplicate.get('is_duplicate'):
            machine_reason = 'Duplicate guard: ' + str(duplicate.get('reason', ''))
        elif selected_missing:
            machine_reason = 'Selected upstream but missing from final content.'
        elif verification.get('status') != 'verified':
            machine_reason = 'Primary retrieval unresolved/not attempted; score alone must not close this omission.'
        else:
            machine_reason = 'Not in final content; explain relevance, novelty or overlapping coverage explicitly.'
        valid = disposition in {'exclude', 'defer', 'retain_with_timing_note'} and specific_reason(reason) and bool(str(editor).strip())
        if str(editor).startswith('event-evaluator') and (issues or needs_date_review):
            valid = False
        if included and disposition != 'retain_with_timing_note':
            valid = False
        if not included and disposition == 'retain_with_timing_note':
            valid = False
        # An exclusion note cannot repair content lost during generation.
        if selected_missing:
            valid = False
        blocking = selected_missing or (included and needs_date_review) or omission_policy == 'blocking'
        review_class = ('selected_content_missing' if selected_missing else
                        'included_timing_conflict' if included and needs_date_review else
                        'omission_review_required' if blocking else 'omission_advisory')
        rows.append({'id': identity, 'fingerprint': fingerprint, 'title': c.get('title'),
                     'urls': urls, 'signals': signals, 'included': included,
                     'score': scores.get('final_news'), 'score_breakdown': c.get('score_breakdown', {}),
                     'primary_verification': verification, 'duplicate': duplicate,
                     'machine_reason': machine_reason, 'disposition': disposition,
                     'review_issues': issues,
                     'blocking': blocking, 'review_class': review_class,
                     'reason': reason, 'editor': editor, 'status': 'resolved' if valid else 'pending',
                     'automated_review': prior.get('automated_review') if prior.get('fingerprint') == fingerprint else None})
    report = {'schema_version': 2, 'kind': 'high_value_coverage_review',
              'omission_policy': omission_policy,
              'updated_at': datetime.now(timezone.utc).isoformat(),
              'selection_sha256': hashlib.sha256(Path(selection_path).read_bytes()).hexdigest(),
              'content_sha256': hashlib.sha256(Path(content_path).read_bytes()).hexdigest(),
              'summary': {'high_value_omissions_or_timing_issues': len(rows),
                          'pending': sum(r['status'] == 'pending' for r in rows),
                          'blocking_pending': sum(r['status'] == 'pending' and r['blocking'] for r in rows),
                          'advisory_pending': sum(r['status'] == 'pending' and not r['blocking'] for r in rows),
                          'final_selection_errors': len(final_errors)},
              'final_selection_errors': final_errors,
              'items': rows,
              'source_health': selection.get('source', {}).get('official_source_errors', [])}
    review_path.parent.mkdir(parents=True, exist_ok=True)
    review_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    lines = ['# 发送前高价值覆盖复核', '',
             '未入选线索保留待复核状态，不冒充已经审核；advisory 策略下不阻断发送，也不触发失败告警。',
             '已选条目丢失、已刊内容时间冲突、最终选源证据违规仍阻断发送。', '',
             f"遗漏策略：{omission_policy}；待复核：{report['summary']['pending']} 条；"
             f"阻断：{report['summary']['blocking_pending']} 条；建议：{report['summary']['advisory_pending']} 条", '']
    for error in final_errors:
        lines += ['- 最终入选校验失败：' + error, '']
    for row in rows:
        lines += [f"## {row['title']}", f"- ID：`{row['id']}`", f"- 来源：{', '.join(row['urls'])}",
                  f"- 信号：{', '.join(row['signals'])}；新闻分：{row['score']}",
                  f"- 核验状态：{row['primary_verification'].get('status', 'not_attempted')}",
                  f"- 当前原因：{row['machine_reason']}", f"- 编辑决定：{row['disposition'] or '待复核'}",
                  f"- 发送影响：{'阻断' if row['blocking'] else '仅建议'}（{row['review_class']}）",
                  f"- 编辑理由：{row['reason'] or '待填写'}", '']
    review_path.with_suffix('.md').write_text('\n'.join(lines))
    return report


def assert_ready(selection_path, content_path, review_path, policy=None):
    report = refresh_review(selection_path, content_path, review_path, policy)
    if report.get('final_selection_errors'):
        raise CoverageReviewError('Final content violates event selection: ' + '; '.join(report['final_selection_errors']))
    if report['summary']['blocking_pending']:
        raise CoverageReviewError(f"{report['summary']['blocking_pending']} blocking coverage issues require correction/review: {review_path}")
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--selection', type=Path, required=True)
    p.add_argument('--content', type=Path, required=True)
    p.add_argument('--review', type=Path, required=True)
    p.add_argument('--check', action='store_true')
    p.add_argument('--item-id')
    p.add_argument('--disposition', choices=['exclude', 'defer', 'retain_with_timing_note'])
    p.add_argument('--reason')
    p.add_argument('--editor')
    a = p.parse_args()
    report = refresh_review(a.selection, a.content, a.review)
    if a.item_id:
        if not all([a.disposition, a.reason and a.reason.strip(), a.editor and a.editor.strip()]):
            p.error('An item decision requires --disposition, --reason, and --editor.')
        row = next((r for r in report['items'] if r['id'] == a.item_id), None)
        if row is None:
            p.error('Unknown item id; refresh and inspect the report.')
        row.update(disposition=a.disposition, reason=a.reason, editor=a.editor)
        a.review.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        report = refresh_review(a.selection, a.content, a.review)
    if a.check:
        assert_ready(a.selection, a.content, a.review)
    print(json.dumps(report['summary'], ensure_ascii=False))


if __name__ == '__main__':
    main()
