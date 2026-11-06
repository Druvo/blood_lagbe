from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from alerts.models import Notification


class Command(BaseCommand):
    help = 'Delete read notifications older than --days (default 90).'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=90)
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Only report how many would be deleted.',
        )

    def handle(self, *args, **options):
        days = options['days']
        if days < 1:
            raise CommandError('--days must be at least 1.')
        cutoff = timezone.now() - timedelta(days=days)
        # Unread messages are kept however old they are.
        qs = Notification.objects.filter(
            read_at__isnull=False, created_at__lt=cutoff,
        )
        total = qs.count()
        if options['dry_run']:
            self.stdout.write(f'Would delete {total} read notification(s).')
            return
        qs.delete()
        self.stdout.write(f'Deleted {total} read notification(s).')
