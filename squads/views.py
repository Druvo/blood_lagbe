from urllib.parse import quote

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from rewards.geo import DISTRICT_CHOICES, DISTRICT_NAMES
from rewards.leaderboard import PERIOD_ALL, PERIOD_MONTH, display_name
from squads.forms import JoinForm, SquadForm
from squads.models import (
    MAX_MEMBERS_PER_SQUAD,
    MAX_SQUADS_PER_USER,
    Squad,
    SquadMember,
)
from squads.services import (
    SquadError,
    create_squad,
    find_by_code,
    join_attempts_blocked,
    join_squad,
    member_board,
    record_failed_join,
    squad_ranking,
    squad_totals,
)

BAD_CODE = "That code didn't work. Check it and try again."
TOO_MANY = 'Too many tries. Please wait a few minutes before trying again.'


def _period(request):
    period = request.GET.get('period', PERIOD_MONTH)
    return period if period in (PERIOD_ALL, PERIOD_MONTH) else PERIOD_MONTH


def _membership(user, squad):
    if not user.is_authenticated:
        return None
    return SquadMember.objects.filter(squad=squad, user=user).first()


def _owner_only(request, squad, action):
    """None if the user owns the squad. Otherwise a redirect, or 404 for
    someone who should not even know a private squad exists."""
    if squad.owner_id == request.user.pk:
        return None
    if not squad.is_public and _membership(request.user, squad) is None:
        raise Http404('No such squad.')
    messages.error(request, f'Only the squad owner can {action}.')
    return redirect('squads:detail', pk=squad.pk)


@login_required
def squad_list(request):
    district = request.GET.get('district', '')
    if district not in DISTRICT_NAMES:
        district = ''

    mine = list(
        Squad.objects.filter(memberships__user=request.user)
        .annotate(member_count=Count('memberships')).order_by('name')
    )
    discover = (
        Squad.objects.filter(is_public=True)
        .exclude(memberships__user=request.user)
        .annotate(member_count=Count('memberships'))
    )
    if district:
        discover = discover.filter(district=district)
    discover = discover.order_by('-member_count', 'name')[:30]

    return render(request, 'squads/list.html', {
        'mine': mine,
        'discover': discover,
        'district': district,
        'district_choices': DISTRICT_CHOICES,
        'join_form': JoinForm(),
        'at_limit': len(mine) >= MAX_SQUADS_PER_USER,
        'max_squads': MAX_SQUADS_PER_USER,
    })


@login_required
def squad_create(request):
    form = SquadForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        try:
            squad = create_squad(request.user, **form.cleaned_data)
        except SquadError as exc:
            messages.error(request, str(exc))
            return redirect('squads:list')
        messages.success(
            request, 'Squad created. Invite your people with the code below.',
        )
        return redirect('squads:detail', pk=squad.pk)
    return render(request, 'squads/form.html', {
        'form': form, 'squad': None, 'max_squads': MAX_SQUADS_PER_USER,
    })


def squad_leaderboard(request):
    period = _period(request)
    return render(request, 'squads/leaderboard.html', {
        'period': period,
        'rows': squad_ranking(period),
    })


def squad_detail(request, pk):
    squad = get_object_or_404(Squad.objects.select_related('owner'), pk=pk)
    membership = _membership(request.user, squad)
    # A private squad does not even admit it exists to outsiders.
    if not squad.is_public and membership is None:
        raise Http404('No such squad.')
    period = _period(request)
    is_owner = (
        membership is not None and membership.role == SquadMember.Role.OWNER
    )

    board = member_board(squad, period)
    listed_ids = {e['user'].pk for e in board}
    members = list(squad.memberships.select_related('user__profile'))
    private_members = [m for m in members if m.user_id not in listed_ids]
    # The owner needs rows to manage; everyone else just sees a count.
    kickable = [
        m for m in private_members
        if is_owner and m.role != SquadMember.Role.OWNER
    ]

    context = {
        'squad': squad,
        'period': period,
        'membership': membership,
        'is_member': membership is not None,
        'is_owner': is_owner,
        'owner_name': display_name(squad.owner),
        'totals': squad_totals(squad),
        'board': board,
        'private_count': len(private_members),
        'private_kickable': kickable,
        'max_members': MAX_MEMBERS_PER_SQUAD,
    }
    if membership is not None:
        join_url = request.build_absolute_uri(
            reverse('squads:join-code', args=[squad.join_code]),
        )
        context.update({
            'join_url': join_url,
            'whatsapp_url': 'https://wa.me/?text=' + quote(
                f'Join my squad "{squad.name}" on {settings.SITE_NAME} and '
                f'donate blood together: {join_url}', safe='',
            ),
        })
    return render(request, 'squads/detail.html', context)


@login_required
def squad_edit(request, pk):
    squad = get_object_or_404(Squad, pk=pk)
    denied = _owner_only(request, squad, 'edit it')
    if denied:
        return denied
    form = SquadForm(
        request.POST if request.method == 'POST' else None, instance=squad,
    )
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Squad updated.')
        return redirect('squads:detail', pk=squad.pk)
    return render(request, 'squads/form.html', {
        'form': form, 'squad': squad, 'max_squads': MAX_SQUADS_PER_USER,
    })


@login_required
def join_with_code(request):
    """Type a code. Every bad code gets the same answer, whether the squad
    exists, is private or the code is just malformed."""
    form = JoinForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST':
        if join_attempts_blocked(request.user):
            messages.error(request, TOO_MANY)
        elif form.is_valid():
            squad = find_by_code(form.cleaned_data['code'])
            if squad is None:
                record_failed_join(request.user)
                messages.error(request, BAD_CODE)
            else:
                return _do_join(request, squad)
        else:
            record_failed_join(request.user)
            messages.error(request, BAD_CODE)
    return render(request, 'squads/join.html', {'form': form})


def _do_join(request, squad):
    try:
        join_squad(request.user, squad)
    except SquadError as exc:
        messages.error(request, str(exc))
        if SquadMember.objects.filter(squad=squad, user=request.user).exists():
            return redirect('squads:detail', pk=squad.pk)
        return redirect('squads:list')
    messages.success(request, f'Welcome to {squad.name}!')
    return redirect('squads:detail', pk=squad.pk)


def join_landing(request, code):
    """An invite link. GET shows which squad is inviting; joining is a POST."""
    squad = find_by_code(code)
    if request.method == 'POST':
        if not request.user.is_authenticated:
            return redirect(f'{settings.LOGIN_URL}?next={request.path}')
        if join_attempts_blocked(request.user):
            messages.error(request, TOO_MANY)
            return redirect('squads:join')
        if squad is None:
            record_failed_join(request.user)
            messages.error(request, BAD_CODE)
            return redirect('squads:join')
        return _do_join(request, squad)

    already = (
        squad is not None and request.user.is_authenticated
        and SquadMember.objects.filter(squad=squad, user=request.user).exists()
    )
    return render(request, 'squads/join_landing.html', {
        'squad': squad,  # None for an unknown code
        'member_count': squad.memberships.count() if squad else 0,
        'already_member': already,
        'code': code,
    })


@login_required
@require_POST
def join_public(request, pk):
    squad = get_object_or_404(Squad, pk=pk, is_public=True)
    return _do_join(request, squad)


@login_required
@require_POST
def squad_leave(request, pk):
    squad = get_object_or_404(Squad, pk=pk)
    membership = _membership(request.user, squad)
    if membership is None:
        messages.error(request, 'You are not in this squad.')
        return redirect('squads:list')
    if membership.role == SquadMember.Role.OWNER:
        messages.error(
            request, 'Owners cannot leave their squad. Delete it instead.',
        )
        return redirect('squads:detail', pk=squad.pk)
    membership.delete()
    messages.success(request, f'You left {squad.name}.')
    return redirect('squads:list')


@login_required
@require_POST
def squad_delete(request, pk):
    squad = get_object_or_404(Squad, pk=pk)
    denied = _owner_only(request, squad, 'delete it')
    if denied:
        return denied
    name = squad.name
    squad.delete()
    messages.success(request, f'{name} was deleted.')
    return redirect('squads:list')


@login_required
@require_POST
def squad_new_code(request, pk):
    squad = get_object_or_404(Squad, pk=pk)
    denied = _owner_only(request, squad, 'change the code')
    if denied:
        return denied
    squad.regenerate_code()
    messages.success(
        request, 'New join code created. The old one stopped working.',
    )
    return redirect('squads:detail', pk=squad.pk)


@login_required
@require_POST
def squad_kick(request, pk, user_id):
    squad = get_object_or_404(Squad, pk=pk)
    denied = _owner_only(request, squad, 'remove members')
    if denied:
        return denied
    target = SquadMember.objects.filter(squad=squad, user_id=user_id).first()
    if target is None:
        messages.error(request, 'That person is not in this squad.')
    elif target.role == SquadMember.Role.OWNER:
        messages.error(
            request, 'You are the owner. Delete the squad to close it.',
        )
    else:
        target.delete()
        messages.success(request, 'Member removed.')
    return redirect('squads:detail', pk=squad.pk)
