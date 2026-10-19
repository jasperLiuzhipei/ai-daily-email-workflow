"""Local success receipt for reusing an API-generated research reading.

This is a file-integrity contract, not another semantic approval. The producer
writes it only after all requested artifacts have been written successfully.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from ai_daily_common import file_sha256
from daily_prose import POLICY_VERSION


CACHE_CONTRACT = "research-reading-cache-v1"
ARTIFACT_KEYS = ("brief_json", "image_prompt", "raw_output", "brief_markdown")


def cache_manifest_path(brief_json: Path) -> Path:
    return Path(str(brief_json) + ".cache.json")


def source_material_sha256(source: dict[str, Any]) -> str:
    canonical = json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def prompt_text_sha256(prompt: str) -> str:
    return hashlib.sha256(prompt.strip().encode("utf-8")).hexdigest()


def generation_policy_sha256() -> str:
    # Review and repair prompts live in the producer rather than the main
    # prompt file. Changes there must invalidate older semantic approvals.
    return file_sha256(Path(__file__).with_name("generate_research_brief_api.py"))


def build_cache_contract(
    *, source: dict[str, Any], system_prompt: str, artifacts: dict[str, Path | None],
) -> dict[str, Any]:
    if set(artifacts) != set(ARTIFACT_KEYS):
        raise ValueError("Research cache artifacts must name exactly the four research outputs.")
    return {
        "schema_version": 1,
        "contract": CACHE_CONTRACT,
        "policy_version": POLICY_VERSION,
        "source_material_sha256": source_material_sha256(source),
        "system_prompt_sha256": prompt_text_sha256(system_prompt),
        "generation_policy_sha256": generation_policy_sha256(),
        "artifacts": {
            key: {"sha256": file_sha256(artifacts[key])} if artifacts[key] is not None else None
            for key in ARTIFACT_KEYS
        },
    }


def write_cache_contract(path: Path, contract: dict[str, Any]) -> None:
    """Atomically publish the final success receipt, never a partial JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".research-cache-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(contract, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
