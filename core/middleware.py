from django.utils.cache import patch_cache_control


class PrivateResponsesMiddleware:
    """Pages built for a signed-in user (phone numbers, inbox, history) must
    not be stored by shared caches or proxies, and should be revalidated
    rather than shown from the browser cache after logging out.

    Views that set their own Cache-Control (the public JSON feed, the service
    worker) are left alone.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        user = getattr(request, 'user', None)
        if (
            user is not None and user.is_authenticated
            and not response.has_header('Cache-Control')
        ):
            patch_cache_control(response, private=True, no_cache=True)
        return response
