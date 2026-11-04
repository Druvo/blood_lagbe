from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from blogapp.models import Author, Category, Post

User = get_user_model()


class BlogTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.author = Author.objects.create(
            author=User.objects.create_user(
                phone='01711000009', password='x',
            ),
        )
        cls.category = Category.objects.create(name='Health')

    def make_post(self, title, age_days=0, overview='Short body.'):
        post = Post.objects.create(
            title=title, author=self.author, overview=overview,
        )
        post.categories.add(self.category)
        Post.objects.filter(pk=post.pk).update(
            timestamp=timezone.now() - timedelta(days=age_days),
        )
        post.refresh_from_db()
        return post

    def test_url_names_are_stable(self):
        self.assertEqual(reverse('blogapp:blog-view'), '/blog/')
        self.assertEqual(reverse('post-view', kwargs={'id': 5}), '/post/5/')

    def test_empty_list_shows_empty_state(self):
        response = self.client.get(reverse('blogapp:blog-view'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No posts yet')

    def test_list_shows_posts_and_sidebar(self):
        post = self.make_post('Why blood matters', overview='A longer story.')
        response = self.client.get(reverse('blogapp:blog-view'))
        self.assertContains(response, 'Why blood matters')
        self.assertContains(response, post.get_absolute_url())
        self.assertContains(response, 'Health')
        self.assertNotContains(response, 'No posts yet')
        self.assertNotContains(response, 'col-xl-6')

    def test_pagination_splits_pages_and_survives_bad_page(self):
        for i in range(6):
            self.make_post(f'Post number {i}', age_days=i)
        url = reverse('blogapp:blog-view')
        first = self.client.get(url)
        self.assertContains(first, 'Page 1 of 2')
        self.assertContains(first, 'rel="next"')
        self.assertNotContains(first, 'rel="prev"')
        second = self.client.get(url, {'page': 2})
        self.assertContains(second, 'rel="prev"')
        self.assertEqual(second.context['queryset'].number, 2)
        # Junk and out-of-range values fall back instead of erroring.
        self.assertEqual(self.client.get(url, {'page': 'abc'}).context['queryset'].number, 1)
        self.assertEqual(self.client.get(url, {'page': 99}).context['queryset'].number, 2)

    def test_single_page_has_no_pager(self):
        self.make_post('Only post')
        self.assertNotContains(
            self.client.get(reverse('blogapp:blog-view')), 'Page 1 of',
        )

    def test_post_detail_renders_article(self):
        post = self.make_post('Aftercare basics', overview='Drink water.\n\nRest well.')
        response = self.client.get(post.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<h1>Aftercare basics</h1>')
        self.assertContains(response, 'Drink water.')
        self.assertContains(response, 'min read')
        self.assertContains(response, 'wa.me')
        self.assertContains(response, 'data-copy=')
        self.assertContains(response, 'property="og:title" content="Aftercare basics"')

    def test_post_view_counts_each_visit(self):
        post = self.make_post('Counter')
        self.client.get(post.get_absolute_url())
        self.client.get(post.get_absolute_url())
        post.refresh_from_db()
        self.assertEqual(post.view_count, 2)

    def test_author_phone_number_is_never_shown(self):
        # Author.__str__ is the login phone; public pages must use the name.
        user = self.author.author
        user.first_name, user.last_name = 'Amina', 'Khatun'
        user.save()
        post = self.make_post('Names only')
        for url in (reverse('blogapp:blog-view'), post.get_absolute_url()):
            response = self.client.get(url)
            self.assertContains(response, 'Amina Khatun')
            self.assertNotContains(response, '01711000009')

    def test_author_without_name_falls_back_to_donor_number(self):
        post = self.make_post('Anonymous author')
        response = self.client.get(post.get_absolute_url())
        self.assertContains(response, f'Donor #{self.author.author.pk}')
        self.assertNotContains(response, '01711000009')

    def test_missing_post_is_404(self):
        self.assertEqual(self.client.get('/post/9999/').status_code, 404)

    def test_previous_and_next_links(self):
        old = self.make_post('Older one', age_days=10)
        mid = self.make_post('Middle one', age_days=5)
        new = self.make_post('Newer one', age_days=1)
        response = self.client.get(mid.get_absolute_url())
        self.assertContains(response, f'href="{old.get_absolute_url()}" rel="prev"')
        self.assertContains(response, f'href="{new.get_absolute_url()}" rel="next"')
        first = self.client.get(old.get_absolute_url())
        self.assertNotContains(first, 'rel="prev"')

    def test_markup_in_post_text_is_escaped(self):
        post = self.make_post('Safe', overview='<script>alert(1)</script>')
        response = self.client.get(post.get_absolute_url())
        self.assertNotContains(response, '<script>alert(1)</script>')
        self.assertContains(response, '&lt;script&gt;')
