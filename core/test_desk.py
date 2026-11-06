import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from events.models import Event
from rewards.models import Donation
from stories.models import Story

User = get_user_model()


def member(phone, **fields):
    user = User.objects.create_user(phone=phone, password='pass12345')
    for name, value in fields.items():
        setattr(user, name, value)
    user.save()
    return user


class StaffDeskTests(TestCase):
    url = '/desk/'

    def setUp(self):
        self.staff = member('01700000100', staff=True)
        self.donor = member('01700000101', first_name='Dina', last_name='Zzyzx')

    def test_anonymous_is_sent_to_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_regular_member_is_refused(self):
        self.client.force_login(self.donor)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_staff_sees_the_queue(self):
        self.client.force_login(self.staff)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Nothing is waiting')
        self.assertContains(response, 'noindex')

    def test_counts_reflect_what_is_waiting(self):
        Donation.objects.create(
            donor=self.donor, blood_group='A+',
            donation_date=timezone.localdate(), location='Bank',
            status=Donation.Status.PENDING,
        )
        Story.objects.create(
            author=self.donor, title='Waiting', body='Body',
            status=Story.Status.PENDING,
        )
        Event.objects.create(
            title='Drive', organizer=self.donor, district='Dhaka',
            starts_at=timezone.now() + datetime.timedelta(days=2),
            status=Event.Status.PENDING,
        )
        self.client.force_login(self.staff)
        response = self.client.get(self.url)
        queue = {item['label']: item['count'] for item in response.context['queue']}
        self.assertEqual(queue['Donations to verify'], 1)
        self.assertEqual(queue['Stories to review'], 1)
        self.assertEqual(queue['Blood drives to approve'], 1)
        self.assertEqual(response.context['waiting'], 3)
        self.assertContains(response, '3 items waiting')

    def test_admin_links_point_at_filtered_changelists(self):
        self.client.force_login(self.staff)
        response = self.client.get(self.url)
        self.assertContains(response, '/admin/rewards/donation/?status__exact=pending')

    def test_no_member_details_are_shown(self):
        Donation.objects.create(
            donor=self.donor, blood_group='A+',
            donation_date=timezone.localdate(), location='Bank',
            status=Donation.Status.PENDING,
        )
        self.client.force_login(self.staff)
        body = self.client.get(self.url).content.decode()
        for secret in ('01700000101', 'Dina', 'Zzyzx'):
            self.assertNotIn(secret, body)

    def test_menu_link_only_for_staff(self):
        self.client.force_login(self.donor)
        self.assertNotContains(self.client.get(reverse('home')), 'Staff desk')
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse('home')), 'Staff desk')
