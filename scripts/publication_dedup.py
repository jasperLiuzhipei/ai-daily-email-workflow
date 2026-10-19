"""Reconcile resolved sources against sent issues, without network or model calls.

Only proved repeats are removed. Ambiguous developments remain eligible. All
edits happen before delivery and preserve the original selection evidence.
"""
from copy import deepcopy
from datetime import date, datetime
import json
from pathlib import Path
import re

from ai_daily_common import file_sha256, load_json_object
from editorial_backlog import item_urls
from event_editorial import canonical_url, digest


def cards(content):
    papers = content.get('research_items')
    if not isinstance(papers, list):
        papers = [content['paper']] if isinstance(content.get('paper'), dict) else []
    return content.get('news', []) + papers


def history(run_date, run_root=Path('daily_runs')):
    """Use sent records only; unreadable optional history cannot stop a daily."""
    ledger = run_root / 'send_ledger.json'
    result, warnings = [], []
    if not ledger.exists():
        return result, warnings
    try:
        records = load_json_object(ledger).get('prod', {})
        for day, record in records.items():
            try:
                age = (date.fromisoformat(run_date) - date.fromisoformat(day)).days
            except ValueError:
                continue
            if not 0 < age <= 7 or not isinstance(record, dict) or not record.get('sent_at'):
                continue
            value = record.get('normalized_content') or record.get('content')
            path = Path(value) if isinstance(value, str) and value else None
            if path is not None and not path.is_absolute():
                path = run_root.parent / path
            published = deepcopy(record.get('published_items') or [])
            if path and path.exists():
                try:
                    published = cards(load_json_object(path))
                except (OSError, ValueError) as exc:
                    warnings.append(f'{day}: content history unavailable ({type(exc).__name__})')
            selection = {}
            try:
                selection_path = run_root / day / f'source_selection_auto_{day}.json'
                if selection_path.exists():
                    selection = load_json_object(selection_path)
            except (OSError, ValueError) as exc:
                warnings.append(f'{day}: source history unavailable ({type(exc).__name__})')
            chosen = selection.get('selected_news', []) + selection.get('selected_research', [])
            for card in published:
                if not isinstance(card, dict):
                    continue
                urls = {canonical_url(u) for u in item_urls(card)}
                selected = next((c for c in chosen if urls & {canonical_url(u) for u in item_urls(c)}), {})
                result.append({'published_on': day, 'title': card.get('title', ''),
                               'urls': sorted(urls - {''}), 'evidence': verified_quotes(selected)})
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        warnings.append('Publication history unavailable: ' + type(exc).__name__)
    return result, warnings


def verified_quotes(candidate):
    from editorial_evidence import supporting_quotes
    try:
        return [q['quote'] for q in supporting_quotes(candidate) if q['read_status'] == 'read']
    except (ValueError, KeyError, TypeError):
        return []


# Retain model variants: Argon, Pro and Flash are distinct releases.
MODEL = re.compile(r'(?<![a-z0-9])(?:gemini|gpt|qwen|deepseek|claude|llama|grok)[ -]*\d+(?:[.\-]\d+)*(?:[ -]+(?:argon|pro|flash|sol|astra|mini|turbo|thinking|preview))*(?![a-z0-9])', re.I)
SCOPES = {'gemini_app': r'gemini\s*(?:app|应用)', 'api': r'\bapi\b',
          'claude_app': r'claude\s*(?:app|应用)', 'chatgpt': r'\bchatgpt\b'}


def release_claim(item):
    title = item.get('title', '')
    model = MODEL.search(title)
    if not model or not re.search(r'开放|推出|上线|发布|进入|available|rolling out|launch', title, re.I):
        return None
    # A change of price, capabilities, access restrictions or region is new
    # information, not a plain reminder that the model is available.
    if re.search(r'新增|扩展|扩大|免费|降价|涨价|地区|国家|价格|更新|支持|测试|评测|性能|隐私|安全|beta|preview', title, re.I):
        return None
    if re.search(r'新增|扩展|扩大|免费|降价|涨价|新地区|new regions|expanded',
                 item.get('editorial_assessment', {}).get('what_changed', ''), re.I):
        return None
    scopes = {key for key, pattern in SCOPES.items() if re.search(pattern, title, re.I)}
    if not scopes:
        return None
    return re.sub(r'\s+', '', model.group()).casefold(), scopes


def primary_day(candidate):
    """Read an explicit saved source dateline, never the aggregator timestamp."""
    packet = candidate.get('editorial_packet', {})
    if digest(packet) != candidate.get('editorial_assessment', {}).get('input_sha256'):
        return None
    primary = candidate.get('editorial_assessment', {}).get('primary_source_id')
    source = next((s for s in packet.get('sources', []) if s.get('source_id') == primary), {})
    if source.get('read_status') != 'read':
        return None
    text = source.get('text', '')
    # X's rendered post timestamp has a middle-dot separator before/after.
    match = re.search(r'·\s*([A-Z][a-z]{2} \d{1,2}, \d{4})\s*·', text)
    if not match:
        match = re.search(r'\b([A-Z][a-z]{2} \d{1,2}, \d{4})\s*\|', text[:240])
    if match:
        try:
            return datetime.strptime(match[1], '%b %d, %Y').date().isoformat()
        except ValueError:
            pass
    return None


def duplicate_check(candidate, previous):
    urls = {canonical_url(u) for u in item_urls(candidate)} - {''}
    for old in previous:
        overlap = urls & set(old['urls'])
        if overlap:
            return {'is_duplicate': True, 'reason': 'resolved_same_url',
                    'matched_previous': old['title'], 'published_on': old['published_on'],
                    'matched_value': sorted(overlap)[0]}
    claim, day = release_claim(candidate), primary_day(candidate)
    if claim and day:
        model, scopes = claim
        for old in previous:
            # A date without a timezone/time cannot prove that a same-day
            # announcement preceded that morning's edition.
            if day >= old['published_on']:
                continue
            for quote in old.get('evidence', []):
                models = {re.sub(r'\s+', '', m.group()).casefold() for m in MODEL.finditer(quote)}
                known_scopes = {key for key, pattern in SCOPES.items() if re.search(pattern, quote, re.I)}
                if (model in models and scopes <= known_scopes
                        and re.search(r'available|rolling out|开放|可使用', quote, re.I)):
                    return {'is_duplicate': True, 'reason': 'already_published_availability',
                            'matched_previous': old['title'], 'published_on': old['published_on'],
                            'source_date': day, 'matched_value': quote}
    return {'is_duplicate': False}


def check_candidates(candidates, run_date):
    previous, warnings = history(run_date)
    checked = deepcopy(candidates)
    for item in checked:
        result = duplicate_check(item, previous)
        item['publication_duplicate_check'] = result
        if result['is_duplicate']:
            item['content_duplicate_check'] = result
    return checked, {'history_count': len(previous), 'warnings': warnings,
                     'duplicates': [{'event_id': c.get('event_id'), 'title': c.get('title'),
                                     **c['publication_duplicate_check']} for c in checked
                                    if c['publication_duplicate_check']['is_duplicate']]}


def reconcile(paths):
    """Drop only confirmed repeats from an unsent draft, keeping lineage valid.

    The original selection/quiet-day evaluation remains evidence; this later
    receipt explains the subtraction. No uncomposed candidates are promoted.
    """
    from send_email_package_smtp import write_json_atomic
    previous, warnings = history(paths.run_date)
    if not previous or not paths.content.exists() or not paths.source_selection.exists():
        return False
    selection = load_json_object(paths.source_selection)
    content = load_json_object(paths.content)
    selected = selection.get('selected_news', []) + selection.get('selected_research', [])
    drops = {c.get('event_id'): duplicate_check(c, previous) for c in selected if c.get('event_id')}
    drops = {key: value for key, value in drops.items() if value['is_duplicate']}
    if not drops:
        return False
    delivery = paths.run_dir / 'smtp_prod_delivery.json'
    if ((delivery.exists() and load_json_object(delivery).get('sent'))
            or (paths.run_dir / 'automatic_delivery_state.json').exists()):
        raise ValueError('Production delivery has started; duplicate reconciliation cannot change this issue')
    from workflow_context import assert_content_lineage
    assert_content_lineage(paths)
    materials = load_json_object(paths.source_materials)
    drop_urls = {canonical_url(u) for c in selected if c.get('event_id') in drops for u in item_urls(c)}
    def keep(item):
        return not (item.get('event_id') in drops or drop_urls & {canonical_url(u) for u in item_urls(item)})
    content['news'] = [c for c in content.get('news', []) if keep(c)]
    if isinstance(content.get('research_items'), list):
        content['research_items'] = [c for c in content['research_items'] if keep(c)]
    if isinstance(content.get('paper'), dict) and not keep(content['paper']):
        content.pop('paper')
    if not cards(content):
        raise ValueError('No new publishable items remain after duplicate reconciliation')
    # Reassign placements to existing prose, without generating feature copy.
    from newspaper_sections import planned_news_sources
    for item in content['news']:
        item.pop('newspaper_role', None)
    content['news'] = planned_news_sources(content['news'])
    content['summary_headline'] = '今日 AI 前沿与实践'
    content['editor_note'] = f"本期收录 {len(content['news'])} 条动态与 {len(cards(content)) - len(content['news'])} 篇研究解读。"
    content['summary'] = '关注本期报道中的产品变化、工程方法与适用边界。'
    for kind in ('news', 'research'):
        selection['selected_' + kind] = [c for c in selection.get('selected_' + kind, []) if keep(c)]
        materials[kind] = [c for c in materials.get(kind, []) if keep(c)]
    news_count, research_count = len(selection['selected_news']), len(selection['selected_research'])
    total_minutes = news_count + 3 * research_count
    selection['selection_summary'] = {**selection.get('selection_summary', {}),
        'news_count': news_count, 'research_count': research_count,
        'reading_estimate': {'news_minutes': news_count, 'research_minutes': 3 * research_count,
            'total_minutes': total_minutes, 'soft_budget_minutes': 10, 'over_soft_budget': total_minutes > 10}}
    if 'health' in selection.get('source', {}):
        from source_selection_output import source_mix_summary
        selection['source']['health']['selected_provenance'] = source_mix_summary(
            selection['selected_research'] + selection['selected_news'])
    receipt = {'stage': 'before_final_review', 'drops': drops, 'warnings': warnings,
               'before': {name: file_sha256(getattr(paths, name)) for name in ('source_selection', 'source_materials', 'content')}}
    selection.setdefault('publication_reconciliation', []).append(receipt)
    files = [paths.source_selection, paths.source_materials, paths.content]
    snapshots = {p: p.read_bytes() for p in files}
    from email_review_history import write_once
    folder = paths.run_dir / 'publication_dedup_history' / digest(receipt)
    for p in files:
        write_once(folder / p.name, load_json_object(p))
    try:
        write_json_atomic(paths.source_selection, selection)
        materials['selection_sha256'] = file_sha256(paths.source_selection)
        write_json_atomic(paths.source_materials, materials)
        content['generation']['selection_sha256'] = file_sha256(paths.source_selection)
        content['generation']['source_material_sha256'] = file_sha256(paths.source_materials)
        write_json_atomic(paths.content, content)
        receipt['after'] = {name: file_sha256(getattr(paths, name)) for name in ('source_selection', 'source_materials', 'content')}
        write_once(folder / 'result.json', receipt)
    except Exception:
        for p, data in snapshots.items():
            p.write_bytes(data)
        raise
    from workflow_run_state import record_run_event
    if getattr(paths, 'workflow_state', None):
        record_run_event(paths.workflow_state, run_date=paths.run_date,
                         action='remove_published_duplicates', phase='CONTENT_READY',
                         message=f'Removed {len(drops)} previously published events; retained existing content and image.',
                         artifacts={'source_selection': paths.source_selection, 'source_materials': paths.source_materials,
                                    'content': paths.content, 'publication_dedup': folder / 'result.json'})
    return True
