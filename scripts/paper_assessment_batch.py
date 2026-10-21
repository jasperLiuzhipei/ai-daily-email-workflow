"""Amortize the unchanged editorial rubric across independent arXiv papers.

Every result still passes the ordinary event validator against its original
packet. Missing or invalid members retry individually; no heuristic scoring.
"""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import json
import re
from urllib.parse import urlsplit

from event_editorial import EditorialError, digest, validate_assessment, assessment_policy
from editorial_runtime_support import atomic_json

VERSION = 'paper-assessment-batch-v1'
BATCH_INSTRUCTIONS = '''
批量协议：以下 events 是相互独立的论文，逐篇按上述完全相同的标准评估，不相互比较、不合并、不按篇数或排名调分。
每篇都返回上述完整对象，外层改为 {"assessments":[对象,...]}，恰好覆盖输入的所有 event_id。
event_id 和 source_id 是本次请求的短别名，必须原样复制，禁止自行生成。引用只能来自该事件自身的 text。
正文可能是论文摘要页；不能声称已读全文、实验表格或附录。正文未给出的数据明确记为未知，保持原有证据与准入标准。
'''


def project_source(source):
    """Remove only a positively identified arXiv metadata/navigation tail."""
    result = deepcopy(source)
    text = source['text']
    abstract = text.find(' Abstract: ')
    subjects = text.find(' Subjects: ', abstract + 1)
    citation = text.find(' Cite as: ', subjects + 1)
    history = text.find(' Submission history ', citation + 1)
    if abstract >= 0 and abstract < subjects < citation < history:
        # Keep title, authors, submission date, complete abstract and comments.
        result['text'] = text[:subjects]
        result['evidence_scope'] = 'arxiv_abstract_page; full paper not supplied'
    return result


def eligible(packet):
    if len(packet['sources']) != 1:
        return False
    source = packet['sources'][0]
    url = urlsplit(source['url'])
    return (source['read_status'] == 'read' and url.hostname == 'arxiv.org'
            and bool(re.fullmatch(r'/abs/\d{4}\.\d{4,5}(?:v\d+)?', url.path)))


def batch_request(packets):
    wire, bindings = [], {}
    for index, packet in enumerate(packets):
        event_alias, source_alias = f'E{index + 1}', f'S{index + 1}'
        item = deepcopy(packet)
        item['event_id'] = event_alias
        item.pop('audience', None)
        source = project_source(packet['sources'][0])
        original_id = source['source_id']
        source['source_id'] = source_alias
        item['sources'] = [source]
        wire.append(item)
        bindings[event_alias] = {'event_id': packet['event_id'], 'source_alias': source_alias,
            'source_id': original_id, 'input_sha256': digest(packet),
            'original_text_sha256': digest(packet['sources'][0]['text']),
            'projected_text_sha256': digest(source['text']),
            'original_chars': len(packet['sources'][0]['text']), 'projected_chars': len(source['text'])}
    return {'assessment_batch_version': VERSION, 'audience': packets[0]['audience'], 'events': wire}, bindings


def restore_ids(raw, binding):
    result = deepcopy(raw)
    result['event_id'] = binding['event_id']
    expected = binding['source_alias']
    if result.get('primary_source_id') != expected:
        raise EditorialError(f'Invalid primary_source_id; expected {expected}')
    result['primary_source_id'] = binding['source_id']
    if not isinstance(result.get('evidence'), list):
        raise EditorialError('Missing evidence list')
    for index, proof in enumerate(result['evidence']):
        if not isinstance(proof, dict) or proof.get('source_id') != expected:
            raise EditorialError(f'Invalid evidence[{index}].source_id; expected {expected}')
        proof['source_id'] = binding['source_id']
    return result


def assess_events(events, materials, policy, evaluator, folder, prompt, *, assess_one, make_packet):
    size = policy.get('paper_batch_size', 4)
    packets = [make_packet(event, materials, policy) for event in events]
    results = [None] * len(events)
    paper_indices, jobs = [], []
    cached = 0
    for index, (event, packet) in enumerate(zip(events, packets)):
        if size > 1 and eligible(packet):
            prior = assess_one(event, materials, policy, evaluator, folder, prompt, cache_only=True)
            if prior is not None:
                results[index] = prior
                cached += 1
            else:
                paper_indices.append(index)
        else:
            jobs.append(('single', [index]))
    batch = []
    for index in paper_indices:
        if batch and (len(batch) >= size or sum(len(packets[i]['sources'][0]['text']) for i in batch + [index]) > 40000):
            jobs.append(('batch', batch)); batch = []
        batch.append(index)
    if batch:
        jobs.append(('batch', batch))

    def work(job):
        kind, indices = job
        if kind == 'single':
            index = indices[0]
            return [(index, assess_one(events[index], materials, policy, evaluator, folder, prompt))], None
        request, bindings = batch_request([packets[i] for i in indices])
        system = prompt + BATCH_INSTRUCTIONS
        path = Path(folder).parent / 'paper_batches' / (digest({'request': request, 'system': system,
            'policy': assessment_policy(policy), 'evaluator': evaluator.identity}) + '.json')
        trace = {'version': VERSION, 'input': request, 'bindings': bindings, 'system_prompt_sha256': digest(system),
                 'items': {}, 'usage_accounting': 'one request; per-event metadata references the same request_id'}
        rows, metadata = [], {}
        cached_response = None
        if path.exists():
            try:
                previous = json.loads(path.read_text())
                if (previous.get('input') == request and previous.get('bindings') == bindings
                        and previous.get('system_prompt_sha256') == digest(system)
                        and isinstance(previous.get('output'), dict)):
                    cached_response = (previous['output'], previous.get('metadata', {}))
            except (OSError, ValueError, AttributeError):
                pass
        trace['cached_response_reused'] = cached_response is not None
        try:
            raw, metadata = cached_response if cached_response is not None else evaluator(system, request)
            trace.update(output=raw, metadata=metadata)
            rows = raw.get('assessments', []) if isinstance(raw, dict) else []
            if not isinstance(rows, list):
                rows = []
        except Exception as exc:
            trace['error'] = type(exc).__name__
        counts = Counter(row.get('event_id') for row in rows if isinstance(row, dict) and isinstance(row.get('event_id'), str))
        completed = []
        # Save the provider response before any retries or exceptional-score review.
        atomic_json(path, trace)
        for index, alias in zip(indices, bindings):
            matches = [row for row in rows if isinstance(row, dict) and row.get('event_id') == alias]
            seed = None
            item = {'event_id': packets[index]['event_id'], 'batch_response_present': counts[alias] == 1}
            if counts[alias] == 1:
                try:
                    restored = restore_ids(matches[0], bindings[alias])
                    projected_packet = deepcopy(packets[index])
                    projected_packet['sources'] = [project_source(packets[index]['sources'][0])]
                    validate_assessment(restored, projected_packet, policy)
                    seed = (restored, {**metadata, 'paper_batch': {'path': str(path), 'event_alias': alias, 'version': VERSION}})
                except (EditorialError, KeyError, TypeError) as exc:
                    item['validation_error'] = str(exc)
            trace['items'][alias] = item
            result = assess_one(events[index], materials, policy, evaluator, folder, prompt, initial_response=seed)
            item['assessment_status'] = result[0]['assessment_status']
            completed.append((index, result))
        trace['unexpected_event_ids'] = sorted(set(counts) - set(bindings))
        atomic_json(path, trace)
        return completed, trace

    traces = []
    with ThreadPoolExecutor(max_workers=policy['workers']) as pool:
        for completed, trace in pool.map(work, jobs):
            for index, result in completed:
                results[index] = result
            if trace is not None:
                traces.append(trace)
    return results, {'version': VERSION, 'batch_size': size, 'cached_papers': cached,
        'batched_papers': len(paper_indices), 'batch_count': len(traces),
        'batch_requests': sum(not t['cached_response_reused'] for t in traces),
        'cached_batch_responses': sum(t['cached_response_reused'] for t in traces),
        'missing_or_invalid_members': sum(not item['batch_response_present'] or 'validation_error' in item
            for trace in traces for item in trace['items'].values()),
        'original_chars': sum(b['original_chars'] for t in traces for b in t['bindings'].values()),
        'projected_chars': sum(b['projected_chars'] for t in traces for b in t['bindings'].values())}
