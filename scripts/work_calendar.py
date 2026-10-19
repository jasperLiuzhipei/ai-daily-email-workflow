"""Versioned company work calendar shared by manual and scheduled runs."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import date, datetime
import fcntl
import json
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT_CALENDAR = Path(__file__).resolve().parent.parent / 'config/work_calendar.json'
SHANGHAI = ZoneInfo('Asia/Shanghai')


def today_iso() -> str:
    return datetime.now(SHANGHAI).date().isoformat()


def workday_decision(day: str, calendar: Path = DEFAULT_CALENDAR) -> tuple[bool, str]:
    current = date.fromisoformat(day)
    data = json.loads(calendar.read_text(encoding='utf-8'))
    if data.get('schema_version') != 1 or data.get('timezone') != 'Asia/Shanghai':
        raise ValueError('Invalid work calendar schema or timezone')
    override = data.get('overrides', {}).get(day)
    if override is not None:
        if not isinstance(override, dict) or type(override.get('workday')) is not bool or not override.get('reason'):
            raise ValueError(f'Invalid calendar override: {day}; workday boolean and reason required')
        return override['workday'], f"company override: {override['reason']}"
    annual = data.get('years', {}).get(str(current.year))
    if not annual or not annual.get('source'):
        raise ValueError(f'Missing verified {current.year} work calendar; update {calendar}')
    workdays = set(annual['workdays'])
    holidays = set(annual['holidays'])
    for entry in workdays | holidays:
        if date.fromisoformat(entry).year != current.year:
            raise ValueError(f'Calendar date belongs to another year: {entry}')
    if workdays & holidays:
        raise ValueError('Calendar workdays and holidays overlap')
    if day in workdays:
        return True, 'official makeup workday'
    if day in holidays:
        return False, 'official holiday'
    return current.weekday() < 5, 'regular weekday' if current.weekday() < 5 else 'weekend'


@contextmanager
def run_lock(run_dir: Path):
    """Use one OS lock for manual and timer preparation, preview and sending."""
    run_dir.mkdir(parents=True, exist_ok=True)
    with (run_dir / '.workflow.lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError(f'Another workflow process is running for {run_dir.name}') from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def previous_publication_cutoff(runs_root: Path, day: str) -> str | None:
    """Resume at the last successfully published issue's actual selection cutoff."""
    from workflow_run_state import is_prod_sent, load_workflow_state
    for candidate in sorted(runs_root.glob('????-??-??/workflow_run.json'), reverse=True):
        previous = candidate.parent.name
        if previous >= day or not is_prod_sent(load_workflow_state(candidate)):
            continue
        selection = candidate.parent / f'source_selection_auto_{previous}.json'
        if not selection.exists():
            raise ValueError(f'Published issue {previous} is missing its source selection cutoff')
        cutoff = json.loads(selection.read_text(encoding='utf-8'))['window']['end_at']
        parsed = datetime.fromisoformat(cutoff)
        if parsed.tzinfo is None:
            raise ValueError(f'Published issue {previous} cutoff must contain timezone')
        return cutoff
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', default=today_iso())
    parser.add_argument('--calendar', type=Path, default=DEFAULT_CALENDAR)
    args = parser.parse_args()
    try:
        workday, reason = workday_decision(args.date, args.calendar)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit(f'Work calendar error: {exc}') from exc
    print(json.dumps({'date': args.date, 'workday': workday, 'reason': reason}, ensure_ascii=False))


if __name__ == '__main__':
    main()
