from django.core.management.base import BaseCommand

from moments.services import sync_impact_updates


class Command(BaseCommand):
    help = (
        'Create "your blood reached someone" updates (and send the opt-in '
        'emails) for requests closed as fulfilled. Idempotent: one per '
        'request and donor. Safe to run hourly or daily.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Count what would be created; change nothing.',
        )

    def handle(self, *args, **options):
        count = sync_impact_updates(dry_run=options['dry_run'])
        verb = 'would be created' if options['dry_run'] else 'created'
        self.stdout.write(f'{count} update(s) {verb}.')
