"""Rules for creating drives, RSVPs and squad entries."""
import datetime

from django.db import IntegrityError, transaction
from django.utils import timezone

from events.models import (
    MAX_EVENTS_PER_DAY,
    MAX_PENDING_EVENTS,
    Event,
    EventRSVP,
    EventTeam,
)
from squads.models import Squad


class EventError(Exception):
    """A rule was broken; the message is safe to show to the user."""


def creation_blocked(user):
    """A friendly reason this person cannot post another drive, or ''."""
    since = timezone.now() - datetime.timedelta(days=1)
    recent = Event.objects.filter(organizer=user, created_at__gte=since).count()
    if recent >= MAX_EVENTS_PER_DAY:
        return (
            f'You can post up to {MAX_EVENTS_PER_DAY} drives a day. '
            'Please try again tomorrow.'
        )
    waiting = Event.objects.filter(
        organizer=user, status=Event.Status.PENDING,
    ).count()
    if waiting >= MAX_PENDING_EVENTS:
        return (
            f'You already have {MAX_PENDING_EVENTS} drives waiting for '
            'approval. We will review them soon.'
        )
    return ''


def rsvp_going(event_id, user, share_name=False):
    """Mark ``user`` as going. Atomic, idempotent, capacity-aware.

    Returns ``(rsvp, created)``. Raises EventError with a friendly reason.
    """
    with transaction.atomic():
        event = Event.objects.select_for_update().get(pk=event_id)
        if not event.accepts_rsvps:
            raise EventError('This drive is not taking RSVPs.')
        existing = EventRSVP.objects.filter(event=event, user=user).first()
        if existing:
            if existing.share_name != share_name:
                existing.share_name = share_name
                existing.save(update_fields=['share_name'])
            return existing, False
        if event.capacity is not None:
            if event.rsvps.count() >= event.capacity:
                raise EventError('Sorry, this drive is full.')
        try:
            with transaction.atomic():
                rsvp = EventRSVP.objects.create(
                    event=event, user=user, share_name=share_name,
                )
        except IntegrityError:  # a double click raced us
            return EventRSVP.objects.get(event=event, user=user), False
        return rsvp, True


def cancel_rsvp(event, user):
    """Remove an RSVP before the start. Returns True if one was removed."""
    if event.has_started:
        raise EventError(
            'The drive has started, so your RSVP is kept as a record.'
        )
    deleted, _ = EventRSVP.objects.filter(event=event, user=user).delete()
    return bool(deleted)


def enter_squad(event, user, squad_id):
    """Enter a squad the user owns. Returns ``(team, created)``."""
    if not event.accepts_rsvps:
        raise EventError('This drive is not taking entries.')
    squad = Squad.objects.filter(pk=squad_id, owner=user).first()
    if squad is None:
        raise EventError('Only a squad owner can enter their squad.')
    with transaction.atomic():
        return EventTeam.objects.get_or_create(
            event=event, squad=squad, defaults={'entered_by': user},
        )
