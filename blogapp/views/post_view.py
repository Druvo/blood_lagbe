from django.db.models import F
from django.shortcuts import get_object_or_404, render

from blogapp.models.post import Post
from .category_count_view import get_category_count


def postView(request, id):
    post = get_object_or_404(Post, id=id)

    Post.objects.filter(id=id).update(view_count=F('view_count') + 1)
    post.view_count += 1

    previous_post = Post.objects.filter(
        timestamp__lt=post.timestamp
    ).order_by('-timestamp').first()
    next_post = Post.objects.filter(
        timestamp__gt=post.timestamp
    ).order_by('timestamp').first()

    context = {
        'post': post,
        'previous_post': previous_post,
        'next_post': next_post,
        'category_count': get_category_count(),
        'latest_post': Post.objects.exclude(id=id).order_by('-timestamp')[0:3],
    }
    return render(request, 'blogapp/post.html', context)
