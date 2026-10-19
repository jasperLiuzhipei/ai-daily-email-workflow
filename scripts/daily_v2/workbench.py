"""Reuse the current editor and newspaper renderer behind a frozen V2 boundary."""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from html import escape
import importlib.metadata
import json
from pathlib import Path
import platform
import re

from bs4 import BeautifulSoup
import newspaper_text
from email_feedback import remove_feedback
from . import VERSION
from .contracts import GateError, canonical, digest, normalize, preflight, recipients, subject
from editorial_backlog import identities

ROOT = Path(__file__).resolve().parents[2]


def provenance():
    paths = [ROOT / "templates/newspaper_text.html", ROOT / "config/newspaper_accent_palette.json",
             ROOT / "config/newspaper_presentation.json", ROOT / "scripts/newspaper_text.py",
             *[ROOT / 'scripts' / name for name in ('run_ai_daily_v2.py', 'email_feedback.py', 'editorial_backlog.py', 'ai_daily_common.py', 'ai_gateway_client.py')],
             *sorted((ROOT / 'feedback_service').glob('*.py')),
             *sorted((ROOT / "scripts/daily_v2").glob("*.py"))]
    return {"version": VERSION, "files": {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in paths},
            "python": platform.python_version(),
            "dependencies": {name: importlib.metadata.version(name) for name in ("beautifulsoup4", "premailer")},
            "effective_systemd": "not_deployed", "model_identity": "legacy_import_not_independently_verified"}


def evidence_map(content, materials):
    rows = []
    for key in ("news", "research"):
        value = materials.get(key, [])
        rows += value if isinstance(value, list) else [value] if isinstance(value, dict) else []
    mapped = {}
    for card in content["news"] + content["deep_features"]:
        matches = []
        for row in rows:
            if set(card["source_identities"]) & identities(row):
                # Keep the actual excerpt and its fetch state; an index-only
                # fallback is never silently promoted to full source evidence.
                matches.append({k: row.get(k) for k in ("primary_url", "resolved_url", "published_at",
                               "fetch_status", "source_quality_tier", "source_text", "fallback_excerpt", "boundary")})
        mapped[card["card_id"]] = matches
    return mapped


def check_document(doc):
    result = preflight(doc["content"], doc["html"], doc["issue_date"], doc["recipients"])
    if doc.get('mode') != 'shadow' or doc.get('production_eligible') is not False or doc.get('schema_version') != 2:
        result['errors'].append('shadow_contract_mismatch')
    if doc.get('subject') != subject(doc['issue_date']) or doc.get('html_sha256') != digest(doc['html'].encode()):
        result['errors'].append('subject_or_html_hash_mismatch')
    for card in doc["content"]["news"] + doc["content"]["deep_features"]:
        evidence = doc["evidence"].get(card["card_id"], [])
        if not any(row.get("source_text") or row.get("fallback_excerpt") for row in evidence):
            result["errors"].append(card["card_id"] + ": missing_evidence")
        elif not any(row.get("source_text") for row in evidence):
            result["warnings"].append(card["card_id"] + ": fallback_evidence_only")
    if not doc["selection"].get("coverage_cutoff"):
        result["warnings"].append("selection_coverage_cutoff_unknown")
    result["warnings"] = sorted(set(result["warnings"]))
    result["errors"] = sorted(set(result["errors"]))
    return result


def build_document(raw, day, recipient_list, *, issue=None, materials=None, selection=None, editorial=None):
    date.fromisoformat(day)
    data = normalize(raw)
    edition = issue if issue is not None else data.get("issue")
    edition_source = 'explicit' if issue is not None else 'content.issue'
    if edition is None:
        match = re.search(r'(?:第\s*(\d+)\s*期|VOLUME\s+(\d+))', str(raw.get('subtitle', '')), re.I)
        if match:
            edition = int(match[1] or match[2])
            edition_source = 'legacy_subtitle'
    if not isinstance(edition, int) or isinstance(edition, bool) or edition < 1:
        raise GateError("An existing positive issue number is required; previews never advance the counter")
    data["issue"] = edition
    palette = json.loads((ROOT / "config/newspaper_accent_palette.json").read_text())
    prior = raw.get("presentation", {})
    accent = prior.get("accent") if prior.get("date") == day else None
    data["presentation"] = {"mode": "newspaper_text", "version": VERSION, "date": day,
                            "accent": accent or palette["colors"][(date.fromisoformat(day) - date.fromisoformat(palette["base_date"])).days % len(palette["colors"])],
                            "palette_version": prior.get("palette_version", palette["version"]) if accent else palette["version"],
                            "image_status": "N_A", "image_reason": "editorial_text_default", "order_policy": "editor_order"}
    legacy = deepcopy(data)
    legacy["research_items"] = legacy.pop("deep_features")
    # This adapter has no date-directory lookup or implicit overrides.
    # Explicit editorial markup was bound to the legacy input before migration.
    override = deepcopy(editorial or {})
    if override:
        if override.get("content_signature") != newspaper_text.content_signature(raw):
            raise GateError("Editorial override does not match imported content")
        override["content_signature"] = newspaper_text.content_signature(legacy)
    if not data.get("news"):
        html = f'<html><head><title>{subject(day)}</title></head><body data-ai-daily-date="{day}"><p>暂无可发布新闻，等待编辑处理。</p></body></html>'
    else:
        html = remove_feedback(newspaper_text.render_email(legacy, editorial_override=override))
    # Some legacy newspaper scaffolding has browser-only metadata. The V2
    # reader email is static and self-contained; no external font downloads.
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.select("meta[http-equiv],link"):
        node.decompose()
    html = str(soup)
    clean_content = {k: data[k] for k in ("schema_version", "issue", "editor_note", "summary_headline", "news", "deep_features", "presentation") if k in data}
    selected = selection or {}
    doc = {"schema_version": 2, "mode": "shadow", "issue_date": day, "subject": subject(day),
           "content": clean_content, "html": html, "html_sha256": digest(html.encode()),
           "recipients": recipients(recipient_list), "recipient_group": "shadow_preview",
           "source_input_sha256": digest(raw), "issue_number_source": edition_source, "evidence": evidence_map(clean_content, materials or {}),
           "materials_sha256": digest(materials or {}),
           "selection": {"snapshot_sha256": digest(selected), "coverage_cutoff": selected.get("coverage_cutoff"),
                         "coverage": selected.get("coverage", []), "legacy_selection_reference": (materials or {}).get("selection"),
                         "selected_card_ids": [c["card_id"] for c in data["news"] + data["deep_features"]]},
           "provenance": provenance(), "editorial_override_sha256": digest(editorial or {}),
           "review_status": "pending", "feedback": "disabled_in_shadow", "production_eligible": False}
    doc["preflight"] = check_document(doc)
    return doc


def edit_content(raw, operations, *, actor, reason):
    """Apply a small audited edit transaction; failed operations commit nothing."""
    if not actor.strip() or not reason.strip():
        raise GateError("Editing requires actor and reason")
    data = normalize(raw)
    before = deepcopy(data)
    allowed = {"title", "one_liner", "why", "observation", "boundary", "category", "url", "links", "evidence", "insight", "mechanism", "problem", "conclusion"}
    for operation in operations:
        kind = operation.get("op")
        if kind == "set_summary":
            for key in ("editor_note", "summary_headline"):
                if key in operation:
                    data[key] = operation[key]
        elif kind == "reorder":
            ids = operation["card_ids"]
            by_id = {c["card_id"]: c for c in data["news"]}
            if len(ids) != len(by_id) or set(ids) != set(by_id):
                raise GateError("Reorder must contain every news card exactly once")
            data["news"] = [by_id[cid] for cid in ids]
        elif kind in {"update", "remove"}:
            matches = [(group, c) for group in ("news", "deep_features") for c in data[group] if c["card_id"] == operation["card_id"]]
            if len(matches) != 1:
                raise GateError("Unknown or ambiguous card ID")
            group, card = matches[0]
            if kind == "remove":
                data[group].remove(card)
            else:
                patch = operation["fields"]
                if not set(patch) <= allowed:
                    raise GateError("Unsupported editable field")
                card.update(patch)
        else:
            raise GateError("Unsupported editorial operation")
    data = normalize(data)
    return data, {"actor": actor, "reason": reason, "before_sha256": digest(before),
                  "after_sha256": digest(data), "operations": operations,
                  "requires_new_preview_and_review": True}


def export_preview(ledger, rid, folder):
    rev = ledger.revision(rid)
    doc = rev["document"]
    folder = Path(folder) / rid
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "email.html").write_text(doc["html"])
    redacted = {k: v for k, v in doc.items() if k not in {"html", "recipients", "evidence"}}
    redacted["recipient_count"] = len(doc["recipients"])
    redacted["recipient_snapshot_sha256"] = digest(doc["recipients"])
    (folder / "manifest.preview.json").write_text(json.dumps(redacted, ensure_ascii=False, indent=2))
    check = doc["preflight"]
    previous = ledger.db.execute("SELECT id FROM revisions WHERE issue_date=? AND revision<? ORDER BY revision DESC LIMIT 1", (doc["issue_date"], rev["revision"])).fetchone()
    changes = []
    if previous:
        old = ledger.revision(previous["id"])["document"]
        for key in ("content", "recipients", "evidence", "selection", "provenance", "html"):
            if digest(old[key]) != digest(doc[key]):
                changes.append(key)
    latest_review = ledger.db.execute("SELECT record FROM reviews WHERE revision_id=? ORDER BY rowid DESC LIMIT 1", (rid,)).fetchone()
    review_status = json.loads(latest_review[0])["result"] if latest_review else "pending"
    errors = "".join(f"<li>{escape(x)}</li>" for x in check["errors"]) or "<li>结构检查无阻断</li>"
    warnings = "".join(f"<li>{escape(x)}</li>" for x in check["warnings"]) or "<li>无软警告</li>"
    html = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>V2 日报编辑预览</title>
<style>body{{margin:0;background:#f3f1ed;color:#272b30;font:16px/1.65 -apple-system,BlinkMacSystemFont,sans-serif}}main{{max-width:1160px;margin:auto;padding:30px 22px}}h1{{font-size:30px}}.tag{{color:#735b9e;font-size:13px;letter-spacing:1px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px}}.box{{background:white;padding:18px;border-radius:10px;margin:16px 0}}.num{{font-size:27px;font-weight:650}}code{{overflow-wrap:anywhere}}a{{color:#735b9e}}iframe{{width:100%;height:1100px;border:0;background:white}}li{{overflow-wrap:anywhere}}.muted{{color:#63676b;font-size:14px}}</style>
<main><div class="tag">AI DAILY · V2 WORKBENCH · SHADOW</div><h1>{escape(doc['subject'])}</h1>
<p>{escape(doc['issue_date'])} · VOLUME {doc['content']['issue']} · 修订 {rev['revision']} · <b>编辑审核：{escape(review_status)}</b></p>
<div class="grid"><div class="box"><div class="num">{check['news_count']} + {check['deep_count']}</div>新闻 / 深度 · 约 {check['reading_minutes']} 分钟</div><div class="box"><div class="num">{len(doc['recipients'])}</div>影子名单人数 · 真实发送 0</div><div class="box"><div class="num">{escape(doc['content']['presentation']['accent']['label'])}</div>纯文字 · 本期颜色已固定</div></div>
<div class="box"><b>版本与范围</b><p class="muted">此工作区用于开发验收，未连接正式发送器。原文使用直链；评分和点击采集在影子版中关闭。浏览器检查不代表邮件客户端已验收。</p><code>{escape(rid)}</code><p>相对上一版：{escape('、'.join(changes) or ('首次预览' if not previous else '无变化'))}</p><p class="muted">内容指纹 {rev['document_hash']}<br>名单指纹 {digest(doc['recipients'])}<br>模型身份：历史导入，未独立核对。覆盖截止：{escape(str(doc['selection']['coverage_cutoff'] or '未知，待补充'))}</p></div>
<div class="grid"><section class="box"><b>确定性检查</b><ul>{errors}</ul></section><section class="box"><b>需要编辑留意</b><ul>{warnings}</ul></section></div>
<p><a href="email.html" target="_blank">打开读者版邮件 ↗</a> · <a href="manifest.preview.json">查看脱敏预览清单 ↗</a></p><iframe title="读者版邮件" sandbox="allow-popups allow-popups-to-escape-sandbox" src="email.html"></iframe></main></html>'''
    (folder / "index.html").write_text(html)
    # A fixed local entry points at immutable per-revision artifacts.
    latest_html = html.replace('href="email.html"', f'href="{rid}/email.html"').replace('href="manifest.preview.json"', f'href="{rid}/manifest.preview.json"').replace('src="email.html"', f'src="{rid}/email.html"')
    (folder.parent / 'index.html').write_text(latest_html)
    return folder / "index.html"
