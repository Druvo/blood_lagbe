"""Tests for the Impact dashboard: stats, charts, pages and the public feed."""
import csv
import datetime
import io
import json

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from insights import charts, stats
from rewards.models import (
    LIVES_PER_DONATION,
    BloodRequest,
    Donation,
    Place,
    RequestResponse,
)

User = get_user_model()

PHONE = '01799999999'
REQUEST_PHONE = '01655555555'
SECRET_NAME = 'Zorbigniew'
SECRET_LAST = 'Quillfeather'

_phone_counter = [0]


def make_user(first='Test', last='Donor', phone=None, **profile):
    if phone is None:
        _phone_counter[0] += 1
        phone = f'0171{_phone_counter[0]:07d}'
    user = User.objects.create_user(phone=phone, password='x')
    user.first_name, user.last_name = first, last
    user.save()
    if profile:
        for key, value in profile.items():
            setattr(user.profile, key, value)
        user.profile.save()
    return user


def donate(user, days_ago=0, status='approved', group='A+', district='',
           place=None, location='Somewhere', on=None):
    return Donation.objects.create(
        donor=user, blood_group=group, status=status, district=district,
        place=place, location=location,
        donation_date=on or timezone.localdate() - datetime.timedelta(
            days=days_ago),
    )


def make_request(requester, group='A+', district='Dhaka', units=1, days=3,
                 **kwargs):
    return BloodRequest.objects.create(
        requester=requester, blood_group=group, hospital='City Hospital',
        district=district, units_needed=units, contact_phone=REQUEST_PHONE,
        needed_by=timezone.localdate() + datetime.timedelta(days=days),
        **kwargs,
    )


class InsightsTestCase(TestCase):
    def setUp(self):
        stats.clear_cache()
        self.addCleanup(stats.clear_cache)
        self.donor = make_user(SECRET_NAME, SECRET_LAST, phone=PHONE)


# ------------------------------------------------------------------ stats

class TotalsTests(InsightsTestCase):
    def test_only_approved_donations_count(self):
        donate(self.donor)
        donate(self.donor, status='pending')
        donate(self.donor, status='rejected')
        totals = stats.totals()
        self.assertEqual(totals['donations'], 1)
        self.assertEqual(totals['donors'], 1)
        self.assertEqual(totals['lives'], LIVES_PER_DONATION)

    def test_donors_are_distinct_people_not_donation_rows(self):
        other = make_user()
        for days in (1, 2, 3):
            donate(self.donor, days_ago=days)
        donate(other)
        totals = stats.totals()
        self.assertEqual(totals['donations'], 4)
        self.assertEqual(totals['donors'], 2)

    def test_empty_database_is_all_zero_with_no_rate(self):
        totals = stats.totals()
        self.assertEqual(totals['donations'], 0)
        self.assertEqual(totals['donors'], 0)
        self.assertEqual(totals['requests_posted'], 0)
        self.assertIsNone(totals['fulfilment_rate'])

    def test_headline_matches_all_time_totals(self):
        donate(self.donor)
        self.assertEqual(stats.headline(), stats.totals('all'))

    def test_period_filters(self):
        today = timezone.localdate()
        donate(self.donor, on=today)
        window_start = stats.add_months(today, -11)
        donate(self.donor, on=window_start)
        donate(self.donor, on=window_start - datetime.timedelta(days=1))
        donate(self.donor, on=today - datetime.timedelta(days=800))
        self.assertEqual(stats.totals('all')['donations'], 4)
        self.assertEqual(stats.totals('12m')['donations'], 2)
        year_start = datetime.date(today.year, 1, 1)
        expected = sum(
            1 for d in (today, window_start,
                        window_start - datetime.timedelta(days=1),
                        today - datetime.timedelta(days=800))
            if d >= year_start
        )
        self.assertEqual(stats.totals('year')['donations'], expected)

    def test_unknown_period_falls_back_to_all(self):
        self.assertEqual(stats.normalise_period('nonsense'), 'all')
        self.assertEqual(stats.normalise_period(None), 'all')

    def test_requests_fulfilled_and_rate(self):
        make_request(self.donor, units=1)
        done = make_request(self.donor, units=2)
        make_request(self.donor, units=1, status='closed')
        for _ in range(2):
            Donation.objects.create(
                donor=self.donor, blood_group='A+', location='x',
                donation_date=timezone.localdate(), status='approved',
                blood_request=done,
            )
        # A pending donation must not fulfil anything.
        Donation.objects.create(
            donor=self.donor, blood_group='A+', location='x',
            donation_date=timezone.localdate(), status='pending',
            blood_request=BloodRequest.objects.first(),
        )
        totals = stats.totals()
        self.assertEqual(totals['requests_posted'], 3)
        self.assertEqual(totals['requests_fulfilled'], 1)
        self.assertEqual(totals['fulfilment_rate'], 33)

    def test_funnel_stages(self):
        a = make_request(self.donor)             # untouched
        b = make_request(self.donor)             # donor offered
        c = make_request(self.donor, units=1)    # fulfilled
        RequestResponse.objects.create(blood_request=b, donor=self.donor)
        Donation.objects.create(
            donor=self.donor, blood_group='A+', location='x', status='approved',
            donation_date=timezone.localdate(), blood_request=c,
        )
        funnel = {f['label']: f for f in stats.request_funnel()}
        self.assertEqual(funnel['Posted']['value'], 3)
        self.assertEqual(funnel['Donors stepped up']['value'], 2)
        self.assertEqual(funnel['Fulfilled']['value'], 1)
        self.assertEqual(funnel['Fulfilled']['percent'], 33)
        self.assertIsNotNone(a)

    def test_funnel_without_requests_has_no_division_by_zero(self):
        for stage in stats.request_funnel():
            self.assertEqual(stage['value'], 0)
            self.assertEqual(stage['percent'], 0)


class SeriesTests(InsightsTestCase):
    def test_twelve_months_zero_filled_oldest_first(self):
        today = timezone.localdate()
        series = stats.monthly_series()
        self.assertEqual(len(series), 12)
        self.assertTrue(all(m['count'] == 0 for m in series))
        self.assertEqual(series[-1]['key'], today.strftime('%Y-%m'))
        keys = [m['key'] for m in series]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(len(set(keys)), 12)

    def test_counts_land_in_the_right_month(self):
        first = timezone.localdate().replace(day=1)
        prev = stats.add_months(first, -1)
        donate(self.donor, on=first)
        donate(self.donor, on=prev)
        donate(self.donor, on=prev + datetime.timedelta(days=5))
        donate(self.donor, on=prev, status='pending')
        donate(self.donor, on=prev, status='rejected')
        counts = {m['key']: m['count'] for m in stats.monthly_series()}
        self.assertEqual(counts[first.strftime('%Y-%m')], 1)
        self.assertEqual(counts[prev.strftime('%Y-%m')], 2)
        self.assertEqual(sum(counts.values()), 3)

    def test_old_donations_fall_outside_the_window(self):
        donate(self.donor, days_ago=500)
        self.assertEqual(sum(m['count'] for m in stats.monthly_series()), 0)

    def test_district_series_only_counts_that_district(self):
        donate(self.donor, district='Dhaka')
        donate(self.donor, district='Sylhet')
        donate(self.donor, district='')
        self.assertEqual(
            sum(m['count'] for m in stats.monthly_series(district='Dhaka')), 1,
        )

    def test_add_months_crosses_year_boundary(self):
        self.assertEqual(
            stats.add_months(datetime.date(2026, 1, 20), -2),
            datetime.date(2025, 11, 1),
        )
        self.assertEqual(
            stats.add_months(datetime.date(2026, 11, 3), 2),
            datetime.date(2027, 1, 1),
        )

    def test_month_comparison_change(self):
        first = timezone.localdate().replace(day=1)
        prev = stats.add_months(first, -1)
        donate(self.donor, on=first)
        donate(self.donor, on=prev)
        donate(self.donor, on=prev + datetime.timedelta(days=1))
        cmp = stats.month_comparison()
        self.assertEqual((cmp['this_month'], cmp['last_month']), (1, 2))
        self.assertEqual(cmp['change'], -50)
        self.assertEqual(cmp['delta'], -1)

    def test_month_comparison_with_no_previous_month_has_no_percent(self):
        donate(self.donor, on=timezone.localdate().replace(day=1))
        cmp = stats.month_comparison()
        self.assertIsNone(cmp['change'])
        self.assertEqual(cmp['this_month'], 1)

    def test_percent_change_helper(self):
        self.assertEqual(stats.percent_change(3, 2), 50)
        self.assertEqual(stats.percent_change(0, 4), -100)
        self.assertIsNone(stats.percent_change(5, 0))


class DistributionTests(InsightsTestCase):
    def test_blood_groups_lists_all_eight_with_shares(self):
        donate(self.donor, group='O-')
        donate(self.donor, group='O-')
        donate(self.donor, group='A+')
        donate(self.donor, group='B+', status='pending')
        groups = stats.blood_groups()
        self.assertEqual(
            [g['group'] for g in groups],
            ['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-'],
        )
        by = {g['group']: g for g in groups}
        self.assertEqual(by['O-']['count'], 2)
        self.assertEqual(by['B+']['count'], 0)
        self.assertEqual(by['O-']['share'], 66.7)
        self.assertAlmostEqual(sum(g['share'] for g in groups), 100, delta=0.2)

    def test_blood_groups_empty_has_zero_shares(self):
        self.assertTrue(all(g['share'] == 0 for g in stats.blood_groups()))

    def test_blank_districts_are_unspecified_and_never_ranked(self):
        donate(self.donor, district='Dhaka')
        donate(self.donor, district='')
        donate(self.donor, district='')
        donate(self.donor, district='Old free text')
        geo = stats.geography()
        self.assertEqual(geo['unspecified'], 3)
        self.assertEqual([d['name'] for d in geo['districts']], ['Dhaka'])
        self.assertNotIn(
            stats.UNSPECIFIED, [d['name'] for d in geo['districts']],
        )
        dhaka_division = next(
            d for d in geo['divisions'] if d['name'] == 'Dhaka')
        self.assertEqual(dhaka_division['count'], 1)
        self.assertEqual(sum(d['count'] for d in geo['divisions']), 1)

    def test_all_eight_divisions_are_always_listed(self):
        names = {d['name'] for d in stats.geography()['divisions']}
        self.assertEqual(len(names), 8)

    def test_division_roll_up_and_top_ten_limit(self):
        districts = [
            'Dhaka', 'Gazipur', 'Tangail', 'Chattogram', 'Cumilla', 'Sylhet',
            'Khulna', 'Bogura', 'Rangpur', 'Barishal', 'Mymensingh', 'Feni',
        ]
        for index, name in enumerate(districts):
            for _ in range(len(districts) - index):
                donate(self.donor, district=name)
        geo = stats.geography()
        self.assertEqual(len(geo['districts']), 10)
        self.assertEqual(geo['districts'][0]['name'], 'Dhaka')
        self.assertEqual(geo['districts_total'], 12)
        divisions = {d['name']: d['count'] for d in geo['divisions']}
        self.assertEqual(divisions['Dhaka'], 12 + 11 + 10)

    def test_ties_are_ordered_alphabetically(self):
        for name in ('Sylhet', 'Bogura', 'Khulna'):
            donate(self.donor, district=name)
        names = [d['name'] for d in stats.geography()['districts']]
        self.assertEqual(names, ['Bogura', 'Khulna', 'Sylhet'])


class PlaceTests(InsightsTestCase):
    def test_curated_places_are_grouped_whatever_the_typed_text(self):
        place = Place.objects.create(name='Popular Hospital', district='Dhaka')
        donate(self.donor, place=place, location='Popular Hospital')
        donate(self.donor, place=place, location='popular hosp (old)')
        places = stats.top_places()
        self.assertEqual(places[0]['name'], 'Popular Hospital')
        self.assertEqual(places[0]['count'], 2)
        self.assertEqual(places[0]['district'], 'Dhaka')

    def test_typed_location_needs_two_donations_to_appear(self):
        donate(self.donor, location='Lonely Clinic')
        self.assertEqual(stats.top_places(), [])
        donate(self.donor, location='lonely clinic')
        places = stats.top_places()
        self.assertEqual(len(places), 1)
        self.assertEqual(places[0]['count'], 2)

    def test_phone_like_text_is_never_shown(self):
        donate(self.donor, location='Call 01712345678')
        donate(self.donor, location='Call 01712345678')
        self.assertEqual(stats.top_places(), [])

    def test_pending_donations_do_not_count_for_places(self):
        place = Place.objects.create(name='Pending Place', district='Dhaka')
        donate(self.donor, place=place, status='pending')
        self.assertEqual(stats.top_places(), [])

    def test_limit_and_ordering(self):
        for i in range(5):
            place = Place.objects.create(name=f'P{i}', district='Dhaka')
            for _ in range(i + 1):
                donate(self.donor, place=place)
        places = stats.top_places(limit=3)
        self.assertEqual([p['name'] for p in places], ['P4', 'P3', 'P2'])


class SupplyDemandTests(InsightsTestCase):
    def rows(self):
        return {r['group']: r for r in stats.supply_demand()}

    def test_only_directory_listed_donors_count_as_supply(self):
        make_user(blood_group='O-', in_directory=True)
        make_user(blood_group='O-', in_directory=True, available_to_donate=False)
        make_user(blood_group='O-', in_directory=False)
        make_user(blood_group='', in_directory=True)
        gone = make_user(blood_group='O-', in_directory=True)
        gone.is_active = False
        gone.save()
        row = self.rows()['O-']
        self.assertEqual(row['donors'], 2)
        self.assertEqual(row['available'], 1)

    def test_demand_is_units_still_needed_on_open_requests(self):
        asker = make_user()
        covered = make_request(asker, group='O-', units=3)
        Donation.objects.create(
            donor=self.donor, blood_group='O-', location='x',
            status='approved', donation_date=timezone.localdate(),
            blood_request=covered,
        )
        make_request(asker, group='O-', units=2, district='Sylhet')
        make_request(asker, group='O-', units=5, status='closed')
        make_request(asker, group='O-', units=5, days=-1)  # expired
        make_request(asker, group='B+', units=1)
        rows = self.rows()
        self.assertEqual(rows['O-']['need'], 4)  # 2 left of 3, plus 2
        self.assertEqual(rows['O-']['requests'], 2)
        self.assertEqual(rows['B+']['need'], 1)
        self.assertEqual(rows['A+']['need'], 0)

    def test_status_and_top_district(self):
        asker = make_user()
        make_request(asker, group='O-', units=4, district='Sylhet')
        make_request(asker, group='O-', units=1, district='Dhaka')
        make_request(asker, group='A+', units=1)
        make_user(blood_group='A+', in_directory=True)
        make_user(blood_group='A+', in_directory=True)
        rows = self.rows()
        self.assertEqual(rows['O-']['status'], 'gap')
        self.assertEqual(rows['O-']['top_district'], 'Sylhet')
        self.assertEqual(rows['A+']['status'], 'covered')
        self.assertEqual(rows['B+']['status'], 'quiet')

    def test_cta_prefers_the_biggest_gap(self):
        asker = make_user()
        make_request(asker, group='O-', units=5, district='Khulna')
        make_request(asker, group='B+', units=2)
        make_user(blood_group='O-', in_directory=True)
        cta = stats.call_to_action()
        self.assertEqual(cta['kind'], 'gap')
        self.assertEqual(cta['group'], 'O-')
        self.assertEqual(cta['top_district'], 'Khulna')

    def test_cta_open_when_donors_cover_requests(self):
        asker = make_user()
        make_request(asker, group='A+', units=1)
        make_user(blood_group='A+', in_directory=True)
        cta = stats.call_to_action()
        self.assertEqual((cta['kind'], cta['group']), ('open', 'A+'))

    def test_cta_recruits_for_scarcest_group_when_nothing_is_open(self):
        for group in ('A+', 'A-', 'B+', 'B-', 'AB+', 'O+', 'O-'):
            make_user(blood_group=group, in_directory=True)
        cta = stats.call_to_action()
        self.assertEqual((cta['kind'], cta['group']), ('recruit', 'AB-'))


class DistrictDetailTests(InsightsTestCase):
    def test_numbers_and_rank(self):
        donate(self.donor, district='Sylhet')
        donate(self.donor, district='Sylhet')
        donate(self.donor, district='Dhaka')
        detail = stats.district_detail('Sylhet')
        self.assertEqual(detail['donations'], 2)
        self.assertEqual(detail['donors'], 1)
        self.assertEqual(detail['rank'], 1)
        self.assertEqual(detail['division'], 'Sylhet')
        self.assertEqual(stats.district_detail('Dhaka')['rank'], 2)
        self.assertIsNone(stats.district_detail('Barishal')['rank'])

    def test_open_requests_are_public_fields_only(self):
        make_request(self.donor, group='O-', district='Dhaka', units=2)
        make_request(self.donor, group='O-', district='Sylhet')
        detail = stats.district_detail('Dhaka')
        self.assertEqual(len(detail['open_requests']), 1)
        entry = detail['open_requests'][0]
        self.assertEqual(
            set(entry), {'pk', 'blood_group', 'hospital', 'remaining',
                         'needed_by'},
        )
        self.assertNotIn(REQUEST_PHONE, json.dumps(detail, default=str))

    def test_available_donors_is_a_number_only(self):
        make_user(blood_group='A+', district='Dhaka', in_directory=True)
        make_user(blood_group='A+', district='Dhaka', in_directory=True,
                  available_to_donate=False)
        make_user(blood_group='A+', district='Dhaka', in_directory=False)
        detail = stats.district_detail('Dhaka')
        self.assertEqual(detail['donors_available'], 1)
        self.assertIsInstance(detail['donors_available'], int)


class CachingTests(InsightsTestCase):
    def test_second_call_hits_the_cache(self):
        donate(self.donor)
        stats.dashboard()
        with self.assertNumQueries(0):
            stats.dashboard()

    def test_clear_cache_forces_recompute(self):
        stats.totals()
        stats.clear_cache()
        with self.assertNumQueries(3):
            stats.totals()

    def test_periods_are_cached_separately(self):
        donate(self.donor, days_ago=900)
        self.assertEqual(stats.totals('all')['donations'], 1)
        self.assertEqual(stats.totals('12m')['donations'], 0)

    def test_writes_bust_the_cache_automatically(self):
        self.assertEqual(stats.headline()['donations'], 0)
        donate(self.donor)
        self.assertEqual(stats.headline()['donations'], 1)

    def test_approving_a_pending_donation_updates_stats(self):
        pending = donate(self.donor, status='pending')
        self.assertEqual(stats.headline()['donations'], 0)
        pending.status = 'approved'
        pending.save()
        self.assertEqual(stats.headline()['donations'], 1)

    def test_profile_change_busts_supply(self):
        user = make_user(blood_group='O+', in_directory=False)
        self.assertEqual(
            {r['group']: r['donors'] for r in stats.supply_demand()}['O+'], 0)
        user.profile.in_directory = True
        user.profile.save()
        self.assertEqual(
            {r['group']: r['donors'] for r in stats.supply_demand()}['O+'], 1)


# ----------------------------------------------------------------- charts

class FormattingTests(SimpleTestCase):
    def test_fmt_thousands_separators(self):
        self.assertEqual(charts.fmt(1234567), '1,234,567')
        self.assertEqual(charts.fmt(0), '0')
        self.assertEqual(charts.fmt(None), '0')
        self.assertEqual(charts.fmt('abc'), '0')

    def test_compact(self):
        self.assertEqual(charts.compact(999), '999')
        self.assertEqual(charts.compact(1000), '1k')
        self.assertEqual(charts.compact(1250), '1.2k')
        self.assertEqual(charts.compact(15300), '15k')
        self.assertEqual(charts.compact(2_500_000), '2.5M')

    def test_nice_ceiling_covers_the_value(self):
        for value in (0, 1, 3, 4, 7, 10, 23, 99, 101, 1234, 99999):
            top, step = charts.nice_ceiling(value)
            self.assertGreaterEqual(top, value)
            self.assertEqual(top, step * 3)
            self.assertGreaterEqual(step, 1)


class MonthlyChartTests(SimpleTestCase):
    def series(self, counts):
        return [
            {'label': f'M{i}', 'count': c, 'year': 2026, 'month': i + 1}
            for i, c in enumerate(counts)
        ]

    def test_renders_one_bar_per_month(self):
        svg = charts.monthly_chart(self.series([1, 0, 5, 2]))
        self.assertTrue(svg.startswith('<svg'))
        self.assertEqual(svg.count('class="bb-bar'), 4)
        self.assertIn('bb-bar--zero', svg)
        self.assertIn('M2', svg)

    def test_empty_and_all_zero_show_friendly_state(self):
        for data in ([], None, self.series([0, 0, 0])):
            html = charts.monthly_chart(data)
            self.assertIn('bb-empty', html)
            self.assertNotIn('<svg', html)

    def test_single_point(self):
        svg = charts.monthly_chart(self.series([7]))
        self.assertIn('<svg', svg)
        self.assertIn('>7<', svg)
        self.assertNotIn('nan', svg.lower())

    def test_evil_label_is_escaped(self):
        data = [{'label': '<script>alert(1)</script>', 'count': 3}]
        svg = charts.monthly_chart(data)
        self.assertNotIn('<script>', svg)
        self.assertIn('&lt;script&gt;', svg)

    def test_evil_unit_and_empty_message_are_escaped(self):
        svg = charts.monthly_chart(self.series([1]), unit='<b>x</b>')
        self.assertNotIn('<b>x</b>', svg)
        html = charts.monthly_chart([], empty='<img src=x onerror=1>')
        self.assertNotIn('<img', html)

    def test_large_numbers_use_separators_and_compact_labels(self):
        svg = charts.monthly_chart(self.series([1_234_567, 10]))
        self.assertIn('1,234,567', svg)
        self.assertIn('1.2M', svg)

    def test_garbage_counts_do_not_crash(self):
        svg = charts.monthly_chart(
            [{'label': 'a', 'count': 'x'}, {'label': 'b', 'count': 2}])
        self.assertIn('<svg', svg)


class HbarTests(SimpleTestCase):
    def test_widths_are_relative_to_the_largest(self):
        html = charts.hbar_rows([
            {'name': 'Dhaka', 'count': 10}, {'name': 'Sylhet', 'count': 5},
        ])
        self.assertIn('width:100.0%', html)
        self.assertIn('width:50.0%', html)

    def test_tiny_nonzero_value_stays_visible_and_zero_is_empty(self):
        html = charts.hbar_rows([
            {'name': 'a', 'count': 1000}, {'name': 'b', 'count': 1},
            {'name': 'c', 'count': 0},
        ])
        self.assertIn('width:3.0%', html)
        self.assertIn('width:0.0%', html)

    def test_empty(self):
        self.assertIn('bb-empty', charts.hbar_rows([]))
        self.assertIn('bb-empty', charts.hbar_rows([{'name': 'a', 'count': 0}]))

    def test_names_urls_and_display_are_escaped(self):
        html = charts.hbar_rows([{
            'name': '<script>x</script>', 'count': 2,
            'url': '/a?b=1&c="2"', 'display': '<i>2</i>',
        }])
        self.assertNotIn('<script>', html)
        self.assertNotIn('<i>2</i>', html)
        self.assertIn('&quot;2&quot;', html)

    def test_long_labels_keep_a_full_title(self):
        name = 'A very long hospital name ' * 5
        html = charts.hbar_rows([{'name': name, 'count': 1}])
        self.assertIn(f'title="{name}"', html)

    def test_large_numbers(self):
        html = charts.hbar_rows([{'name': 'x', 'count': 9876543}])
        self.assertIn('9,876,543', html)


class DonutTests(SimpleTestCase):
    def test_slices_and_legend(self):
        donut = charts.donut([
            {'label': 'A+', 'count': 3}, {'label': 'B+', 'count': 1},
            {'label': 'O-', 'count': 0},
        ])
        self.assertFalse(donut['empty'])
        self.assertEqual(donut['svg'].count('class="bb-dn bb-dn--'), 2)
        self.assertIn('75%', donut['legend'])
        self.assertIn('is-zero', donut['legend'])
        self.assertIn('>4<', donut['svg'])

    def test_single_slice_fills_the_ring(self):
        donut = charts.donut([{'label': 'A+', 'count': 9}])
        self.assertIn('100%', donut['svg'])
        self.assertEqual(donut['svg'].count('class="bb-dn bb-dn--'), 1)

    def test_empty_has_no_division_by_zero(self):
        for data in ([], [{'label': 'A+', 'count': 0}]):
            donut = charts.donut(data)
            self.assertTrue(donut['empty'])
            self.assertIn('bb-empty', donut['svg'])
            self.assertEqual(donut['legend'], '')

    def test_labels_are_escaped(self):
        donut = charts.donut([{'label': '<script>x</script>', 'count': 2}])
        self.assertNotIn('<script>', donut['svg'] + donut['legend'])


class DivisionMapTests(SimpleTestCase):
    def divisions(self, **counts):
        names = ['Barishal', 'Chattogram', 'Dhaka', 'Khulna', 'Mymensingh',
                 'Rajshahi', 'Rangpur', 'Sylhet']
        return [{'name': n, 'count': counts.get(n, 0)} for n in names]

    def test_eight_labelled_tiles_and_legend(self):
        chart = charts.division_map(self.divisions(Dhaka=40, Sylhet=10))
        self.assertEqual(chart['svg'].count('<g class="bb-map__tile'), 8)
        for name in ('Barishal', 'Dhaka', 'Rangpur'):
            self.assertIn(name, chart['svg'])
        self.assertIn('Fewer', chart['legend'])

    def test_intensity_levels(self):
        chart = charts.division_map(self.divisions(Dhaka=40, Sylhet=1))
        self.assertIn('bb-map__tile--l4', chart['svg'])  # the maximum
        self.assertIn('bb-map__tile--l1', chart['svg'])  # a small count
        self.assertIn('bb-map__tile--l0', chart['svg'])  # zero

    def test_empty(self):
        chart = charts.division_map(self.divisions())
        self.assertTrue(chart['empty'])
        self.assertIn('bb-empty', chart['svg'])
        self.assertTrue(charts.division_map(None)['empty'])

    def test_unknown_division_names_are_ignored_and_escaped(self):
        chart = charts.division_map(
            self.divisions(Dhaka=5)
            + [{'name': '<script>x</script>', 'count': 99}]
        )
        self.assertNotIn('<script>', chart['svg'])
        self.assertNotIn('&lt;script', chart['svg'])

    def test_accessible_summary(self):
        chart = charts.division_map(self.divisions(Dhaka=1234))
        self.assertIn('role="img"', chart['svg'])
        self.assertIn('Dhaka: 1,234', chart['svg'])


class SupplyDemandChartTests(SimpleTestCase):
    def test_rows_and_scale(self):
        html = charts.supply_demand_rows([
            {'group': 'O-', 'need': 4, 'donors': 2, 'status': 'gap'},
            {'group': 'A+', 'need': 0, 'donors': 8, 'status': 'quiet'},
        ])
        self.assertIn('4 needed', html)
        self.assertIn('8 listed', html)
        self.assertIn('width:100.0%', html)  # donors 8 is the maximum
        self.assertIn('width:50.0%', html)   # need 4 of 8
        self.assertIn('Needs donors', html)

    def test_empty(self):
        self.assertIn('bb-empty', charts.supply_demand_rows([]))
        self.assertIn('bb-empty', charts.supply_demand_rows(
            [{'group': 'A+', 'need': 0, 'donors': 0, 'status': 'quiet'}]))

    def test_group_label_is_escaped(self):
        html = charts.supply_demand_rows(
            [{'group': '<b>', 'need': 1, 'donors': 1, 'status': 'gap'}])
        self.assertNotIn('<b>', html)


# ------------------------------------------------------------------ pages

class ImpactPageTests(InsightsTestCase):
    def test_public_page_renders_for_anonymous_visitors(self):
        donate(self.donor, district='Dhaka', group='O-')
        response = self.client.get(reverse('impact'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'insights/impact.html')
        self.assertContains(response, 'data-count="1"')
        self.assertContains(response, 'estimate')
        self.assertContains(response, 'verified')

    def test_empty_database_renders_friendly_states(self):
        response = self.client.get(reverse('impact'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No verified donations')
        self.assertContains(response, 'bb-empty')

    def test_site_name_comes_from_settings(self):
        from django.conf import settings
        response = self.client.get(reverse('impact'))
        self.assertContains(response, settings.SITE_NAME)
        self.assertNotContains(response, 'Blood Lagbe')

    def test_period_filter_changes_the_numbers(self):
        donate(self.donor)
        donate(self.donor, days_ago=900)
        all_time = self.client.get(reverse('impact'))
        recent = self.client.get(reverse('impact'), {'period': '12m'})
        self.assertEqual(all_time.context['totals']['donations'], 2)
        self.assertEqual(recent.context['totals']['donations'], 1)
        self.assertContains(recent, 'aria-current="true"')

    def test_bad_period_is_ignored(self):
        response = self.client.get(reverse('impact'), {'period': '<script>'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['period'], 'all')
        self.assertNotContains(response, '<script>alert')

    def test_district_bars_link_to_district_pages(self):
        donate(self.donor, district="Cox's Bazar")
        response = self.client.get(reverse('impact'))
        self.assertContains(
            response, escape(reverse('impact-district', args=["Cox's Bazar"])))

    def test_call_to_action_links_use_real_filters(self):
        asker = make_user()
        make_request(asker, group='O-', units=4, district='Sylhet')
        response = self.client.get(reverse('impact'))
        cta = response.context['cta']
        self.assertEqual(cta['kind'], 'gap')
        self.assertIn('group=O-', cta['donors_url'])
        self.assertIn('district=Sylhet', cta['donors_url'])
        self.assertTrue(cta['donors_url'].startswith(
            reverse('donor-directory')))
        self.assertIn('blood_group=O-', cta['requests_url'])
        self.assertContains(response, 'We need more O- donors in Sylhet')

    def test_renders_for_logged_in_users_too(self):
        self.client.force_login(self.donor)
        self.assertEqual(self.client.get(reverse('impact')).status_code, 200)

    def test_post_is_not_allowed(self):
        self.assertEqual(self.client.post(reverse('impact')).status_code, 405)

    def test_phone_numbers_and_names_never_appear(self):
        other = make_user('Hidden', 'Person', phone='01788888888')
        donate(self.donor, district='Dhaka')
        make_request(other, district='Dhaka')
        response = self.client.get(reverse('impact'))
        for secret in (PHONE, REQUEST_PHONE, '01788888888', SECRET_NAME,
                       SECRET_LAST, 'Hidden Person'):
            self.assertNotContains(response, secret)

    def test_hidden_donors_count_in_totals_but_are_never_named(self):
        hidden = make_user('Ghostly', 'Giver', show_on_leaderboard=False)
        for _ in range(3):
            donate(hidden, district='Dhaka')
        response = self.client.get(reverse('impact'))
        self.assertEqual(response.context['totals']['donations'], 3)
        self.assertNotContains(response, 'Ghostly')
        self.assertNotContains(response, 'Giver')

    def test_pending_and_rejected_are_not_on_the_page(self):
        donate(self.donor, status='pending')
        donate(self.donor, status='rejected')
        response = self.client.get(reverse('impact'))
        self.assertEqual(response.context['totals']['donations'], 0)
        self.assertFalse(response.context['has_data'])


class DistrictPageTests(InsightsTestCase):
    def test_valid_district(self):
        donate(self.donor, district='Sylhet')
        url = reverse('impact-district', args=['Sylhet'])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Sylhet')
        self.assertEqual(response.context['d']['donations'], 1)

    def test_apostrophe_district_and_case_insensitive_lookup(self):
        response = self.client.get(
            reverse('impact-district', args=["Cox's Bazar"]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.client.get('/impact/district/dhaka/').status_code, 200)

    def test_unknown_district_is_404(self):
        for name in ('Atlantis', '%3Cscript%3E', 'Not%20Specified', 'x' * 300):
            response = self.client.get(f'/impact/district/{name}/')
            self.assertEqual(response.status_code, 404, name)

    def test_empty_district_renders(self):
        response = self.client.get(reverse('impact-district', args=['Feni']))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No verified donations in Feni yet')
        self.assertContains(response, 'No open requests in Feni')

    def test_open_requests_listed_without_phone_numbers(self):
        make_request(self.donor, group='O-', district='Dhaka', units=2)
        make_request(self.donor, group='O-', district='Sylhet')
        response = self.client.get(reverse('impact-district', args=['Dhaka']))
        self.assertContains(response, 'City Hospital')
        self.assertContains(response, '2 bags needed')
        self.assertNotContains(response, REQUEST_PHONE)

    def test_no_donor_names_or_phones(self):
        donate(self.donor, district='Dhaka')
        make_user('Dir', 'Ectory', blood_group='A+', district='Dhaka',
                  in_directory=True, phone='01777777777')
        response = self.client.get(reverse('impact-district', args=['Dhaka']))
        for secret in (PHONE, SECRET_NAME, SECRET_LAST, 'Ectory',
                       '01777777777'):
            self.assertNotContains(response, secret)
        self.assertEqual(response.context['d']['donors_available'], 1)

    def test_links_to_other_districts_in_the_division(self):
        response = self.client.get(reverse('impact-district', args=['Dhaka']))
        self.assertContains(
            response, reverse('impact-district', args=['Gazipur']))


class PublicFeedTests(InsightsTestCase):
    def seed(self):
        donate(self.donor, district='Dhaka', group='O-')
        hidden = make_user('Ghostly', 'Giver', show_on_leaderboard=False,
                           phone='01766666666')
        donate(hidden, district='Sylhet')
        make_request(self.donor, district='Dhaka')
        make_user('Dir', 'Ectory', blood_group='O-', in_directory=True,
                  phone='01744444444')

    def test_json_is_public_and_cacheable(self):
        self.seed()
        response = self.client.get(reverse('impact-data'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/json')
        self.assertIn('public', response['Cache-Control'])
        self.assertIn('max-age=300', response['Cache-Control'])
        self.assertEqual(response['Access-Control-Allow-Origin'], '*')
        data = response.json()
        self.assertEqual(data['totals']['donations'], 2)
        self.assertEqual(len(data['monthly']), 12)
        self.assertEqual(len(data['blood_groups']), 8)
        self.assertEqual(len(data['divisions']), 8)
        self.assertIn('generated_at', data)
        self.assertIn('estimate', data['estimate_note'])

    def test_json_has_no_personal_data(self):
        self.seed()
        body = self.client.get(reverse('impact-data')).content.decode()
        for secret in (PHONE, REQUEST_PHONE, '01766666666', '01744444444',
                       SECRET_NAME, SECRET_LAST, 'Ghostly', 'Giver', 'Dir ',
                       'Ectory', 'phone', 'email'):
            self.assertNotIn(secret, body)

    def test_json_keys_are_only_aggregates(self):
        self.seed()
        data = self.client.get(reverse('impact-data')).json()
        self.assertEqual(set(data), {
            'period', 'estimate_note', 'totals', 'monthly', 'month_compare',
            'blood_groups', 'divisions', 'districts',
            'district_not_specified', 'request_funnel', 'supply_vs_demand',
            'generated_at',
        })

    def test_json_period_param(self):
        donate(self.donor, days_ago=900)
        data = self.client.get(reverse('impact-data'), {'period': '12m'}).json()
        self.assertEqual(data['totals']['donations'], 0)
        self.assertEqual(data['period'], '12m')

    def test_json_on_empty_database(self):
        data = self.client.get(reverse('impact-data')).json()
        self.assertEqual(data['totals']['donations'], 0)
        self.assertIsNone(data['totals']['fulfilment_rate'])

    def test_json_rejects_post(self):
        self.assertEqual(self.client.post(reverse('impact-data')).status_code,
                         405)

    def test_csv_export(self):
        donate(self.donor)
        response = self.client.get(reverse('impact-export'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/csv', response['Content-Type'])
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertIn('max-age=300', response['Cache-Control'])
        rows = list(csv.reader(io.StringIO(response.content.decode())))
        self.assertEqual(rows[0], ['month', 'verified_donations'])
        self.assertEqual(len(rows), 13)
        self.assertEqual(sum(int(r[1]) for r in rows[1:]), 1)

    def test_csv_has_no_personal_data(self):
        self.seed()
        body = self.client.get(reverse('impact-export')).content.decode()
        for secret in (PHONE, SECRET_NAME, SECRET_LAST, 'Ghostly'):
            self.assertNotIn(secret, body)
