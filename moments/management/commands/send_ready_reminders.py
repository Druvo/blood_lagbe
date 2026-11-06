from django.core.management.base import BaseCommand

from moments.services import ready_reminders_due, send_ready_reminder


class Command(BaseCommand):
    help = (
        'Email opted-in donors once per donation cycle when their recovery '
        'interval is over. A reminder, not medical clearance. Safe to run '
        'daily.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Show who would get a reminder; send nothing.',
        )

    def handle(self, *args, **options):
        due = ready_reminders_due()
        sent = 0
        for pref, eligibility in due:
            if options['dry_run']:
                self.stdout.write(
                    f'Would remind user #{pref.user_id} '
                    f'(cycle of {eligibility.last_date})',
                )
                continue
            if send_ready_reminder(pref, eligibility):
                sent += 1
        if options['dry_run']:
            self.stdout.write(f'Dry run: {len(due)} reminder(s) would be sent.')
        else:
            self.stdout.write(f'Sent {sent} reminder(s).')
