import datetime
import re
from itertools import count

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.test import Client, SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import get_resolver, reverse
from django.utils import timezone

from accounts.models import User
from atlas import audit, data, geo, layers
from pages.test_accessibility import problems_in
from rewards.audit_fixtures import (
    CONTACT_PHONES,
    FLAGGED_HOSPITAL,
    HOSPITAL,
    NAME_PROTECTED,
    PATIENT,
    PENDING_TITLE,
    PEOPLE,
    PRIVATE_SQUAD,
    build_cast,
    email_for,
)
from rewards.models import BloodRequest, Donation, RequestReport

_phones = count(1)


def today():
    return timezone.localdate()


def days(n):
    return datetime.timedelta(days=n)


class AtlasCase(TestCase):
    """Helpers that build people, requests and donations."""

    def setUp(self):
        cache.clear()
        data.clear_cache()
        self.client = Client()

    def user(self, group='O+', district='Dhaka', listed=True, available=True,
             active=True, **fields):
        phone = f'0177{next(_phones):07d}'
        user = User.objects.create_user(phone=phone, password='testpass123')
        user.first_name, user.last_name = 'Test', f'Person{user.pk}'
        user.is_active = active
        user.email = f'person{user.pk}@atlas.test'
        user.save()
        profile = user.profile
        profile.blood_group = group
        profile.district = district
        profile.in_directory = listed
        profile.available_to_donate = available
        for name, value in fields.items():
            setattr(profile, name, value)
        profile.save()
        return user

    def donors(self, n, **kwargs):
        return [self.user(**kwargs) for _ in range(n)]

    def request(self, group='B+', district='Dhaka', units=1, in_days=3,
                **fields):
        owner = fields.pop('owner', None) or self.user(listed=False)
        return BloodRequest.objects.create(
            requester=owner, blood_group=group, hospital='Secret Hospital Q',
            district=district, units_needed=units,
            needed_by=today() + days(in_days), contact_phone='01966600077',
            patient_name='Patientia', note='Ward nine', **fields,
        )

    def donation(self, user=None, when=None, status=Donation.Status.APPROVED,
                 district='Dhaka', group='O+'):
        return Donation.objects.create(
            donor=user or self.user(listed=False), blood_group=group, district=district,
            donation_date=when or today(), status=status, location='Somewhere',
        )

    def get(self, path, user=None):
        client = Client()
        if user:
            client.force_login(user)
        return client.get(path)


# ------------------------------------------------------------------- geo

class GeoTests(SimpleTestCase):
    def test_every_district_has_exactly_one_tile(self):
        self.assertEqual(set(geo.TILES), set(geo.DISTRICT_NAMES))
        self.assertEqual(len(geo.TILES), 64)
        self.assertEqual(len(set(geo.TILES.values())), 64)

    def test_codes_are_three_unique_letters_and_slugs_are_unique(self):
        codes = list(geo.CODES.values())
        self.assertEqual(len(set(codes)), 64)
        self.assertTrue(all(len(c) == 3 and c.isalpha() for c in codes))
        self.assertEqual(len(set(geo.SLUGS.values())), 64)
        self.assertEqual(geo.SLUGS["Cox's Bazar"], 'coxs-bazar')
        for name, slug in geo.SLUGS.items():
            self.assertEqual(geo.district_for_slug(slug), name)
        self.assertIsNone(geo.district_for_slug('atlantis'))

    def test_layout_follows_real_geography(self):
        col = lambda n: geo.TILES[n][0]  # noqa: E731
        row = lambda n: geo.TILES[n][1]  # noqa: E731
        self.assertEqual(row('Panchagarh'), 0)  # the far north
        self.assertLess(row('Dinajpur'), row('Dhaka'))
        # Dhaka sits in the middle of the grid
        self.assertLessEqual(abs(col('Dhaka') - (geo.COLUMNS - 1) / 2), 1)
        self.assertLessEqual(abs(row('Dhaka') - (geo.ROWS - 1) / 2), 1)
        # Sylhet: north-east of Dhaka
        self.assertGreater(col('Sylhet'), col('Dhaka'))
        self.assertLess(row('Sylhet'), row('Dhaka'))
        # Chattogram and Cox's Bazar: south-east, Cox's below Chattogram
        self.assertGreater(col('Chattogram'), col('Dhaka'))
        self.assertGreater(row('Chattogram'), row('Dhaka'))
        self.assertGreater(row("Cox's Bazar"), row('Chattogram'))
        # Khulna and Barishal: south-west and south
        self.assertLess(col('Khulna'), col('Dhaka'))
        self.assertGreater(row('Khulna'), row('Dhaka'))
        self.assertGreater(row('Barishal'), row('Dhaka'))
        # Rajshahi is west of Dhaka, Rangpur is north of Rajshahi
        self.assertLess(col('Rajshahi'), col('Dhaka'))
        self.assertLess(row('Rangpur'), row('Rajshahi'))

    def test_divisions_are_grouped_not_scattered(self):
        """Every tile has a side-or-corner neighbour in its own division
        (a division is never a lone tile in another's territory)."""
        by_cell = {cell: geo.DISTRICT_TO_DIVISION[n]
                   for n, cell in geo.TILES.items()}
        for name, (c, r) in geo.TILES.items():
            division = geo.DISTRICT_TO_DIVISION[name]
            near = [
                by_cell.get((c + dc, r + dr))
                for dc in (-1, 0, 1) for dr in (-1, 0, 1) if (dc, dr) != (0, 0)
            ]
            self.assertIn(division, near, name)


class SuppressionTests(SimpleTestCase):
    def test_counts_below_three_are_never_exact(self):
        for n in (0, 1, 2):
            value = data.supply_value(n)
            self.assertFalse(value['exact'])
            self.assertEqual(str(value['text']), 'fewer than 3')
            self.assertEqual(str(data.count_text(n)), 'fewer than 3')
        self.assertEqual(data.supply_value(3)['text'], '3')
        self.assertEqual(data.count_text(7), '7')

    def test_range_mode_never_prints_the_number(self):
        value = data.supply_value(8, exact=False)
        self.assertEqual(str(value['text']), '3 to 9')
        self.assertFalse(value['exact'])

    def test_ranks_share_places(self):
        rows = data.rank({'A': 5, 'B': 5, 'C': 3, 'D': 3, 'E': 0})
        self.assertEqual([r[0] for r in rows], [1, 1, 3, 3, 5])
        self.assertEqual(data.winners({'A': 5, 'B': 5, 'C': 3}), ['A', 'B'])
        self.assertEqual(data.winners({'A': 0, 'B': 0}), [])


# ------------------------------------------------------------------- map

class MapTests(AtlasCase):
    def test_map_renders_64_linked_tiles(self):
        r = self.client.get(reverse('atlas:map'))
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertEqual(len(re.findall(r'<a class="at-t ', html)), 64)
        for slug in geo.SLUGS.values():
            self.assertIn(f'href="/atlas/d/{slug}/"', html)
        self.assertIn('role="img"', html)
        self.assertIn('viewBox=', html)
        self.assertNotRegex(html, r'<svg class="at-map[^>]*\swidth="')
        # tile: three-letter code, number, full name in title and aria-label
        self.assertIn('>DHA</text>', html)
        self.assertRegex(html, r'aria-label="Dhaka, Dhaka division: 0 open requests"')
        self.assertIn('<title>Dhaka, Dhaka division', html)

    def test_text_alternative_table_lists_every_district(self):
        html = self.client.get(reverse('atlas:map')).content.decode()
        self.assertIn('<details', html)
        for name in geo.DISTRICT_NAMES:
            self.assertIn(f'>{name}</a></th>', html.replace("&#x27;", "'"))

    def test_layers_work_through_links_and_bad_layer_falls_back(self):
        for key, _label in layers.LAYER_CHOICES:
            r = self.client.get(reverse('atlas:map') + f'?layer={key}')
            self.assertEqual(r.status_code, 200)
            self.assertContains(r, f'?layer={key}')
        r = self.client.get(reverse('atlas:map') + '?layer=nonsense')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content.decode().count('aria-current="page"'), 2)
        self.assertEqual(r.context['layer'], 'demand')

    def test_demand_counts_only_public_open_unfulfilled_requests(self):
        self.request(district='Dhaka')
        self.request(district='Dhaka')
        self.request(district='Sylhet')
        # excluded: closed, expired, fulfilled, flagged
        self.request(district='Dhaka', status=BloodRequest.Status.CLOSED)
        expired = self.request(district='Dhaka')
        BloodRequest.objects.filter(pk=expired.pk).update(
            needed_by=today() - days(1))
        full = self.request(district='Dhaka', units=1)
        Donation.objects.create(
            donor=self.user(), blood_group='B+', donation_date=today(),
            district='Dhaka', location='x', blood_request=full,
            status=Donation.Status.APPROVED,
        )
        flagged = self.request(district='Dhaka')
        for _ in range(3):
            RequestReport.objects.create(
                blood_request=flagged, reporter=self.user(),
                reason=RequestReport.Reason.SPAM,
            )
        built = layers.build(layers.DEMAND)
        self.assertEqual(built['tiles']['Dhaka']['text'], '2')
        self.assertEqual(built['tiles']['Sylhet']['text'], '1')
        self.assertEqual(data.demand()['national']['requests'], 3)

    def test_donors_layer_counts_only_opted_in_available_active_members(self):
        self.donors(2)                                  # opted in
        self.donors(5, listed=False)                    # not in the directory
        self.donors(5, available=False)                 # not available
        self.donors(5, active=False)                    # deactivated
        self.donors(4, district='Sylhet')
        built = layers.build(layers.DONORS)
        dhaka, sylhet = built['tiles']['Dhaka'], built['tiles']['Sylhet']
        self.assertEqual(dhaka['text'], '<3')
        self.assertEqual(dhaka['detail'], 'fewer than 3 available donors')
        self.assertEqual(sylhet['text'], '4')
        html = self.client.get(reverse('atlas:map') + '?layer=donors').content.decode()
        self.assertNotRegex(html, r'Dhaka, Dhaka division: [012] available donor')
        self.assertIn('fewer than 3 available donors', html)

    def test_suppression_hides_zero_one_and_two_alike(self):
        details = set()
        for n in (0, 1, 2):
            cache.clear()
            data.clear_cache()
            User.objects.all().delete()
            self.donors(n)
            tile = layers.build(layers.DONORS)['tiles']['Dhaka']
            details.add((tile['text'], tile['detail'], tile['level']))
        self.assertEqual(len(details), 1)

    def test_donations_layer_counts_verified_donations_in_twelve_months(self):
        self.donation(district='Dhaka')
        self.donation(district='Dhaka', when=today() - days(40))
        self.donation(district='Dhaka', status=Donation.Status.PENDING)
        self.donation(district='Dhaka', status=Donation.Status.REJECTED)
        self.donation(district='Dhaka', when=today() - days(500))
        self.donation(district='')
        built = layers.build(layers.DONATIONS)
        self.assertEqual(built['tiles']['Dhaka']['text'], '2')
        self.assertEqual(data.donations_12m('Dhaka'), 2)

    def test_gap_layer_never_uses_the_hidden_number(self):
        self.request(district='Dhaka')
        self.donors(1)
        built_one = layers.build(layers.GAP)['tiles']['Dhaka']
        cache.clear()
        data.clear_cache()
        self.donors(1)
        built_two = layers.build(layers.GAP)['tiles']['Dhaka']
        self.assertEqual((built_one['text'], built_one['level']), ('!', 4))
        self.assertEqual((built_two['text'], built_two['level']), ('!', 4))
        cache.clear()
        data.clear_cache()
        self.donors(8)
        ok = layers.build(layers.GAP)['tiles']['Dhaka']
        self.assertNotEqual(ok['text'], '!')

    def test_empty_states_and_summary(self):
        r = self.client.get(reverse('atlas:map'))
        self.assertContains(r, 'No open blood requests in any district right now.')
        self.request(district='Dhaka')
        data.clear_cache()
        r = self.client.get(reverse('atlas:map'))
        self.assertContains(r, 'Open blood requests by district: 1 in total')

    def test_page_passes_accessibility_checks(self):
        self.request()
        self.donors(4)
        for path in ('/atlas/', '/atlas/?layer=donors', '/atlas/gap/',
                     '/atlas/divisions/', '/atlas/d/dhaka/'):
            html = self.client.get(path).content.decode()
            self.assertEqual(problems_in(html, path), [], path)
            self.assertEqual(html.count('<h1'), 1, path)

    def test_stylesheet_uses_variables_only(self):
        from pathlib import Path
        css = (Path(settings.BASE_DIR) / 'atlas' / 'static' / 'atlas'
               / 'atlas.css').read_text(encoding='utf8')
        body = css.split('.at-page', 1)[1]  # past the token block
        # the only literal colour allowed outside tokens is white on dark tiles
        self.assertEqual(sorted(set(re.findall(r'#[0-9a-fA-F]{3,6}\b', body))), ['#fff'])
        self.assertIn('prefers-reduced-motion', css)
        self.assertIn('prefers-color-scheme: dark', css)


# ------------------------------------------------------------------- gap

class GapTests(AtlasCase):
    def row(self, gap, group):
        return next(r for r in gap['rows'] if r['group'] == group)

    def test_supply_follows_compatibility_and_demand_counts_units(self):
        for group in ('A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-'):
            self.donors(3, group=group)
        self.donors(1, group='O-')
        self.request(group='A+', units=2)
        self.request(group='A+', units=3)
        self.request(group='AB-', units=1)
        gap = data.gap()
        a_pos = self.row(gap, 'A+')
        self.assertEqual(a_pos['demand'], 5)
        self.assertTrue(gap['exact'])
        # A+ can receive from A+, A-, O+ and O-: 3 + 3 + 3 + 4
        self.assertEqual(a_pos['supply']['est'], 13)
        self.assertEqual(self.row(gap, 'O-')['supply']['est'], 4)  # O- only
        self.assertEqual(self.row(gap, 'B-')['supply']['est'], 7)  # B-, O-

    def test_donors_inside_the_donation_interval_are_not_supply(self):
        ready = self.donors(3, group='O-')
        resting = self.donors(2, group='O-')
        interval = settings.DONATION_INTERVAL_DAYS
        self.donation(resting[0], when=today() - days(interval - 5))
        self.donation(resting[1], when=today() - days(5),
                      status=Donation.Status.PENDING)
        self.donation(ready[0], when=today() - days(interval + 5))
        self.donation(ready[1], when=today() - days(5),
                      status=Donation.Status.REJECTED)
        self.request(group='O-', units=1)
        gap = data.gap('Dhaka')
        # three ready donors: 'ready' [0], [1], [2]; both resting ones excluded
        self.assertEqual(self.row(gap, 'O-')['supply']['est'], 6)  # 3 to 9 range
        self.assertEqual(str(self.row(gap, 'O-')['supply']['text']), '3 to 9')
        self.assertEqual(data.donors()['national']['O-'], (5, 3))

    def test_district_supply_is_a_range_never_an_exact_number(self):
        self.donors(7, group='O-')
        gap = data.gap('Dhaka')
        for row in gap['rows']:
            self.assertFalse(row['supply']['exact'])
        html = self.get('/atlas/gap/?district=Dhaka').content.decode()
        self.assertNotRegex(html, r'>7<')
        self.assertIn('3 to 9', html)

    def test_national_supply_is_exact_only_while_every_group_is_safe(self):
        for group in ('A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-'):
            self.donors(3, group=group)
        gap = data.gap()
        self.assertTrue(gap['exact'])
        self.assertEqual(self.row(gap, 'O-')['supply']['text'], '3')
        self.assertEqual(self.row(gap, 'AB+')['supply']['text'], '24')
        cache.clear()
        data.clear_cache()
        self.user(group='A-')
        User.objects.filter(profile__blood_group='A-').exclude(
            pk=User.objects.filter(profile__blood_group='A-').first().pk,
        ).delete()
        gap = data.gap()
        self.assertFalse(gap['exact'])
        for row in gap['rows']:
            self.assertFalse(row['supply']['exact'])

    def test_most_needed_lists_at_most_three_groups_by_pressure(self):
        self.donors(10, group='O+')
        for group, units in (('A+', 4), ('B+', 1), ('AB-', 2), ('O-', 5)):
            self.request(group=group, units=units)
        r = self.get('/atlas/gap/')
        self.assertEqual(r.status_code, 200)
        top = r.context['top']
        self.assertLessEqual(len(top), 3)
        self.assertEqual(len(top), 3)
        # O- has 5 units and no donors who can give: most pressure
        self.assertEqual(top[0]['group'], 'O-')
        self.assertContains(r, 'Most needed right now')
        self.assertContains(r, 'units of O- are still needed')

    def test_no_demand_shows_a_calm_message(self):
        r = self.get('/atlas/gap/')
        self.assertEqual(r.context['top'], [])
        self.assertContains(r, 'No open requests right now.')

    def test_chart_has_values_as_text(self):
        self.request(group='B+', units=2)
        r = self.get('/atlas/gap/')
        html = r.content.decode()
        self.assertIn('<svg class="at-chart"', html)
        self.assertIn('2 units', html)
        self.assertIn('<th scope="row">B+</th>', html)

    def test_unknown_district_is_ignored(self):
        r = self.get('/atlas/gap/?district=Atlantis')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['district'], '')

    def test_invitation_only_for_signed_in_members_in_a_short_group(self):
        self.request(group='A-', units=3)
        short = self.user(group='A-', listed=False)
        elsewhere = self.user(group='AB+', listed=False)
        listed = self.user(group='A-', listed=True)
        self.assertContains(self.get('/atlas/gap/', short), 'groups people are asking for')
        self.assertContains(self.get('/atlas/gap/', short), reverse('privacy'))
        self.assertNotContains(self.get('/atlas/gap/'), 'groups people are asking for')
        self.assertNotContains(self.get('/atlas/gap/', elsewhere), 'groups people are asking for')
        self.assertNotContains(self.get('/atlas/gap/', listed), 'groups people are asking for')


# -------------------------------------------------------------- district

class DistrictTests(AtlasCase):
    def test_unknown_slug_is_404(self):
        self.assertEqual(self.get('/atlas/d/atlantis/').status_code, 404)
        self.assertEqual(self.get('/atlas/d/DHAKA/').status_code, 404)
        self.assertEqual(self.get('/atlas/d/dhaka/').status_code, 200)
        self.assertEqual(self.get('/atlas/d/coxs-bazar/').status_code, 200)

    def test_headline_numbers(self):
        self.request(district='Dhaka')
        self.request(district='Dhaka')
        self.request(district='Sylhet')
        self.donors(5, district='Dhaka')
        self.donation(district='Dhaka')
        r = self.get('/atlas/d/dhaka/')
        self.assertEqual(r.context['requests'], 2)
        self.assertEqual(r.context['available_text'], '5')
        self.assertEqual(r.context['donations'], 1)

    def test_urgent_cards_show_group_units_date_and_urgency_only(self):
        req = self.request(group='AB-', units=2, in_days=1)
        self.request(group='A+', district='Sylhet')
        flagged = self.request(group='O+')
        for _ in range(3):
            RequestReport.objects.create(
                blood_request=flagged, reporter=self.user(),
                reason=RequestReport.Reason.SPAM)
        r = self.get('/atlas/d/dhaka/')
        html = r.content.decode()
        self.assertEqual([x.pk for x in r.context['urgent']], [req.pk])
        self.assertIn(reverse('request-detail', args=[req.pk]), html)
        self.assertIn('AB-', html)
        self.assertIn('2 bags needed', html)
        self.assertIn('Needed tomorrow', html)
        for secret in ('Secret Hospital Q', 'Patientia', 'Ward nine',
                       '01966600077', req.requester.phone):
            self.assertNotIn(secret, html)

    def test_donors_by_group_are_ranges_and_hide_small_counts(self):
        self.donors(8, group='O+')
        self.donors(2, group='A+')
        r = self.get('/atlas/d/dhaka/')
        groups = {g['group']: g for g in r.context['groups']}
        self.assertEqual(str(groups['O+']['supply']['text']), '3 to 9')
        self.assertEqual(str(groups['A+']['supply']['text']), 'fewer than 3')
        self.assertEqual(str(groups['B+']['supply']['text']), 'fewer than 3')
        html = r.content.decode()
        self.assertNotRegex(html, r'>\s*[012]\s*</span>\s*</li>')

    def test_total_cannot_be_subtracted_into_a_hidden_cell(self):
        """The total is exact (8 here) but the group cells are ranges, so
        total minus the shown cells cannot give the hidden one."""
        self.donors(7, group='O+')
        self.donors(1, group='AB-')
        r = self.get('/atlas/d/dhaka/')
        self.assertEqual(r.context['available_text'], '8')
        shown = [str(g['supply']['text']) for g in r.context['groups']]
        self.assertNotIn('7', shown)
        self.assertNotIn('1', shown)
        self.assertEqual(shown.count('fewer than 3'), 7)

    def test_drives_squads_and_sparkline(self):
        from events.models import Event
        from squads.models import Squad
        owner = self.user()
        soon = timezone.now() + datetime.timedelta(days=4)
        Event.objects.create(
            title='Dhaka Drive One', organizer=owner, starts_at=soon,
            district='Dhaka', status=Event.Status.APPROVED, venue='Town hall',
            approved_at=timezone.now())
        Event.objects.create(
            title='Pending Drive Two', organizer=owner, starts_at=soon,
            district='Dhaka', status=Event.Status.PENDING)
        Event.objects.create(
            title='Sylhet Drive Three', organizer=owner, starts_at=soon,
            district='Sylhet', status=Event.Status.APPROVED,
            approved_at=timezone.now())
        Squad.objects.create(name='Open Dhaka Squad', owner=owner,
                             is_public=True, district='Dhaka')
        Squad.objects.create(name='Hidden Dhaka Squad', owner=owner,
                             is_public=False, district='Dhaka')
        Squad.objects.create(name='Sylhet Squad', owner=owner,
                             is_public=True, district='Sylhet')
        self.donation(district='Dhaka')
        self.donation(district='Dhaka', when=today() - days(70))
        html = self.get('/atlas/d/dhaka/').content.decode()
        self.assertIn('Dhaka Drive One', html)
        self.assertNotIn('Pending Drive Two', html)
        self.assertNotIn('Sylhet Drive Three', html)
        self.assertIn('Open Dhaka Squad', html)
        self.assertNotIn('Hidden Dhaka Squad', html)
        self.assertNotIn('Sylhet Squad', html)
        self.assertIn('class="at-spark"', html)
        self.assertIn('2 verified donations in the last 12 months', html)

    def test_no_sparkline_without_donations(self):
        html = self.get('/atlas/d/dhaka/').content.decode()
        self.assertNotIn('class="at-spark"', html)
        self.assertIn('No verified donations here', html)

    def test_sparkline_has_a_text_alternative(self):
        self.donation(district='Dhaka')
        html = self.get('/atlas/d/dhaka/').content.decode()
        self.assertRegex(html, r'<svg class="at-spark"[^>]*role="img"[^>]*aria-label="Verified donations per month')


# ------------------------------------------------------------- divisions

class DivisionTests(AtlasCase):
    def test_standings_use_this_month_and_shared_ranks(self):
        for _ in range(3):
            self.donation(district='Dhaka')
        for _ in range(3):
            self.donation(district='Sylhet')
        for _ in range(2):
            self.donation(district='Khulna')
        self.donation(district='Rangpur', status=Donation.Status.PENDING)
        r = self.get('/atlas/divisions/')
        rows = {x['name']: x for x in r.context['rows']}
        self.assertEqual(rows['Dhaka']['rank'], 1)
        self.assertEqual(rows['Sylhet']['rank'], 1)
        self.assertEqual(rows['Khulna']['rank'], 3)
        self.assertEqual(rows['Rangpur']['count'], 0)
        self.assertEqual(r.context['leaders'], ['Dhaka', 'Sylhet'])
        self.assertContains(r, 'share the same place')
        self.assertContains(r, '<svg class="at-chart"')

    def test_last_month_winner_and_ties(self):
        first = today().replace(day=1)
        last_day = first - days(1)
        for _ in range(2):
            self.donation(district='Rajshahi', when=last_day)
        self.donation(district='Khulna', when=last_day)
        r = self.get('/atlas/divisions/')
        self.assertEqual(r.context['last_winners'], ['Rajshahi'])
        self.assertContains(r, 'Rajshahi led with 2 verified donations.')
        cache.clear()
        data.clear_cache()
        self.donation(district='Khulna', when=last_day)
        r = self.get('/atlas/divisions/')
        self.assertEqual(r.context['last_winners'], ['Khulna', 'Rajshahi'])
        self.assertContains(r, 'Shared first place')

    def test_older_months_and_blank_districts_do_not_count(self):
        self.donation(district='Dhaka', when=today().replace(day=1) - days(70))
        self.donation(district='')
        r = self.get('/atlas/divisions/')
        self.assertEqual(r.context['total'], 0)
        self.assertContains(r, 'No verified donations yet this month')
        self.assertEqual({x['rank'] for x in r.context['rows']}, {'–'})

    def test_explains_counting_and_says_no_population_data(self):
        r = self.get('/atlas/divisions/')
        self.assertContains(r, 'raw counts')
        self.assertContains(r, 'we do not have verified population data')
        self.assertContains(r, 'Only verified donations count')


# ----------------------------------------------------------------- panels

class PanelTests(AtlasCase):
    def test_home_panel_hides_the_map_without_demand(self):
        from atlas.templatetags.atlas_tags import atlas_home
        ctx = atlas_home({})
        self.assertNotIn('mini', ctx)
        self.request()
        data.clear_cache()
        ctx = atlas_home({})
        self.assertIn('at-map--mini', str(ctx['mini']))
        self.assertNotIn('<a ', str(ctx['mini']))

    def test_home_page_shows_panel_with_link(self):
        html = self.client.get('/').content.decode()
        self.assertIn(reverse('atlas:map'), html)
        self.assertNotIn('at-map--mini', html)
        self.request()
        data.clear_cache()
        html = self.client.get('/').content.decode()
        self.assertIn('at-map--mini', html)
        self.assertIn('1 open request across 1 district right now.', html)

    def test_hub_panel_shows_the_members_district(self):
        member = self.user(group='B+', district='Dhaka', listed=False)
        self.request(group='B+', district='Dhaka', units=4)
        from events.models import Event
        Event.objects.create(
            title='Hub Drive', organizer=member,
            starts_at=timezone.now() + datetime.timedelta(days=3),
            district='Dhaka', status=Event.Status.APPROVED,
            approved_at=timezone.now())
        r = self.get(reverse('log-donation'), member)
        html = r.content.decode()
        self.assertIn('Your district', html)
        self.assertIn('1 open request', html)
        self.assertIn('1 upcoming drive', html)
        self.assertIn('B+ is one of the groups people are asking for here.', html)
        self.assertIn(reverse('atlas:district', args=['dhaka']), html)

    def test_hub_panel_is_absent_without_a_district_or_when_signed_out(self):
        nowhere = self.user(district='')
        html = self.get(reverse('log-donation'), nowhere).content.decode()
        self.assertNotIn('at-hub-h', html)
        from atlas.templatetags.atlas_tags import atlas_hub

        class Req:
            user = type('U', (), {'is_authenticated': False})()
        self.assertEqual(atlas_hub({'request': Req()}), {})

    def test_request_panel_counts_other_open_requests(self):
        mine = self.request(district='Dhaka')
        r = self.get(reverse('request-detail', args=[mine.pk]))
        self.assertNotIn('other open request', r.content.decode())
        self.request(district='Dhaka')
        self.request(district='Sylhet')
        html = self.get(reverse('request-detail', args=[mine.pk])).content.decode()
        self.assertIn('1 other open request in Dhaka', html)
        self.request(district='Dhaka')
        html = self.get(reverse('request-detail', args=[mine.pk])).content.decode()
        self.assertIn('2 other open requests in Dhaka', html)
        self.assertIn(reverse('atlas:district', args=['dhaka']), html)

    def test_request_panel_is_absent_for_closed_requests(self):
        closed = self.request(district='Dhaka', status=BloodRequest.Status.CLOSED)
        self.request(district='Dhaka')
        html = self.get(reverse('request-detail', args=[closed.pk])).content.decode()
        self.assertNotIn('other open request', html)

    def test_request_panel_ignores_flagged_requests(self):
        mine = self.request(district='Dhaka')
        flagged = self.request(district='Dhaka')
        for _ in range(3):
            RequestReport.objects.create(
                blood_request=flagged, reporter=self.user(),
                reason=RequestReport.Reason.SPAM)
        html = self.get(reverse('request-detail', args=[mine.pk])).content.decode()
        self.assertNotIn('other open request', html)


# ----------------------------------------------------- caching and queries

class CachingTests(AtlasCase):
    def map_queries(self, path='/atlas/'):
        cache.clear()
        data.clear_cache()
        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(self.client.get(path).status_code, 200)
        return len(ctx)

    def test_map_page_queries_are_fixed_whatever_the_amount_of_data(self):
        paths = ('/atlas/', '/atlas/?layer=gap', '/atlas/gap/',
                 '/atlas/divisions/', '/atlas/data.csv')
        small = {p: self.map_queries(p) for p in paths}
        for _ in range(12):
            district = geo.DISTRICT_NAMES[_ * 5]
            self.request(district=district, group='A+')
            self.donation(district=district)
        self.donors(25, district='Dhaka')
        self.donors(6, district='Sylhet', group='A-')
        large = {p: self.map_queries(p) for p in paths}
        self.assertEqual(small, large)
        self.assertLessEqual(large['/atlas/'], 8)
        self.assertLessEqual(large['/atlas/data.csv'], 4)

    def test_second_visit_reads_the_cache(self):
        self.request()
        self.client.get('/atlas/')
        with CaptureQueriesContext(connection) as ctx:
            self.client.get('/atlas/')
        names = ' '.join(q['sql'] for q in ctx.captured_queries)
        self.assertNotIn('rewards_bloodrequest', names)
        self.assertNotIn('accounts_profile', names)
        self.assertNotIn('rewards_donation', names)

    def test_numbers_may_be_stale_for_five_minutes_then_clear_cache_refreshes(self):
        self.request(district='Dhaka')
        self.assertEqual(data.demand()['national']['requests'], 1)
        self.request(district='Dhaka')
        self.assertEqual(data.demand()['national']['requests'], 1)  # stale, by design
        self.assertEqual(data.CACHE_TTL, 300)
        data.clear_cache()
        self.assertEqual(data.demand()['national']['requests'], 2)

    def test_keys_include_layer_and_district(self):
        self.request(district='Dhaka', group='B+')
        self.request(district='Sylhet', group='B+', units=3)
        self.assertEqual(data.gap('Dhaka')['units'], 1)
        self.assertEqual(data.gap('Sylhet')['units'], 3)
        self.assertEqual(data.gap()['units'], 4)

    def test_cache_key_changes_with_the_day(self):
        self.request()
        data.demand()
        keys = [k for k in getattr(cache, '_cache', {}) if ':demand:' in k]
        self.assertTrue(all(timezone.localdate().isoformat() in k for k in keys))


# --------------------------------------------------------------------- csv

class CsvTests(AtlasCase):
    def test_csv_is_public_aggregate_only(self):
        member = self.user(district='Dhaka', listed=False)
        self.donors(2, district='Dhaka')
        self.donors(5, district='Sylhet')
        self.request(district='Dhaka')
        self.donation(member, district='Dhaka')
        r = self.client.get(reverse('atlas:data-csv'))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r['Content-Type'].startswith('text/csv'))
        self.assertIn('attachment', r['Content-Disposition'])
        lines = r.content.decode().strip().splitlines()
        self.assertEqual(lines[0], 'district,division,open_requests,'
                                   'verified_donations_12m,available_donors')
        self.assertEqual(len(lines), 65)
        rows = {l.split(',')[0]: l.split(',') for l in lines[1:]}
        self.assertEqual(rows['Dhaka'][1:], ['Dhaka', '1', '1', 'fewer than 3'])
        self.assertEqual(rows['Sylhet'][4], '5')
        self.assertEqual(rows['Barguna'][4], 'fewer than 3')
        body = r.content.decode()
        for user in User.objects.all():
            self.assertNotIn(user.phone, body)
            self.assertNotIn(user.email or '@@', body)

    def test_post_is_not_allowed(self):
        self.assertEqual(self.client.post('/atlas/data.csv').status_code, 405)
        self.assertEqual(self.client.post('/atlas/').status_code, 405)


# ----------------------------------------------------------------- privacy

class PrivacyCrawlTests(TestCase):
    """Every atlas page, as several viewers, over the shared audit cast."""

    @classmethod
    def setUpTestData(cls):
        cls.cast = build_cast()
        cls.paths = audit.paths(cls.cast)

    def setUp(self):
        cache.clear()
        data.clear_cache()

    def viewers(self):
        u = self.cast.users
        yield 'anonymous', Client()
        for key in ('stranger', 'owner', 'hidden', 'directory', 'staff'):
            client = Client()
            client.force_login(u[key])
            yield key, client

    def test_no_phone_email_or_protected_name_appears(self):
        u = self.cast.users
        for label, client in self.viewers():
            for name, path in self.paths:
                r = client.get(path)
                self.assertEqual(r.status_code, 200, (label, path))
                body = r.content.decode()
                if label != 'anonymous':
                    # the page chrome may show the viewer's own details
                    own = u[label]
                    for token in (own.phone, own.email):
                        body = body.replace(token or '@@', '')
                for key, (first, last, phone) in PEOPLE.items():
                    if key == label:
                        continue
                    for secret in (phone, email_for(key)):
                        self.assertNotIn(secret, body, (label, path, key))
                for key in NAME_PROTECTED:
                    if key == label:
                        continue
                    first, last, _phone = PEOPLE[key]
                    self.assertNotIn(f'{first} {last}', body, (label, path, key))
                    self.assertNotIn(last, body, (label, path, key))
                for phone in CONTACT_PHONES.values():
                    self.assertNotIn(phone, body, (label, path))
                for secret in (HOSPITAL, FLAGGED_HOSPITAL, PATIENT,
                               PENDING_TITLE, PRIVATE_SQUAD, 'Ward five'):
                    self.assertNotIn(secret, body, (label, path))

    def test_opted_out_donor_is_counted_nowhere_by_name(self):
        """The opted-out members donate and organise, yet only counts show."""
        body = self.client.get('/atlas/d/dhaka/').content.decode()
        self.assertNotIn(PEOPLE['hidden'][1], body)
        self.assertNotIn('Donor #', body)

    def test_audit_paths_cover_every_atlas_url_name(self):
        names = set()

        def walk(patterns, ns=''):
            for p in patterns:
                if hasattr(p, 'url_patterns'):
                    inner = f'{ns}{p.namespace}:' if p.namespace else ns
                    yield from walk(p.url_patterns, inner)
                elif p.name and ns == 'atlas:':
                    yield f'{ns}{p.name}'
        names = set(walk(get_resolver().url_patterns))
        self.assertEqual(names, {name for name, _path in self.paths})

    def test_every_audit_path_is_get_only_and_ok(self):
        client = Client()
        for name, path in self.paths:
            self.assertEqual(client.get(path).status_code, 200, path)
            self.assertEqual(client.post(path).status_code, 405, path)
