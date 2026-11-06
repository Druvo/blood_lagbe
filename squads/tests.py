"""Squads: rules, permissions, privacy and totals."""
import datetime
import re

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from rewards.models import Donation
from rewards.tests import make_user
from squads.models import (
    CODE_ALPHABET,
    CODE_LENGTH,
    MAX_MEMBERS_PER_SQUAD,
    MAX_SQUADS_PER_USER,
    Squad,
    SquadMember,
)
from squads.services import (
    JOIN_FAIL_LIMIT,
    SquadError,
    create_squad,
    join_squad,
    member_board,
    normalize_code,
    squad_ranking,
    squad_totals,
)

_n = iter(range(1, 100000))


def person(name='Pat', **profile):
    user = make_user(phone=f'0175{next(_n):07d}', first_name=name)
    for key, value in profile.items():
        setattr(user.profile, key, value)
    if profile:
        user.profile.save()
    return user


def give(user, days_ago=0, status=Donation.Status.APPROVED, request=None):
    return Donation.objects.create(
        donor=user, blood_group='A+', location='Bank', status=status,
        donation_date=timezone.localdate() - datetime.timedelta(days=days_ago),
        blood_request=request,
    )


def squad_with(owner, *members, **fields):
    fields.setdefault('name', f'Squad {next(_n)}')
    squad = create_squad(owner, **fields)
    for m in members:
        join_squad(m, squad)
    return squad


class ModelTests(TestCase):
    def test_codes_use_unambiguous_alphabet_and_length(self):
        for _ in range(30):
            code = Squad.objects.create(
                name='x', owner=person()).join_code
            self.assertEqual(len(code), CODE_LENGTH)
            self.assertTrue(set(code) <= set(CODE_ALPHABET))
        for bad in 'ILO01':
            self.assertNotIn(bad, CODE_ALPHABET)

    def test_codes_are_unique(self):
        owner = person()
        codes = {Squad.objects.create(name=str(i), owner=owner).join_code for i in range(60)}
        self.assertEqual(len(codes), 60)

    def test_code_collision_is_retried(self):
        from unittest import mock
        owner = person()
        first = Squad.objects.create(name='a', owner=owner)
        with mock.patch(
            'squads.models.generate_code',
            side_effect=[first.join_code, 'ZZZZ2222'],
        ):
            second = Squad.objects.create(name='b', owner=owner)
        self.assertEqual(second.join_code, 'ZZZZ2222')

    def test_one_membership_per_pair(self):
        owner = person()
        squad = create_squad(owner, name='dup')
        with self.assertRaises(IntegrityError), transaction.atomic():
            SquadMember.objects.create(squad=squad, user=owner)

    def test_creating_makes_owner_a_member(self):
        owner = person()
        squad = create_squad(owner, name='mine')
        self.assertEqual(
            squad.memberships.get(user=owner).role, SquadMember.Role.OWNER,
        )

    def test_normalize_code(self):
        self.assertEqual(normalize_code(' ab-cd 23 '), 'ABCD23')
        self.assertEqual(normalize_code(None), '')


class CapTests(TestCase):
    def test_member_cap(self):
        owner = person()
        squad = create_squad(owner, name='full')
        users = [person(f'M{i}') for i in range(MAX_MEMBERS_PER_SQUAD - 1)]
        SquadMember.objects.bulk_create(
            [SquadMember(squad=squad, user=u) for u in users],
        )
        self.assertEqual(squad.memberships.count(), MAX_MEMBERS_PER_SQUAD)
        with self.assertRaisesMessage(SquadError, 'full'):
            join_squad(person('Late'), squad)
        self.assertEqual(squad.memberships.count(), MAX_MEMBERS_PER_SQUAD)

    def test_squads_per_user_cap_on_create(self):
        owner = person()
        for i in range(MAX_SQUADS_PER_USER):
            create_squad(owner, name=f's{i}')
        with self.assertRaises(SquadError):
            create_squad(owner, name='one too many')
        self.assertEqual(owner.squad_memberships.count(), MAX_SQUADS_PER_USER)

    def test_squads_per_user_cap_on_join(self):
        user = person()
        for i in range(MAX_SQUADS_PER_USER):
            create_squad(user, name=f's{i}')
        other = squad_with(person(), name='other')
        with self.assertRaises(SquadError):
            join_squad(user, other)

    def test_cap_counts_joined_squads_too(self):
        user = person()
        for i in range(MAX_SQUADS_PER_USER):
            join_squad(user, squad_with(person(), name=f'o{i}'))
        self.client.force_login(user)
        response = self.client.post(reverse('squads:create'), {'name': 'New'})
        self.assertRedirects(response, reverse('squads:list'))
        self.assertFalse(Squad.objects.filter(name='New').exists())

    def test_cannot_join_twice(self):
        owner, user = person(), person()
        squad = squad_with(owner, user)
        with self.assertRaises(SquadError):
            join_squad(user, squad)
        self.assertEqual(squad.memberships.count(), 2)


class CreateAndEditTests(TestCase):
    def setUp(self):
        self.user = person('Owner')
        self.client.force_login(self.user)

    def test_requires_login(self):
        self.client.logout()
        for name in ('squads:list', 'squads:create', 'squads:join'):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 302)
            self.assertIn('/accounts/login/', response.url)

    def test_create_squad(self):
        response = self.client.post(reverse('squads:create'), {
            'name': '  Dhaka   Uni  ', 'description': 'Friends',
            'district': 'Dhaka', 'is_public': 'on',
        })
        squad = Squad.objects.get()
        self.assertRedirects(response, reverse('squads:detail', args=[squad.pk]))
        self.assertEqual(squad.name, 'Dhaka Uni')
        self.assertEqual(squad.owner, self.user)
        self.assertTrue(squad.is_public)

    def test_private_by_default(self):
        self.client.post(reverse('squads:create'), {'name': 'Quiet'})
        self.assertFalse(Squad.objects.get().is_public)

    def test_name_required_and_length_limited(self):
        for payload in ({'name': ''}, {'name': 'x' * 61},
                        {'name': 'ok', 'description': 'd' * 301}):
            response = self.client.post(reverse('squads:create'), payload)
            self.assertEqual(response.status_code, 200)
        self.assertFalse(Squad.objects.exists())

    def test_duplicate_public_name_rejected_case_insensitive(self):
        squad_with(person(), name='Red Cross Club', is_public=True)
        response = self.client.post(reverse('squads:create'), {'name': 'red cross CLUB'})
        self.assertContains(response, 'already uses this name')
        self.assertEqual(Squad.objects.count(), 1)

    def test_private_names_can_repeat(self):
        squad_with(person(), name='Friends')
        self.client.post(reverse('squads:create'), {'name': 'Friends'})
        self.assertEqual(Squad.objects.count(), 2)

    def test_names_and_descriptions_are_escaped(self):
        squad = squad_with(
            self.user, name='<b>Bold</b>', description='<script>x()</script>',
            is_public=True,
        )
        for url in (
            reverse('squads:detail', args=[squad.pk]),
            reverse('squads:list'),
            reverse('squads:leaderboard'),
        ):
            html = self.client.get(url).content.decode()
            self.assertNotIn('<b>Bold</b>', html)
            self.assertNotIn('<script>x()</script>', html)
        self.assertContains(
            self.client.get(reverse('squads:detail', args=[squad.pk])),
            '&lt;b&gt;Bold&lt;/b&gt;',
        )

    def test_only_owner_can_edit(self):
        squad = squad_with(self.user, name='Mine')
        member = person()
        join_squad(member, squad)
        self.client.force_login(member)
        url = reverse('squads:edit', args=[squad.pk])
        response = self.client.post(url, {'name': 'Hijacked'})
        self.assertRedirects(response, reverse('squads:detail', args=[squad.pk]))
        squad.refresh_from_db()
        self.assertEqual(squad.name, 'Mine')

    def test_owner_can_edit(self):
        squad = squad_with(self.user, name='Mine')
        self.client.post(
            reverse('squads:edit', args=[squad.pk]),
            {'name': 'Renamed', 'is_public': 'on'},
        )
        squad.refresh_from_db()
        self.assertEqual((squad.name, squad.is_public), ('Renamed', True))

    def test_outsider_editing_private_squad_gets_404(self):
        squad = squad_with(person(), name='Hidden')
        self.assertEqual(
            self.client.get(reverse('squads:edit', args=[squad.pk])).status_code, 404,
        )


class VisibilityTests(TestCase):
    def setUp(self):
        self.owner = person('Owner')
        self.private = squad_with(self.owner, name='Secret Society')
        self.public = squad_with(self.owner, name='Open House', is_public=True)
        self.outsider = person('Outsider')

    def test_private_squad_404_for_outsider_and_anonymous(self):
        url = reverse('squads:detail', args=[self.private.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_private_squad_visible_to_member(self):
        join_squad(self.outsider, self.private)
        self.client.force_login(self.outsider)
        response = self.client.get(reverse('squads:detail', args=[self.private.pk]))
        self.assertContains(response, 'Secret Society')

    def test_private_squad_not_listed_anywhere_for_outsiders(self):
        self.client.force_login(self.outsider)
        for name in ('squads:list', 'squads:leaderboard'):
            self.assertNotContains(self.client.get(reverse(name)), 'Secret Society')

    def test_private_squad_never_in_ranking_even_with_donations(self):
        give(self.owner)
        names = [r['squad'].name for r in squad_ranking('all')]
        self.assertEqual(names, ['Open House'])

    def test_public_squad_visible_without_code_or_invite_panel(self):
        self.client.force_login(self.outsider)
        response = self.client.get(reverse('squads:detail', args=[self.public.pk]))
        self.assertContains(response, 'Open House')
        self.assertNotContains(response, self.public.join_code)
        self.assertNotContains(response, 'Invite people')
        self.assertContains(response, reverse('squads:join-public', args=[self.public.pk]))

    def test_public_squad_visible_to_anonymous_without_code(self):
        response = self.client.get(reverse('squads:detail', args=[self.public.pk]))
        self.assertContains(response, 'Open House')
        self.assertNotContains(response, self.public.join_code)

    def test_members_see_join_code(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse('squads:detail', args=[self.public.pk]))
        self.assertContains(response, self.public.join_code)

    def test_discovery_district_filter(self):
        squad_with(person(), name='Sylhet Squad', is_public=True, district='Sylhet')
        self.client.force_login(self.outsider)
        response = self.client.get(reverse('squads:list'), {'district': 'Sylhet'})
        self.assertContains(response, 'Sylhet Squad')
        self.assertNotContains(response, 'Open House')
        response = self.client.get(reverse('squads:list'), {'district': 'Bogus'})
        self.assertContains(response, 'Open House')

    def test_discovery_hides_squads_you_are_in(self):
        self.client.force_login(self.owner)
        context = self.client.get(reverse('squads:list')).context
        self.assertEqual(list(context['discover']), [])
        self.assertEqual({s.pk for s in context['mine']}, {self.private.pk, self.public.pk})

    def test_join_public_squad(self):
        self.client.force_login(self.outsider)
        response = self.client.post(reverse('squads:join-public', args=[self.public.pk]))
        self.assertRedirects(response, reverse('squads:detail', args=[self.public.pk]))
        self.assertTrue(self.public.memberships.filter(user=self.outsider).exists())

    def test_cannot_join_private_squad_by_id(self):
        self.client.force_login(self.outsider)
        response = self.client.post(reverse('squads:join-public', args=[self.private.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertFalse(self.private.memberships.filter(user=self.outsider).exists())

    def test_join_public_requires_post(self):
        self.client.force_login(self.outsider)
        self.assertEqual(
            self.client.get(reverse('squads:join-public', args=[self.public.pk])).status_code,
            405,
        )


class JoinCodeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = person('Owner')
        self.squad = squad_with(self.owner, name='Codes')
        self.user = person('Joiner')
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def test_join_with_code(self):
        response = self.client.post(
            reverse('squads:join'), {'code': self.squad.join_code.lower()},
        )
        self.assertRedirects(response, reverse('squads:detail', args=[self.squad.pk]))
        self.assertTrue(self.squad.memberships.filter(user=self.user).exists())

    def test_code_tolerates_spaces_and_dashes(self):
        code = self.squad.join_code
        self.client.post(reverse('squads:join'), {'code': f'{code[:4]} - {code[4:]}'})
        self.assertTrue(self.squad.memberships.filter(user=self.user).exists())

    def test_invalid_codes_behave_identically(self):
        # Wrong, malformed, empty and too-long codes all get one answer.
        bodies = set()
        for code in ('ZZZZZZZZ', 'abc', '!!!', '', 'X' * 24):
            response = self.client.post(reverse('squads:join'), {'code': code})
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "That code didn&#x27;t work", html=False) \
                if False else None
            messages = [str(m) for m in response.context['messages']]
            bodies.add(tuple(messages))
        self.assertEqual(bodies, {("That code didn't work. Check it and try again.",)})

    def test_private_and_nonexistent_codes_indistinguishable_when_invalid(self):
        landing = self.client.get(reverse('squads:join-code', args=['NOSUCHCD']))
        again = self.client.get(reverse('squads:join-code', args=['ANOTHER2']))
        self.assertEqual(landing.status_code, again.status_code)

        # The CSRF token (in the account menu's logout form) is different on
        # every response, and the language switcher's hidden "next" field
        # echoes the visitor's own URL, so compare with both masked out.
        def mask(response):
            body = re.sub(
                rb'name="csrfmiddlewaretoken" value="[^"]+"', b'',
                response.content,
            )
            return re.sub(rb'name="next" value="[^"]*"', b'', body)
        self.assertEqual(mask(landing), mask(again))
        self.assertContains(landing, "isn't valid")
        self.assertNotContains(landing, 'Codes')

    def test_landing_asks_to_confirm_and_get_does_not_join(self):
        url = reverse('squads:join-code', args=[self.squad.join_code])
        response = self.client.get(url)
        self.assertContains(response, 'Codes')
        self.assertContains(response, 'Join this squad')
        self.assertFalse(self.squad.memberships.filter(user=self.user).exists())
        self.client.post(url)
        self.assertTrue(self.squad.memberships.filter(user=self.user).exists())

    def test_landing_shows_name_only_not_members_or_description(self):
        self.squad.description = 'Top secret mission'
        self.squad.save()
        response = self.client.get(reverse('squads:join-code', args=[self.squad.join_code]))
        self.assertNotContains(response, 'Top secret mission')
        self.assertNotContains(response, 'Owner')

    def test_landing_for_member_says_already_in(self):
        join_squad(self.user, self.squad)
        response = self.client.get(reverse('squads:join-code', args=[self.squad.join_code]))
        self.assertContains(response, 'already in this squad')

    def test_landing_anonymous_can_view_but_not_join(self):
        self.client.logout()
        url = reverse('squads:join-code', args=[self.squad.join_code])
        self.assertContains(self.client.get(url), 'Log in')
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)
        self.assertEqual(self.squad.memberships.count(), 1)

    def test_throttle_blocks_after_repeated_failures(self):
        for _ in range(JOIN_FAIL_LIMIT):
            self.client.post(reverse('squads:join'), {'code': 'WRONGONE'})
        # Even the right code is refused while blocked.
        response = self.client.post(
            reverse('squads:join'), {'code': self.squad.join_code},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('Too many tries', [str(m) for m in response.context['messages']][-1])
        self.assertFalse(self.squad.memberships.filter(user=self.user).exists())

    def test_throttle_is_per_user(self):
        for _ in range(JOIN_FAIL_LIMIT):
            self.client.post(reverse('squads:join'), {'code': 'WRONGONE'})
        other = person('Other')
        self.client.force_login(other)
        self.client.post(reverse('squads:join'), {'code': self.squad.join_code})
        self.assertTrue(self.squad.memberships.filter(user=other).exists())

    def test_new_code_invalidates_old_one(self):
        old = self.squad.join_code
        self.client.force_login(self.owner)
        self.client.post(reverse('squads:new-code', args=[self.squad.pk]))
        self.squad.refresh_from_db()
        self.assertNotEqual(self.squad.join_code, old)
        self.client.force_login(self.user)
        self.client.post(reverse('squads:join'), {'code': old})
        self.assertFalse(self.squad.memberships.filter(user=self.user).exists())

    def test_member_cannot_make_new_code(self):
        join_squad(self.user, self.squad)
        old = self.squad.join_code
        self.client.post(reverse('squads:new-code', args=[self.squad.pk]))
        self.squad.refresh_from_db()
        self.assertEqual(self.squad.join_code, old)

    def test_join_over_cap_reports_full(self):
        SquadMember.objects.bulk_create([
            SquadMember(squad=self.squad, user=person(f'F{i}'))
            for i in range(MAX_MEMBERS_PER_SQUAD - 1)
        ])
        response = self.client.post(
            reverse('squads:join'), {'code': self.squad.join_code}, follow=True,
        )
        self.assertContains(response, 'This squad is full')
        self.assertFalse(self.squad.memberships.filter(user=self.user).exists())


class LeaveDeleteKickTests(TestCase):
    def setUp(self):
        self.owner = person('Owner')
        self.member = person('Member')
        self.other = person('Other')
        self.squad = squad_with(self.owner, self.member, self.other, name='Team')
        self.pk = self.squad.pk

    def post(self, user, name, *args):
        self.client.force_login(user)
        return self.client.post(reverse(f'squads:{name}', args=args))

    def test_member_can_leave(self):
        response = self.post(self.member, 'leave', self.pk)
        self.assertRedirects(response, reverse('squads:list'))
        self.assertFalse(self.squad.memberships.filter(user=self.member).exists())

    def test_owner_cannot_leave(self):
        response = self.post(self.owner, 'leave', self.pk)
        self.assertRedirects(response, reverse('squads:detail', args=[self.pk]))
        self.assertTrue(self.squad.memberships.filter(user=self.owner).exists())
        self.assertTrue(Squad.objects.filter(pk=self.pk).exists())

    def test_non_member_leave_is_harmless(self):
        outsider = person('Outsider')
        self.post(outsider, 'leave', self.pk)
        self.assertEqual(self.squad.memberships.count(), 3)

    def test_leave_requires_post(self):
        self.client.force_login(self.member)
        self.assertEqual(
            self.client.get(reverse('squads:leave', args=[self.pk])).status_code, 405,
        )

    def test_owner_can_delete(self):
        response = self.post(self.owner, 'delete', self.pk)
        self.assertRedirects(response, reverse('squads:list'))
        self.assertFalse(Squad.objects.filter(pk=self.pk).exists())
        self.assertFalse(SquadMember.objects.filter(squad_id=self.pk).exists())

    def test_member_cannot_delete(self):
        self.post(self.member, 'delete', self.pk)
        self.assertTrue(Squad.objects.filter(pk=self.pk).exists())

    def test_outsider_cannot_delete_or_learn_it_exists(self):
        response = self.post(person('Outsider'), 'delete', self.pk)
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Squad.objects.filter(pk=self.pk).exists())

    def test_delete_requires_post(self):
        self.client.force_login(self.owner)
        self.assertEqual(
            self.client.get(reverse('squads:delete', args=[self.pk])).status_code, 405,
        )

    def test_owner_can_kick(self):
        self.post(self.owner, 'kick', self.pk, self.member.pk)
        self.assertFalse(self.squad.memberships.filter(user=self.member).exists())
        self.assertTrue(self.squad.memberships.filter(user=self.other).exists())

    def test_member_cannot_kick(self):
        self.post(self.member, 'kick', self.pk, self.other.pk)
        self.assertTrue(self.squad.memberships.filter(user=self.other).exists())

    def test_owner_cannot_kick_self(self):
        self.post(self.owner, 'kick', self.pk, self.owner.pk)
        self.assertTrue(self.squad.memberships.filter(user=self.owner).exists())

    def test_kick_of_stranger_does_nothing(self):
        stranger = person('Stranger')
        self.post(self.owner, 'kick', self.pk, stranger.pk)
        self.assertEqual(self.squad.memberships.count(), 3)

    def test_kick_only_affects_this_squad(self):
        other_squad = squad_with(person(), self.member, name='Elsewhere')
        self.post(self.owner, 'kick', self.pk, self.member.pk)
        self.assertTrue(other_squad.memberships.filter(user=self.member).exists())

    def test_cannot_kick_from_someone_elses_squad(self):
        elsewhere = squad_with(person('Boss'), self.member, name='Elsewhere')
        self.post(self.owner, 'kick', elsewhere.pk, self.member.pk)
        self.assertTrue(elsewhere.memberships.filter(user=self.member).exists())

    def test_kick_requires_post(self):
        self.client.force_login(self.owner)
        url = reverse('squads:kick', args=[self.pk, self.member.pk])
        self.assertEqual(self.client.get(url).status_code, 405)

    def test_only_owner_sees_management_controls(self):
        self.client.force_login(self.member)
        html = self.client.get(reverse('squads:detail', args=[self.pk])).content.decode()
        self.assertNotIn(reverse('squads:delete', args=[self.pk]), html)
        self.assertNotIn(reverse('squads:kick', args=[self.pk, self.other.pk]), html)
        self.client.force_login(self.owner)
        html = self.client.get(reverse('squads:detail', args=[self.pk])).content.decode()
        self.assertIn(reverse('squads:delete', args=[self.pk]), html)
        self.assertIn(reverse('squads:kick', args=[self.pk, self.member.pk]), html)


class TotalsTests(TestCase):
    def setUp(self):
        self.owner = person('Owner')
        self.a = person('Alice')
        self.squad = squad_with(self.owner, self.a, name='Totals')

    def test_only_approved_donations_count(self):
        give(self.a)
        give(self.a, days_ago=1, status=Donation.Status.PENDING)
        give(self.a, days_ago=2, status=Donation.Status.REJECTED)
        totals = squad_totals(self.squad)
        self.assertEqual((totals['month'], totals['all']), (1, 1))
        self.assertEqual(totals['lives'], 3)
        self.assertEqual(totals['members'], 2)

    def test_month_vs_all_time(self):
        give(self.a)
        give(self.owner, days_ago=100)
        totals = squad_totals(self.squad)
        self.assertEqual((totals['month'], totals['all']), (1, 2))

    def test_non_members_donations_excluded(self):
        give(person('Stranger'))
        self.assertEqual(squad_totals(self.squad)['all'], 0)

    def test_leaving_removes_member_from_totals(self):
        give(self.a)
        self.squad.memberships.filter(user=self.a).delete()
        self.assertEqual(squad_totals(self.squad)['all'], 0)

    def test_member_in_two_squads_counts_in_both_once(self):
        other = squad_with(person('Boss'), self.a, name='Second')
        give(self.a)
        self.assertEqual(squad_totals(self.squad)['all'], 1)
        self.assertEqual(squad_totals(other)['all'], 1)

    def test_page_shows_totals(self):
        give(self.a)
        give(self.a, days_ago=1)
        self.client.force_login(self.owner)
        response = self.client.get(reverse('squads:detail', args=[self.squad.pk]))
        self.assertEqual(response.context['totals']['all'], 2)

    def test_opted_out_donations_still_in_totals(self):
        give(self.a)
        self.a.profile.show_on_leaderboard = False
        self.a.profile.save()
        self.assertEqual(squad_totals(self.squad)['all'], 1)

    def test_member_board_ranks_by_points(self):
        give(self.a)
        give(self.a, days_ago=1)
        give(self.owner)
        board = member_board(self.squad, 'all')
        self.assertEqual([e['name'] for e in board], ['Alice', 'Owner'])
        self.assertEqual([e['rank'] for e in board], [1, 2])
        self.assertEqual(board[0]['points'], 20)

    def test_member_board_ties_share_rank(self):
        give(self.a)
        give(self.owner)
        board = member_board(self.squad, 'month')
        self.assertEqual([e['rank'] for e in board], [1, 1])

    def test_member_board_period(self):
        give(self.a, days_ago=100)
        month = {e['name']: e['donations'] for e in member_board(self.squad, 'month')}
        alltime = {e['name']: e['donations'] for e in member_board(self.squad, 'all')}
        self.assertEqual(month['Alice'], 0)
        self.assertEqual(alltime['Alice'], 1)

    def test_invalid_period_falls_back(self):
        self.client.force_login(self.owner)
        response = self.client.get(
            reverse('squads:detail', args=[self.squad.pk]), {'period': 'junk'},
        )
        self.assertEqual(response.context['period'], 'month')


class MemberPrivacyTests(TestCase):
    def setUp(self):
        self.owner = person('Owner')
        self.shy = person('Shyperson')
        self.open = person('Openperson')
        self.squad = squad_with(self.owner, self.shy, self.open, name='Privacy', is_public=True)
        self.shy.profile.show_on_leaderboard = False
        self.shy.profile.save()
        self.viewer = person('Viewer')

    def test_opted_out_member_not_named_to_outsiders(self):
        self.client.force_login(self.viewer)
        response = self.client.get(reverse('squads:detail', args=[self.squad.pk]))
        self.assertNotContains(response, 'Shyperson')
        self.assertContains(response, 'Openperson')
        self.assertEqual(response.context['private_count'], 1)
        self.assertContains(response, '1 member who keep')

    def test_opted_out_member_not_named_to_other_members(self):
        self.client.force_login(self.open)
        response = self.client.get(reverse('squads:detail', args=[self.squad.pk]))
        self.assertNotContains(response, 'Shyperson')

    def test_opted_out_member_not_named_to_anonymous(self):
        self.assertNotContains(
            self.client.get(reverse('squads:detail', args=[self.squad.pk])), 'Shyperson',
        )

    def test_owner_can_remove_private_member_without_seeing_name(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse('squads:detail', args=[self.squad.pk]))
        self.assertNotContains(response, 'Shyperson')
        self.assertContains(response, 'Private member')
        self.assertContains(
            response, reverse('squads:kick', args=[self.squad.pk, self.shy.pk]),
        )

    def test_opted_out_member_excluded_from_board(self):
        give(self.shy)
        names = [e['name'] for e in member_board(self.squad, 'all')]
        self.assertNotIn('Shyperson', names)

    def test_phone_numbers_never_rendered(self):
        for user in (self.owner, self.shy, self.open):
            give(user)
        self.client.force_login(self.owner)
        pages = [
            reverse('squads:detail', args=[self.squad.pk]),
            reverse('squads:list'), reverse('squads:leaderboard'),
            reverse('squads:join-code', args=[self.squad.join_code]),
        ]
        for url in pages:
            html = self.client.get(url).content.decode()
            for user in (self.owner, self.shy, self.open, self.viewer):
                self.assertNotIn(user.phone, html, url)

    def test_nameless_member_shows_donor_number(self):
        nameless = make_user(phone='01766600001')
        join_squad(nameless, self.squad)
        self.client.force_login(self.viewer)
        response = self.client.get(reverse('squads:detail', args=[self.squad.pk]))
        self.assertContains(response, f'Donor #{nameless.pk}')
        self.assertNotContains(response, '01766600001')

    def test_badge_card_link_follows_card_privacy(self):
        self.open.profile.public_badge_card = False
        self.open.profile.save()
        response = self.client.get(reverse('squads:detail', args=[self.squad.pk]))
        self.assertNotContains(response, reverse('badge-card', args=[self.open.pk]))


class SquadRankingTests(TestCase):
    def test_ranks_public_squads_by_member_donations(self):
        a_owner, b_owner = person('A'), person('B')
        squad_with(a_owner, name='Big', is_public=True)
        squad_with(b_owner, name='Small', is_public=True)
        give(a_owner)
        give(a_owner, days_ago=1)
        give(b_owner)
        rows = squad_ranking('all')
        self.assertEqual([(r['squad'].name, r['donations']) for r in rows],
                         [('Big', 2), ('Small', 1)])
        self.assertEqual([r['rank'] for r in rows], [1, 2])
        self.assertEqual(rows[0]['lives'], 6)

    def test_month_window_and_unapproved(self):
        owner = person('O')
        squad_with(owner, name='S', is_public=True)
        give(owner, days_ago=120)
        give(owner, status=Donation.Status.PENDING)
        self.assertEqual(squad_ranking('month'), [])
        self.assertEqual(squad_ranking('all')[0]['donations'], 1)  # old only
        give(owner)
        self.assertEqual(squad_ranking('month')[0]['donations'], 1)

    def test_tie_shares_rank(self):
        a, b = person('A'), person('B')
        squad_with(a, name='One', is_public=True)
        squad_with(b, name='Two', is_public=True)
        give(a)
        give(b)
        self.assertEqual([r['rank'] for r in squad_ranking('all')], [1, 1])

    def test_member_in_two_squads_counts_for_each_public_one(self):
        shared = person('Shared')
        one = squad_with(person('X'), shared, name='One', is_public=True)
        two = squad_with(person('Y'), shared, name='Two', is_public=True)
        give(shared)
        counts = {r['squad'].pk: r['donations'] for r in squad_ranking('all')}
        self.assertEqual(counts, {one.pk: 1, two.pk: 1})

    def test_page_is_public(self):
        owner = person('O')
        squad_with(owner, name='Visible', is_public=True)
        give(owner)
        response = self.client.get(reverse('squads:leaderboard'))
        self.assertContains(response, 'Visible')
        response = self.client.get(reverse('squads:leaderboard'), {'period': 'bad'})
        self.assertEqual(response.context['period'], 'month')

    def test_empty_state(self):
        self.assertContains(
            self.client.get(reverse('squads:leaderboard')), 'No squad has a verified donation',
        )


class AdminTests(TestCase):
    def test_admin_created_squad_gets_owner_membership(self):
        from django.contrib.admin.sites import site
        from squads.admin import SquadAdmin

        owner = person('AdminMade')
        squad = Squad(name='From admin', owner=owner)
        SquadAdmin(Squad, site).save_model(None, squad, None, False)
        self.assertEqual(
            squad.memberships.get(user=owner).role, SquadMember.Role.OWNER,
        )
