from django.core.management.base import BaseCommand

from events.awards import sync_all


class Command(BaseCommand):
    help = (
        'Award campaign badges to every donor with an approved donation '
        'inside an active campaign window. Safe to run again.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Count the badges that would be awarded without saving.',
        )

    def handle(self, *args, **options):
        dry = options['dry_run']
        count = sync_all(dry_run=dry)
        verb = 'Would award' if dry else 'Awarded'
        self.stdout.write(f'{verb} {count} campaign badge(s).')
