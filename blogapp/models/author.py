from django.db import models
from django.contrib.auth import get_user_model
from django.utils.html import format_html


class Author(models.Model):
    User = get_user_model()
    author = models.OneToOneField(User, on_delete=models.CASCADE)
    image = models.ImageField(upload_to='post', default='no_pic.jpg')

    def __str__(self):
        # Never the login phone number: this label can end up in public
        # markup (blog bylines, admin links), so use the display name.
        from rewards.leaderboard import display_name
        return display_name(self.author)

    def admin_photo(self):
        return format_html('<img src="{}" width="100" />', self.image.url)

    admin_photo.short_description = 'Image'
    admin_photo.allow_tags = True
