"""Revoke a shared number and block a requester (donor-side controls)."""
import datetime

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from rewards.directory import contact_block_reason, phone_withheld_from
from rewards.models import (
    CONTACT_RETRY_DAYS,
    BloodRequest,
    ContactBlock,
    ContactRequest,
    RequestResponse,
)
from rewards.test_directory import listed
from rewards.tests import make_user

Status = ContactRequest.Status

_n = iter(range(1, 1000))


def asker(name='Asker'):
    return make_user(
        phone=f'0161{next(_n):07d}', first_name=name,
        email=f'{name.lower()}{next(_n)}@example.com',
    )


class ControlsCase(TestCase):
    def setUp(self):
        self.donor = listed(name='Dolly', district='Dhaka')
        self.sender = asker('Sami')
        self.donor_phone = self.donor.phone
        self.accepted = ContactRequest.objects.create(
            sender=self.sender, donor=self.donor, status=Status.ACCEPTED,
            answered_at=timezone.now(),
        )

    def login(self, user):
        self.client.logout()
        self.client.force_login(user)

    def page(self, user, name, *args):
        self.login(user)
        return self.client.get(reverse(name, args=args)).content.decode()

    def asker_pages(self):
        """Every page where the donor's phone could appear for the asker."""
        return {
            'directory': self.page(self.sender, 'donor-directory'),
            'inbox': self.page(self.sender, 'contact-inbox'),
        }

    def responder_page(self):
        """The asker's own request, with the donor among its responders."""
        req = BloodRequest.objects.create(
            requester=self.sender, blood_group='O+', hospital='Dhaka Medical',
            district='Dhaka', units_needed=1, contact_phone='01711000999',
            needed_by=timezone.localdate() + datetime.timedelta(days=3),
        )
        RequestResponse.objects.get_or_create(blood_request=req, donor=self.donor)
        return self.page(self.sender, 'request-detail', req.pk)


class BaselineTests(ControlsCase):
    def test_phone_is_visible_everywhere_before_any_control(self):
        """Control: without revoke or block the phone shows on every page,
        so the tests below prove the control, not an empty page."""
        for name, body in self.asker_pages().items():
            self.assertIn(self.donor_phone, body, name)
        self.assertIn(self.donor_phone, self.responder_page())


class RevokeTests(ControlsCase):
    def revoke(self, user=None):
        self.login(user or self.donor)
        return self.client.post(
            reverse('contact-revoke', args=[self.accepted.pk]),
        )

    def test_revoke_hides_the_phone_on_every_page(self):
        response = self.revoke()
        self.assertRedirects(response, reverse('contact-inbox'))
        self.accepted.refresh_from_db()
        self.assertEqual(self.accepted.status, Status.REVOKED)
        self.assertIsNotNone(self.accepted.revoked_at)
        for name, body in self.asker_pages().items():
            self.assertNotIn(self.donor_phone, body, name)
        self.assertNotIn(self.donor_phone, self.responder_page())

    def test_responder_list_says_number_not_shared(self):
        self.revoke()
        self.assertIn('Number not shared', self.responder_page())

    def test_asker_is_told_neutrally(self):
        self.revoke()
        body = self.page(self.sender, 'contact-inbox')
        self.assertIn('not sharing their number any more', body)
        self.assertIn('nothing you did', body)
        for blame in ('blocked', 'refused', 'rejected', 'reported'):
            self.assertNotIn(blame, body.lower().split('requests you sent')[1])

    def test_revoke_hides_the_askers_phone_from_the_donor_too(self):
        self.revoke()
        self.assertNotIn(
            self.sender.phone, self.page(self.donor, 'contact-inbox'),
        )

    def test_donor_sees_the_asker_phone_before_revoking(self):
        self.assertIn(
            self.sender.phone, self.page(self.donor, 'contact-inbox'),
        )

    def test_only_the_donor_can_revoke(self):
        for who in (self.sender, asker('Other')):
            response = self.revoke(who)
            self.assertEqual(response.status_code, 404)
        self.accepted.refresh_from_db()
        self.assertEqual(self.accepted.status, Status.ACCEPTED)

    def test_get_is_not_allowed_and_login_is_required(self):
        url = reverse('contact-revoke', args=[self.accepted.pk])
        self.login(self.donor)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.logout()
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)
        self.accepted.refresh_from_db()
        self.assertEqual(self.accepted.status, Status.ACCEPTED)

    def test_revoke_twice_is_harmless(self):
        self.revoke()
        stamp = ContactRequest.objects.get(pk=self.accepted.pk).revoked_at
        response = self.revoke()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            ContactRequest.objects.get(pk=self.accepted.pk).revoked_at, stamp,
        )

    def test_revoke_covers_every_accepted_request_from_that_person(self):
        extra = ContactRequest.objects.create(
            sender=self.sender, donor=self.donor, status=Status.ACCEPTED,
            answered_at=timezone.now(),
        )
        self.revoke()
        extra.refresh_from_db()
        self.assertEqual(extra.status, Status.REVOKED)

    def test_revoke_does_not_touch_other_askers(self):
        other = asker('Other')
        theirs = ContactRequest.objects.create(
            sender=other, donor=self.donor, status=Status.ACCEPTED,
            answered_at=timezone.now(),
        )
        self.revoke()
        theirs.refresh_from_db()
        self.assertEqual(theirs.status, Status.ACCEPTED)
        self.assertIn(self.donor_phone, self.page(other, 'donor-directory'))

    def test_asker_must_wait_before_asking_again(self):
        self.revoke()
        reason = contact_block_reason(self.sender, self.donor)
        self.assertIn('sharing their number any more', reason)
        ContactRequest.objects.filter(pk=self.accepted.pk).update(
            revoked_at=timezone.now()
            - datetime.timedelta(days=CONTACT_RETRY_DAYS + 1),
        )
        self.assertIsNone(contact_block_reason(self.sender, self.donor))

    def test_new_acceptance_after_a_revoke_shows_the_phone_again(self):
        self.revoke()
        ContactRequest.objects.create(
            sender=self.sender, donor=self.donor, status=Status.ACCEPTED,
            answered_at=timezone.now(),
        )
        self.assertEqual(phone_withheld_from(self.sender, [self.donor.pk]),
                         set())
        self.assertIn(self.donor_phone, self.responder_page())


class BlockTests(ControlsCase):
    def block(self, user=None, pk=None):
        self.login(user or self.donor)
        return self.client.post(
            reverse('contact-block', args=[pk or self.accepted.pk]),
        )

    def test_blocked_user_cannot_send_requests(self):
        self.block()
        self.assertTrue(
            ContactBlock.objects.filter(
                donor=self.donor, blocked_user=self.sender).exists(),
        )
        before = ContactRequest.objects.count()
        self.login(self.sender)
        response = self.client.post(
            reverse('contact-donor', args=[self.donor.pk]),
            {'message': 'please', 'next': reverse('donor-directory')},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ContactRequest.objects.count(), before)
        self.assertEqual(
            ContactRequest.objects.filter(
                sender=self.sender, donor=self.donor,
                status=Status.PENDING).count(), 0,
        )

    def test_block_alone_stops_a_stranger_who_never_asked(self):
        """Mutation check: the block itself must refuse the request, not the
        cooling-off period that a revoke or decline leaves behind."""
        fresh = asker('Fresh')
        ContactBlock.objects.create(donor=self.donor, blocked_user=fresh)
        self.login(fresh)
        self.client.post(
            reverse('contact-donor', args=[self.donor.pk]), {'message': 'hi'},
        )
        self.assertFalse(
            ContactRequest.objects.filter(sender=fresh).exists(),
        )

    def test_block_reason_is_vague(self):
        self.block()
        reason = contact_block_reason(self.sender, self.donor)
        self.assertIn("can't take requests right now", reason)
        self.assertNotIn('block', reason.lower())

    def test_blocked_user_cannot_see_the_phone_anywhere(self):
        self.block()
        for name, body in self.asker_pages().items():
            self.assertNotIn(self.donor_phone, body, name)
        self.assertNotIn(self.donor_phone, self.responder_page())

    def test_blocked_user_does_not_see_the_donor_listed(self):
        self.block()
        body = self.page(self.sender, 'donor-directory')
        self.assertNotIn('Dolly', body)
        # everyone else still does
        self.assertIn('Dolly', self.page(asker('Third'), 'donor-directory'))

    def test_blocked_user_is_not_told(self):
        self.block()
        body = self.page(self.sender, 'contact-inbox')
        self.assertNotIn('block', body.lower())

    def test_pending_requests_are_auto_declined(self):
        pending = ContactRequest.objects.create(
            sender=self.sender, donor=self.donor, status=Status.PENDING,
        )
        self.block()
        pending.refresh_from_db()
        self.assertEqual(pending.status, Status.DECLINED)
        self.assertIsNotNone(pending.answered_at)

    def test_accepted_requests_are_revoked(self):
        self.block()
        self.accepted.refresh_from_db()
        self.assertEqual(self.accepted.status, Status.REVOKED)

    def test_other_askers_pending_requests_stay_pending(self):
        other = asker('Other')
        theirs = ContactRequest.objects.create(
            sender=other, donor=self.donor, status=Status.PENDING,
        )
        self.block()
        theirs.refresh_from_db()
        self.assertEqual(theirs.status, Status.PENDING)

    def test_block_is_idempotent(self):
        self.block()
        self.block()
        self.assertEqual(ContactBlock.objects.count(), 1)

    def test_only_the_donor_can_block(self):
        for who in (self.sender, asker('Other')):
            self.assertEqual(self.block(who).status_code, 404)
        self.assertFalse(ContactBlock.objects.exists())

    def test_get_not_allowed(self):
        self.login(self.donor)
        url = reverse('contact-block', args=[self.accepted.pk])
        self.assertEqual(self.client.get(url).status_code, 405)

    def test_block_is_one_way(self):
        """The donor can still ask the person they blocked."""
        self.block()
        self.assertFalse(
            ContactBlock.objects.filter(
                donor=self.sender, blocked_user=self.donor).exists(),
        )

    def test_unblock_restores_directory_and_requests(self):
        self.block()
        block = ContactBlock.objects.get()
        self.login(self.donor)
        response = self.client.post(reverse('contact-unblock', args=[block.pk]))
        self.assertRedirects(response, reverse('contact-inbox'))
        self.assertFalse(ContactBlock.objects.exists())
        self.assertIn('Dolly', self.page(self.sender, 'donor-directory'))

    def test_only_the_blocker_can_unblock(self):
        self.block()
        block = ContactBlock.objects.get()
        self.login(self.sender)
        response = self.client.post(reverse('contact-unblock', args=[block.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(ContactBlock.objects.exists())

    def test_blocked_people_are_listed_by_display_name_only(self):
        self.block()
        body = self.page(self.donor, 'contact-inbox')
        self.assertIn('People you blocked', body)
        self.assertIn('Unblock', body)
        self.assertNotIn(self.sender.email, body)

    def test_self_block_is_refused_by_the_database(self):
        from django.db import IntegrityError, transaction
        with self.assertRaises(IntegrityError), transaction.atomic():
            ContactBlock.objects.create(
                donor=self.donor, blocked_user=self.donor,
            )

    def test_inbox_offers_block_and_revoke_buttons(self):
        body = self.page(self.donor, 'contact-inbox')
        self.assertIn(reverse('contact-revoke', args=[self.accepted.pk]), body)
        self.assertIn(reverse('contact-block', args=[self.accepted.pk]), body)
