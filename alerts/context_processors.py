from alerts.service import unread_count


def unread_alerts(request):
    """``alerts_unread``: one indexed count, signed-in members only, once per
    request however many templates render."""
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return {'alerts_unread': 0}
    if not hasattr(request, '_alerts_unread'):
        request._alerts_unread = unread_count(user)
    return {'alerts_unread': request._alerts_unread}
