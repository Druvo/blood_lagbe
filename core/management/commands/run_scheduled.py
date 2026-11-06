"""Run every recurring job in one go, so a single cron line is enough.

    python manage.py run_scheduled            # all jobs
    python manage.py run_scheduled --dry-run  # report what would happen
    python manage.py run_scheduled --only send_weekly_digest
    python manage.py run_scheduled --list

Each job is idempotent (reminders, digests and updates are logged per user),
so running this daily, or more often, is safe. A failing job does not stop the
others; the command exits non-zero if any job failed.
"""
import time

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

# (command, takes --dry-run). Order matters little; updates go out first.
JOBS = (
    ('send_impact_updates', True),
    ('send_ready_reminders', True),
    ('send_weekly_digest', True),
    ('award_campaign_badges', True),
    ('award_monthly_heroes', True),
    ('prune_notifications', True),
)


class Command(BaseCommand):
    help = 'Run all recurring jobs (reminders, digests, badges, cleanup).'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--only', action='append', default=[],
                            help='Run just this job (repeatable).')
        parser.add_argument('--list', action='store_true',
                            help='List the jobs and exit.')

    def handle(self, *args, **options):
        names = [name for name, _ in JOBS]
        if options['list']:
            for name in names:
                self.stdout.write(name)
            return
        unknown = [n for n in options['only'] if n not in names]
        if unknown:
            raise CommandError(
                f'Unknown job(s): {", ".join(unknown)}. Known: {", ".join(names)}.'
            )

        failed = []
        for name, takes_dry_run in JOBS:
            if options['only'] and name not in options['only']:
                continue
            kwargs = {}
            if options['dry_run'] and takes_dry_run:
                kwargs['dry_run'] = True
            started = time.monotonic()
            try:
                call_command(name, stdout=self.stdout, **kwargs)
            except Exception as exc:  # one broken job must not block the rest
                failed.append(name)
                self.stderr.write(self.style.ERROR(f'{name}: failed: {exc!r}'))
            else:
                elapsed = time.monotonic() - started
                self.stdout.write(self.style.SUCCESS(f'{name}: ok ({elapsed:.1f}s)'))
        if failed:
            raise CommandError(f'{len(failed)} job(s) failed: {", ".join(failed)}')
