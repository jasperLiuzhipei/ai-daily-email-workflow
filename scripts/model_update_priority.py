"""Conservative editorial priority, not a measurement of model popularity.

Reach is a configurable editorial prior. Attention requires dated, reviewed
evidence from independent sources; feed inclusion and release counts do not
measure attention. Unknown evidence never earns a bonus.
"""
from datetime import datetime
import re
from urllib.parse import urlparse


def subject_prior(title, policy):
    # A competitor mentioned after 'releases/beats/对标' is not the subject.
    subject = re.split(r'发布|推出|上线|对标|超过|超越|击败|\breleases?\b|\blaunches?\b|\bbeats?\b', title.lower())[0]
    for tier, entries in policy.get('reach_priors', {}).items():
        for name, aliases in entries.items():
            for alias in aliases:
                pattern = re.escape(alias.lower())
                if alias.isascii():
                    pattern = r'(?<![a-z])' + pattern + r'(?![a-z])'
                if re.search(pattern, subject):
                    return tier, name
    return 'unestablished', 'unknown'


def attention(candidate):
    """Use only explicitly reviewed, recent evidence; cap correlated signals."""
    reference = candidate.get('published_at')
    try:
        reference = datetime.fromisoformat(reference.replace('Z', '+00:00'))
        if reference.tzinfo is None:
            return 0, 'unknown', []
    except (ValueError, TypeError, AttributeError):
        return 0, 'unknown', []
    accepted = []
    domains = set()
    origins = set()
    for item in candidate.get('model_attention_evidence', []):
        if not isinstance(item, dict) or item.get('reviewed') is not True:
            continue
        if item.get('kind') not in {'independent_coverage', 'adoption_growth', 'search_growth'}:
            continue
        url = str(item.get('url', ''))
        host = (urlparse(url).hostname or '').removeprefix('www.')
        if not host or not url.startswith('https://') or not item.get('finding'):
            continue
        if item.get('independent_origin') is not True:
            continue
        try:
            observed = datetime.fromisoformat(item['observed_at'].replace('Z', '+00:00'))
            age = (reference - observed).total_seconds() / 86400
        except (KeyError, ValueError, TypeError):
            continue
        if not 0 <= age <= 7:
            continue
        # Syndicated stories must share origin_id, not count as separate press.
        origin = str(item.get('origin_id') or host)
        if host in domains or origin in origins:
            continue
        domains.add(host)
        origins.add(origin)
        accepted.append({'url':url, 'kind':item['kind'], 'finding':item['finding'], 'observed_at':item['observed_at']})
    if len(accepted) < 2:
        return 0, 'insufficient' if accepted else 'unknown', accepted
    return min(4, len(accepted)), 'corroborated', accepted


def assess_model_update(candidate, policy):
    title = str(candidate.get('title', ''))
    text = (title + ' ' + str(candidate.get('description') or candidate.get('source_excerpt', '')) + ' ' + str(candidate.get('source_text', ''))).lower()
    tier, subject = subject_prior(title, policy)
    attention_bonus, attention_status, evidence = attention(candidate)
    primary = candidate.get('source_quality_tier') in {'primary', 'verified_social'}
    unusable = candidate.get('fetch_status') in {'failed', 'blocked', 'captcha'} or bool(
        re.search(r'captcha|wappoc_appmsgcaptcha', str(candidate.get('resolved_url', '')))
    )
    unusable = unusable or candidate.get('primary_verification', {}).get('status') in {'unresolved', 'not_attempted_budget'}
    primary = primary and not unusable
    available = bool(re.search(r'开放|开源|权重|可用|上线|api|available|weights|open.source|发布|推出|released?|launched?', text))
    minor = bool(re.search(r'修复|小版本|补丁|拼写|图标|bug.?fix|patch|minor update', title.lower()))
    generation = bool(re.search(r'新一代|大版本|旗舰|全新架构|下一代|next.generation|flagship', text))
    quantified = bool(re.search(r'(?:价格|成本|延迟|吞吐|上下文|price|cost|latency|throughput|context).{0,24}(?:\d+(?:\.\d+)?\s*[%倍xkKmM]|减半)', text))
    new_capability = bool(re.search(r'首次支持|首次开放|新增.{0,12}(?:模态|实时|推理|视频|音频)|first.{0,15}(?:reasoning|video|audio|modality)', text))
    substantive = available and (generation or quantified or new_capability)
    magnitude = 'minor' if minor else ('substantive' if substantive else 'routine_or_unclear')
    # A source-grounded update plus either established reach or independently
    # corroborated attention can qualify. Unfamiliar entrants are not banned.
    major = substantive and primary and (tier == 'established' or attention_status == 'corroborated') and not minor
    bonus = 6 if major else (2 if substantive and not minor else 0)
    caps = policy.get('score_caps', {})
    if minor:
        cap = caps.get('minor', 69)
    elif major:
        cap = caps.get('major_verified', 96)
    elif substantive:
        cap = caps.get('substantive_unverified', 79)
    elif tier == 'established':
        cap = caps.get('routine_established', 76)
    else:
        cap = caps.get('routine_unestablished', 72)
    return {
        'policy_version':policy.get('version', 'model-priority-v1'),
        'subject':subject, 'reach_prior':tier, 'reach_is_editorial_prior':True,
        'update_magnitude':magnitude, 'primary_evidence':primary,
        'source_unusable':unusable, 'public_attention':attention_status,
        'attention_evidence':evidence, 'attention_bonus':attention_bonus,
        'priority_bonus':bonus, 'score_cap':cap, 'lead_eligible':major,
        'reason':'Major priority requires substantive change, primary evidence and established reach or corroborated attention; release count and headline superlatives earn no bonus.',
    }
