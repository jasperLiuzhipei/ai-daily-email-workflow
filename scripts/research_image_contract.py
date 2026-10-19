#!/usr/bin/env python3
"""Normalize legacy/current Visual Handoffs and compile one auditable prompt.

No model calls, rendering, or approval happen here. Legacy presentation policy
is not copied into the new prompt; the original input remains in the caller's
brief and its hash is retained by the compilation receipt.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

FIELDS = ("subject", "takeaway", "mechanism", "evidence", "scope", "labels")
POLICY_PATH = Path(__file__).resolve().parent.parent / "config/research_image_policy.json"
ROUTES = ("model_native", "hybrid_overlay", "deterministic_infographic")


class HandoffError(ValueError):
    pass


def joined(*values: Any) -> str:
    parts: list[str] = []
    for value in values:
        if isinstance(value, list):
            value = joined(*value)
        if isinstance(value, str) and value.strip() and value.strip() not in parts:
            parts.append(value.strip())
    return "\n".join(parts)


def normalize_handoff(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise HandoffError("visual_handoff must be an object")
    if any(key in raw for key in FIELDS):
        invalid = [key for key in FIELDS if key in raw and not (
            isinstance(raw[key], str) or isinstance(raw[key], list)
            and all(isinstance(item, str) for item in raw[key]))]
        if invalid:
            raise HandoffError("Handoff fields must be text or text lists: " + ", ".join(invalid))
        result = {key: joined(raw.get(key)) for key in FIELDS}
    else:
        result = {
            "subject": joined(raw.get("Display Title"), raw.get("Named Subject")),
            "takeaway": joined(raw.get("Core Idea")),
            # Scene-only legacy inputs must be re-edited: copying presentation
            # instructions here silently reintroduces obsolete layout rules.
            "mechanism": joined(raw.get("Must Preserve"), raw.get("Distinctive Detail")),
            "evidence": joined(raw.get("Main Evidence Anchor"), raw.get("Supporting Evidence Details")),
            "scope": joined(raw.get("Boundary Cue"), raw.get("Exception Cue"), raw.get("Do Not Imply")),
            "labels": joined(raw.get("Exact Labels")) or joined(raw.get("Display Title"), raw.get("Optional Subtitle")),
        }
    missing = [key for key in FIELDS if not result[key]]
    if missing:
        raise HandoffError("Visual Handoff lacks source-grounded fields: " + ", ".join(missing) + "; supply factual fields before compiling")
    return result


def load_policy() -> dict[str, Any]:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def directed_relations(mechanism: str) -> list[dict[str, str]]:
    """Read explicit semantic edges within the existing mechanism field."""
    result: list[dict[str, str]] = []
    seen: set[tuple[str, ...]] = set()
    for line in mechanism.splitlines():
        if not line.strip().startswith("关系："):
            continue
        parts = tuple(part.strip() for part in line.strip()[3:].split("｜"))
        if len(parts) != 3 or not all(parts):
            raise HandoffError("Use 关系：起点｜动作或传递对象｜终点 for directed relations")
        if parts not in seen:
            seen.add(parts)
            result.append(dict(id=f"R{len(result)+1}", source=parts[0], relation=parts[1], target=parts[2]))
    return result


def drawing_requirements() -> str:
    """Share the same maintained visual policy with generation and visual QA."""
    policy = load_policy()
    return "\n".join(policy[key] for key in ("canvas", "palette", "story", "integrity"))


def compile_prompt(raw: Any, *, render_mode: str = "model_native") -> str:
    if render_mode not in ROUTES:
        raise HandoffError(f"Unsupported render mode: {render_mode}")
    handoff = normalize_handoff(raw)
    directed_relations(handoff["mechanism"])
    policy = load_policy()
    headings = ("主题", "核心判断", "机制关系", "证据", "适用范围", "文字清单")
    content = "\n\n".join(f"{heading}：\n{handoff[key]}" for heading, key in zip(headings, FIELDS, strict=True))
    rules = drawing_requirements()
    return f"为 AI 日报制作一张完整的解释图。\n\n{content}\n\n绘制要求：\n{rules}\n{policy['routes'][render_mode]}\n"


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def compilation_record(raw: Any, prompt: str, *, render_mode: str = "model_native") -> dict[str, Any]:
    if prompt != compile_prompt(raw, render_mode=render_mode):
        raise HandoffError("Prompt differs from compiler output")
    return {
        "schema_version": 1,
        "kind": "research_image_prompt_compilation",
        "policy_version": load_policy()["version"],
        "policy_sha256": digest(POLICY_PATH.read_bytes()),
        "compiler_sha256": digest(Path(__file__).read_bytes()),
        "input_handoff_sha256": digest(canonical(raw)),
        "handoff_sha256": digest(canonical(normalize_handoff(raw))),
        "prompt_sha256": digest(prompt.encode("utf-8")),
        "render_mode": render_mode,
        "legacy_input": not any(key in raw for key in FIELDS),
        "not_a_generation_or_approval_receipt": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brief-json", type=Path, required=True)
    parser.add_argument("--prompt-out", type=Path, required=True)
    parser.add_argument("--contract-out", type=Path, required=True)
    parser.add_argument("--render-mode", choices=ROUTES, default="model_native")
    args = parser.parse_args()
    try:
        if args.prompt_out.resolve() == args.contract_out.resolve():
            raise HandoffError("Prompt and receipt paths must differ")
        if args.prompt_out.exists() or args.contract_out.exists():
            raise HandoffError("Refusing to overwrite existing compilation artifacts")
        data = json.loads(args.brief_json.read_text(encoding="utf-8"))
        raw = data.get("visual_handoff", data)
        prompt = compile_prompt(raw, render_mode=args.render_mode)
        record = compilation_record(raw, prompt, render_mode=args.render_mode)
        record["brief_sha256"] = digest(args.brief_json.read_bytes())
        for target in (args.prompt_out, args.contract_out):
            target.parent.mkdir(parents=True, exist_ok=True)
        args.prompt_out.write_text(prompt, encoding="utf-8")
        args.contract_out.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Compiled {len(prompt)} characters; route={args.render_mode}; policy={record['policy_version']}")
    except (OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
