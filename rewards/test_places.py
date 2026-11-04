"""District and hospital selects for donations and urgent requests."""
import datetime

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from rewards.forms import BloodRequestForm, DonationForm
from rewards.geo import DISTRICT_NAMES, DISTRICT_TO_DIVISION, DIVISIONS
from rewards.models import BloodRequest, Donation, Place
from rewards.tests import make_user


class GeoTests(TestCase):
    def test_sixty_four_unique_districts_in_eight_divisions(self):
        self.assertEqual(len(DIVISIONS), 8)
        self.assertEqual(len(DISTRICT_NAMES), 64)
        self.assertEqual(len(set(DISTRICT_NAMES)), 64)
        self.assertEqual(DISTRICT_TO_DIVISION['Sylhet'], 'Sylhet')

    def test_starter_places_seeded_in_known_districts(self):
        self.assertTrue(Place.objects.exists())
        for place in Place.objects.all():
            self.assertIn(place.district, DISTRICT_NAMES)


class DonationPlaceFormTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.dmch = Place.objects.get(name='Dhaka Medical College Hospital')

    def data(self, **extra):
        data = {
            'blood_group': 'A+',
            'donation_date': timezone.localdate().isoformat(),
            'district': 'Dhaka',
        }
        data.update(extra)
        return data

    def test_listed_place_fills_location(self):
        form = DonationForm(self.data(place=self.dmch.pk), user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        donation = form.save(commit=False)
        self.assertEqual(donation.location, self.dmch.name)
        self.assertEqual(donation.place, self.dmch)

    def test_typed_place_when_not_listed(self):
        form = DonationForm(
            self.data(location='  Village camp  '), user=self.user,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save(commit=False).location, 'Village camp')

    def test_place_overrides_typed_text(self):
        form = DonationForm(
            self.data(place=self.dmch.pk, location='junk'), user=self.user,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save(commit=False).location, self.dmch.name)

    def test_needs_place_or_typed_name(self):
        form = DonationForm(self.data(), user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn('location', form.errors)

    def test_district_required(self):
        data = self.data(place=self.dmch.pk)
        del data['district']
        form = DonationForm(data, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn('district', form.errors)

    def test_place_must_be_in_chosen_district(self):
        form = DonationForm(
            self.data(district='Sylhet', place=self.dmch.pk), user=self.user,
        )
        self.assertFalse(form.is_valid())
        self.assertIn('place', form.errors)

    def test_unknown_district_rejected(self):
        form = DonationForm(
            self.data(district='Atlantis', location='x'), user=self.user,
        )
        self.assertFalse(form.is_valid())
        self.assertIn('district', form.errors)

    def test_unlisted_place_not_offered(self):
        hidden = Place.objects.create(
            name='Hidden Clinic', district='Dhaka', is_approved=False,
        )
        form = DonationForm(self.data(place=hidden.pk), user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn('place', form.errors)

    def test_options_carry_district_for_the_js_filter(self):
        html = str(DonationForm(user=self.user)['place'])
        self.assertIn('data-district="Dhaka"', html)
        self.assertIn('data-district="Sylhet"', html)


class DonationPlaceViewTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)

    def test_post_saves_district_and_place(self):
        place = Place.objects.get(name='Square Hospitals')
        response = self.client.post(reverse('log-donation'), {
            'blood_group': 'B+',
            'donation_date': timezone.localdate().isoformat(),
            'district': 'Dhaka', 'place': place.pk,
        })
        self.assertEqual(response.status_code, 302)
        donation = Donation.objects.get(donor=self.user)
        self.assertEqual(
            (donation.district, donation.place, donation.location),
            ('Dhaka', place, 'Square Hospitals'),
        )

    def test_page_renders_selects_and_script(self):
        response = self.client.get(reverse('log-donation'))
        self.assertContains(response, 'name="district"')
        self.assertContains(response, 'name="place"')
        self.assertContains(response, 'js/place-select.js')
        self.assertContains(response, 'js-place-text')

    def test_history_shows_district(self):
        Donation.objects.create(
            donor=self.user, blood_group='A+', district='Rangpur',
            donation_date=timezone.localdate(), location='Camp',
        )
        response = self.client.get(reverse('log-donation'))
        self.assertContains(response, 'Rangpur')


class RequestPlaceTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)
        self.place = Place.objects.get(name='Square Hospitals')

    def data(self, **extra):
        data = {
            'blood_group': 'O-', 'district': 'Dhaka', 'units_needed': 1,
            'needed_by': (timezone.localdate()
                          + datetime.timedelta(days=1)).isoformat(),
            'contact_phone': '01798765432',
        }
        data.update(extra)
        return data

    def test_listed_place_fills_hospital(self):
        form = BloodRequestForm(self.data(place=self.place.pk), user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        req = form.save(commit=False)
        self.assertEqual(
            (req.hospital, req.place), ('Square Hospitals', self.place),
        )

    def test_typed_hospital_still_works(self):
        form = BloodRequestForm(
            self.data(hospital='Local Clinic'), user=self.user,
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_needs_place_or_hospital(self):
        form = BloodRequestForm(self.data(), user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn('hospital', form.errors)

    def test_mismatched_district_rejected(self):
        form = BloodRequestForm(
            self.data(district='Khulna', place=self.place.pk), user=self.user,
        )
        self.assertFalse(form.is_valid())
        self.assertIn('place', form.errors)

    def test_create_view_saves_place(self):
        response = self.client.post(
            reverse('request-create'), self.data(place=self.place.pk),
        )
        self.assertEqual(response.status_code, 302)
        req = BloodRequest.objects.get(requester=self.user)
        self.assertEqual(req.place, self.place)
        self.assertEqual(req.hospital, 'Square Hospitals')
