"""Optional, fail-open text route; a missing registry preserves configured models.

The updater writes decisions; production only reads them. Credentials and gateway
addresses remain in the existing runtime configuration. QA always resolves the
unmanaged provider. A process snapshot is inherited by generation subprocesses.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE = ROOT.parent / "runtime_state" / "model_updates"
SNAPSHOT_ENV = "AI_DAILY_TEXT_ROUTE_SNAPSHOT"


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def gateway_key(provider) -> str:
    return hashlib.sha256((provider.base_url + "/" + provider.endpoint_path).encode()).hexdigest()


def read_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def state_dir(config: dict) -> Path | None:
    settings = config.get("managed_text_models", {})
    if not isinstance(settings, dict) or settings.get("enabled") is not True:
        return None
    path = Path(settings.get("state_dir") or DEFAULT_STATE)
    return path if path.is_absolute() else ROOT / path


def route(provider, config: dict, *, now: datetime | None = None) -> dict:
    directory = state_dir(config)
    if directory is None:
        return {}
    now = now or datetime.now(timezone.utc)
    try:
        raw = os.environ.get(SNAPSHOT_ENV)
        decision = json.loads(raw) if raw else read_object(directory / "active.json")
        if (not isinstance(decision, dict) or decision.get("status") != "approved"
                or decision.get("baseline_model") != provider.model
                or decision.get("gateway_key") != gateway_key(provider)
                or not isinstance(decision.get("model"), str) or not decision["model"]
                or datetime.fromisoformat(decision["effective_at"]) > now):
            return {}
        # A quarantine invalidates even a pinned route; subsequent resolutions
        # recover to the configured baseline. It never changes the QA route.
        blocked = read_object(directory / "quarantine.json")
        denied = blocked.get(gateway_key(provider), {})
        approved = [decision["model"], *decision.get("fallback_models", []), provider.model]
        available = [m for m in approved if isinstance(m, str) and m and m not in denied]
        if not available:
            return {}
        decision = {**decision, "model": available[0], "fallback_models": list(dict.fromkeys(available[1:]))}
        return decision
    except (KeyError, TypeError, ValueError, OSError):
        return {}


def managed_provider(provider, config: dict):
    decision = route(provider, config)
    return replace(provider, model=decision["model"]) if decision else provider


def pin_route(config: dict) -> dict:
    from ai_gateway_client import resolve_provider
    baseline = resolve_provider("text", config, managed=False)
    decision = route(baseline, config)
    os.environ[SNAPSHOT_ENV] = json.dumps(decision, separators=(",", ":"))
    return {"baseline": baseline.model, "generation": decision.get("model", baseline.model),
            "approval_id": decision.get("approval_id"), "qa": os.environ.get("AI_DAILY_AUTO_QA_MODEL") or baseline.model}


def quarantine_failed_model(provider) -> None:
    """Best-effort circuit breaker, called only after a technical API failure."""
    from ai_gateway_client import load_runtime_config
    import fcntl
    try:
        config = load_runtime_config()
        directory = state_dir(config)
        if directory is None:
            return
        decision = read_object(directory / "active.json")
        key = gateway_key(provider)
        approved = [decision.get("model"), *decision.get("fallback_models", [])]
        if (provider.model not in approved or provider.model == decision.get("baseline_model")
                or decision.get("gateway_key") != key):
            return
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / "quarantine.lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            blocked = read_object(directory / "quarantine.json")
            blocked.setdefault(key, {})[provider.model] = {
                "at": datetime.now(timezone.utc).isoformat(), "reason": "technical_gateway_failure",
                "approval_id": decision.get("approval_id")}
            atomic_json(directory / "quarantine.json", blocked)
    except Exception:
        # Registry maintenance must not mask the original generation error.
        return
