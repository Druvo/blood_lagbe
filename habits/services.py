"""Daily micro-actions and the cosmetic daily-visit streak.

Everything here is separate from donor points, tiers, the leaderboard and the
donation streak. Missing a day is forgiven once per ISO week, and the streak
never asks anyone to donate sooner: it only counts small visits.
"""
import datetime

from django.db import IntegrityError, transaction
from django.utils import timezone

from core import ratelimit
from habits import content
from habits.models import DailyCompletion, ShareAction
from rewards.models import BloodRequest

SHARE = DailyCompletion.Task.SHARE
AVAILABILITY = DailyCompletion.Task.AVAILABILITY
FACT = DailyCompletion.Task.FACT
QUIZ = DailyCompletion.Task.QUIZ

# The catalog of daily tasks, in the order they are shown.
TASK_ORDER = [SHARE, AVAILABILITY, FACT, QUIZ]
DAILY_TASK_COUNT = 3
QUIZ_QUESTIONS = 3
# A member can open this many requests through the share link per day.
MAX_SHARES_PER_DAY = 10
# Write endpoints: this many POSTs per rate-limit window per member.
WRITE_LIMIT = 40
SHARE_CANDIDATES = 30

ONE_DAY = datetime.timedelta(days=1)


def today():
    return timezone.localdate()


# ------------------------------------------------------------ the day's plan

def _seed(user, day):
    return day.toordinal() + user.pk


def pick_share_request(user, day=None):
    """An open request that still needs donors, chosen by date and user so
    the same member sees the same one all day. Prefers their own district.
    Never their own request, and never one the community has flagged."""
    day = day or today()
    candidates = list(
        BloodRequest.objects.still_needed().public()
        .exclude(requester=user)
        .order_by('needed_by', '-created_at', 'pk')[:SHARE_CANDIDATES]
    )
    if not candidates:
        return None
    district = getattr(getattr(user, 'profile', None), 'district', '')
    local = [r for r in candidates if district and r.district == district]
    pool = local or candidates
    return pool[_seed(user, day) % len(pool)]


def assigned_tasks(user, day=None, share_request=None):
    """The (usually three) task keys for this member today. Deterministic in
    the date and the user id: one task of the four rests each day. With no
    request to share, the other three are shown."""
    day = day or today()
    if share_request is None:
        return [t for t in TASK_ORDER if t != SHARE]
    resting = TASK_ORDER[_seed(user, day) % len(TASK_ORDER)]
    return [t for t in TASK_ORDER if t != resting]


def done_tasks(user, day=None):
    day = day or today()
    return set(
        DailyCompletion.objects.filter(user=user, day=day)
        .values_list('task', flat=True)
    )


def todays_plan(user, day=None):
    """Everything the Today page and the panels need, in one place."""
    day = day or today()
    share_request = pick_share_request(user, day)
    done = done_tasks(user, day)
    keys = assigned_tasks(user, day, share_request)
    # A task finished earlier today stays on the list even if the day's
    # plan has since changed (the request it was about has closed).
    keys = [t for t in TASK_ORDER if t in keys or t in done]
    return {
        'day': day,
        'tasks': keys,
        'done': done,
        'share_request': share_request,
        'total': len(keys),
        'done_count': len([t for t in keys if t in done]),
    }


def can_do(user, task, day=None):
    """True when `task` is on today's list for this member."""
    plan = todays_plan(user, day)
    return task in plan['tasks']


# ----------------------------------------------------------- facts and quiz

def fact_for(user, day=None):
    day = day or today()
    all_facts = content.facts()
    return {
        'text': all_facts[_seed(user, day) % len(all_facts)],
        'footer': content.FACT_FOOTER,
    }


def quiz_for(user, day=None):
    """Three different questions, rotating every day."""
    day = day or today()
    bank = content.quiz_bank()
    start = (day.toordinal() * QUIZ_QUESTIONS + user.pk) % len(bank)
    return [
        dict(bank[(start + i) % len(bank)], index=i)
        for i in range(QUIZ_QUESTIONS)
    ]


def parse_answers(raw):
    """"M,F,F" to ['M', 'F', 'F']; None unless there are exactly three valid
    answers."""
    parts = [p.strip().upper() for p in (raw or '').split(',')]
    if len(parts) != QUIZ_QUESTIONS or any(p not in ('M', 'F') for p in parts):
        return None
    return parts


def quiz_review(user, day, answers=None):
    """Today's questions with the member's answers marked, for the reveal."""
    questions = quiz_for(user, day)
    rows = []
    score = 0
    for q, given in zip(questions, answers or [None] * len(questions)):
        correct = given is not None and given == q['answer']
        score += 1 if correct else 0
        rows.append(dict(q, given=given, correct=correct))
    return rows, score


# ------------------------------------------------------------- completions

def complete_task(user, task, day=None, detail=''):
    """Record a finished task once. Returns (completion, created); a
    replayed request returns the existing row and creates nothing."""
    day = day or today()
    try:
        with transaction.atomic():
            return DailyCompletion.objects.get_or_create(
                user=user, day=day, task=task,
                defaults={
                    'detail': detail[:40],
                    'hour': timezone.localtime().hour,
                },
            )
    except IntegrityError:  # lost a race with a twin request
        return DailyCompletion.objects.get(user=user, day=day, task=task), False


def record_share(user, blood_request, day=None):
    """Count a tracked share click: once per member, request and day, and
    never more than MAX_SHARES_PER_DAY a day. Returns True when it was
    recorded now."""
    day = day or today()
    if ShareAction.objects.filter(user=user, day=day).count() >= MAX_SHARES_PER_DAY:
        return False
    try:
        with transaction.atomic():
            _, created = ShareAction.objects.get_or_create(
                user=user, blood_request=blood_request, day=day,
            )
    except IntegrityError:
        return False
    return created


def write_limited(user, scope='habits-write'):
    """Cheap abuse guard for the POST endpoints. True when over the limit."""
    if ratelimit.is_limited(scope, user.pk, WRITE_LIMIT):
        return True
    ratelimit.hit(scope, user.pk)
    return False


# ------------------------------------------------------------------ streaks

def _iso_week(day):
    iso = day.isocalendar()
    return iso[0], iso[1]


def _walk(days, end, in_progress=False):
    """(streak, forgiven ISO weeks) counting back from `end`."""
    streak = 0
    forgiven_weeks = set()
    day = end
    while True:
        if day in days:
            streak += 1
        elif day == end and in_progress:
            pass
        elif (day - ONE_DAY) in days and _iso_week(day) not in forgiven_weeks:
            forgiven_weeks.add(_iso_week(day))
        else:
            break
        day -= ONE_DAY
    return streak, forgiven_weeks


def streak_ending(days, end, in_progress=False):
    """Consecutive days with at least one completion, counting back from
    `end`. One missed day per ISO week is forgiven (it bridges the gap but is
    not counted). `in_progress` means `end` is today and may still be done, so
    an empty today breaks nothing."""
    return _walk(days, end, in_progress)[0]


def longest_streak(days):
    """Best streak over a set of days, using the same forgiveness rule."""
    best = 0
    for end in days:
        best = max(best, streak_ending(days, end))
    return best


def visit_days(user, year=None):
    qs = DailyCompletion.objects.filter(user=user)
    if year is not None:
        qs = qs.filter(day__year=year)
    return set(qs.order_by().values_list('day', flat=True).distinct())


def streak_info(user, day=None):
    """Current cosmetic streak plus whether this week's grace is spoken for."""
    day = day or today()
    days = visit_days(user)
    current, forgiven = _walk(days, day, in_progress=True)
    return {
        'current': current,
        'done_today': day in days,
        'grace_used': current > 0 and _iso_week(day) in forgiven,
    }


def summary(user, day=None):
    """Cheap numbers for the small hub and home panels, in one query:
    tasks finished today (out of the usual three) and the visit streak."""
    day = day or today()
    rows = list(
        DailyCompletion.objects.filter(user=user).order_by()
        .values_list('day', 'task')
    )
    days = {row_day for row_day, _ in rows}
    done_today = {task for row_day, task in rows if row_day == day}
    current, forgiven = _walk(days, day, in_progress=True)
    return {
        'day': day,
        'done_count': min(len(done_today), DAILY_TASK_COUNT),
        'total': DAILY_TASK_COUNT,
        'streak': {
            'current': current,
            'done_today': day in days,
            'grace_used': current > 0 and _iso_week(day) in forgiven,
        },
    }
