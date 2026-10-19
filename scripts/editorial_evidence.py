"""Shared, attributed evidence for news generation and final-copy review."""
from __future__ import annotations

import hashlib
import re

MAX_SOURCE_CHARS = 12000
EVIDENCE_VERSION = "attributed-editorial-evidence-v1"
EVIDENCE_GUIDANCE = """证据使用规则：source_evidence_provenance 区分抓取正文与发现摘要，并给出各自出处、指纹及截断情况。
supporting_evidence 是同一已选事件的其他来源逐字引文，已校验引文存在于保存的来源正文；它与主正文一样可用于核对断言，但不表示独立证实来源所述事实。必须保留各自 URL、引用范围与报道/研究的证据边界。
发现摘要或上游编辑摘要可以帮助理解线索，但不自动成为已核实的原文证据；摘要中的数字不能归称为另一篇正文或法院文件直接支持。
主正文未提及某断言不等于正文否定它。先查 supporting_evidence 和摘要及实际出处；区分“与来源矛盾”和“现有证据不足”。关键数字或核心结论只有摘要支持且未核验时，应省去该细节或准确保留证据边界，不能提升为已核实事实。"""

# Long integrity hashes stay in the bound source artifacts, outside review.
REVIEW_EVIDENCE_GUIDANCE = (
    "证据使用规则：source_evidence_provenance 区分抓取正文与发现摘要，并给出角色、出处、截断情况和可引用的短标识。完整指纹由程序校验并保留在归档材料中，审核输入省略长哈希及重复元数据。\n"
    + EVIDENCE_GUIDANCE.partition("\n")[2]
)


def supporting_quotes(candidate: dict) -> list[dict]:
    """Resolve only an already-selected event's saved assessment quotations."""
    from event_editorial import digest, normalized_text, canonical_url
    assessment = candidate.get("editorial_assessment") or {}
    packet = candidate.get("editorial_packet") or {}
    if not assessment:
        return []
    if not packet or digest(packet) != assessment.get("input_sha256"):
        raise ValueError("Supporting evidence packet changed after editorial assessment")
    sources = {source["source_id"]: source for source in packet.get("sources", [])}
    result = []
    for proof in assessment.get("evidence", []):
        source = sources.get(proof.get("source_id"))
        quote = proof.get("quote")
        if (not source or not isinstance(quote, str) or not 8 <= len(quote.strip()) <= 1600
                or normalized_text(quote) not in normalized_text(source.get("text", ""))
                or not canonical_url(source.get("url"))):
            raise ValueError("Supporting quotation is absent from its attributed source")
        # Readability is distinct from quote matching and from factual truth.
        result.append({"source_id": source["source_id"], "url": source["url"],
            "quote": quote, "read_status": source.get("read_status", "unknown"),
            "source_text_sha256": hashlib.sha256(str(source.get("text", "")).encode()).hexdigest(),
            "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
            "assessment_input_sha256": assessment["input_sha256"]})
    return result


def compact_source_excerpt(item: dict) -> str:
    value = str(item.get("fallback_excerpt") or "").strip()
    if "🔗" in value:
        value = value.split("🔗", 1)[0]
    value = re.sub(r"https?://\S+", "", value)
    value = value.replace("📌 One-Sentence Summary", "").replace("📝 Summary", "")
    value = " ".join(value.split())
    chinese_chars = sum("\u4e00" <= char <= "\u9fff" for char in value)
    latin_chars = sum("a" <= char.lower() <= "z" for char in value)
    structured = item.get("source_quality_tier") == "primary" and item.get("source_type") == "model_release"
    if not value or (not structured and (chinese_chars == 0 or latin_chars > chinese_chars * 1.2)):
        title = str(item.get("title") or "今日入选 AI 行业动态").strip()
        return f"该消息聚焦“{title}”，具体事实与适用范围请以原始链接为准。"
    if len(value) > 460:
        end = max(value.rfind(mark, 0, 460) for mark in "。！？")
        value = value[:end + 1] if end >= 180 else value[:460].rstrip("，；： ") + "…"
    return value


def evidence_for_item(item: dict) -> dict:
    """Use identical bounded text and provenance on both sides of the review."""
    original = str(item.get("source_text") or item.get("source_excerpt") or "").strip()
    document = original[:MAX_SOURCE_CHARS]
    summary = compact_source_excerpt(item)
    status = str(item.get("fetch_status") or "unknown")
    document_url = str(item.get("resolved_url") or item.get("primary_url") or "")
    summary_url = str(item.get("discovery_url") or item.get("aihot_url") or "")
    assessment = item.get("editorial_assessment") or {}
    summary_role = "editorial_summary" if assessment else "discovery_summary"
    if assessment:
        summary_url = ""  # Synthesized from several sources, not quoted from one URL.
    supporting = item.get("supporting_evidence") or []
    def provenance(text, role, url, **extra):
        sha = hashlib.sha256(text.encode()).hexdigest()
        identity = hashlib.sha256((role + "\n" + url + "\n" + sha).encode()).hexdigest()
        return {"id": identity[:20], "role": role, "url": url or None, "sha256": sha, **extra}
    return {
        "source_text": document,
        "material_excerpt": summary,
        "supporting_evidence": supporting,
        "source_evidence_provenance": {
            "version": EVIDENCE_VERSION,
            "document": provenance(document,
                "fetched_document" if status in {"primary", "fetched", "ok", "success"} else "unverified_excerpt",
                document_url, fetch_status=status, input_chars=len(original), truncated=len(original) > MAX_SOURCE_CHARS),
            # Never infer that a feed summary originated in the primary article.
            "summary": provenance(summary, summary_role, summary_url,
                source_field="editorial_assessment.what_changed" if assessment else "fallback_excerpt",
                assessment_input_sha256=assessment.get("input_sha256"), independently_verified=False),
            "supporting": [{k: proof[k] for k in ("source_id", "url", "quote_sha256", "source_text_sha256")}
                           for proof in supporting],
        },
    }
