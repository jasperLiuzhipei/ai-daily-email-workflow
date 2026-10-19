"""Bounded requests, observable token usage, and integrity-checked material cache."""
from pathlib import Path
import json
import os
import tempfile
import time
from threading import Lock

from event_editorial import EditorialError, digest


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, default=str)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class RuntimeBudget:
    def __init__(self, policy, clock=time.monotonic):
        self.clock = clock
        self.started = clock()
        self.limit_seconds = policy['runtime_budget_seconds']
        self.max_requests = policy['max_api_requests']
        self.lock = Lock()
        self.requests = self.successes = self.failures = 0
        self.input_tokens = self.output_tokens = self.usage_records = 0

    def reserve(self):
        with self.lock:
            remaining = self.limit_seconds - (self.clock() - self.started)
            if remaining < 1 or self.requests >= self.max_requests:
                raise EditorialError('Editorial runtime/request budget exhausted; remaining events are deferred')
            self.requests += 1
            return remaining

    def remaining(self):
        return max(0, self.limit_seconds - (self.clock() - self.started))

    def finish(self, metadata=None, failed=False):
        with self.lock:
            self.failures += int(failed)
            self.successes += int(not failed)
            usage = (metadata or {}).get('usage') or {}
            if usage:
                self.usage_records += 1
                self.input_tokens += int(usage.get('prompt_tokens') or usage.get('input_tokens') or 0)
                self.output_tokens += int(usage.get('completion_tokens') or usage.get('output_tokens') or 0)

    def summary(self):
        elapsed = round(self.clock() - self.started, 3)
        return {'elapsed_seconds': elapsed, 'budget_seconds': self.limit_seconds,
                'api_requests': self.requests, 'max_api_requests': self.max_requests,
                'successes': self.successes, 'failures': self.failures,
                'reported_input_tokens': self.input_tokens, 'reported_output_tokens': self.output_tokens,
                'usage_records': self.usage_records,
                'warning': elapsed >= self.limit_seconds * 0.8 or self.requests >= self.max_requests * 0.8,
                'currency_cost': None, 'cost_note': 'No price assumptions; token counts only where reported.'}


def read_material_cache(path, fingerprint, ttl_seconds, *, offline=False, now=None):
    try:
        record = json.loads(Path(path).read_text())
        if not isinstance(record, dict):
            return None
        if record.get('input_sha256') != fingerprint or digest(record['text']) != record['text_sha256']:
            return None
        if record['read_status'] != 'read' and not offline:
            return None  # A failed live fetch is retryable, not an eternal cache hit.
        age = (time.time() if now is None else now) - record.get('cached_at_epoch', 0)
        if not offline and not 0 <= age <= ttl_seconds:
            return None
        return record
    except (OSError, ValueError, KeyError, TypeError):
        return None
