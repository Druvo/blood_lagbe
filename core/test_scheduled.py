from io import StringIO
from unittest import mock

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase

from core.management.commands import run_scheduled


class RunScheduledTests(TestCase):
    def run_command(self, *args):
        out, err = StringIO(), StringIO()
        call_command('run_scheduled', *args, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def test_list_names_every_job(self):
        out, _ = self.run_command('--list')
        self.assertEqual(
            out.split(), [name for name, _ in run_scheduled.JOBS],
        )

    def test_dry_run_runs_every_job_and_sends_nothing(self):
        out, _ = self.run_command('--dry-run')
        for name, _ in run_scheduled.JOBS:
            self.assertIn(f'{name}: ok', out)

    def test_only_runs_the_named_job(self):
        out, _ = self.run_command('--dry-run', '--only', 'prune_notifications')
        self.assertIn('prune_notifications: ok', out)
        self.assertNotIn('send_weekly_digest', out)

    def test_unknown_job_is_an_error(self):
        with self.assertRaises(CommandError):
            self.run_command('--only', 'no_such_job')


class FailingJobTests(SimpleTestCase):
    def test_one_failure_does_not_stop_the_rest(self):
        calls = []

        def fake(name, **kwargs):
            calls.append(name)
            if name == 'send_ready_reminders':
                raise RuntimeError('boom')

        err = StringIO()
        with mock.patch.object(run_scheduled, 'call_command', fake):
            with self.assertRaises(CommandError) as ctx:
                call_command('run_scheduled', stdout=StringIO(), stderr=err)
        self.assertEqual(calls, [name for name, _ in run_scheduled.JOBS])
        self.assertIn('send_ready_reminders', str(ctx.exception))
        self.assertIn('boom', err.getvalue())
