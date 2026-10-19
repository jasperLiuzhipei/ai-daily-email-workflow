from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch
from types import SimpleNamespace
import json
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from work_calendar import DEFAULT_CALENDAR, workday_decision, run_lock, previous_publication_cutoff
from workflow_run_state import new_workflow_state
import run_ai_daily as runner


class WorkCalendarTests(TestCase):
    def test_official_calendar(self):
        for day, expected in [('2026-09-20', True), ('2026-10-10', True), ('2026-01-04', True), ('2026-02-14', True), ('2026-02-28', True), ('2026-05-09', True), ('2026-09-25', False), ('2026-10-01', False), ('2026-09-19', False), ('2026-09-21', True), ('2026-02-23', False)]:
            with self.subTest(day=day):
                self.assertEqual(workday_decision(day)[0], expected)

    def test_overrides_and_missing_year(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'calendar.json'
            data = json.loads(DEFAULT_CALENDAR.read_text())
            data['overrides'] = {'2026-09-20': {'workday': False, 'reason': '公司休息'}, '2026-09-25': {'workday': True, 'reason': '临时补刊'}}
            path.write_text(json.dumps(data))
            self.assertFalse(workday_decision('2026-09-20', path)[0])
            self.assertTrue(workday_decision('2026-09-25', path)[0])
            with self.assertRaisesRegex(ValueError, 'Missing verified 2027'):
                workday_decision('2027-01-04', path)
            data['overrides']['2026-09-20']['workday'] = 'false'
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                workday_decision('2026-09-20', path)

    def test_lock_exclusion_and_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            with run_lock(Path(tmp)):
                with self.assertRaisesRegex(ValueError, 'Another workflow'):
                    with run_lock(Path(tmp)):
                        pass
            with run_lock(Path(tmp)):
                pass

    def test_holiday_gap_uses_published_selection_cutoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for day, sent in [('2026-09-18', True), ('2026-09-19', False)]:
                directory = root / day
                directory.mkdir()
                state = new_workflow_state(day)
                if sent:
                    state['phase'] = 'SENT'
                (directory / 'workflow_run.json').write_text(json.dumps(state))
                (directory / f'source_selection_auto_{day}.json').write_text(json.dumps({'window': {'end_at': day+'T08:30:00+08:00'}}))
            self.assertEqual(previous_publication_cutoff(root, '2026-09-20'), '2026-09-18T08:30:00+08:00')

    def test_nonworkday_never_starts_models_or_send(self):
        for action in ['prepare', 'prepare-reviewed', 'publish-reviewed', 'preview', 'prod']:
            args = SimpleNamespace(action=action, send=True, date='2026-09-25', work_calendar=DEFAULT_CALENDAR)
            with patch.object(runner, 'parse_args', return_value=args), patch.object(runner, 'execute_workflow') as execute:
                runner.main()
                execute.assert_not_called()

    def test_scheduled_historical_date_does_not_catch_up(self):
        args = SimpleNamespace(action='prepare', send=False, date='2026-01-04', work_calendar=DEFAULT_CALENDAR, scheduled=True)
        with patch.object(runner, 'parse_args', return_value=args), patch.object(runner, 'execute_workflow') as execute:
            runner.main()
            execute.assert_not_called()

    def test_published_issue_does_not_rebuild(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = runner.default_paths('2026-09-20')
            paths.run_dir = Path(tmp)
            args = SimpleNamespace(action='prepare', send=False, date='2026-09-20', work_calendar=DEFAULT_CALENDAR, scheduled=False, rebuild=True)
            with patch.object(runner, 'parse_args', return_value=args), patch.object(runner, 'apply_overrides', return_value=paths), patch.object(runner, 'has_existing_prod_send', return_value=True), patch.object(runner, 'execute_workflow') as execute:
                runner.main()
                execute.assert_not_called()

    def test_prepare_once_and_explicit_rebuild(self):
        with tempfile.TemporaryDirectory() as tmp:
            day = '2026-09-20'
            paths = runner.default_paths(day)
            paths.run_dir = Path(tmp)
            paths.workflow_state = Path(tmp)/'workflow_run.json'
            paths.html = Path(tmp)/'preview.html'
            args = SimpleNamespace(action='prepare', send=False, date=day, work_calendar=DEFAULT_CALENDAR, scheduled=False, rebuild=False, start=None)
            with patch.object(runner, 'parse_args', return_value=args), patch.object(runner, 'apply_overrides', return_value=paths), patch.object(runner, 'has_existing_prod_send', return_value=False), patch.object(runner, 'previous_publication_cutoff', return_value='2026-09-18T08:30:00+08:00'), patch.object(runner, 'execute_workflow') as execute:
                runner.main()
                runner.main()
                self.assertEqual(execute.call_count, 1)
                self.assertTrue(args.allow_wide_news_window)
                args.rebuild = True
                runner.main()
                self.assertEqual(execute.call_count, 2)

    def test_failed_prepare_can_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = runner.default_paths('2026-09-20')
            paths.run_dir = Path(tmp)
            paths.workflow_state = Path(tmp)/'workflow_run.json'
            paths.html = Path(tmp)/'preview.html'
            args = SimpleNamespace(action='prepare', send=False, date='2026-09-20', work_calendar=DEFAULT_CALENDAR, scheduled=False, rebuild=False, start='2026-09-18T08:30:00+08:00')
            with patch.object(runner, 'parse_args', return_value=args), patch.object(runner, 'apply_overrides', return_value=paths), patch.object(runner, 'has_existing_prod_send', return_value=False), patch.object(runner, 'execute_workflow', side_effect=SystemExit('failure')):
                with self.assertRaises(SystemExit):
                    runner.main()
                self.assertFalse((Path(tmp)/'preparation_completed.json').exists())

if __name__ == '__main__':
    main()
