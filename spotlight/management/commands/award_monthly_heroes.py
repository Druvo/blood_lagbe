"""Save the final Hero of the Month awards for a finished month.

    python manage.py award_monthly_heroes                  # the month just ended
    python manage.py award_monthly_heroes --month 2026-09  # a chosen month
    python manage.py award_monthly_heroes --dry-run        # show, save nothing

Safe to run every day: an award that already exists is left alone (one winner
per award per month is a database constraint). Without --month, last month is
used once the first few days of the new month have passed, so a donation
approved just after midnight still counts.
"""
import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from spotlight import heroes


class Command(BaseCommand):
    help = 'Save the final Hero of the Month awards for a finished month.'

    def add_arguments(self, parser):
        parser.add_argument('--month', help='YYYY-MM (a month that has ended).')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        today = timezone.localdate()
        if options['month']:
            try:
                parsed = datetime.datetime.strptime(options['month'], '%Y-%m')
            except ValueError:
                raise CommandError('--month must look like 2026-09.')
            year, month = parsed.year, parsed.month
            if not heroes.valid_month(year, month, today):
                raise CommandError('That month is not valid.')
            if not heroes.month_is_final(year, month, today):
                raise CommandError(
                    'That month is not over yet; final awards wait until it ends.')
        else:
            year, month = heroes.target_month(today)
            if not heroes.valid_month(year, month, today):
                self.stdout.write('No month to award yet.')
                return

        dry = options['dry_run']
        results = heroes.award_month(year, month, dry_run=dry)
        label = f'{year}-{month:02d}'
        if not results:
            self.stdout.write(f'{label}: nothing to award (already saved or no winners).')
            return
        for entry, created in results:
            verb = 'would award' if dry else ('awarded' if created else 'kept')
            self.stdout.write(
                f'{label}: {verb} {entry["kind"]} to {entry["name"]} '
                f'({entry["count"]}).'
            )
