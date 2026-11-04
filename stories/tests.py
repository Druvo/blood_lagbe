import datetime

from django.contrib import admin
from django.contrib.admin.sites import AdminSite
from django.core import mail
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.template import Context, Template
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from rewards.models import Donation
from rewards.tests import make_user
from stories.admin import StoryAdmin, approve_stories, reject_stories
from stories.models import (
    BODY_MAX,
    MAX_PENDING_PER_AUTHOR,
    REPORT_THRESHOLD,
    Story,
    StoryReaction,
    StoryReport,
)
from stories.templatetags.story_tags import latest_stories

GOOD_BODY = 'I gave blood for the first time last winter and it changed me.'


def make_story(author, status=Story.Status.APPROVED, **kwargs):
    defaults = dict(
        kind=Story.Kind.DONOR, title='My first donation', body=GOOD_BODY,
    )
    defaults.update(kwargs)
    story = Story.objects.create(author=author, status=status, **defaults)
    if status == Story.Status.APPROVED and not story.approved_at:
        story.approved_at = timezone.now()
        story.save(update_fields=['approved_at'])
    return story


def make_donation(donor, status=Donation.Status.APPROVED, **kwargs):
    return Donation.objects.create(
        donor=donor, blood_group='A+', location='City Hospital',
        donation_date=timezone.localdate() - datetime.timedelta(days=5),
        status=status, **kwargs,
    )


def form_data(**overrides):
    data = {
        'kind': 'donor', 'title': 'A good day', 'body': GOOD_BODY,
        'district': '', 'blood_group': '', 'donation': '',
    }
    data.update(overrides)
    return data


class StoryBase(TestCase):
    def setUp(self):
        self.author = make_user(
            phone='01755500001', first_name='Rina', last_name='Chowdhury',
            email='rina.secret@example.com',
        )
        self.other = make_user(phone='01755500002', first_name='Karim')
        self.staff = make_user(phone='01755500003', staff=True)


class ModelTests(StoryBase):
    def test_defaults_to_pending(self):
        story = Story.objects.create(
            author=self.author, title='Hello there', body=GOOD_BODY,
        )
        self.assertEqual(story.status, Story.Status.PENDING)
        self.assertIsNone(story.approved_at)

    def test_title_and_body_length_limits(self):
        story = Story(author=self.author, title='x' * 81, body=GOOD_BODY)
        with self.assertRaises(ValidationError) as ctx:
            story.full_clean()
        self.assertIn('title', ctx.exception.message_dict)
        story = Story(author=self.author, title='Fine title', body='y' * (BODY_MAX + 1))
        with self.assertRaises(ValidationError) as ctx:
            story.full_clean()
        self.assertIn('body', ctx.exception.message_dict)

    def test_too_short_rejected(self):
        story = Story(author=self.author, title='Hi', body='short')
        with self.assertRaises(ValidationError) as ctx:
            story.full_clean()
        self.assertIn('title', ctx.exception.message_dict)
        self.assertIn('body', ctx.exception.message_dict)

    def test_html_rejected(self):
        story = Story(
            author=self.author, title='<b>bold</b> title',
            body=GOOD_BODY + ' <script>alert(1)</script>',
        )
        with self.assertRaises(ValidationError) as ctx:
            story.full_clean()
        self.assertIn('title', ctx.exception.message_dict)
        self.assertIn('body', ctx.exception.message_dict)

    def test_plain_less_than_is_fine(self):
        story = Story(
            author=self.author, title='Under 18 and nervous',
            body='I was < 18 and 5 < 6 felt scary but I did it anyway, I am proud.',
        )
        story.full_clean()

    def test_whitespace_normalised_on_save(self):
        story = Story(
            author=self.author, title='  Spaced   out  title ',
            body='\r\nLine one is long enough to pass the minimum.\r\n\r\n\r\n\r\nLine two here.  \r\n',
        )
        story.full_clean()
        story.save()
        self.assertEqual(story.title, 'Spaced out title')
        self.assertEqual(
            story.body,
            'Line one is long enough to pass the minimum.\n\nLine two here.',
        )

    def test_district_must_be_a_real_district(self):
        story = Story(
            author=self.author, title='Fine title', body=GOOD_BODY,
            district='Atlantis',
        )
        with self.assertRaises(ValidationError) as ctx:
            story.full_clean()
        self.assertIn('district', ctx.exception.message_dict)

    def test_donation_must_belong_to_author(self):
        donation = make_donation(self.other)
        story = Story(
            author=self.author, title='Fine title', body=GOOD_BODY,
            donation=donation,
        )
        with self.assertRaises(ValidationError) as ctx:
            story.full_clean()
        self.assertIn('donation', ctx.exception.message_dict)

    def test_donation_must_be_approved(self):
        donation = make_donation(self.author, status=Donation.Status.PENDING)
        story = Story(
            author=self.author, title='Fine title', body=GOOD_BODY,
            donation=donation,
        )
        with self.assertRaises(ValidationError) as ctx:
            story.full_clean()
        self.assertIn('donation', ctx.exception.message_dict)

    def test_own_approved_donation_is_accepted(self):
        story = Story(
            author=self.author, title='Fine title', body=GOOD_BODY,
            donation=make_donation(self.author),
        )
        story.full_clean()

    def test_reaction_unique_per_user_story_kind(self):
        story = make_story(self.author)
        StoryReaction.objects.create(user=self.other, story=story, kind='heart')
        with self.assertRaises(IntegrityError), transaction.atomic():
            StoryReaction.objects.create(user=self.other, story=story, kind='heart')
        StoryReaction.objects.create(user=self.other, story=story, kind='thanks')

    def test_report_unique_per_reporter_story(self):
        story = make_story(self.author)
        StoryReport.objects.create(reporter=self.other, story=story)
        with self.assertRaises(IntegrityError), transaction.atomic():
            StoryReport.objects.create(reporter=self.other, story=story)

    def test_approve_and_reject_set_approved_at(self):
        story = make_story(self.author, status=Story.Status.PENDING)
        story.approve()
        self.assertIsNotNone(story.approved_at)
        story.reject('Too many details')
        story.refresh_from_db()
        self.assertEqual(story.status, Story.Status.REJECTED)
        self.assertIsNone(story.approved_at)
        self.assertEqual(story.moderation_note, 'Too many details')

    def test_hides_author_rules(self):
        story = make_story(self.author)
        self.assertFalse(story.hides_author)
        self.assertEqual(story.public_name, 'Rina Chowdhury')
        story.anonymous = True
        self.assertTrue(story.hides_author)
        self.assertEqual(story.public_name, 'A BloodBond donor')
        story.anonymous = False
        self.author.profile.show_on_leaderboard = False
        self.author.profile.save()
        story = Story.objects.get(pk=story.pk)
        self.assertTrue(story.hides_author)
        self.assertNotIn('Rina', story.public_name)


class VisibilityTests(StoryBase):
    def setUp(self):
        super().setUp()
        self.approved = make_story(self.author, title='Approved story')
        self.pending = make_story(
            self.author, status=Story.Status.PENDING, title='Pending story',
        )
        self.rejected = make_story(
            self.author, status=Story.Status.REJECTED, title='Rejected story',
        )

    def test_feed_shows_only_approved(self):
        response = self.client.get(reverse('stories'))
        self.assertContains(response, 'Approved story')
        self.assertNotContains(response, 'Pending story')
        self.assertNotContains(response, 'Rejected story')

    def test_non_approved_detail_is_404_for_public_and_others(self):
        for story in (self.pending, self.rejected):
            url = reverse('story-detail', args=[story.pk])
            self.assertEqual(self.client.get(url).status_code, 404)
            self.client.force_login(self.other)
            self.assertEqual(self.client.get(url).status_code, 404)
            self.client.logout()

    def test_author_sees_own_non_approved(self):
        self.client.force_login(self.author)
        for story in (self.pending, self.rejected):
            response = self.client.get(reverse('story-detail', args=[story.pk]))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'noindex')
        response = self.client.get(reverse('story-detail', args=[self.pending.pk]))
        self.assertContains(response, 'pending review')

    def test_staff_sees_any_story(self):
        self.client.force_login(self.staff)
        for story in (self.pending, self.rejected):
            response = self.client.get(reverse('story-detail', args=[story.pk]))
            self.assertEqual(response.status_code, 200)

    def test_unapproved_has_no_share_buttons(self):
        self.client.force_login(self.author)
        response = self.client.get(reverse('story-detail', args=[self.pending.pk]))
        self.assertNotContains(response, 'wa.me')

    def test_approved_detail_public(self):
        response = self.client.get(reverse('story-detail', args=[self.approved.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'wa.me')
        self.assertContains(response, 'facebook.com/sharer')
        self.assertContains(response, 'data-copy=')
        self.assertContains(response, 'data-story-share')

    def test_no_stories_empty_state(self):
        Story.objects.all().delete()
        response = self.client.get(reverse('stories'))
        self.assertContains(response, 'No stories yet')

    def test_unapproved_cannot_be_reacted_to_or_reported(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(
            reverse('story-react', args=[self.pending.pk]), {'kind': 'heart'},
        ).status_code, 404)
        self.assertEqual(self.client.post(
            reverse('story-report', args=[self.pending.pk]), {'reason': 'spam'},
        ).status_code, 404)

    def test_queryset_visible_to(self):
        self.assertEqual(Story.objects.visible_to(None).count(), 1)
        self.assertEqual(Story.objects.visible_to(self.other).count(), 1)
        self.assertEqual(Story.objects.visible_to(self.author).count(), 3)
        self.assertEqual(Story.objects.visible_to(self.staff).count(), 3)


class FeedTests(StoryBase):
    def test_newest_first(self):
        now = timezone.now()
        make_story(self.author, title='Older one', approved_at=now - datetime.timedelta(days=2))
        make_story(self.author, title='Newer one', approved_at=now)
        response = self.client.get(reverse('stories'))
        titles = [s.title for s in response.context['stories']]
        self.assertEqual(titles, ['Newer one', 'Older one'])

    def test_filter_by_kind(self):
        make_story(self.author, title='Donor tale', kind='donor')
        make_story(self.author, title='Family tale', kind='recipient')
        response = self.client.get(reverse('stories'), {'kind': 'recipient'})
        self.assertContains(response, 'Family tale')
        self.assertNotContains(response, 'Donor tale')

    def test_filter_by_district(self):
        make_story(self.author, title='Dhaka tale', district='Dhaka')
        make_story(self.author, title='Sylhet tale', district='Sylhet')
        response = self.client.get(reverse('stories'), {'district': 'Sylhet'})
        self.assertContains(response, 'Sylhet tale')
        self.assertNotContains(response, 'Dhaka tale')

    def test_filter_with_no_match_shows_empty_state(self):
        make_story(self.author, district='Dhaka')
        response = self.client.get(reverse('stories'), {'district': 'Sylhet'})
        self.assertContains(response, 'No stories match')

    def test_bad_filter_values_ignored(self):
        make_story(self.author, title='Still here')
        response = self.client.get(
            reverse('stories'), {'kind': 'nope', 'district': '<script>'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Still here')
        self.assertEqual(response.context['kind'], '')
        self.assertEqual(response.context['district'], '')
        self.assertNotContains(response, 'Clear filters')

    def test_pagination(self):
        for i in range(11):
            make_story(
                self.author, title=f'Story number {i:02d}',
                approved_at=timezone.now() - datetime.timedelta(minutes=i),
            )
        first = self.client.get(reverse('stories'))
        self.assertEqual(len(first.context['stories']), 9)
        self.assertContains(first, 'Page 1 of 2')
        second = self.client.get(reverse('stories'), {'page': 2})
        self.assertEqual(len(second.context['stories']), 2)
        self.assertContains(second, 'Story number 10')
        # Junk page numbers fall back instead of erroring.
        self.assertEqual(
            self.client.get(reverse('stories'), {'page': 'abc'}).status_code, 200,
        )
        self.assertEqual(
            self.client.get(reverse('stories'), {'page': 99}).status_code, 200,
        )

    def test_pagination_keeps_filters(self):
        for i in range(10):
            make_story(self.author, title=f'Dhaka story {i}', district='Dhaka')
        response = self.client.get(reverse('stories'), {'district': 'Dhaka'})
        self.assertContains(response, 'district=Dhaka&amp;page=2')

    def test_reaction_counts_shown(self):
        story = make_story(self.author)
        StoryReaction.objects.create(user=self.other, story=story, kind='heart')
        StoryReaction.objects.create(user=self.staff, story=story, kind='heart')
        response = self.client.get(reverse('stories'))
        buttons = response.context['stories'][0].reaction_buttons
        counts = {b['kind']: b['count'] for b in buttons}
        self.assertEqual(counts, {'heart': 2, 'thanks': 0, 'proud': 0})

    def test_active_reaction_flagged_for_user(self):
        story = make_story(self.author)
        StoryReaction.objects.create(user=self.other, story=story, kind='thanks')
        self.client.force_login(self.other)
        response = self.client.get(reverse('stories'))
        active = {b['kind']: b['active'] for b in response.context['stories'][0].reaction_buttons}
        self.assertEqual(active, {'heart': False, 'thanks': True, 'proud': False})

    def test_feed_query_count_is_flat(self):
        for i in range(6):
            make_story(make_user(phone=f'01766600{i:03d}'), title=f'Story {i}')
        # Paginator count + one joined stories query, however many stories.
        with self.assertNumQueries(2):
            self.client.get(reverse('stories'))


class AnonymousLeakTests(StoryBase):
    SECRETS = ('Rina', 'Chowdhury', '01755500001', 'rina.secret@example.com')

    def setUp(self):
        super().setUp()
        self.story = make_story(
            self.author, anonymous=True, title='Quietly given',
            district='Dhaka', donation=make_donation(self.author),
        )

    def assertNoLeak(self, response):
        html = response.content.decode()
        for secret in self.SECRETS:
            self.assertNotIn(secret, html, f'{secret} leaked')

    def test_detail_page(self):
        response = self.client.get(reverse('story-detail', args=[self.story.pk]))
        self.assertNoLeak(response)
        self.assertContains(response, 'A BloodBond donor')

    def test_og_tags_and_share_links(self):
        response = self.client.get(reverse('story-detail', args=[self.story.pk]))
        html = response.content.decode()
        self.assertIn('property="og:title" content="Quietly given"', html)
        for secret in self.SECRETS:
            self.assertNotIn(secret, html)
        self.assertNotIn('Donor #%d' % self.author.pk, html)

    def test_feed(self):
        self.assertNoLeak(self.client.get(reverse('stories')))

    def test_home_strip(self):
        response = self.client.get(reverse('home'))
        self.assertContains(response, 'Quietly given')
        # Other home sections (top donors) may legitimately name a donor who
        # opted in there; only the stories strip is ours to check.
        html = response.content.decode()
        strip = html[html.index('bbs-strip__head'):html.index('Who can donate')]
        for secret in self.SECRETS:
            self.assertNotIn(secret, strip)
        self.assertIn('A BloodBond donor', strip)

    def test_react_json(self):
        self.client.force_login(self.other)
        response = self.client.post(
            reverse('story-react', args=[self.story.pk]), {'kind': 'heart'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(response.json(), {'kind': 'heart', 'active': True, 'count': 1})
        self.assertNoLeak(response)

    def test_leaderboard_opt_out_makes_story_anonymous(self):
        story = make_story(self.author, title='Opted out story')
        self.author.profile.show_on_leaderboard = False
        self.author.profile.save()
        for url in (reverse('story-detail', args=[story.pk]), reverse('stories')):
            response = self.client.get(url)
            self.assertContains(response, 'Opted out story')
            self.assertNoLeak(response)
        html = self.client.get(reverse('home')).content.decode()
        strip = html[html.index('bbs-strip__head'):html.index('Who can donate')]
        self.assertIn('Opted out story', strip)
        for secret in self.SECRETS:
            self.assertNotIn(secret, strip)

    def test_named_story_shows_name_but_never_phone_or_email(self):
        story = make_story(self.author, title='Named story')
        response = self.client.get(reverse('story-detail', args=[story.pk]))
        self.assertContains(response, 'Rina Chowdhury')
        self.assertNotContains(response, '01755500001')
        self.assertNotContains(response, 'rina.secret@example.com')

    def test_unnamed_user_never_shows_phone(self):
        user = make_user(phone='01788800077')
        story = make_story(user, title='No name story')
        for url in (reverse('story-detail', args=[story.pk]), reverse('stories'), reverse('home')):
            response = self.client.get(url)
            self.assertNotContains(response, '01788800077')
            self.assertContains(response, 'No name story')

    def test_named_og_tags_have_no_author_name(self):
        story = make_story(self.author, title='Named story')
        html = self.client.get(reverse('story-detail', args=[story.pk])).content.decode()
        head = html.split('</head>')[0]
        self.assertNotIn('Rina', head)


class CreateTests(StoryBase):
    def test_login_required(self):
        response = self.client.get(reverse('story-create'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response['Location'])
        response = self.client.post(reverse('story-create'), form_data())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Story.objects.count(), 0)

    def test_form_renders(self):
        self.client.force_login(self.author)
        response = self.client.get(reverse('story-create'))
        self.assertContains(response, 'Submit for review')
        self.assertContains(response, 'Prefer not to say')

    def test_form_prefills_from_profile(self):
        self.author.profile.district = 'Dhaka'
        self.author.profile.blood_group = 'O+'
        self.author.profile.save()
        self.client.force_login(self.author)
        form = self.client.get(reverse('story-create')).context['form']
        self.assertEqual(form.initial['district'], 'Dhaka')
        self.assertEqual(form.initial['blood_group'], 'O+')

    def test_create_is_pending_and_confirms(self):
        self.client.force_login(self.author)
        response = self.client.post(
            reverse('story-create'), form_data(district='Dhaka', anonymous='on'),
            follow=True,
        )
        story = Story.objects.get()
        self.assertEqual(story.author, self.author)
        self.assertEqual(story.status, Story.Status.PENDING)
        self.assertTrue(story.anonymous)
        self.assertRedirects(response, reverse('story-mine'))
        self.assertContains(response, 'pending review')
        # Not public yet.
        self.client.logout()
        self.assertNotContains(self.client.get(reverse('stories')), 'A good day')

    def test_status_cannot_be_forced_by_post(self):
        self.client.force_login(self.author)
        self.client.post(reverse('story-create'), form_data(
            status='approved', author=self.other.pk, approved_at='2020-01-01',
        ))
        story = Story.objects.get()
        self.assertEqual(story.status, Story.Status.PENDING)
        self.assertEqual(story.author, self.author)
        self.assertIsNone(story.approved_at)

    def test_invalid_input_rerenders_with_errors(self):
        self.client.force_login(self.author)
        response = self.client.post(reverse('story-create'), form_data(title='', body='short'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Story.objects.count(), 0)
        self.assertTrue(response.context['form'].errors)

    def test_html_input_rejected(self):
        self.client.force_login(self.author)
        response = self.client.post(
            reverse('story-create'), form_data(title='<img src=x onerror=alert(1)>'),
        )
        self.assertEqual(Story.objects.count(), 0)
        self.assertContains(response, 'plain text')

    def test_can_link_own_approved_donation(self):
        donation = make_donation(self.author)
        self.client.force_login(self.author)
        self.client.post(reverse('story-create'), form_data(donation=donation.pk))
        self.assertEqual(Story.objects.get().donation, donation)

    def test_cannot_link_someone_elses_donation(self):
        donation = make_donation(self.other)
        self.client.force_login(self.author)
        response = self.client.post(reverse('story-create'), form_data(donation=donation.pk))
        self.assertEqual(Story.objects.count(), 0)
        self.assertTrue(response.context['form'].errors['donation'])

    def test_cannot_link_unapproved_donation(self):
        donation = make_donation(self.author, status=Donation.Status.PENDING)
        self.client.force_login(self.author)
        self.client.post(reverse('story-create'), form_data(donation=donation.pk))
        self.assertEqual(Story.objects.count(), 0)

    def test_donation_choices_limited_to_own_approved(self):
        mine = make_donation(self.author)
        make_donation(self.author, status=Donation.Status.PENDING)
        make_donation(self.other)
        self.client.force_login(self.author)
        form = self.client.get(reverse('story-create')).context['form']
        self.assertEqual(list(form.fields['donation'].queryset), [mine])

    def test_pending_cap(self):
        for i in range(MAX_PENDING_PER_AUTHOR):
            make_story(self.author, status=Story.Status.PENDING, title=f'Waiting {i}')
        self.client.force_login(self.author)
        response = self.client.post(reverse('story-create'), form_data())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'waiting for review')
        self.assertEqual(Story.objects.count(), MAX_PENDING_PER_AUTHOR)


class MineAndDeleteTests(StoryBase):
    def setUp(self):
        super().setUp()
        self.approved = make_story(self.author, title='Mine approved')
        self.pending = make_story(self.author, status='pending', title='Mine pending')
        self.rejected = make_story(
            self.author, status='rejected', title='Mine rejected',
            moderation_note='Please remove the hospital ward number.',
        )
        self.theirs = make_story(self.other, title='Karim story')

    def test_login_required(self):
        self.assertEqual(self.client.get(reverse('story-mine')).status_code, 302)

    def test_lists_only_own_with_status_pills(self):
        self.client.force_login(self.author)
        response = self.client.get(reverse('story-mine'))
        self.assertContains(response, 'Mine approved')
        self.assertContains(response, 'Mine pending')
        self.assertContains(response, 'Mine rejected')
        self.assertNotContains(response, 'Karim story')
        self.assertContains(response, 'Published')
        self.assertContains(response, 'Pending review')
        self.assertContains(response, 'Not published')
        self.assertContains(response, 'Please remove the hospital ward number.')

    def test_empty_state(self):
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse('story-mine')), 'not shared a story yet')

    def test_author_deletes_any_status(self):
        self.client.force_login(self.author)
        for story in (self.approved, self.pending, self.rejected):
            response = self.client.post(reverse('story-delete', args=[story.pk]))
            self.assertRedirects(response, reverse('story-mine'))
        self.assertFalse(Story.objects.filter(author=self.author).exists())

    def test_cannot_delete_someone_elses(self):
        self.client.force_login(self.author)
        response = self.client.post(reverse('story-delete', args=[self.theirs.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Story.objects.filter(pk=self.theirs.pk).exists())

    def test_staff_user_still_cannot_delete_via_public_route(self):
        self.client.force_login(self.staff)
        self.assertEqual(
            self.client.post(reverse('story-delete', args=[self.theirs.pk])).status_code, 404,
        )

    def test_delete_requires_post_and_login(self):
        url = reverse('story-delete', args=[self.approved.pk])
        self.client.force_login(self.author)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertTrue(Story.objects.filter(pk=self.approved.pk).exists())

    def test_delete_removes_reactions_and_reports(self):
        StoryReaction.objects.create(user=self.other, story=self.approved, kind='heart')
        StoryReport.objects.create(reporter=self.other, story=self.approved)
        self.client.force_login(self.author)
        self.client.post(reverse('story-delete', args=[self.approved.pk]))
        self.assertEqual(StoryReaction.objects.count(), 0)
        self.assertEqual(StoryReport.objects.count(), 0)


class ReactionTests(StoryBase):
    def setUp(self):
        super().setUp()
        self.story = make_story(self.author)
        self.url = reverse('story-react', args=[self.story.pk])

    def test_login_required(self):
        response = self.client.post(self.url, {'kind': 'heart'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(StoryReaction.objects.count(), 0)

    def test_get_not_allowed(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_toggle_on_and_off_plain_redirect(self):
        self.client.force_login(self.other)
        response = self.client.post(self.url, {'kind': 'heart'})
        self.assertRedirects(response, self.story.get_absolute_url())
        self.assertEqual(StoryReaction.objects.count(), 1)
        self.client.post(self.url, {'kind': 'heart'})
        self.assertEqual(StoryReaction.objects.count(), 0)

    def test_ajax_returns_json_counts(self):
        self.client.force_login(self.other)
        response = self.client.post(
            self.url, {'kind': 'thanks'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(response.json(), {'kind': 'thanks', 'active': True, 'count': 1})
        self.client.force_login(self.staff)
        response = self.client.post(
            self.url, {'kind': 'thanks'}, HTTP_ACCEPT='application/json',
        )
        self.assertEqual(response.json()['count'], 2)
        response = self.client.post(
            self.url, {'kind': 'thanks'}, HTTP_ACCEPT='application/json',
        )
        self.assertEqual(response.json(), {'kind': 'thanks', 'active': False, 'count': 1})

    def test_kinds_are_independent(self):
        self.client.force_login(self.other)
        for kind in ('heart', 'thanks', 'proud'):
            self.client.post(self.url, {'kind': kind})
        self.assertEqual(StoryReaction.objects.filter(user=self.other).count(), 3)

    def test_bad_kind(self):
        self.client.force_login(self.other)
        response = self.client.post(self.url, {'kind': 'hate'}, HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 400)
        self.client.post(self.url, {'kind': 'hate'})
        self.assertEqual(StoryReaction.objects.count(), 0)

    def test_next_redirect_is_local_only(self):
        self.client.force_login(self.other)
        response = self.client.post(self.url, {'kind': 'heart', 'next': 'https://evil.example/x'})
        self.assertRedirects(response, self.story.get_absolute_url())
        response = self.client.post(self.url, {'kind': 'proud', 'next': '/stories/?kind=donor'})
        self.assertEqual(response['Location'], '/stories/?kind=donor')

    def test_author_may_react_to_own_story(self):
        # Allowed: a thank-you to yourself is harmless and avoids edge cases.
        self.client.force_login(self.author)
        self.client.post(self.url, {'kind': 'proud'})
        self.assertEqual(StoryReaction.objects.count(), 1)

    def test_logged_out_buttons_link_to_login(self):
        response = self.client.get(self.story.get_absolute_url())
        self.assertContains(response, 'Log in to react')


class ReportTests(StoryBase):
    def setUp(self):
        super().setUp()
        self.story = make_story(self.author)
        self.url = reverse('story-report', args=[self.story.pk])
        self.reporters = [make_user(phone=f'01777700{i:03d}') for i in range(5)]

    def test_login_required(self):
        self.assertEqual(self.client.post(self.url, {'reason': 'spam'}).status_code, 302)
        self.assertEqual(StoryReport.objects.count(), 0)

    def test_get_not_allowed(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_report_recorded_once_per_user(self):
        self.client.force_login(self.other)
        self.client.post(self.url, {'reason': 'spam'})
        response = self.client.post(self.url, {'reason': 'untrue'}, follow=True)
        self.assertEqual(StoryReport.objects.count(), 1)
        self.assertEqual(StoryReport.objects.get().reason, 'spam')
        self.assertContains(response, 'already reported')

    def test_cannot_report_own_story(self):
        self.client.force_login(self.author)
        self.client.post(self.url, {'reason': 'spam'})
        self.assertEqual(StoryReport.objects.count(), 0)

    def test_unknown_reason_falls_back_to_other(self):
        self.client.force_login(self.other)
        self.client.post(self.url, {'reason': '<b>'})
        self.assertEqual(StoryReport.objects.get().reason, 'other')

    def test_below_threshold_stays_published(self):
        for user in self.reporters[:REPORT_THRESHOLD - 1]:
            self.client.force_login(user)
            self.client.post(self.url, {'reason': 'spam'})
        self.story.refresh_from_db()
        self.assertEqual(self.story.status, Story.Status.APPROVED)

    def test_threshold_flips_back_to_pending(self):
        for user in self.reporters[:REPORT_THRESHOLD]:
            self.client.force_login(user)
            self.client.post(self.url, {'reason': 'spam'})
        self.story.refresh_from_db()
        self.assertEqual(self.story.status, Story.Status.PENDING)
        self.assertEqual(self.client.get(self.story.get_absolute_url()).status_code, 404)
        self.client.logout()
        self.assertNotContains(self.client.get(reverse('stories')), 'My first donation')

    def test_reapproved_story_needs_fresh_reports(self):
        # Backdate the old reports so they predate the re-approval.
        for user in self.reporters[:REPORT_THRESHOLD]:
            self.client.force_login(user)
            self.client.post(self.url, {'reason': 'spam'})
        StoryReport.objects.update(
            created_at=timezone.now() - datetime.timedelta(days=1),
        )
        self.story.refresh_from_db()
        self.story.approve()
        self.client.force_login(self.reporters[3])
        self.client.post(self.url, {'reason': 'spam'})
        self.story.refresh_from_db()
        self.assertEqual(self.story.status, Story.Status.APPROVED)

    def test_report_form_shown_to_logged_in_non_author(self):
        self.client.force_login(self.other)
        self.assertContains(self.client.get(self.story.get_absolute_url()), 'Send report')
        self.client.force_login(self.author)
        self.assertNotContains(self.client.get(self.story.get_absolute_url()), 'Send report')


class XssTests(StoryBase):
    PAYLOAD_TITLE = '<script>alert("t")</script>'
    PAYLOAD_BODY = 'Hello <img src=x onerror=alert(1)> there <script>alert(2)</script>'

    def setUp(self):
        super().setUp()
        # Bypass validation to prove the templates escape on their own.
        self.story = make_story(
            self.author, title=self.PAYLOAD_TITLE, body=self.PAYLOAD_BODY + ' padding text',
        )

    def test_detail_escapes(self):
        html = self.client.get(self.story.get_absolute_url()).content.decode()
        self.assertNotIn('<script>alert', html)
        self.assertNotIn('<img src=x', html)
        self.assertIn('&lt;script&gt;alert(2)&lt;/script&gt;', html)

    def test_feed_home_mine_escape(self):
        self.client.force_login(self.author)
        for name in ('stories', 'home', 'story-mine'):
            html = self.client.get(reverse(name)).content.decode()
            self.assertNotIn('<script>alert', html, name)
            self.assertNotIn('<img src=x', html, name)

    def test_og_tags_escape(self):
        html = self.client.get(self.story.get_absolute_url()).content.decode()
        head = html.split('</head>')[0]
        self.assertNotIn('<script>alert', head)
        self.assertNotIn('onerror=alert(1)>', head)

    def test_line_breaks_rendered_as_paragraphs(self):
        story = make_story(
            self.author, title='Line breaks',
            body='First paragraph is here for sure.\n\nSecond paragraph follows after.',
        )
        html = self.client.get(story.get_absolute_url()).content.decode()
        self.assertIn('<p>First paragraph is here for sure.</p>', html)
        self.assertIn('<p>Second paragraph follows after.</p>', html)


class TemplateTagTests(StoryBase):
    def test_returns_three_newest_approved(self):
        now = timezone.now()
        for i in range(5):
            make_story(
                self.author, title=f'Strip story {i}',
                approved_at=now - datetime.timedelta(hours=i),
            )
        make_story(self.author, status='pending', title='Hidden pending')
        stories = latest_stories()
        self.assertEqual([s.title for s in stories], ['Strip story 0', 'Strip story 1', 'Strip story 2'])
        self.assertEqual(len(latest_stories(limit=2)), 2)

    def test_single_query(self):
        for i in range(3):
            make_story(self.author, title=f'Strip story {i}')
        with self.assertNumQueries(1):
            stories = latest_stories()
            # Names and the opt-out flag must not trigger further queries.
            for story in stories:
                story.public_name
                story.initial

    def test_renders_in_template(self):
        make_story(self.author, title='Strip story')
        html = Template(
            '{% load story_tags %}{% latest_stories as s %}{% for x in s %}[{{ x.title }}]{% endfor %}'
        ).render(Context())
        self.assertEqual(html, '[Strip story]')

    def test_home_strip_with_stories(self):
        make_story(self.author, title='Hello strip')
        response = self.client.get(reverse('home'))
        self.assertContains(response, 'Hello strip')
        self.assertContains(response, reverse('stories'))
        self.assertContains(response, reverse('story-detail', args=[1]))

    def test_home_strip_empty_prompts_first_story(self):
        response = self.client.get(reverse('home'))
        self.assertContains(response, 'Be the first')
        self.assertContains(response, reverse('story-create'))


class AdminTests(StoryBase):
    def setUp(self):
        super().setUp()
        self.admin = StoryAdmin(Story, AdminSite())
        self.request = RequestFactory().get('/admin/')
        self.request.user = self.staff
        # message_user needs the messages framework; stub it.
        self.admin.message_user = lambda *a, **k: None
        self.s1 = make_story(self.author, status='pending', title='Admin one')
        self.s2 = make_story(self.other, status='pending', title='Admin two')

    def test_registered(self):
        for model in (Story, StoryReport, StoryReaction):
            self.assertIn(model, admin.site._registry)

    def test_bulk_approve_sets_approved_at_and_emails(self):
        approve_stories(self.admin, self.request, Story.objects.filter(pk__in=[self.s1.pk, self.s2.pk]))
        self.s1.refresh_from_db()
        self.s2.refresh_from_db()
        self.assertEqual(self.s1.status, Story.Status.APPROVED)
        self.assertIsNotNone(self.s1.approved_at)
        self.assertEqual(self.s2.status, Story.Status.APPROVED)
        # Only the author with an email address is notified.
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['rina.secret@example.com'])
        self.assertIn('BloodBond', mail.outbox[0].subject)

    def test_bulk_reject_clears_approved_at(self):
        self.s1.approve()
        reject_stories(self.admin, self.request, Story.objects.filter(pk=self.s1.pk))
        self.s1.refresh_from_db()
        self.assertEqual(self.s1.status, Story.Status.REJECTED)
        self.assertIsNone(self.s1.approved_at)
        self.assertEqual(len(mail.outbox), 1)

    def test_approving_already_approved_does_not_email_again(self):
        approve_stories(self.admin, self.request, Story.objects.filter(pk=self.s1.pk))
        mail.outbox.clear()
        approve_stories(self.admin, self.request, Story.objects.filter(pk=self.s1.pk))
        self.assertEqual(len(mail.outbox), 0)

    def test_report_count_column(self):
        StoryReport.objects.create(reporter=self.other, story=self.s1)
        StoryReport.objects.create(reporter=self.staff, story=self.s1)
        obj = self.admin.get_queryset(self.request).get(pk=self.s1.pk)
        self.assertEqual(self.admin.report_count(obj), 2)

    def test_admin_pages_load(self):
        admin_user = make_user(phone='01755500009', staff=True, admin=True)
        self.client.force_login(admin_user)
        response = self.client.get(reverse('admin:stories_story_changelist'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Admin one')
        response = self.client.get(
            reverse('admin:stories_story_changelist') + '?status__exact=pending&q=Admin',
        )
        self.assertEqual(response.status_code, 200)
        response = self.client.get(reverse('admin:stories_story_change', args=[self.s1.pk]))
        self.assertEqual(response.status_code, 200)

    def test_status_change_in_form_sets_approved_at(self):
        form = type('F', (), {})()
        self.s1.status = Story.Status.APPROVED
        self.admin.save_model(self.request, self.s1, form, change=True)
        self.s1.refresh_from_db()
        self.assertIsNotNone(self.s1.approved_at)
        self.assertEqual(len(mail.outbox), 1)
