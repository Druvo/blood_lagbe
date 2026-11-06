"""Send the weekly digest to members who opted in.

Run it once a week from cron: `python manage.py send_weekly_digest`.
Safe to run twice: a member gets at most one digest per ISO week.
"""
from django.conf import settings
from django.core.mail import EmailMessage
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from habits import digest
from habits.models import DigestLog, DigestPref


class _AlreadySent(Exception):
    pass


class Command(BaseCommand):
    help = 'Email the weekly digest to members who opted in (once per ISO week).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Print what would be sent. Sends nothing, logs nothing.',
        )
        parser.add_argument(
            '--user', type=int, default=None, metavar='ID',
            help='Only this user id (they still must have opted in).',
        )

    def handle(self, *args, dry_run=False, user=None, **options):
        today = timezone.localdate()
        year, week = digest.iso_week(today)
        prefs = DigestPref.objects.filter(
            enabled=True, user__is_active=True,
        ).select_related('user', 'user__profile').order_by('user_id')
        if user is not None:
            prefs = prefs.filter(user_id=user)
            if not prefs.exists():
                raise CommandError(
                    f'User {user} has not opted in to the weekly digest, '
                    'or does not exist.'
                )

        sent = skipped = failed = 0
        for pref in prefs:
            member = pref.user
            if not member.email:
                skipped += 1
                self.stdout.write(f'skip {member.pk}: no email address')
                continue
            if DigestLog.objects.filter(
                user=member, iso_year=year, iso_week=week,
            ).exists():
                skipped += 1
                self.stdout.write(f'skip {member.pk}: already sent {digest.week_key(today)}')
                continue

            message = digest.build_digest(member, today)
            if dry_run:
                sent += 1
                self.stdout.write(
                    f'--- would send to user {member.pk} ({digest.week_key(today)}) ---\n'
                    f'Subject: {message.subject}\n\n{message.body}\n'
                )
                continue

            try:
                with transaction.atomic():
                    _, created = DigestLog.objects.get_or_create(
                        user=member, iso_year=year, iso_week=week,
                        defaults={
                            'points': message.points, 'level': message.level,
                        },
                    )
                    if not created:
                        raise _AlreadySent
                    self._send(member, message)
                    pref.last_sent_week = digest.week_key(today)
                    pref.save(update_fields=['last_sent_week', 'updated_at'])
            except _AlreadySent:
                skipped += 1
            except Exception as exc:  # the log row rolled back: retry next run
                failed += 1
                self.stderr.write(f'fail {member.pk}: {exc}')
            else:
                sent += 1

        verb = 'Would send' if dry_run else 'Sent'
        self.stdout.write(
            f'{verb} {sent}, skipped {skipped}, failed {failed}.'
        )

    def _send(self, member, message):
        link = digest.absolute(digest.unsubscribe_path(member))
        EmailMessage(
            subject=message.subject, body=message.body,
            from_email=settings.DEFAULT_FROM_EMAIL, to=[member.email],
            headers={
                'List-Unsubscribe': f'<{link}>',
                'List-Unsubscribe-Post': 'List-Unsubscribe=One-Click',
            },
        ).send(fail_silently=False)
