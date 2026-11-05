"""Community stories: short true accounts from donors, recipient families and
volunteers. Every story is moderated before it appears publicly."""
import re

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.validators import MaxLengthValidator, MinLengthValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from rewards.blood import BLOOD_GROUP_CHOICES
from rewards.geo import DISTRICT_CHOICES

TITLE_MAX = 80
BODY_MAX = 1200
TITLE_MIN = 5
BODY_MIN = 40
# A story reported this many times since it was last approved goes back to
# the moderation queue until someone re-reads it.
REPORT_THRESHOLD = 3
# Stops one account flooding the review queue.
MAX_PENDING_PER_AUTHOR = 3
# Rolling 24 hour caps per account, whatever the moderation outcome.
MAX_STORIES_PER_DAY = 5
MAX_REPORTS_PER_DAY = 10

_HTML_LIKE = re.compile(r'<\s*[/!?a-zA-Z]')


def validate_plain_text(value):
    """Stories are plain text. Reject anything that looks like markup rather
    than silently rewriting what someone wrote about a hard moment."""
    if _HTML_LIKE.search(value or ''):
        raise ValidationError(
            _('Please write plain text only, without HTML or tags.'),
            code='html',
        )


def normalise_text(value, multiline=False):
    """Trim, unify line endings and cap blank lines. Never touches wording."""
    value = (value or '').replace('\r\n', '\n').replace('\r', '\n')
    if not multiline:
        return ' '.join(value.split())
    lines = [line.rstrip() for line in value.strip().split('\n')]
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines))


class StoryQuerySet(models.QuerySet):
    def approved(self):
        return self.filter(status=Story.Status.APPROVED)

    def visible_to(self, user):
        """Approved stories for everyone; staff also see all; authors see
        their own whatever the status."""
        if user is not None and user.is_authenticated:
            if getattr(user, 'is_staff', False):
                return self
            return self.filter(
                models.Q(status=Story.Status.APPROVED) | models.Q(author=user)
            )
        return self.approved()


class Story(models.Model):
    class Kind(models.TextChoices):
        DONOR = 'donor', _('Donor')
        RECIPIENT = 'recipient', _('Recipient family')
        VOLUNTEER = 'volunteer', _('Volunteer')

    class Status(models.TextChoices):
        PENDING = 'pending', _('Pending review')
        APPROVED = 'approved', _('Published')
        REJECTED = 'rejected', _('Not published')

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='stories',
    )
    kind = models.CharField(
        _('I am sharing as'), max_length=10, choices=Kind.choices,
        default=Kind.DONOR,
    )
    title = models.CharField(
        _('Title'), max_length=TITLE_MAX,
        validators=[MinLengthValidator(TITLE_MIN), validate_plain_text],
    )
    body = models.TextField(
        _('Your story'), max_length=BODY_MAX,
        validators=[
            MinLengthValidator(BODY_MIN), MaxLengthValidator(BODY_MAX),
            validate_plain_text,
        ],
    )
    district = models.CharField(
        _('District'), max_length=100, choices=DISTRICT_CHOICES, blank=True,
    )
    blood_group = models.CharField(
        _('Blood group'), max_length=3, choices=BLOOD_GROUP_CHOICES,
        blank=True,
    )
    donation = models.ForeignKey(
        'rewards.Donation', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='stories', verbose_name=_('Link a verified donation'),
    )
    anonymous = models.BooleanField(
        _('Share without my name'), default=False,
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    moderation_note = models.TextField(
        _('Moderation note'), blank=True,
        help_text=_('Shown to the author when a story is not published.'),
    )

    objects = StoryQuerySet.as_manager()

    class Meta:
        ordering = ['-approved_at', '-created_at', '-id']
        verbose_name_plural = 'stories'
        indexes = [models.Index(fields=['status', '-approved_at'])]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse('story-detail', args=[self.pk])

    # -- validation -------------------------------------------------------
    def clean(self):
        self.title = normalise_text(self.title)
        self.body = normalise_text(self.body, multiline=True)
        if self.donation_id:
            donation = self.donation
            if donation.donor_id != self.author_id:
                raise ValidationError({
                    'donation': _('You can only link your own donation.'),
                })
            if donation.status != donation.Status.APPROVED:
                raise ValidationError({
                    'donation': _('Only a verified donation can be linked.'),
                })

    def save(self, *args, **kwargs):
        self.title = normalise_text(self.title)
        self.body = normalise_text(self.body, multiline=True)
        super().save(*args, **kwargs)

    # -- state ------------------------------------------------------------
    @property
    def is_approved(self):
        return self.status == self.Status.APPROVED

    def approve(self, note=''):
        self.status = self.Status.APPROVED
        self.approved_at = timezone.now()
        if note:
            self.moderation_note = note
        self.save(update_fields=['status', 'approved_at', 'moderation_note'])

    def reject(self, note=''):
        self.status = self.Status.REJECTED
        self.approved_at = None
        if note:
            self.moderation_note = note
        self.save(update_fields=['status', 'approved_at', 'moderation_note'])

    # -- display ----------------------------------------------------------
    @property
    def hides_author(self):
        """True when the author must not be identifiable: they asked for it
        on this story, or opted out of public listings on their profile."""
        if self.anonymous:
            return True
        try:
            return not self.author.profile.show_on_leaderboard
        except ObjectDoesNotExist:
            return False

    @property
    def public_name(self):
        from rewards.leaderboard import display_name
        if self.hides_author:
            noun = {
                self.Kind.DONOR: 'donor',
                self.Kind.RECIPIENT: 'family',
                self.Kind.VOLUNTEER: 'volunteer',
            }.get(self.kind, 'member')
            return f'A {settings.SITE_NAME} {noun}'
        return display_name(self.author)

    @property
    def initial(self):
        """Avatar letter. Empty for anonymous authors and for the generic
        'Donor #12' fallback, so templates show an icon instead."""
        if self.hides_author:
            return ''
        first = (self.author.first_name or '').strip()
        return first[:1].upper()

    @property
    def share_text(self):
        """What goes into a share message: the title only, never a name."""
        return f'{self.title} - a story on {settings.SITE_NAME}'


class StoryReaction(models.Model):
    class Kind(models.TextChoices):
        HEART = 'heart', _('Moved me')
        THANKS = 'thanks', _('Thank you')
        PROUD = 'proud', _('Proud of you')

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='story_reactions',
    )
    story = models.ForeignKey(
        Story, on_delete=models.CASCADE, related_name='reactions',
    )
    kind = models.CharField(max_length=10, choices=Kind.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'story', 'kind'], name='unique_story_reaction',
            ),
        ]

    def __str__(self):
        return f'{self.kind} on story {self.story_id}'


class StoryReport(models.Model):
    class Reason(models.TextChoices):
        UNTRUE = 'untrue', _('Does not seem true')
        PRIVACY = 'privacy', _('Shares private details')
        OFFENSIVE = 'offensive', _('Offensive or unkind')
        SPAM = 'spam', _('Spam or advert')
        OTHER = 'other', _('Something else')

    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='story_reports',
    )
    story = models.ForeignKey(
        Story, on_delete=models.CASCADE, related_name='reports',
    )
    reason = models.CharField(
        max_length=10, choices=Reason.choices, default=Reason.OTHER,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['reporter', 'story'], name='unique_story_report',
            ),
        ]

    def __str__(self):
        return f'{self.reason} report on story {self.story_id}'
